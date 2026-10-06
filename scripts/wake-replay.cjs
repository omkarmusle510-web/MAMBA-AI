#!/usr/bin/env node
/**
 * Offline wake-word replay harness (diagnostic).
 *
 * Feeds a 16 kHz mono WAV through the same sherpa-onnx KWS model and
 * configuration the production pipeline uses:
 *
 *   direct — builds the spotter with the exact production config
 *            (mirrors electron/wakeKws.cjs loadKws byte-for-byte)
 *   prod   — drives electron/wakeKws.cjs itself
 *
 * A `<file>.json` manifest next to the WAV (written by
 * electron/wakeCapture.cjs during a live session) supplies the
 * threshold/score active during the recording; CLI flags override it.
 *
 * Usage:
 *   node scripts/wake-replay.cjs <file.wav> [--via direct|prod|both]
 *     [--model-dir DIR] [--threshold N] [--score N] [--chunk N]
 *     [--keywords "TOKENS"] [--keywords-file FILE]
 *     [--pad-seconds N] [--no-drain] [--verbose]
 *
 * Detection beyond EOF: after all samples are fed, direct mode calls
 * inputFinished() and drains remaining decode steps (labeled "drain",
 * toggled by --no-drain); prod mode mirrors live streaming exactly and
 * never drains.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const SAMPLE_RATE = 16000;
const VALUE_FLAGS = new Set([
  "--via", "--model-dir", "--threshold", "--score", "--chunk",
  "--keywords", "--keywords-file", "--pad-seconds",
]);

function usage() {
  console.error("usage: node scripts/wake-replay.cjs <file.wav> [--via direct|prod|both]");
  console.error("       [--model-dir DIR] [--threshold N] [--score N] [--chunk N]");
  console.error("       [--keywords \"TOKENS\"] [--keywords-file FILE] [--pad-seconds N]");
  console.error("       [--no-drain] [--verbose]");
}

function parseArgs(argv) {
  const args = {
    wav: null, via: "both", modelDir: null, threshold: null, score: null,
    chunk: 4096, keywords: null, keywordsFile: null, padSeconds: 1.0,
    drain: true, verbose: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (VALUE_FLAGS.has(a)) {
      const v = argv[++i];
      if (v === undefined) throw new Error(`missing value for ${a}`);
      if (a === "--via") args.via = v;
      else if (a === "--model-dir") args.modelDir = v;
      else if (a === "--threshold") args.threshold = Number(v);
      else if (a === "--score") args.score = Number(v);
      else if (a === "--chunk") args.chunk = Number(v);
      else if (a === "--keywords") args.keywords = v;
      else if (a === "--keywords-file") args.keywordsFile = v;
      else if (a === "--pad-seconds") args.padSeconds = Number(v);
    } else if (a === "--no-drain") args.drain = false;
    else if (a === "--verbose") args.verbose = true;
    else if (a.startsWith("--")) throw new Error(`unknown flag: ${a}`);
    else if (args.wav === null) args.wav = a;
    else throw new Error(`unexpected argument: ${a}`);
  }
  return args;
}

/**
 * Minimal RIFF parser. Supports PCM16 (format 1), IEEE float32 (format 3),
 * and WAVE_FORMAT_EXTENSIBLE (0xFFFE) wrapping either. Downmixes to mono.
 */
