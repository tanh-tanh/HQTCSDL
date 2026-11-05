import { Color, DoubleSide, NormalBlending, ShaderMaterial, Texture, Vector2 } from "three";

// One archival print: rounded corners, soft contact shadow, cover-cropped image,
// play glyph and timecode badge, readable from both sides (never mirrored),
// optionally bent along the ring's cylinder.
const vertexShader = /* glsl */ `
  uniform vec2 uPlane;
  uniform float uBend;
  varying vec2 vUv;
  void main() {
    vUv = uv;
    vec3 p = position;
    if (uBend > 0.0) {
      float a = (p.x * uPlane.x) / uBend;
      p.x = uBend * sin(a) / uPlane.x;
      p.z = uBend * (cos(a) - 1.0);
    }
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  }
`;

const fragmentShader = /* glsl */ `
  precision highp float;
  uniform sampler2D uMap;
  uniform sampler2D uBadge;
  uniform float uHasMap, uLoad, uTexAspect;
  uniform float uHasBadge, uBadgeAspect, uDecor;
  uniform float uOpacity, uHover, uFocus;
  uniform vec2 uCard, uPlane;
  uniform vec3 uPlate;
  varying vec2 vUv;

  float sdRound(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
  }
  float fill(float d) {
    float aa = fwidth(d) * 0.8 + 1e-6;
    return 1.0 - smoothstep(-aa, aa, d);
  }

  void main() {
    vec2 p = (vUv - 0.5) * uPlane;
    if (!gl_FrontFacing) p.x = -p.x;
    vec2 hs = uCard * 0.5;
    float h = uCard.y;
    float d = sdRound(p, hs, 0.045 * h);
    float inside = fill(d);
    float ds = sdRound(p - vec2(0.0, -0.045 * h), hs - vec2(0.02 * h), 0.08 * h);
    float shadow = (1.0 - smoothstep(-0.03 * h, 0.14 * h, ds)) * 0.16;

    vec2 cuv = p / uCard + 0.5;
    float aspect = uCard.x / uCard.y;
    vec2 tuv = cuv;
    if (aspect > uTexAspect) tuv.y = 0.5 + (cuv.y - 0.5) * uTexAspect / aspect;
    else tuv.x = 0.5 + (cuv.x - 0.5) * aspect / uTexAspect;
    tuv.y = 1.0 - tuv.y;

    vec3 plate = uPlate * (0.965 + 0.05 * cuv.y);
    vec3 col = plate;
    if (uHasMap > 0.5) col = mix(plate, texture2D(uMap, tuv).rgb, uLoad);

    if (uDecor > 0.5) {
      vec2 q = (p + hs) / h;
      float s = 0.19;
      float inset = 0.07;
      vec2 c = q - vec2(inset + s * 0.5);
      col = mix(col, vec3(0.035), fill(sdRound(c, vec2(s * 0.5), 0.04)) * 0.88);
      vec2 t = c / (s * 0.5);
      float tri = max(-(t.x + 0.30), abs(t.y) - (0.46 - t.x) * 0.58);
      col = mix(col, vec3(0.98), fill(tri));
      if (uHasBadge > 0.5) {
        float bh = 0.155;
        vec2 bsize = vec2(bh * uBadgeAspect, bh);
        vec2 bmin = vec2(uCard.x / h - inset - bsize.x, inset);
        vec2 buv = (q - bmin) / bsize;
        if (buv.x >= 0.0 && buv.x <= 1.0 && buv.y >= 0.0 && buv.y <= 1.0) {
          vec4 b = texture2D(uBadge, vec2(buv.x, 1.0 - buv.y));
          col = mix(col, b.rgb, b.a);
        }
      }
    }

    col = mix(col, min(col * 1.07 + 0.015, 1.0), uHover);
    float ring = fill(abs(d + 0.012 * h) - 0.009 * h);
    col = mix(col, vec3(0.06), ring * uFocus);

    float alpha = inside + shadow * (1.0 - inside);
    vec3 rgb = col * inside / max(alpha, 1e-4);
    alpha *= uOpacity;
    if (alpha < 0.003) discard;
    gl_FragColor = vec4(rgb, alpha);
  }
`;

export function createCardMaterial(blank: Texture): ShaderMaterial {
  return new ShaderMaterial({
    vertexShader,
    fragmentShader,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    side: DoubleSide,
    blending: NormalBlending,
    uniforms: {
      uMap: { value: blank },
      uBadge: { value: blank },
      uHasMap: { value: 0 },
      uLoad: { value: 0 },
      uTexAspect: { value: 16 / 9 },
      uHasBadge: { value: 0 },
      uBadgeAspect: { value: 2.4 },
      uDecor: { value: 1 },
      uOpacity: { value: 0 },
      uHover: { value: 0 },
      uFocus: { value: 0 },
      uCard: { value: new Vector2(1.6, 0.9) },
      uPlane: { value: new Vector2(1.8, 1.1) },
      uBend: { value: 0 },
      uPlate: { value: new Color(0.89, 0.87, 0.83) },
    },
  });
}
