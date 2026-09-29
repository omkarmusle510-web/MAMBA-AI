# Wake-word assets (pinned) — sherpa-onnx local KWS prototype

## Runtime boundary
The sherpa-onnx WASM build (`sherpa-onnx-wasm-nodejs`) requires Emscripten
NODERAWFS, which is only supported in a Node.js environment. It **cannot**
initialize inside the Chromium renderer, so the spotter runs in the
**Electron main process** (`electron/wakeKws.cjs`, plain Node.js):
- main process: WASM + KWS model + decode loop
- orb renderer: microphone capture only, streams 16 kHz PCM chunks to main
  over IPC (`mamba:wake-kws-audio`); detections come back as
  `mamba:wake-kws-detected` and flow through the existing WakeController
  trigger path.

## Runtime
- **sherpa-onnx npm package:** 1.13.8
  - `electron/sherpa/sherpa-onnx-wasm-nodejs.cjs` — Emscripten glue,
    pristine upstream (UMD), loaded via `require()` in the main process
  - `electron/sherpa/sherpa-onnx-kws.cjs` — KWS JS API, pristine upstream
    (Node `module.exports` guard passes in the main process)
  - `electron/sherpa/sherpa-onnx-wasm-nodejs.wasm` — 15 MB WASM build,
    loaded from disk next to the glue (`__dirname`-relative locateFile)
  - No native addons, no electron-rebuild, no Python at runtime.

## KWS model (English, streaming Zipformer)
- **Model:** `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01`
- **Source:** https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2
- **License:** Apache 2.0 (runtime and model)
- **Location:** `public/wake/kws/` → built to `dist/wake/kws/`; the main
  process reads the model files from disk at `<project>/dist/wake/kws/`
  (int8 encoder/decoder/joiner + `tokens.txt` + `keywords.txt`).

## Keyword configuration
- **Phrase:** HEY MAMBA
- **Generated:** `sherpa-onnx-cli text2token --tokens ./tokens.txt --tokens-type bpe --bpe-model ./bpe.model`
- **Input:** `HEY MAMBA`
- **Output (`public/wake/kws/keywords.txt`):** `▁HE Y ▁MA M B A`
- All 6 BPE tokens verified present in the model's `tokens.txt`. No model training was performed.
