/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 * Mamba Orb renderer — a living field of light, not a spinning object.
 *
 * Three draws: the additive haze, the main particle field, and a sparse outer
 * atmosphere that gives the field depth. Every visual parameter is interpolated
 * toward its state target in the frame loop, so state changes never snap.
 */

import * as THREE from "three";
import { particleFS, particleVS } from "./shaders/particle";
import { hazeFS, hazeVS } from "./shaders/haze";
import { Analyser } from "./analyser";

export type OrbState =
  | "idle"
  | "listening"
  | "thinking"
  | "executing"
  | "verifying"
  | "speaking"
  | "permission"
  | "error";

interface StateVisualConfig {
  speed: number;
  turbulence: number;
  pull: number;
  expand: number;
  primary: number;
  secondary: number;
  accent: number;
  mix: number;
  hazeCore: number;
  hazeEdge: number;
  hazeIntensity: number;
  breath: number;
  cohesion: number;
  directional: number;
}

/** The restrained Mamba ramp: mist cyan, soft blue-violet, sea glass, ash rose. */
const STATE_VISUALS: Record<OrbState, StateVisualConfig> = {
  idle: {
    speed: 0.3,
    turbulence: 0.34,
    pull: 0.02,
    expand: 0.03,
    primary: 0x6fc6da,
    secondary: 0x2c4a6e,
    accent: 0xdfeef5,
    mix: 0.0,
    hazeCore: 0x63bdd2,
    hazeEdge: 0x1b2740,
    hazeIntensity: 0.34,
    breath: 0.01,
    cohesion: 0.1,
    directional: 0.0,
  },
  listening: {
    speed: 0.48,
    turbulence: 0.46,
    pull: 0.46,
    expand: 0.04,
    primary: 0x7fd4e6,
    secondary: 0x2f6f96,
    accent: 0xeaf7fb,
    mix: 0.55,
    hazeCore: 0x8addec,
    hazeEdge: 0x1d3252,
    hazeIntensity: 0.46,
    breath: 0.006,
    cohesion: 0.16,
    directional: 0.0,
  },
  thinking: {
    speed: 1.05,
    turbulence: 1.2,
    pull: 0.08,
    expand: 0.12,
    primary: 0x9aa6ea,
    secondary: 0x4a4a86,
    accent: 0xc9d2ff,
    mix: 0.85,
    hazeCore: 0x93a0e4,
    hazeEdge: 0x26264a,
    hazeIntensity: 0.42,
    breath: 0.008,
    cohesion: 0.06,
    directional: 0.05,
  },
  executing: {
    speed: 1.2,
    turbulence: 0.55,
    pull: 0.06,
    expand: 0.1,
    primary: 0x7fb6e8,
    secondary: 0x35608c,
    accent: 0xd7ecff,
    mix: 0.85,
    hazeCore: 0x79aede,
    hazeEdge: 0x1e3149,
    hazeIntensity: 0.44,
    breath: 0.004,
    cohesion: 0.1,
    directional: 0.85,
  },
  verifying: {
    speed: 0.55,
    turbulence: 0.22,
    pull: 0.1,
    expand: 0.05,
    primary: 0x9adfc0,
    secondary: 0x3b7a68,
    accent: 0xdff5ea,
    mix: 0.85,
    hazeCore: 0x92d8bb,
    hazeEdge: 0x1d3630,
    hazeIntensity: 0.4,
    breath: 0.003,
    cohesion: 0.55,
    directional: 0.15,
  },
  speaking: {
    speed: 0.68,
    turbulence: 0.58,
    pull: 0.04,
    expand: 0.5,
    primary: 0x8fe0d8,
    secondary: 0x2f7f78,
    accent: 0xe8fbf7,
    mix: 0.85,
    hazeCore: 0x8bdcd4,
    hazeEdge: 0x1c3a3d,
    hazeIntensity: 0.46,
    breath: 0.008,
    cohesion: 0.08,
    directional: 0.1,
  },
  permission: {
    speed: 0.45,
    turbulence: 0.36,
    pull: 0.08,
    expand: 0.08,
    primary: 0xdcb26b,
    secondary: 0x8a6b3c,
    accent: 0xf5e3bd,
    mix: 0.9,
    hazeCore: 0xd3aa68,
    hazeEdge: 0x37291a,
    hazeIntensity: 0.36,
    breath: 0.004,
    cohesion: 0.24,
    directional: 0.0,
  },
  error: {
    speed: 0.7,
    turbulence: 0.78,
    pull: 0.08,
    expand: 0.18,
    primary: 0xd98b98,
    secondary: 0x8a4c58,
    accent: 0xf1ccd3,
    mix: 0.9,
    hazeCore: 0xd1848f,
    hazeEdge: 0x331b20,
    hazeIntensity: 0.34,
    breath: 0.002,
    cohesion: 0.06,
    directional: 0.0,
  },
};

