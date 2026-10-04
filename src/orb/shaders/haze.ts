/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 * Mamba Orb atmosphere — one additive billboard of soft light.
 *
 * Replaces the old lit sphere: the Orb has no silhouette, no rim and no
 * boundary. Depth comes from the particle field in front of it.
 */

export const hazeVS = `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

export const hazeFS = `
precision highp float;
uniform vec3 uCore;
uniform vec3 uEdge;
uniform float uIntensity;
uniform float uTime;
uniform float uBreath;
uniform float uAudio;
varying vec2 vUv;

void main() {
  vec2 p = vUv - 0.5;
  float d = min(length(p) * 2.0, 1.0);
  float core = 1.0 - d;
  float glow = pow(core, 2.6);
  float halo = pow(core, 1.1) * 0.42;
  float shimmer = 0.96 + 0.04 * sin(uTime * 0.7 + p.x * 2.4 + p.y * 1.9);
  float a = (glow + halo) * uIntensity * shimmer * (1.0 + uBreath + uAudio * 0.22);
  vec3 col = mix(uEdge, uCore, glow);
  gl_FragColor = vec4(col * (0.72 + 0.5 * glow), clamp(a, 0.0, 0.72));
}`;
