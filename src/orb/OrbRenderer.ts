/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 * Preserved and adapted from bit2zero/Orb
 */

import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { FXAAShader } from "three/addons/shaders/FXAAShader.js";
import { EXRLoader } from "three/addons/loaders/EXRLoader.js";

import { backdropFS, backdropVS } from "./shaders/backdrop";
import { sphereVS } from "./shaders/sphere";
import { Analyser } from "./analyser";

export type OrbState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "permission"
  | "error";

interface StateColorConfig {
  color: number;
  emissive: number;
  emissiveIntensity: number;
  lightColor: number;
}

const STATE_COLORS: Record<OrbState, StateColorConfig> = {
  idle: {
    color: 0x0a0d24,
    emissive: 0x1e1b4b,
    emissiveIntensity: 1.6,
    lightColor: 0x6366f1,
  },
  listening: {
    color: 0x041824,
    emissive: 0x0891b2,
    emissiveIntensity: 2.2,
    lightColor: 0x06b6d4,
  },
  thinking: {
    color: 0x1a072b,
    emissive: 0x9333ea,
    emissiveIntensity: 2.4,
    lightColor: 0xa855f7,
  },
  speaking: {
    color: 0x04241e,
    emissive: 0x0d9488,
    emissiveIntensity: 2.2,
    lightColor: 0x14b8a6,
  },
  permission: {
    color: 0x291804,
    emissive: 0xd97706,
    emissiveIntensity: 2.5,
    lightColor: 0xf59e0b,
  },
  error: {
    color: 0x2b0707,
    emissive: 0xe11d48,
    emissiveIntensity: 2.2,
    lightColor: 0xf43f5e,
  },
};

export class OrbRenderer {
  private canvas: HTMLCanvasElement;
  private scene!: THREE.Scene;
  private camera!: THREE.PerspectiveCamera;
  private renderer!: THREE.WebGLRenderer;
  private composer!: EffectComposer;
  private sphere!: THREE.Mesh<THREE.IcosahedronGeometry, THREE.MeshStandardMaterial>;
  private backdrop!: THREE.Mesh<THREE.IcosahedronGeometry, THREE.RawShaderMaterial>;
  private bloomPass!: UnrealBloomPass;
  private fxaaPass!: ShaderPass;
  private keyLight!: THREE.PointLight;
  private rimLight!: THREE.DirectionalLight;

  private inputAnalyser: Analyser;
  private outputAnalyser: Analyser;

