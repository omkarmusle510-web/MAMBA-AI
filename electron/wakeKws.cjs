/**
 * Wake-word KWS service — runs in the Electron MAIN process (Node.js).
 *
 * Why here and not the renderer: the sherpa-onnx WASM build
 * (sherpa-onnx-wasm-nodejs) requires Emscripten NODERAWFS, which is only
 * supported in a Node.js environment. It cannot initialize inside the
 * Chromium renderer (nodeIntegration: false). The orb renderer therefore
 * only captures microphone audio and streams PCM chunks here over IPC;
 * this service runs the keyword spotter and reports detections back.
 *
 * Fully offline: pinned int8 Zipformer model, no network, no API key.
 * Model assets live in dist/wake/kws/ (built from public/wake/kws/).
 * No audio is persisted; chunks are decoded and dropped.
 *
 * Lifecycle: init() loads WASM + model once (~2s, lazy on first wake
 * enable). stop() frees the decode stream; the loaded model is kept for
 * instant re-arm. Nothing here touches Mamba Core, Phase A, or the UI.
 */

const path = require("path");
const fs = require("fs");

const SAMPLE_RATE = 16000;

let kwsApi = null;
let Module = null;
let kws = null;
let stream = null;
let ready = false;
let initPromise = null;
let activeThreshold = null;

// TEMP DIAG (audio path): verify PCM chunks arrive intact over IPC.
// Throttled to ~1/s. Removed after diagnosis.
let diagChunks = 0;
let diagLastLog = 0;

// TEMP DIAG (decoder): decode-loop instrumentation. Removed after diagnosis.
let diagDecodes = 0; // total kws.decode() calls
let diagReadyCalls = 0; // acceptAudio calls where isReady() fired >= once
let diagCumSamples = 0; // cumulative samples passed to acceptWaveform
let diagT0 = 0; // wall-clock ms when feeding started
let diagLastBeat = 0;

// ---- TEMP DIAG: capture exact PCM fed to Sherpa, dump WAV, replay through the SAME kws ----
// Enable with MAMBA_WAKE_CAPTURE=1. Remove after diagnosis.
const CAP_ON = process.env.MAMBA_WAKE_CAPTURE === "1";
const CAP_SECONDS = 10;
const CAP_MAX_DUMPS = 3;
let capChunks = [];
let capSamples = 0;
let capDumps = 0;

function capWav(samples) {
  const buf = Buffer.alloc(44 + samples.length * 2);
  buf.write("RIFF", 0);
  buf.writeUInt32LE(36 + samples.length * 2, 4);
  buf.write("WAVE", 8);
  buf.write("fmt ", 12);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);
  buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(SAMPLE_RATE, 24);
  buf.writeUInt32LE(SAMPLE_RATE * 2, 28);
  buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34);
  buf.write("data", 36);
  buf.writeUInt32LE(samples.length * 2, 40);
  for (let i = 0; i < samples.length; i++) {
    const v = Math.max(-1, Math.min(1, samples[i]));
    buf.writeInt16LE(Math.round(v < 0 ? v * 32768 : v * 32767), 44 + i * 2);
  }
  return buf;
}

// Feed `samples` to a FRESH stream on the production spotter.
// chunk=0 -> single acceptWaveform; tail -> zero samples appended; then inputFinished.
function runKws(samples, chunk, tail) {
  if (!kws) return null;
  const s = kws.createStream();
  const hits = [];
  const drain = (atSample) => {
    while (kws.isReady(s)) {
      kws.decode(s);
      const r = kws.getResult(s);
      if (r && typeof r.keyword === "string" && r.keyword.length > 0) {
        hits.push({ keyword: r.keyword, atSec: +(atSample / SAMPLE_RATE).toFixed(2) });
        kws.reset(s);
      }
    }
  };
  try {
    if (chunk > 0) {
      for (let off = 0; off < samples.length; off += chunk) {
        const end = Math.min(off + chunk, samples.length);
        s.acceptWaveform(SAMPLE_RATE, samples.subarray(off, end));
        drain(end);
      }
    } else {
      s.acceptWaveform(SAMPLE_RATE, samples);
      drain(samples.length);
    }
    if (tail > 0) {
      s.acceptWaveform(SAMPLE_RATE, new Float32Array(tail));
      drain(samples.length + tail);
    }
    s.inputFinished();
    drain(samples.length + tail);
  } finally {
    s.free();
  }
  return hits;
}

