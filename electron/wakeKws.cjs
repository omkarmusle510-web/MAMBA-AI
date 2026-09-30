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
        loadKws(t);
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

    loadKws(threshold);
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
function loadKws(threshold) {
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
    keywordsScore: 1.0,
    keywordsThreshold: threshold,
    keywords,
  });
  activeThreshold = threshold;
  stream = kws.createStream();
  console.log(`[WakeKWS] KWS READY in ${Date.now() - t1}ms (threshold=${threshold})`);
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
    let decodesThisCall = 0;
    while (kws.isReady(stream)) {
      kws.decode(stream);
      decodesThisCall++;
    }
    diagDecodes += decodesThisCall;
    if (decodesThisCall > 0) diagReadyCalls++;
    const r = kws.getResult(stream);
    const kw = r && typeof r.keyword === "string" ? r.keyword : "";
    // TEMP DIAG (decoder): throttled heartbeat ~5s. impliedHz exposes a
    // sample-rate mismatch: browser delivering 48 kHz while we label the
    // stream 16 kHz shows up here as impliedHz ~= 48000.
    const beatNow = Date.now();
    if (beatNow - diagLastBeat >= 5000) {
      diagLastBeat = beatNow;
      const elapsed = (beatNow - diagT0) / 1000;
      const impliedHz = elapsed > 0 ? Math.round(diagCumSamples / elapsed) : 0;
      console.log(
        `[WakeKWS diag] decoder: cumSamples=${diagCumSamples} elapsed=${elapsed.toFixed(1)}s ` +
          `impliedHz=${impliedHz} (expect ~${SAMPLE_RATE}) ` +
          `decodes=${diagDecodes} readyCalls=${diagReadyCalls} lastKeyword=${JSON.stringify(kw)}`
      );
    }
    if (kw.length > 0) {
      console.log(`[WakeKWS diag] DETECTED keyword "${kw}"`); // TEMP DIAG
      try {
        kws.reset(stream);
      } catch {}
      return kw;
    }
  } catch (err) {
    console.error(`[WakeKWS] decode error: ${(err && err.message) || err}`);
  }
  return null;
}

/** Release the decode stream (mic-side already stopped by the renderer). */
function stop() {
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
