/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 * Mamba Dense Particle Orb Shaders
 */

export const particleVS = `
uniform float time;
uniform vec4 inputData;
uniform vec4 outputData;
uniform float stateSpeed;
uniform float stateTurbulence;
uniform float statePull;
uniform float stateExpand;
uniform vec3 statePrimary;
uniform vec3 stateSecondary;
uniform vec3 stateAccent;
uniform float stateMix;

attribute vec3 basePosition;
attribute vec3 customColor;
attribute float size;
attribute float phase;

varying vec3 vColor;
varying float vAlpha;

void main() {
    float r = length(basePosition);

    // Continuous flow, spiral rotation around central core
    float speed = stateSpeed * (0.85 + 0.3 * sin(phase * 6.28318));
    float angle = time * speed + r * 1.8;
    float cosA = cos(angle);
    float sinA = sin(angle);

    vec3 pos = basePosition;
    // Spiral around tilted orbital axes
    pos.xz = mat2(cosA, -sinA, sinA, cosA) * pos.xz;
    float tiltAngle = time * speed * 0.4 + phase * 3.14159;
    pos.xy = mat2(cos(tiltAngle * 0.25), -sin(tiltAngle * 0.25), sin(tiltAngle * 0.25), cos(tiltAngle * 0.25)) * pos.xy;

    // Organic curl / turbulent fluid reorganization
    vec3 curl = vec3(
        sin(pos.y * 3.5 + time * 1.4 + phase * 6.28318) * cos(pos.z * 3.0 + time * 1.1),
        cos(pos.x * 3.5 + time * 1.3 + phase * 6.28318) * sin(pos.z * 3.0 + time * 1.5),
        sin(pos.x * 3.0 + time * 1.2 + phase * 6.28318) * cos(pos.y * 3.5 + time * 1.3)
    );
    pos += curl * (0.075 * stateTurbulence);

    // Audio reactivity & State dynamics:
    // Listening / mic audio: particles pull inward toward the dark core
    float micEnergy = inputData.x;
    float pull = (statePull + micEnergy * 0.4) * 0.20;
    float currentR = length(pos);
    float targetR = max(0.74, currentR - pull * (0.5 + 0.5 * sin(time * 3.2 + phase * 6.28318)));

    // Speaking / output speech audio: energetic particles flow outward
    float outEnergy = outputData.x;
    float expand = (stateExpand + outEnergy * 0.5) * 0.24;
    targetR += expand * (0.5 + 0.5 * sin(time * 4.5 + phase * 9.42477));

    pos = normalize(pos) * targetR;

    vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
    gl_Position = projectionMatrix * mvPosition;

    // Particle size with distance attenuation
    float pSize = size * (210.0 / -mvPosition.z);
    pSize *= (1.0 + 0.25 * sin(time * 2.8 + phase * 6.28318) + 0.35 * outEnergy);
    gl_PointSize = clamp(pSize, 1.5, 20.0);

    // Fade naturally into transparency
    // Dark core surface is at 0.70; outer field fades smoothly towards 1.72
    float coreFade = smoothstep(0.71, 0.80, targetR);
    float outerFade = smoothstep(1.72, 1.05, targetR);
    vAlpha = coreFade * outerFade * (0.65 + 0.35 * sin(time * 2.2 + phase * 6.28318));

    // Dynamic state color blending
    vec3 themed = mix(stateSecondary, statePrimary, customColor.g);
    if (customColor.r > 0.85 && customColor.b > 0.85) {
        themed = mix(themed, stateAccent, 0.9); // bright accent / white
    }
    vColor = mix(customColor, themed, stateMix);
}
`;

export const particleFS = `
precision highp float;

varying vec3 vColor;
varying float vAlpha;

void main() {
    vec2 coord = gl_PointCoord - vec2(0.5);
    float dist = length(coord);
    if (dist > 0.5) discard;

    // High quality soft glowing particle profile
    float gaussian = exp(-dist * dist * 12.0);
    float softEdge = smoothstep(0.5, 0.08, dist);
    float alpha = vAlpha * softEdge;

    // Hot central nucleus (subtle white center)
    vec3 color = mix(vColor, vec3(1.0), gaussian * 0.55);

    gl_FragColor = vec4(color, alpha);
}
`;