function capDump(all) {
  const dir = path.join(path.resolve(__dirname, ".."), ".mamba", "wake-diag");
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `capture-${Date.now()}.wav`);
  fs.writeFileSync(file, capWav(all));

  let peak = 0;
  const perSec = [];
  for (let sec = 0; sec * SAMPLE_RATE < all.length; sec++) {
    const seg = all.subarray(sec * SAMPLE_RATE, (sec + 1) * SAMPLE_RATE);
    let sum = 0;
    let pk = 0;
    for (let i = 0; i < seg.length; i++) {
      const v = seg[i];
      sum += v * v;
      const a = v < 0 ? -v : v;
      if (a > pk) pk = a;
    }
    if (pk > peak) peak = pk;
    perSec.push(`${Math.sqrt(sum / seg.length).toFixed(4)}/${pk.toFixed(3)}`);
  }
  console.log(`[WakeKWS diag] CAPTURE saved ${file} (${(all.length / SAMPLE_RATE).toFixed(1)}s)`);
  console.log(`[WakeKWS diag] per-second rms/peak: ${perSec.join("  ")}`);

  const gain = peak > 1e-4 ? 0.9 / peak : 1;
  const norm = all.map((v) => v * gain);
  const A = runKws(all, 4096, 0);
  const B = runKws(all, 0, Math.round(0.66 * SAMPLE_RATE));
  const C = runKws(norm, 4096, 0);
  console.log(`[WakeKWS diag] REPLAY A live-like 4096 chunks      : ${JSON.stringify(A)}`);
  console.log(`[WakeKWS diag] REPLAY B one-shot + 0.66s tail      : ${JSON.stringify(B)}`);
  console.log(`[WakeKWS diag] REPLAY C normalized x${gain.toFixed(1)} (4096)  : ${JSON.stringify(C)}`);
}

function captureForDiag(samples) {
  if (!CAP_ON || capDumps >= CAP_MAX_DUMPS || !kws) return;
  capChunks.push(samples.slice());
  capSamples += samples.length;
  if (capSamples < CAP_SECONDS * SAMPLE_RATE) return;
  const all = new Float32Array(capSamples);
  let off = 0;
  for (const c of capChunks) {
    all.set(c, off);
    off += c.length;
  }
  capChunks = [];
  capSamples = 0;
  capDumps++;
  try {
    capDump(all);
  } catch (err) {
    console.error(`[WakeKWS diag] capture/replay failed: ${(err && err.message) || err}`);
  }
}

function kwsDir() {
  // <project>/dist/wake/kws — written by `npm run build` from public/wake/kws.
  return path.join(path.resolve(__dirname, ".."), "dist", "wake", "kws");
}

function forwardSlashes(p) {
  return p.replace(/\\/g, "/");
}

/**
 * Load WASM + model (once). Returns {ok:true} or {ok:false, error}.
 * Safe to call repeatedly; concurrent calls share one init.
 */
async function init(opts = {}) {
  if (ready) {
    // Re-arm after stop(): the decode stream was freed; recreate it.
    if (!stream && kws) {
      try {
        stream = kws.createStream();
      } catch (err) {
        return { ok: false, error: String((err && err.message) || err) };
      }
    }
    // Threshold changes after load: rebuild the spotter (WASM stays loaded).
    const t = typeof opts.threshold === "number" ? opts.threshold : activeThreshold;
    if (t !== null && t !== activeThreshold) {
      try {
        loadKws(t, opts.keywordsScore);
      } catch (err) {
        return { ok: false, error: String((err && err.message) || err) };
      }
    }
    return { ok: true };
  }
  if (initPromise) return initPromise;
  initPromise = (async () => {
    const threshold = typeof opts.threshold === "number" ? opts.threshold : 0.25;
    console.log("[WakeKWS] init: loading sherpa-onnx WASM in main process ...");
    const t0 = Date.now();
    const glue = require("./sherpa/sherpa-onnx-wasm-nodejs.cjs");
    kwsApi = require("./sherpa/sherpa-onnx-kws.cjs");
    Module = await glue();
    console.log(`[WakeKWS] WASM ready in ${Date.now() - t0}ms (NODERAWFS OK)`);

    const dir = kwsDir();
    for (const f of [
      "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
      "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
      "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
      "tokens.txt",
      "keywords.txt",
    ]) {
      if (!fs.existsSync(path.join(dir, f))) {
        throw new Error(`missing model asset: ${path.join(dir, f)} (run npm run build)`);
      }
    }
    const keywords = fs.readFileSync(path.join(dir, "keywords.txt"), "utf8");
    console.log(`[WakeKWS] keywords: ${JSON.stringify(keywords.trim())}`);

    // TEMP DIAG: identify the exact runtime assets (compare with the offline-test model).
    try {
      const crypto = require("crypto");
      for (const f of [
        "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "tokens.txt",
      ]) {
        const b = fs.readFileSync(path.join(dir, f));
        console.log(`[WakeKWS diag] asset ${f} bytes=${b.length} sha256=${crypto.createHash("sha256").update(b).digest("hex").slice(0, 16)}`);
      }
    } catch {}

    loadKws(threshold, opts.keywordsScore);
    ready = true;
    // TEMP DIAG (decoder): the exact rates sherpa assumes. Removed after diagnosis.
    console.log(`[WakeKWS diag] rates: acceptWaveform=${SAMPLE_RATE} featConfig.samplingRate=${SAMPLE_RATE}`);
    return { ok: true };
  })();
  try {
    return await initPromise;
  } catch (err) {
    initPromise = null;
    const msg = String((err && err.message) || err);
    console.error(`[WakeKWS] init FAILED: ${msg}`);
    return { ok: false, error: msg };
  }
}

