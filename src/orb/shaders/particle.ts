/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 * Mamba Orb particle field — the living layer.
 *
 * No shell, no rim, no boundary: particles fade in past the inner light and
 * out at the far edge, and depth is carried by size + alpha rather than by a
 * drawn sphere.
 */

export const particleVS = `
uniform float time;
uniform vec4 inputData;
uniform vec4 outputData;
uniform float stateSpeed;
uniform float stateTurbulence;
uniform float statePull;
uniform float stateExpand;
uniform float stateCohesion;
uniform float stateDirectional;
uniform vec3 stateFlowAxis;
uniform float uBreath;
uniform vec2 uParallax;
uniform vec3 statePrimary;
uniform vec3 stateSecondary;
uniform vec3 stateAccent;
uniform float stateMix;
uniform float uCoreIn;
uniform float uCoreOut;
uniform float uEdgeOut;
uniform float uEdgeIn;
uniform float uAlphaScale;

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

    // Cohesion: the settled states gather the field instead of freezing it
    float band = mix(1.16, 0.96, stateCohesion);
    float shellR = length(pos);
    pos = normalize(pos) * (shellR + (band - shellR) * stateCohesion * 0.35);

    // Directional flow: the working state streams along a slowly precessing axis
    vec3 axis = normalize(stateFlowAxis);
    float along = dot(pos, axis);
    pos += axis * (along * stateDirectional * 0.24 * (0.6 + 0.4 * sin(time * 0.8 + phase * 6.28318)));

    // Breath: the quiet states swell almost imperceptibly
    pos *= 1.0 + uBreath;

    // Audio reactivity & State dynamics:
    // Listening / mic audio: particles pull inward toward the light
    float micEnergy = inputData.x;
    float pull = (statePull + micEnergy * 0.4) * 0.20;
    float currentR = length(pos);
    float targetR = max(0.74, currentR - pull * (0.5 + 0.5 * sin(time * 3.2 + phase * 6.28318)));

    // Speaking / output speech audio: energy flows outward
    float outEnergy = outputData.x;
    float expand = (stateExpand + outEnergy * 0.5) * 0.24;
    targetR += expand * (0.5 + 0.5 * sin(time * 4.5 + phase * 9.42477));

    pos = normalize(pos) * targetR;

    vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
    // Pointer parallax, weighted so the near field leads the far field
    mvPosition.xy += uParallax * (0.55 - 0.45 * (length(pos) - 0.7));
    gl_Position = projectionMatrix * mvPosition;

    // Depth cue: far particles dim instead of staying equally bright
    float depthFade = clamp(1.0 - (-mvPosition.z - 3.0) / 3.4, 0.35, 1.0);

    // Particle size with distance attenuation, capped so depth never blooms
    float pSize = size * (210.0 / -mvPosition.z);
    pSize *= (1.0 + 0.25 * sin(time * 2.8 + phase * 6.28318) + 0.35 * outEnergy);
    gl_PointSize = clamp(pSize, 1.2, 13.0);

    // Soft in from the inner light, soft out at the far edge — no silhouette
    float coreFade = smoothstep(uCoreIn, uCoreOut, targetR);
    float outerFade = 1.0 - smoothstep(uEdgeOut, uEdgeIn, targetR);
    vAlpha = coreFade * outerFade * (0.55 + 0.35 * sin(time * 1.6 + phase * 6.28318)) * depthFade * uAlphaScale;

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

    float gaussian = exp(-dist * dist * 14.0);
    float softEdge = smoothstep(0.5, 0.12, dist);
    float alpha = vAlpha * softEdge * 0.82;

    // Only a hint of a hot nucleus — the light comes from the haze layer
    vec3 color = mix(vColor, vec3(1.0), gaussian * 0.22);

    gl_FragColor = vec4(color, alpha);
}
`;
