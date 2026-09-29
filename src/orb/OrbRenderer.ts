/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 * Mamba Fluid Particle Orb Visualizer
 */

import * as THREE from "three";
import { particleFS, particleVS } from "./shaders/particle";
import { Analyser } from "./analyser";

export type OrbState =
  | "idle"
  | "listening"
  | "thinking"
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
  coreEmissive: number;
  coreEmissiveIntensity: number;
  mix: number;
}

const STATE_VISUALS: Record<OrbState, StateVisualConfig> = {
  idle: {
    speed: 0.45,
    turbulence: 0.5,
    pull: 0.04,
    expand: 0.04,
    primary: 0x00f5ff,    // Electric cyan
    secondary: 0x0066ff,  // Electric blue
    accent: 0xffffff,     // Subtle white spark
    coreEmissive: 0x02050f,
    coreEmissiveIntensity: 0.2,
    mix: 0.0,             // Pure base palette (cyan, blue, deep-blue, white)
  },
  listening: {
    speed: 0.75,
    turbulence: 0.8,
    pull: 0.48,           // Reacts and pulls inward toward core
    expand: 0.05,
    primary: 0x00f5ff,    // Bright electric cyan
    secondary: 0x0891b2,  // Cyan energy
    accent: 0xffffff,
    coreEmissive: 0x041824,
    coreEmissiveIntensity: 0.4,
    mix: 0.4,
  },
  thinking: {
    speed: 1.6,           // Turbulent reorganizing motion
    turbulence: 1.8,
    pull: 0.1,
    expand: 0.15,
    primary: 0xa855f7,    // Electric violet
    secondary: 0x9333ea,  // Deep purple
    accent: 0x00f5ff,     // Cyan accent
    coreEmissive: 0x14041e,
    coreEmissiveIntensity: 0.45,
    mix: 0.85,
  },
  speaking: {
    speed: 0.95,
    turbulence: 0.9,
    pull: 0.05,
    expand: 0.55,         // Energy flows outward in pulses
    primary: 0x14b8a6,    // Electric teal
    secondary: 0x0d9488,  // Turquoise
    accent: 0xffffff,
    coreEmissive: 0x041a16,
    coreEmissiveIntensity: 0.4,
    mix: 0.85,
  },
  permission: {
    speed: 0.65,
    turbulence: 0.7,
    pull: 0.1,
    expand: 0.1,
    primary: 0xf59e0b,    // Amber
    secondary: 0xd97706,  // Deep gold
    accent: 0xfef08a,
    coreEmissive: 0x241202,
    coreEmissiveIntensity: 0.45,
    mix: 0.9,
  },
  error: {
    speed: 1.15,
    turbulence: 1.5,
    pull: 0.1,
    expand: 0.25,
    primary: 0xf43f5e,    // Electric rose
    secondary: 0xe11d48,  // Crimson
    accent: 0xfda4af,
    coreEmissive: 0x280406,
    coreEmissiveIntensity: 0.45,
    mix: 0.9,
  },
};

export class OrbRenderer {
  private canvas: HTMLCanvasElement;
  private scene!: THREE.Scene;
  private camera!: THREE.PerspectiveCamera;
  private renderer!: THREE.WebGLRenderer;
  private coreMesh!: THREE.Mesh<THREE.SphereGeometry, THREE.MeshStandardMaterial>;
  private particleSystem!: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>;
  private coreLight!: THREE.PointLight;

  private inputAnalyser: Analyser;
  private outputAnalyser: Analyser;

  private animationFrameId: number | null = null;
  private prevTime = performance.now();
  private currentState: OrbState = "idle";
  private isDisposed = false;

  // Smoothed dynamics for fluid state transitions
  private currentSpeed = 0.45;
  private currentTurbulence = 0.5;
  private currentPull = 0.04;
  private currentExpand = 0.04;
  private currentMix = 0.0;
  private currentPrimary = new THREE.Color(0x00f5ff);
  private currentSecondary = new THREE.Color(0x0066ff);
  private currentAccent = new THREE.Color(0xffffff);