/** (Re)create the spotter. Model files stay on disk; Module stays loaded. */
function loadKws(threshold, keywordsScore) {
  if (stream) {
    try {
      stream.free();
    } catch {}
    stream = null;
  }
  if (kws) {
    try {
      kws.free();
    } catch {}
    kws = null;
  }
  const dir = kwsDir();
  const keywords = fs.readFileSync(path.join(dir, "keywords.txt"), "utf8");
  const t1 = Date.now();
  const score = typeof keywordsScore === "number" ? keywordsScore : 1.5;
  kws = kwsApi.createKws(Module, {
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
    keywordsScore: score,
    keywordsThreshold: threshold,
    keywords,
  });
  activeThreshold = threshold;
  stream = kws.createStream();
  console.log(`[WakeKWS] KWS READY in ${Date.now() - t1}ms (threshold=${threshold}, score=${score})`);
}

/**
 * Feed one 16 kHz mono float32 chunk. Returns the detected keyword string,
 * or null. Never throws.
 */
function acceptAudio(input) {
  if (!ready || !kws || !stream) return null;
  let samples = null;
  if (input instanceof Float32Array) samples = input;
  else if (input && input.length) {
    try {
      samples = Float32Array.from(input);
    } catch {
      return null;
    }
  }
  if (!samples || samples.length === 0) return null;
  captureForDiag(samples);
  // TEMP DIAG (audio path): chunk arrival, sample count, RMS, peak.
  diagChunks++;
  const diagNow = Date.now();
  if (diagNow - diagLastLog >= 1000) {
    diagLastLog = diagNow;
    let peak = 0;
    let sum = 0;
    for (let i = 0; i < samples.length; i++) {
      const v = samples[i];
      sum += v * v;
      const a = v < 0 ? -v : v;
      if (a > peak) peak = a;
    }
    const rms = Math.sqrt(sum / samples.length);
    console.log(
      `[WakeKWS diag] audio in: chunks=${diagChunks} samples=${samples.length} ` +
        `rms=${rms.toFixed(4)} peak=${peak.toFixed(4)}`
    );
  }
  try {
    stream.acceptWaveform(SAMPLE_RATE, samples);
    diagCumSamples += samples.length;
    if (!diagT0) {
      diagT0 = Date.now();
      diagLastBeat = diagT0; // first heartbeat 5s after feeding starts
    }
    let detectedKw = null;
    let decodesThisCall = 0;
    while (kws.isReady(stream)) {
      kws.decode(stream);
      decodesThisCall++;
      // Check detection immediately after each decode step so subsequent steps in chunk do not overwrite it
      const r = kws.getResult(stream);
      const kw = r && typeof r.keyword === "string" ? r.keyword : "";
      if (kw.length > 0 && !detectedKw) {
        detectedKw = kw;
      }
    }
    diagDecodes += decodesThisCall;
    if (decodesThisCall > 0) diagReadyCalls++;

    // Fallback check after decode loop
    if (!detectedKw) {
      const r = kws.getResult(stream);
      const kw = r && typeof r.keyword === "string" ? r.keyword : "";
      if (kw.length > 0) {
        detectedKw = kw;
      }
    }

    const beatNow = Date.now();
    if (beatNow - diagLastBeat >= 5000) {
      diagLastBeat = beatNow;
      const elapsed = (beatNow - diagT0) / 1000;
      const impliedHz = elapsed > 0 ? Math.round(diagCumSamples / elapsed) : 0;
      console.log(
        `[WakeKWS diag] decoder: cumSamples=${diagCumSamples} elapsed=${elapsed.toFixed(1)}s ` +
          `impliedHz=${impliedHz} (expect ~${SAMPLE_RATE}) ` +
          `decodes=${diagDecodes} readyCalls=${diagReadyCalls} lastKeyword=${JSON.stringify(detectedKw || "")}`
      );
    }
    if (detectedKw && detectedKw.length > 0) {
      console.log(`[WakeKWS diag] DETECTED keyword "${detectedKw}"`); // TEMP DIAG
      try {
        kws.reset(stream);
      } catch {}
      return detectedKw;
    }
  } catch (err) {
    console.error(`[WakeKWS] decode error: ${(err && err.message) || err}`);
  }
  return null;
}

/** Release the decode stream (mic-side already stopped by the renderer). */
function stop() {
  capChunks = [];
  capSamples = 0;
  if (stream) {
    try {
      stream.free();
    } catch {}
    stream = null;
  }
  // Keep the loaded model for instant re-arm.
}

function isReady() {
  return ready;
}

module.exports = { init, acceptAudio, stop, isReady };
