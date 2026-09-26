/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 * Preserved and adapted from bit2zero/Orb
 */

export class Analyser {
  private analyser: AnalyserNode | null = null;
  private bufferLength = 16;
  private dataArray: Uint8Array<ArrayBuffer> = new Uint8Array(new ArrayBuffer(16));

  constructor(nodeOrAnalyser?: AudioNode | AnalyserNode | null) {
    if (nodeOrAnalyser) {
      this.attach(nodeOrAnalyser);
    }
  }

  attach(nodeOrAnalyser: AudioNode | AnalyserNode | null): void {
    if (!nodeOrAnalyser) {
      this.analyser = null;
      return;
    }

    if ("getByteFrequencyData" in nodeOrAnalyser && typeof (nodeOrAnalyser as any).getByteFrequencyData === "function") {
      // Already an AnalyserNode
      this.analyser = nodeOrAnalyser as AnalyserNode;
      this.bufferLength = this.analyser.frequencyBinCount;
      this.dataArray = new Uint8Array(new ArrayBuffer(this.bufferLength));
    } else if (nodeOrAnalyser.context) {
      // AudioNode - create dedicated AnalyserNode with fftSize=32 matching Orb
      try {
        this.analyser = nodeOrAnalyser.context.createAnalyser();
        this.analyser.fftSize = 32;
        this.bufferLength = this.analyser.frequencyBinCount;
        this.dataArray = new Uint8Array(new ArrayBuffer(this.bufferLength));
        nodeOrAnalyser.connect(this.analyser);
      } catch (err) {
        console.warn("Could not connect audio node to Analyser:", err);
      }
    }
  }

  update(): void {
    if (this.analyser) {
      try {
        this.analyser.getByteFrequencyData(this.dataArray);
      } catch {
        // AudioContext may be suspended or closed
      }
    }
  }

  get data(): Uint8Array<ArrayBuffer> {
    return this.dataArray;
  }
}

