# Wake-word assets (pinned) — sherpa-onnx local KWS prototype

## Runtime
- **sherpa-onnx npm package:** 1.13.8
  - `src/wake/sherpa/sherpa-onnx-wasm-nodejs.js` — Emscripten glue, vendored byte-identical
  - `src/wake/sherpa/sherpa-onnx-kws.js` — KWS JS API, vendored with the
    Node-only `module.exports` guard replaced by an ESM export (only change)
  - `public/wake/sherpa-onnx-wasm-nodejs.wasm` — 15 MB WASM build (environment-adaptive: WEB branch in renderer)

## KWS model (English, streaming Zipformer)
- **Model:** `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01`
- **Source:** https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2
- **License:** Apache 2.0 (runtime and model)
- **Files used (int8 variants):** `public/wake/kws/`
  - `encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx` (4.8 MB)
  - `decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx` (0.3 MB)
  - `joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx` (0.2 MB)
  - `tokens.txt`
  - `keywords.txt`

## Keyword configuration
- **Phrase:** HEY MAMBA
- **Generated:** `sherpa-onnx-cli text2token --tokens ./tokens.txt --tokens-type bpe --bpe-model ./bpe.model`
- **Input:** `HEY MAMBA`
- **Output (`public/wake/kws/keywords.txt`):** `▁HE Y ▁MA M B A`
- All 6 BPE tokens verified present in the model's `tokens.txt`. No model training was performed.