  constructor(
    canvas: HTMLCanvasElement,
    options?: {
      inputNode?: AudioNode | AnalyserNode | null;
      outputNode?: AudioNode | AnalyserNode | null;
      initialState?: OrbState;
    }
  ) {
    this.canvas = canvas;
    this.currentState = options?.initialState || "idle";
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

  public setState(state: OrbState): void {
    this.currentState = state;
    const config = STATE_VISUALS[state] || STATE_VISUALS.idle;
    if (this.coreMesh) {
      this.coreMesh.material.emissive.setHex(config.coreEmissive);
      this.coreMesh.material.emissiveIntensity = config.coreEmissiveIntensity;
    }
    if (this.coreLight) {
      this.coreLight.color.setHex(config.primary);
    }
  }

  private init(): void {
    const width = this.canvas.clientWidth || 220;
    const height = this.canvas.clientHeight || 220;

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

    // 4. Central dark core (neural-energy / black-hole core)
    const coreGeo = new THREE.SphereGeometry(0.70, 48, 48);
    const initialConfig = STATE_VISUALS[this.currentState] || STATE_VISUALS.idle;
    const coreMat = new THREE.MeshStandardMaterial({
      color: 0x010204, // Deep pitch black
      roughness: 0.88,
      metalness: 0.12,
      emissive: initialConfig.coreEmissive,
      emissiveIntensity: initialConfig.coreEmissiveIntensity,
    });
    const coreMesh = new THREE.Mesh(coreGeo, coreMat);
    scene.add(coreMesh);
    this.coreMesh = coreMesh;

    // Subtle lights to define the core curvature
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.25);
    scene.add(ambientLight);

    const coreLight = new THREE.PointLight(initialConfig.primary, 1.8, 8);
    coreLight.position.set(2, 2, 3);
    scene.add(coreLight);
    this.coreLight = coreLight;

    // 5. Dense fine particle field (4,000 particles)
    const particleCount = 4200;
    const geometry = new THREE.BufferGeometry();
    const basePositions = new Float32Array(particleCount * 3);
    const colors = new Float32Array(particleCount * 3);
    const sizes = new Float32Array(particleCount);
    const phases = new Float32Array(particleCount);

    // Color swatches for base electric blue/cyan with subtle white and deep-blue variation
    const cyan = new THREE.Color(0x00f5ff);      // Electric cyan (60%)
    const brightBlue = new THREE.Color(0x0099ff);// Electric blue (20%)
    const deepBlue = new THREE.Color(0x1d4ed8);  // Deep blue (15%)
    const white = new THREE.Color(0xffffff);     // White spark nodes (5%)

    for (let i = 0; i < particleCount; i++) {
      // Concentrated spherical shell: radius 0.74 to 1.62
      const radiusPower = Math.pow(Math.random(), 1.7);
      const r = 0.74 + radiusPower * 0.88;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1) - Math.PI / 2;

      const x = r * Math.cos(phi) * Math.cos(theta);
      const y = r * Math.sin(phi);
      const z = r * Math.cos(phi) * Math.sin(theta);

      basePositions[i * 3] = x;
      basePositions[i * 3 + 1] = y;
      basePositions[i * 3 + 2] = z;

      // Color variation distribution
      const randColor = Math.random();
      let pColor = cyan;
      if (randColor < 0.55) {
        pColor = cyan;
      } else if (randColor < 0.78) {
        pColor = brightBlue;
      } else if (randColor < 0.94) {
        pColor = deepBlue;
      } else {
        pColor = white;
      }

      colors[i * 3] = pColor.r;
      colors[i * 3 + 1] = pColor.g;
      colors[i * 3 + 2] = pColor.b;

      // Fine particle sizes: 1.8 to 4.2
      sizes[i] = 1.8 + Math.random() * 2.4;
      phases[i] = Math.random();
    }