function readWav(file) {
  const buf = fs.readFileSync(file);
  if (buf.length < 12 || buf.toString("ascii", 0, 4) !== "RIFF" || buf.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error(`not a RIFF/WAVE file: ${file}`);
  }
  let fmt = null;
  let dataOffset = -1;
  let dataLen = 0;
  let recovered = false;
  let off = 12;
  while (off + 8 <= buf.length) {
    const id = buf.toString("ascii", off, off + 4);
    const size = buf.readUInt32LE(off + 4);
    const body = off + 8;
    if (id === "fmt " && size >= 16) {
      let format = buf.readUInt16LE(body);
      const channels = buf.readUInt16LE(body + 2);
      const sampleRate = buf.readUInt32LE(body + 4);
      const bits = buf.readUInt16LE(body + 14);
      if (format === 0xfffe && size >= 40) format = buf.readUInt16LE(body + 24);
      fmt = { format, channels, sampleRate, bits };
    } else if (id === "data") {
      dataOffset = body;
      const actual = buf.length - body;
      if (size === 0 && actual > 0) {
        // Sizes are only patched on clean close; a killed process leaves
        // data size 0 while the PCM bytes are still on disk.
        dataLen = actual;
        recovered = true;
      } else {
        dataLen = Math.min(size, actual);
      }
    }
    off = body + size + (size % 2);
  }
  if (!fmt) throw new Error("missing fmt chunk");
  if (dataOffset < 0) throw new Error("missing data chunk");
  if (fmt.sampleRate !== SAMPLE_RATE) {
    throw new Error(`expected ${SAMPLE_RATE} Hz, got ${fmt.sampleRate} Hz (resample first)`);
  }
  if (fmt.channels < 1 || fmt.channels > 2) {
    throw new Error(`unsupported channel count: ${fmt.channels}`);
  }
  const ch = fmt.channels;
  let samples;
  if (fmt.format === 1 && fmt.bits === 16) {
    const frames = Math.floor(dataLen / (2 * ch));
    samples = new Float32Array(frames);
    for (let i = 0; i < frames; i++) {
      let acc = 0;
      for (let c = 0; c < ch; c++) {
        acc += buf.readInt16LE(dataOffset + (i * ch + c) * 2) / 0x8000;
      }
      samples[i] = acc / ch;
    }
  } else if (fmt.format === 3 && fmt.bits === 32) {
    const frames = Math.floor(dataLen / (4 * ch));
    samples = new Float32Array(frames);
    for (let i = 0; i < frames; i++) {
      let acc = 0;
      for (let c = 0; c < ch; c++) {
        acc += buf.readFloatLE(dataOffset + (i * ch + c) * 4);
      }
      samples[i] = acc / ch;
    }
  } else {
    throw new Error(`unsupported format: format=${fmt.format} bits=${fmt.bits} (need PCM16 or float32)`);
  }
  return { samples, channels: ch, format: fmt.format, bits: fmt.bits, recovered };
}

function forwardSlashes(p) {
  return p.replace(/\\/g, "/");
}

function defaultModelDir() {
  const built = path.join(PROJECT_ROOT, "dist", "wake", "kws");
  if (fs.existsSync(path.join(built, "tokens.txt"))) return built;
  return path.join(PROJECT_ROOT, "public", "wake", "kws");
}

function resolveConfig(args) {
  const modelDir = args.modelDir
    ? path.resolve(args.modelDir)
    : defaultModelDir();
  for (const f of ["tokens.txt", "keywords.txt"]) {
    if (!fs.existsSync(path.join(modelDir, f))) {
      throw new Error(`missing ${f} in model dir: ${modelDir}`);
    }
  }
  let threshold = args.threshold;
  let score = args.score;
  const manifestCandidates = [
    args.wav + ".json",
    args.wav.replace(/\.wav$/i, "") + ".json",
  ];
  let manifest = null;
  for (const manifestPath of manifestCandidates) {
    if (fs.existsSync(manifestPath)) {
      try {
        manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
      } catch {
        manifest = null;
      }
      break;
    }
  }
  if (manifest) {
    if (threshold === null && typeof manifest.threshold === "number") threshold = manifest.threshold;
    if (score === null && typeof manifest.score === "number") score = manifest.score;
  }
  // Defaults mirror the production operating point (sensitivity 60):
  // threshold 0.03 / boost 5.0, calibrated on real-mic captures.
  if (threshold === null) threshold = 0.03;
  if (score === null) score = 5.0;
  if (!(args.chunk > 0)) throw new Error(`invalid --chunk: ${args.chunk}`);
  if (!(args.padSeconds >= 0)) throw new Error(`invalid --pad-seconds: ${args.padSeconds}`);

  let keywords = null;
  if (args.keywords !== null) keywords = args.keywords;
  else if (args.keywordsFile) keywords = fs.readFileSync(path.resolve(args.keywordsFile), "utf8");
  else keywords = fs.readFileSync(path.join(modelDir, "keywords.txt"), "utf8");

  return { modelDir, threshold, score, keywords, manifest };
}

