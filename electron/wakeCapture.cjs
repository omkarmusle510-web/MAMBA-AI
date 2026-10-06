/**
 * TEMP DIAGNOSTIC ONLY — wake-word PCM capture.
 *
 * Writes the exact PCM fed to the production KWS (i.e. the exact samples
 * received from the renderer over IPC) to 16-bit mono 16 kHz WAV files under
 * wake-captures/, so the live signal can be replayed offline through the
 * same model/configuration (scripts/wake-replay.cjs).
 *
 * Enabled only when MAMBA_WAKE_CAPTURE=1 (optionally MAMBA_WAKE_CAPTURE_DIR).
 * Disabled: every call is a cheap no-op. Never throws into the wake path.
 * Delete this file and its wakeKws.cjs call sites once diagnosed.
 */

const fs = require("fs");
const path = require("path");

const SAMPLE_RATE = 16000;
const MAX_SECONDS = 120;

const enabled = process.env.MAMBA_WAKE_CAPTURE === "1";
const outDir =
  process.env.MAMBA_WAKE_CAPTURE_DIR ||
  path.join(path.resolve(__dirname, ".."), "wake-captures");

let fd = null;
let currentPath = "";
let fileIndex = 0;
let totalSamples = 0;
let totalChunks = 0;
let sumSquares = 0;
let peak = 0;
let startedAt = 0;
let config = { threshold: null, score: null };

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-` +
    `${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
  );
}

function header(dataBytes) {
  const h = Buffer.alloc(44);
  h.write("RIFF", 0, "ascii");
  h.writeUInt32LE(36 + dataBytes, 4);
  h.write("WAVE", 8, "ascii");
  h.write("fmt ", 12, "ascii");
  h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); // PCM
  h.writeUInt16LE(1, 22); // mono
  h.writeUInt32LE(SAMPLE_RATE, 24);
  h.writeUInt32LE(SAMPLE_RATE * 2, 28);
  h.writeUInt16LE(2, 32);
  h.writeUInt16LE(16, 34);
  h.write("data", 36, "ascii");
  h.writeUInt32LE(dataBytes, 40);
  return h;
}

function resetStats() {
  totalSamples = 0;
  totalChunks = 0;
  sumSquares = 0;
  peak = 0;
}

function openFile() {
  closeFile();
  fs.mkdirSync(outDir, { recursive: true });
  fileIndex += 1;
  currentPath = path.join(outDir, `wake-${stamp()}-${String(fileIndex).padStart(2, "0")}.wav`);
  fd = fs.openSync(currentPath, "w");
  // Explicit positions everywhere: a positional write does not advance the
  // fd offset (pwrite semantics), so appends must place themselves at 44+N.
  fs.writeSync(fd, header(0), 0, 44, 0);
  resetStats();
  startedAt = Date.now();
}

function closeFile() {
  if (fd === null) return;
  try {
    fs.writeSync(fd, header(totalSamples * 2), 0, 44, 0);
    fs.closeSync(fd);
    const seconds = totalSamples / SAMPLE_RATE;
    const manifest = {
      wav: path.basename(currentPath),
      sampleRate: SAMPLE_RATE,
      seconds: +seconds.toFixed(3),
      chunks: totalChunks,
      rms: totalSamples ? +Math.sqrt(sumSquares / totalSamples).toFixed(5) : 0,
      peak: +peak.toFixed(5),
      threshold: config.threshold,
      score: config.score,
      startedAt: startedAt ? new Date(startedAt).toISOString() : null,
      endedAt: new Date().toISOString(),
    };
    fs.writeFileSync(currentPath + ".json", JSON.stringify(manifest, null, 2));
    console.log(
      `[WakeCapture] saved ${currentPath} (${seconds.toFixed(1)}s, rms=${manifest.rms}, peak=${manifest.peak})`
    );
  } catch (err) {
    console.error(`[WakeCapture] finalize failed: ${(err && err.message) || err}`);
  } finally {
    fd = null;
    currentPath = "";
  }
}

/** Arm a capture session: the next fed chunk opens a fresh WAV. */
function begin(opts = {}) {
  if (!enabled) return;
  try {
    closeFile();
    config = { threshold: opts.threshold ?? null, score: opts.score ?? null };
    console.log(`[WakeCapture] armed: writing fed PCM into ${outDir}`);
  } catch (err) {
    console.error(`[WakeCapture] begin failed: ${(err && err.message) || err}`);
  }
}

/** Append one chunk of the exact PCM about to be fed to the KWS. */
function feed(samples) {
  if (!enabled || !samples || !samples.length) return;
  try {
    if (fd === null || totalSamples >= SAMPLE_RATE * MAX_SECONDS) openFile();
    const buf = Buffer.allocUnsafe(samples.length * 2);
    let ss = 0;
    let pk = 0;
    for (let i = 0; i < samples.length; i++) {
      let s = samples[i];
      if (s > 1) s = 1;
      else if (s < -1) s = -1;
      const a = s < 0 ? -s : s;
      if (a > pk) pk = a;
      ss += s * s;
      buf.writeInt16LE(s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff), i * 2);
    }
    fs.writeSync(fd, buf, 0, buf.length, 44 + totalSamples * 2);
    totalSamples += samples.length;
    totalChunks += 1;
    sumSquares += ss;
    if (pk > peak) peak = pk;
    // Keep the header sizes current after every chunk so a hard kill (no
    // clean close) still leaves a valid, fully readable WAV.
    fs.writeSync(fd, header(totalSamples * 2), 0, 44, 0);
    if (totalChunks === 1) {
      console.log(
        `[WakeCapture] first chunk: ${samples.constructor.name} len=${samples.length} ` +
          `rms=${Math.sqrt(ss / samples.length).toFixed(4)} peak=${pk.toFixed(4)}`
      );
    } else if (totalChunks % 40 === 1) {
      // Live progress (~every 10s of audio): makes it visible whether the
      // real microphone is actually delivering audio or digital silence.
      // Stats only — never raw samples.
      console.log(
        `[WakeCapture] progress: t=${(totalSamples / SAMPLE_RATE).toFixed(1)}s chunks=${totalChunks} ` +
          `avg_rms=${Math.sqrt(sumSquares / totalSamples).toFixed(4)} peak=${peak.toFixed(4)} ` +
          `last_chunk_rms=${Math.sqrt(ss / samples.length).toFixed(4)}`
      );
    }
  } catch (err) {
    // Diagnostics must never break the wake path.
    try {
      if (fd !== null) fs.closeSync(fd);
    } catch {}
    fd = null;
    console.error(`[WakeCapture] feed failed (capture off): ${(err && err.message) || err}`);
  }
}

/** Close the current capture file (called on stop / re-arm). */
function end() {
  if (!enabled) return;
  closeFile();
}

module.exports = { begin, feed, end, enabled };