    geometry.setAttribute("position", new THREE.BufferAttribute(basePositions.slice(), 3));
    geometry.setAttribute("basePosition", new THREE.BufferAttribute(basePositions, 3));
    geometry.setAttribute("customColor", new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute("size", new THREE.BufferAttribute(sizes, 1));
    geometry.setAttribute("phase", new THREE.BufferAttribute(phases, 1));

    const particleMaterial = new THREE.ShaderMaterial({
      vertexShader: particleVS,
      fragmentShader: particleFS,
      uniforms: {
        time: { value: 0 },
        inputData: { value: new THREE.Vector4() },
        outputData: { value: new THREE.Vector4() },
        stateSpeed: { value: initialConfig.speed },
        stateTurbulence: { value: initialConfig.turbulence },
        statePull: { value: initialConfig.pull },
        stateExpand: { value: initialConfig.expand },
        statePrimary: { value: new THREE.Color(initialConfig.primary) },
        stateSecondary: { value: new THREE.Color(initialConfig.secondary) },
        stateAccent: { value: new THREE.Color(initialConfig.accent) },
        stateMix: { value: initialConfig.mix },
      },
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    const particleSystem = new THREE.Points(geometry, particleMaterial);
    scene.add(particleSystem);
    this.particleSystem = particleSystem;

    this.resize(width, height);
    this.animate();
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

    // Smoothly interpolate dynamics towards current state target
    const lerpFactor = Math.min(dt * 4.5, 1.0);
    this.currentSpeed += (target.speed - this.currentSpeed) * lerpFactor;
    this.currentTurbulence += (target.turbulence - this.currentTurbulence) * lerpFactor;
    this.currentPull += (target.pull - this.currentPull) * lerpFactor;
    this.currentExpand += (target.expand - this.currentExpand) * lerpFactor;
    this.currentMix += (target.mix - this.currentMix) * lerpFactor;

    this.currentPrimary.lerp(new THREE.Color(target.primary), lerpFactor);
    this.currentSecondary.lerp(new THREE.Color(target.secondary), lerpFactor);
    this.currentAccent.lerp(new THREE.Color(target.accent), lerpFactor);

    if (this.particleSystem && this.particleSystem.material) {
      const uniforms = this.particleSystem.material.uniforms;
      uniforms.time.value += dt * (1.0 + outEnergy * 0.8);
      uniforms.stateSpeed.value = this.currentSpeed;
      uniforms.stateTurbulence.value = this.currentTurbulence;
      uniforms.statePull.value = this.currentPull;
      uniforms.stateExpand.value = this.currentExpand;
      uniforms.stateMix.value = this.currentMix;
      uniforms.statePrimary.value.copy(this.currentPrimary);
      uniforms.stateSecondary.value.copy(this.currentSecondary);
      uniforms.stateAccent.value.copy(this.currentAccent);

      uniforms.inputData.value.set(
        micEnergy,
        inData[1] ? inData[1] / 255 : 0,
        inData[2] ? inData[2] / 255 : 0,
        0
      );
      uniforms.outputData.value.set(
        outEnergy,
        outData[1] ? outData[1] / 255 : 0,
        outData[2] ? outData[2] / 255 : 0,
        0
      );
    }

    // Slow organic axial rotation of the scene for continuous 3D depth
    if (this.particleSystem) {
      this.particleSystem.rotation.y += dt * 0.08 * (this.currentState === "thinking" ? 2.5 : 1.0);
      this.particleSystem.rotation.x += dt * 0.03;
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

    if (this.coreMesh) {
      this.coreMesh.geometry.dispose();
      this.coreMesh.material.dispose();
    }

    if (this.particleSystem) {
      this.particleSystem.geometry.dispose();
      this.particleSystem.material.dispose();
    }

    if (this.renderer) {
      this.renderer.dispose();
    }
  }
}