  private animationFrameId: number | null = null;
  private prevTime = performance.now();
  private rotation = new THREE.Vector3(0, 0, 0);
  private currentState: OrbState = "idle";
  private isDisposed = false;

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
    this.applyStateTheme(state);
  }

  private applyStateTheme(state: OrbState): void {
    if (!this.sphere) return;
    const theme = STATE_COLORS[state] || STATE_COLORS.idle;
    const mat = this.sphere.material;
    mat.color.setHex(theme.color);
    mat.emissive.setHex(theme.emissive);
    mat.emissiveIntensity = theme.emissiveIntensity;
    if (this.keyLight) {
      this.keyLight.color.setHex(theme.lightColor);
    }
  }

  private init(): void {
    const width = this.canvas.clientWidth || 300;
    const height = this.canvas.clientHeight || 300;

    // 1. Scene & Background
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x070913);
    this.scene = scene;

    // 2. Backdrop from bit2zero/Orb
    const backdropGeo = new THREE.IcosahedronGeometry(10, 5);
    const backdropMat = new THREE.RawShaderMaterial({
      uniforms: {
        resolution: { value: new THREE.Vector2(width, height) },
        rand: { value: 0 },
      },
      vertexShader: backdropVS,
      fragmentShader: backdropFS,
      glslVersion: THREE.GLSL3,
      side: THREE.BackSide,
      depthWrite: false,
    });
    const backdrop = new THREE.Mesh(backdropGeo, backdropMat);
    scene.add(backdrop);
    this.backdrop = backdrop;

    // 3. Camera
    const camera = new THREE.PerspectiveCamera(75, width / height, 0.1, 1000);
    camera.position.set(2, -2, 5);
    this.camera = camera;

    // 4. Lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambientLight);

    const keyLight = new THREE.PointLight(STATE_COLORS[this.currentState].lightColor, 3.5, 20);
    keyLight.position.set(3, 4, 4);
    scene.add(keyLight);
    this.keyLight = keyLight;

    const rimLight = new THREE.DirectionalLight(0x88bbff, 1.8);
    rimLight.position.set(-4, -3, -2);
    scene.add(rimLight);
    this.rimLight = rimLight;

    // 5. Renderer
    const renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: false,
      alpha: true,
      powerPreference: "high-performance",
    });
    renderer.setSize(width, height, false);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer = renderer;

    // 6. Geometry & Shader deformation from bit2zero/Orb
    const geometry = new THREE.IcosahedronGeometry(1, 10);
    const initialTheme = STATE_COLORS[this.currentState] || STATE_COLORS.idle;

    const sphereMaterial = new THREE.MeshStandardMaterial({
      color: initialTheme.color,
      metalness: 0.5,
      roughness: 0.1,
      emissive: initialTheme.emissive,
      emissiveIntensity: initialTheme.emissiveIntensity,
    });

    sphereMaterial.onBeforeCompile = (shader) => {
      shader.uniforms.time = { value: 0 };
      shader.uniforms.inputData = { value: new THREE.Vector4() };
      shader.uniforms.outputData = { value: new THREE.Vector4() };

      sphereMaterial.userData.shader = shader;
      shader.vertexShader = sphereVS;
    };

    const sphere = new THREE.Mesh(geometry, sphereMaterial);
    scene.add(sphere);
    sphere.visible = true; // Always visible; optional EXR adds reflection
    this.sphere = sphere;

    // 7. Optional environment map loader (non-blocking fallback preserved)
    try {
      const pmremGenerator = new THREE.PMREMGenerator(renderer);
      pmremGenerator.compileEquirectangularShader();
      new EXRLoader().load(
        "piz_compressed.exr",
        (texture: THREE.Texture) => {
          texture.mapping = THREE.EquirectangularReflectionMapping;
          const exrCubeRenderTarget = pmremGenerator.fromEquirectangular(texture);
          sphereMaterial.envMap = exrCubeRenderTarget.texture;
          sphereMaterial.needsUpdate = true;
        },
        undefined,
        () => {
          // Fallback gracefully without error
        }
      );
    } catch {
      // Non-critical environment map failure
    }

    // 8. Post-processing Composer & Bloom
    const renderPass = new RenderPass(scene, camera);
    const bloomPass = new UnrealBloomPass(
      new THREE.Vector2(width, height),
      2.8, // Bloom strength adjusted for balanced glow
      0.45, // Radius
      0.15  // Threshold
    );
    this.bloomPass = bloomPass;

    const fxaaPass = new ShaderPass(FXAAShader);
    this.fxaaPass = fxaaPass;

    const composer = new EffectComposer(renderer);
    composer.addPass(renderPass);
    composer.addPass(bloomPass);
    this.composer = composer;

    this.resize(width, height);
    this.animate();
  }

  public resize(width: number, height: number): void {
    if (!this.renderer || !this.composer || width <= 0 || height <= 0) return;

    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();

    const dpr = this.renderer.getPixelRatio();
    this.renderer.setSize(width, height, false);
    this.composer.setSize(width, height);

    if (this.backdrop && this.backdrop.material) {
      this.backdrop.material.uniforms.resolution.value.set(width * dpr, height * dpr);
    }

    if (this.fxaaPass && this.fxaaPass.material) {
      this.fxaaPass.material.uniforms["resolution"].value.set(
        1 / (width * dpr),
        1 / (height * dpr)
      );
    }
  }

  private animate = (): void => {
    if (this.isDisposed) return;

    this.animationFrameId = requestAnimationFrame(this.animate);

    this.inputAnalyser.update();
    this.outputAnalyser.update();

    const t = performance.now();
    const dt = (t - this.prevTime) / (1000 / 60);
    this.prevTime = t;

    if (!this.backdrop || !this.sphere) return;

    const backdropMaterial = this.backdrop.material;
    backdropMaterial.uniforms.rand.value = Math.random() * 10000;

    const sphereMaterial = this.sphere.material;
    if (sphereMaterial.userData.shader) {
      const uniforms = sphereMaterial.userData.shader.uniforms;

      // Extract raw audio data
      const inData = this.inputAnalyser.data;
      const outData = this.outputAnalyser.data;

      // Baseline procedural presence pulsation according to Mamba state
      const statePulse =
        this.currentState === "listening"
          ? 0.35
          : this.currentState === "thinking"
          ? 0.65
          : this.currentState === "speaking"
          ? 0.5
          : this.currentState === "permission"
          ? 0.4
          : 0.15;

      const idleSine = Math.sin(t * 0.002) * 0.5 + 0.5;
      const activeSine = Math.sin(t * 0.005) * 0.5 + 0.5;

      // Calculate effective audio / presence energy
      const micEnergy = inData[0] || (this.currentState === "listening" ? idleSine * 80 : 0);
      const micBand1 = inData[1] || (this.currentState === "listening" ? activeSine * 60 : 0);
      const micBand2 = inData[2] || (this.currentState === "listening" ? idleSine * 50 : 0);

      const outEnergy = outData[0] || (this.currentState === "speaking" ? activeSine * 120 : this.currentState === "thinking" ? activeSine * 70 : 0);
      const outBand1 = outData[1] || (this.currentState === "speaking" ? idleSine * 90 : this.currentState === "thinking" ? idleSine * 50 : 0);
      const outBand2 = outData[2] || (this.currentState === "speaking" ? activeSine * 70 : 0);

      // Preserve bit2zero/Orb scale deformation equation
      const scaleMultiplier = 1 + (0.2 * outBand1) / 255 + (statePulse * 0.08 * Math.sin(t * 0.003));
      this.sphere.scale.setScalar(scaleMultiplier);

      // Preserve bit2zero/Orb camera rotation equation
      const f = 0.001;
      const baseRotSpeed = this.currentState === "thinking" ? 3.0 : 1.0;
      this.rotation.x += (dt * f * 0.5 * outBand1 * baseRotSpeed) / 255 + (0.002 * baseRotSpeed);
      this.rotation.z += (dt * f * 0.5 * micBand1) / 255;
      this.rotation.y += (dt * f * 0.25 * micBand2) / 255;
      this.rotation.y += (dt * f * 0.25 * outBand2 * baseRotSpeed) / 255 + (0.003 * baseRotSpeed);

      const euler = new THREE.Euler(this.rotation.x, this.rotation.y, this.rotation.z);
      const quaternion = new THREE.Quaternion().setFromEuler(euler);
      const vector = new THREE.Vector3(0, 0, 5);
      vector.applyQuaternion(quaternion);
      this.camera.position.copy(vector);
      this.camera.lookAt(this.sphere.position);

      // Preserve bit2zero/Orb shader time & vector uniforms
      uniforms.time.value += (dt * 0.1 * Math.max(outEnergy, 25)) / 255;
      uniforms.inputData.value.set(
        (1 * micEnergy) / 255,
        (0.1 * micBand1) / 255,
        (10 * micBand2) / 255,
        0
      );
      uniforms.outputData.value.set(
        (2 * outEnergy) / 255,
        (0.1 * outBand1) / 255,
        (10 * outBand2) / 255,
        0
      );
    }

    this.composer.render();
  };

  public dispose(): void {
    this.isDisposed = true;
    if (this.animationFrameId !== null) {
      cancelAnimationFrame(this.animationFrameId);
      this.animationFrameId = null;
    }

    if (this.backdrop) {
      this.backdrop.geometry.dispose();
      this.backdrop.material.dispose();
    }

    if (this.sphere) {
      this.sphere.geometry.dispose();
      this.sphere.material.dispose();
    }

    if (this.composer) {
      this.composer.dispose();
    }

    if (this.renderer) {
      this.renderer.dispose();
    }
  }
}