interface FieldFade {
  coreIn: number;
  coreOut: number;
  edgeOut: number;
  edgeIn: number;
  alphaScale: number;
}

export class OrbRenderer {
  private canvas: HTMLCanvasElement;
  private scene!: THREE.Scene;
  private camera!: THREE.PerspectiveCamera;
  private renderer!: THREE.WebGLRenderer;
  private hazeMesh!: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial>;
  private fields: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>[] = [];

  private inputAnalyser: Analyser;
  private outputAnalyser: Analyser;

  private animationFrameId: number | null = null;
  private prevTime = performance.now();
  private currentState: OrbState = "idle";
  private isDisposed = false;
  private animated: boolean;

  // Smoothed dynamics for fluid state transitions
  private currentSpeed = STATE_VISUALS.idle.speed;
  private currentTurbulence = STATE_VISUALS.idle.turbulence;
  private currentPull = STATE_VISUALS.idle.pull;
  private currentExpand = STATE_VISUALS.idle.expand;
  private currentMix = STATE_VISUALS.idle.mix;
  private currentCohesion = STATE_VISUALS.idle.cohesion;
  private currentDirectional = STATE_VISUALS.idle.directional;
  private currentBreath = STATE_VISUALS.idle.breath;
  private currentHazeIntensity = STATE_VISUALS.idle.hazeIntensity;
  private currentPrimary = new THREE.Color(STATE_VISUALS.idle.primary);
  private currentSecondary = new THREE.Color(STATE_VISUALS.idle.secondary);
  private currentAccent = new THREE.Color(STATE_VISUALS.idle.accent);
  private currentHazeCore = new THREE.Color(STATE_VISUALS.idle.hazeCore);
  private currentHazeEdge = new THREE.Color(STATE_VISUALS.idle.hazeEdge);

  // Shared uniform objects: both particle layers track state through these.
  private uniforms!: { [name: string]: THREE.IUniform };
  private flowAxis = new THREE.Vector3(0, 1, 0);

  private pointer = new THREE.Vector2(0, 0);
  private pointerTarget = new THREE.Vector2(0, 0);

  constructor(
    canvas: HTMLCanvasElement,
    options?: {
      inputNode?: AudioNode | AnalyserNode | null;
      outputNode?: AudioNode | AnalyserNode | null;
      initialState?: OrbState;
      /** False with reduced motion / the in-app motion toggle: a still field. */
      animated?: boolean;
    }
  ) {
    this.canvas = canvas;
    this.currentState = options?.initialState || "idle";
    this.animated = options?.animated !== false;
    this.inputAnalyser = new Analyser(options?.inputNode);
    this.outputAnalyser = new Analyser(options?.outputNode);

    this.init();
  }

  public setInputNode(node: AudioNode | AnalyserNode | null): void {
    this.inputAnalyser.attach(node);
  }

  public setOutputNode(node: AudioNode | AnalyserNode | null): void {
    this.outputAnalyser.attach(node);
  }

  /** Normalised -1..1 pointer offset for the (capped) parallax. */
  public setPointer(x: number, y: number): void {
    this.pointerTarget.set(x, y);
  }

  public setState(state: OrbState): void {
    // Presentation only: the frame loop interpolates every visual parameter, so
    // nothing is applied imperatively here.
    this.currentState = state;
  }