function pad(audio, seconds) {
  if (!(seconds > 0)) return audio;
  const zeros = new Float32Array(Math.round(seconds * SAMPLE_RATE));
  const out = new Float32Array(audio.length + zeros.length);
  out.set(audio, 0);
  return out;
}

function buildDirectConfig(cfg) {
  const dir = cfg.modelDir;
  return {
    featConfig: { samplingRate: SAMPLE_RATE, featureDim: 80 },
    modelConfig: {
      transducer: {
        encoder: forwardSlashes(path.join(dir, "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx")),
        decoder: forwardSlashes(path.join(dir, "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx")),
        joiner: forwardSlashes(path.join(dir, "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx")),
      },
      tokens: forwardSlashes(path.join(dir, "tokens.txt")),
      provider: "cpu",
      modelType: "",
      numThreads: 1,
      debug: 0,
      modelingUnit: "bpe",
    },
    maxActivePaths: 4,
    numTrailingBlanks: 1,
    keywordsScore: cfg.score,
    keywordsThreshold: cfg.threshold,
    keywords: cfg.keywords,
  };
}

async function runDirect(args, audio, cfg) {
  const tag = "[replay:direct]";
  const glue = require(path.join(PROJECT_ROOT, "electron", "sherpa", "sherpa-onnx-wasm-nodejs.cjs"));
  const kwsApi = require(path.join(PROJECT_ROOT, "electron", "sherpa", "sherpa-onnx-kws.cjs"));
  const Module = await glue();
  const kws = kwsApi.createKws(Module, buildDirectConfig(cfg));
  const stream = kws.createStream();

  const detections = [];
  let decodes = 0;
  let chunks = 0;

  const checkResult = (r, t, during) => {
    const kw = r && typeof r.keyword === "string" ? r.keyword : "";
    if (args.verbose && kw.length > 0) {
      console.log(`${tag} t=${t.toFixed(2)}s result=${JSON.stringify(r)}`);
    }
    if (kw.length > 0) {
      detections.push({ t: Number(t.toFixed(3)), during, keyword: kw });
      console.log(`${tag} DETECTED "${kw}" at t=${t.toFixed(2)}s (${during})`);
      return true;
    }
    return false;
  };

  for (let off = 0; off < audio.length; off += args.chunk) {
    const c = audio.subarray(off, Math.min(off + args.chunk, audio.length));
    chunks++;
    stream.acceptWaveform(SAMPLE_RATE, c);
    let det = false;
    while (kws.isReady(stream)) {
      kws.decode(stream);
      decodes++;
      det = checkResult(
        kws.getResult(stream),
        (off + c.length) / SAMPLE_RATE,
        "stream",
      ) || det;
    }
    if (!det) {
      // Mirror production: final result check after the inner loop.
      checkResult(kws.getResult(stream), (off + c.length) / SAMPLE_RATE, "stream");
    }
  }

  if (args.drain) {
    stream.inputFinished();
    const tEnd = audio.length / SAMPLE_RATE;
    while (kws.isReady(stream)) {
      kws.decode(stream);
      decodes++;
      checkResult(kws.getResult(stream), tEnd, "drain");
    }
    checkResult(kws.getResult(stream), tEnd, "drain");
  }

  stream.free();
  kws.free();
  console.log(`${tag} summary: chunks=${chunks} decodes=${decodes} detections=${JSON.stringify(detections)}`);
  return detections;
}

