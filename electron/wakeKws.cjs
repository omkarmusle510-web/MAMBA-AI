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

    loadKws(threshold, opts.keywordsScore);
    ready = true;
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
  try {
    stream.acceptWaveform(SAMPLE_RATE, samples);
    let detectedKw = null;
    while (kws.isReady(stream)) {
      kws.decode(stream);
      // Check detection immediately after each decode step so subsequent steps in chunk do not overwrite it
      const r = kws.getResult(stream);
      const kw = r && typeof r.keyword === "string" ? r.keyword : "";
      if (kw.length > 0 && !detectedKw) {
        detectedKw = kw;
      }
    }
    // Fallback check after decode loop
    if (!detectedKw) {
      const r = kws.getResult(stream);
      const kw = r && typeof r.keyword === "string" ? r.keyword : "";
      if (kw.length > 0) {
        detectedKw = kw;
      }
    }

    if (detectedKw && detectedKw.length > 0) {
      console.log(`[WakeKWS] detected keyword "${detectedKw}"`);
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