  private init(): void {
    const width = this.canvas.clientWidth || 220;
    const height = this.canvas.clientHeight || 220;
    const initial = STATE_VISUALS[this.currentState] || STATE_VISUALS.idle;

    // 1. Scene - transparent background
    const scene = new THREE.Scene();
    scene.background = null;
    this.scene = scene;

    // 2. Camera
    const camera = new THREE.PerspectiveCamera(58, width / height, 0.1, 100);
    camera.position.set(0, 0, 3.8);
    this.camera = camera;

    // 3. Renderer with full native alpha transparency
    const renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      alpha: true,
      premultipliedAlpha: false,
      powerPreference: "high-performance",
    });
    renderer.setSize(width, height, false);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0); // 100% transparent clear
    this.renderer = renderer;

    // 4. Atmosphere: soft light with no surface to trace
    const hazeMat = new THREE.ShaderMaterial({
      vertexShader: hazeVS,
      fragmentShader: hazeFS,
      uniforms: {
        uCore: { value: new THREE.Color(initial.hazeCore) },
        uEdge: { value: new THREE.Color(initial.hazeEdge) },
        uIntensity: { value: initial.hazeIntensity },
        uTime: { value: 0 },
        uBreath: { value: 0 },
        uAudio: { value: 0 },
      },
      transparent: true,
      depthWrite: false,
      depthTest: false,
      blending: THREE.AdditiveBlending,
    });
    const hazeMesh = new THREE.Mesh(new THREE.PlaneGeometry(3.3, 3.3), hazeMat);
    hazeMesh.renderOrder = -1;
    scene.add(hazeMesh);
    this.hazeMesh = hazeMesh;

    // 5. Shared state uniforms for every particle layer
    this.uniforms = {
      time: { value: 0 },
      inputData: { value: new THREE.Vector4() },
      outputData: { value: new THREE.Vector4() },
      stateSpeed: { value: initial.speed },
      stateTurbulence: { value: initial.turbulence },
      statePull: { value: initial.pull },
      stateExpand: { value: initial.expand },
      stateCohesion: { value: initial.cohesion },
      stateDirectional: { value: initial.directional },
      stateFlowAxis: { value: this.flowAxis },
      uBreath: { value: 0 },
      uParallax: { value: new THREE.Vector2(0, 0) },
      statePrimary: { value: new THREE.Color(initial.primary) },
      stateSecondary: { value: new THREE.Color(initial.secondary) },
      stateAccent: { value: new THREE.Color(initial.accent) },
      stateMix: { value: initial.mix },
    };

    // 6. Two fields: the body of the light, and a sparse outer atmosphere.
    this.fields.push(
      this.buildField(2600, 0.76, 0.8, 1.6, 3.4, {
        coreIn: 0.71,
        coreOut: 0.8,
        edgeOut: 1.1,
        edgeIn: 1.85,
        alphaScale: 1.0,
      })
    );
    this.fields.push(
      this.buildField(900, 1.5, 1.35, 0.9, 2.0, {
        coreIn: 1.3,
        coreOut: 1.75,
        edgeOut: 2.2,
        edgeIn: 3.4,
        alphaScale: 0.5,
      })
    );

    this.resize(width, height);
    this.animate();
  }

  private buildField(
    count: number,
    rMin: number,
    rSpread: number,
    sizeMin: number,
    sizeMax: number,
    fade: FieldFade
  ): THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial> {
    const geometry = new THREE.BufferGeometry();
    const basePositions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const sizes = new Float32Array(count);
    const phases = new Float32Array(count);

    // Mamba's own base ramp — this is what idle actually looks like.
    const mist = new THREE.Color(0x6fc6da);
    const dusk = new THREE.Color(0x4f6f95);
    const deep = new THREE.Color(0x2c4260);
    const spark = new THREE.Color(0xdfeef5);

    for (let i = 0; i < count; i++) {
      const radiusPower = Math.pow(Math.random(), 1.7);
      const r = rMin + radiusPower * rSpread;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1) - Math.PI / 2;

      basePositions[i * 3] = r * Math.cos(phi) * Math.cos(theta);
      basePositions[i * 3 + 1] = r * Math.sin(phi);
      basePositions[i * 3 + 2] = r * Math.cos(phi) * Math.sin(theta);

      const roll = Math.random();
      const pColor = roll < 0.55 ? mist : roll < 0.78 ? dusk : roll < 0.95 ? deep : spark;
      colors[i * 3] = pColor.r;
      colors[i * 3 + 1] = pColor.g;
      colors[i * 3 + 2] = pColor.b;

      sizes[i] = sizeMin + Math.random() * (sizeMax - sizeMin);
      phases[i] = Math.random();
    }

    geometry.setAttribute("position", new THREE.BufferAttribute(basePositions.slice(), 3));
    geometry.setAttribute("basePosition", new THREE.BufferAttribute(basePositions, 3));
    geometry.setAttribute("customColor", new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute("size", new THREE.BufferAttribute(sizes, 1));
    geometry.setAttribute("phase", new THREE.BufferAttribute(phases, 1));

    const material = new THREE.ShaderMaterial({
      vertexShader: particleVS,
      fragmentShader: particleFS,
      // The state uniforms are shared; only the per-layer geometry fades differ.
      uniforms: {
        ...this.uniforms,
        uCoreIn: { value: fade.coreIn },
        uCoreOut: { value: fade.coreOut },
        uEdgeOut: { value: fade.edgeOut },
        uEdgeIn: { value: fade.edgeIn },
        uAlphaScale: { value: fade.alphaScale },
      },
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    const points = new THREE.Points(geometry, material);
    this.scene.add(points);
    return points;
  }

  public resize(width: number, height: number): void {
    if (!this.renderer || width <= 0 || height <= 0) return;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  private animate = (): void => {
    if (this.isDisposed) return;
    this.animationFrameId = requestAnimationFrame(this.animate);

    this.inputAnalyser.update();
    this.outputAnalyser.update();

    const t = performance.now();
    const dt = Math.min((t - this.prevTime) / 1000, 0.1);
    this.prevTime = t;

    const inData = this.inputAnalyser.data;
    const outData = this.outputAnalyser.data;

    // Calculate effective audio energies
    const micEnergy = inData[0] ? inData[0] / 255 : 0;
    const outEnergy = outData[0] ? outData[0] / 255 : 0;

    const target = STATE_VISUALS[this.currentState] || STATE_VISUALS.idle;

    // Smoothly interpolate dynamics towards current state target. Slow enough
    // that a state change reads as a mood shift, never a cut.
    const lerpFactor = Math.min(dt * 2.6, 1.0);
    this.currentSpeed += (target.speed - this.currentSpeed) * lerpFactor;
    this.currentTurbulence += (target.turbulence - this.currentTurbulence) * lerpFactor;
    this.currentPull += (target.pull - this.currentPull) * lerpFactor;
    this.currentExpand += (target.expand - this.currentExpand) * lerpFactor;
    this.currentMix += (target.mix - this.currentMix) * lerpFactor;
    this.currentCohesion += (target.cohesion - this.currentCohesion) * lerpFactor;
    this.currentDirectional += (target.directional - this.currentDirectional) * lerpFactor;
    this.currentBreath += (target.breath - this.currentBreath) * lerpFactor;
    this.currentHazeIntensity += (target.hazeIntensity - this.currentHazeIntensity) * lerpFactor;

    this.currentPrimary.lerp(new THREE.Color(target.primary), lerpFactor);
    this.currentSecondary.lerp(new THREE.Color(target.secondary), lerpFactor);
    this.currentAccent.lerp(new THREE.Color(target.accent), lerpFactor);
    this.currentHazeCore.lerp(new THREE.Color(target.hazeCore), lerpFactor);
    this.currentHazeEdge.lerp(new THREE.Color(target.hazeEdge), lerpFactor);

    const u = this.uniforms;
    if (this.animated) {
      u.time.value += dt * (1.0 + outEnergy * 0.8);
      this.pointer.lerp(this.pointerTarget, Math.min(dt * 1.6, 1.0));
      (u.uParallax.value as THREE.Vector2).set(this.pointer.x * 0.055, this.pointer.y * 0.045);
    }
    const clock = u.time.value as number;
    this.flowAxis
      .set(Math.sin(clock * 0.13), Math.cos(clock * 0.09), Math.sin(clock * 0.07))
      .normalize();

    u.stateSpeed.value = this.currentSpeed;
    u.stateTurbulence.value = this.currentTurbulence;
    u.statePull.value = this.currentPull;
    u.stateExpand.value = this.currentExpand;
    u.stateMix.value = this.currentMix;
    u.stateCohesion.value = this.currentCohesion;
    u.stateDirectional.value = this.currentDirectional;
    u.uBreath.value = this.animated ? this.currentBreath * Math.sin(clock * 0.55) : 0;
    u.statePrimary.value.copy(this.currentPrimary);
    u.stateSecondary.value.copy(this.currentSecondary);
    u.stateAccent.value.copy(this.currentAccent);

    u.inputData.value.set(
      micEnergy,
      inData[1] ? inData[1] / 255 : 0,
      inData[2] ? inData[2] / 255 : 0,
      0
    );
    u.outputData.value.set(
      outEnergy,
      outData[1] ? outData[1] / 255 : 0,
      outData[2] ? outData[2] / 255 : 0,
      0
    );

    const haze = this.hazeMesh?.material;
    if (haze) {
      if (this.animated) haze.uniforms.uTime.value += dt;
      haze.uniforms.uBreath.value = u.uBreath.value;
      haze.uniforms.uAudio.value = Math.max(micEnergy, outEnergy);
      haze.uniforms.uIntensity.value = this.currentHazeIntensity;
      haze.uniforms.uCore.value.copy(this.currentHazeCore);
      haze.uniforms.uEdge.value.copy(this.currentHazeEdge);
    }

    // Slow opposite rotation of the two layers for continuous 3D depth
    if (this.animated) {
      const main = this.fields[0];
      const outer = this.fields[1];
      if (main) {
        main.rotation.y += dt * 0.08;
        main.rotation.x += dt * 0.03;
      }
      if (outer) {
        outer.rotation.y -= dt * 0.03;
        outer.rotation.x -= dt * 0.012;
      }
    }

    // Render directly with transparent alpha clear
    this.renderer.clear();
    this.renderer.render(this.scene, this.camera);
  };

  public dispose(): void {
    this.isDisposed = true;
    if (this.animationFrameId !== null) {
      cancelAnimationFrame(this.animationFrameId);
      this.animationFrameId = null;
    }

    if (this.hazeMesh) {
      this.hazeMesh.geometry.dispose();
      this.hazeMesh.material.dispose();
    }

    for (const field of this.fields) {
      field.geometry.dispose();
      field.material.dispose();
    }
    this.fields = [];

    if (this.renderer) {
      this.renderer.dispose();
    }
  }
}