async function runProd(args, audio, cfg) {
  const tag = "[replay:prod]";
  const wakeKws = require(path.join(PROJECT_ROOT, "electron", "wakeKws.cjs"));
  const initRes = await wakeKws.init({ threshold: cfg.threshold, keywordsScore: cfg.score });
  if (!initRes || !initRes.ok) {
    throw new Error(`wakeKws.init failed: ${initRes && initRes.error}`);
  }
  const detections = [];
  let chunks = 0;
  for (let off = 0; off < audio.length; off += args.chunk) {
    const c = audio.subarray(off, Math.min(off + args.chunk, audio.length));
    chunks++;
    const kw = wakeKws.acceptAudio(c);
    if (kw && kw.length > 0) {
      const t = (off + c.length) / SAMPLE_RATE;
      detections.push({ t: Number(t.toFixed(3)), during: "stream", keyword: kw });
      console.log(`${tag} DETECTED "${kw}" at t=${t.toFixed(2)}s (stream)`);
    }
  }
  wakeKws.stop();
  console.log(`${tag} summary: chunks=${chunks} detections=${JSON.stringify(detections)}`);
  return detections;
}

async function main() {
  const rawArgs = process.argv.slice(2);
  let args;
  try {
    args = parseArgs(rawArgs);
  } catch (err) {
    console.error(String(err.message || err));
    usage();
    process.exit(2);
  }
  if (!args.wav) {
    usage();
    process.exit(2);
  }
  if (!["direct", "prod", "both"].includes(args.via)) {
    console.error(`invalid --via: ${args.via} (expected direct|prod|both)`);
    process.exit(2);
  }
  if (!fs.existsSync(args.wav)) {
    console.error(`no such file: ${args.wav}`);
    process.exit(2);
  }

  if (args.via === "both") {
    // One Emscripten instance per process; run each mode in a child.
    const passthrough = [];
    for (let i = 0; i < rawArgs.length; i++) {
      if (rawArgs[i] === "--via") { i++; continue; }
      passthrough.push(rawArgs[i]);
    }
    let exitCode = 0;
    for (const mode of ["direct", "prod"]) {
      console.log(`[replay] --- mode: ${mode} ---`);
      const child = spawnSync(
        process.execPath,
        [__filename, ...passthrough, "--via", mode],
        { stdio: "inherit" },
      );
      if (child.error) {
        console.error(`[replay] failed to spawn ${mode}: ${child.error.message}`);
        exitCode = 1;
      } else if (child.status !== 0) {
        exitCode = child.status || 1;
      }
    }
    process.exit(exitCode);
  }

  const modelDirArg = { modelDir: args.modelDir };
  let cfg;
  try {
    cfg = resolveConfig({ ...args, ...modelDirArg });
  } catch (err) {
    console.error(String(err.message || err));
    process.exit(2);
  }

  const wav = readWav(args.wav);
  if (wav.recovered) {
    console.log(
      `[replay] note: WAV declares data size 0 (unclean close) — ` +
      `recovered ${wav.samples.length * 2} data bytes from file length`,
    );
  }
  const audio = pad(wav.samples, args.padSeconds);
  const seconds = audio.length / SAMPLE_RATE;
  console.log(
    `[replay] file=${args.wav} (${wav.channels}ch fmt=${wav.format} bits=${wav.bits}) ` +
    `samples=${wav.samples.length} (${(wav.samples.length / SAMPLE_RATE).toFixed(2)}s) ` +
    `+pad=${args.padSeconds}s -> ${seconds.toFixed(2)}s`,
  );
  console.log(
    `[replay] modelDir=${cfg.modelDir} threshold=${cfg.threshold} score=${cfg.score} ` +
    `chunk=${args.chunk} keywords=${JSON.stringify(cfg.keywords.trim())}`,
  );
  if (cfg.manifest) {
    console.log(`[replay] capture manifest: ${JSON.stringify(cfg.manifest)}`);
  }

  let detections;
  try {
    detections = args.via === "direct"
      ? await runDirect(args, audio, cfg)
      : await runProd(args, audio, cfg);
  } catch (err) {
    console.error(`[replay:${args.via}] ERROR: ${(err && err.stack) || err}`);
    process.exit(1);
  }
  console.log(`[replay:${args.via}] RESULT ${JSON.stringify({ mode: args.via, detections })}`);
}

main().catch((err) => {
  console.error(`[replay] fatal: ${(err && err.stack) || err}`);
  process.exit(1);
});
