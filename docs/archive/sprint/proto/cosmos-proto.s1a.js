// выдра — ЧЕРНОВИК сцены S1 (арт-директор). Тот же API, что у vydra/static/cosmos.js, плюс pulse()/setEnergy().
// Стенд подменяет им /static/cosmos.js (.sprint/proto/rig.py). Исполнитель фундамента (S2) переносит это в cosmos.js.
//
// Главное отличие от текущей сцены: композиция задаётся CSS-токенами на .hero (--bh-x, --bh-y, --bh-r, ширины ореола…),
// шейдер только читает их. Поэтому варианты первого экрана — это разные CSS, а не разные шейдеры.
//
// Слои (сзади вперёд): дальний план (текстура agy, линзирование) → звёзды (процедурно, линзирование) →
// туман (fbm, два слоя, оттенок платформы в подсвеченной части) → ореол, фотонное кольцо, волокна, частицы →
// хроматическая кайма (единственный цвет) → анаморфные полосы (текстура agy) → тень → тон-маппинг, зерно.

const VERT = `attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }`;

const FRAG = `
precision highp float;
uniform vec2  uRes;
uniform float uH;          // высота сцены, px холста (ширины ореола и тумана — в долях H)
uniform float uTime;
uniform vec2  uPar;        // параллакс -1..1
uniform vec3  uMouse;      // xy — курсор в px холста (GL, y вверх), z — сила линзы 0..1
uniform float uScroll;
uniform vec2  uC;          // центр дыры, px холста (GL)
uniform float uR;          // радиус тени, px холста
uniform vec4  uHalo;       // ядро, середина, дымка (доли H), сила
uniform float uCA;         // хроматическая кайма
uniform float uBeam;       // угол самой яркой стороны, рад (0 — вправо, PI — влево, PI/2 — вверх)
uniform float uBeamAmt;    // доплеровская асимметрия 0..1
uniform float uLensE;      // эйнштейновский радиус, доли R
uniform vec3  uTint;       // оттенок подсвеченного тумана (платформа)
uniform float uTintAmt;
uniform vec4  uStreak;     // x — верх полосы (GL y, px), y — высота, z — сила, w — центр по x
uniform float uPulse;      // импульс «Скачать» 0..1 (огибающая)
uniform float uPulsePh;    // фаза импульса 0..1 (разлёт черты)
uniform float uEq;         // экватор — центр поля ссылки по y, px холста (GL)
uniform float uEnergy;     // активность загрузок 0..1
uniform float uFog;
uniform vec4  uFar;        // xy — точка текстуры под центром дыры (доли), z — ширина текстуры в долях H, w — сила
uniform float uStars;
uniform float uInner;      // серая кайма внутри тени
uniform float uFib;        // сила волокон в ореоле
uniform float uInvert;
uniform float uSeed;
uniform sampler2D uFarTex;
uniform sampler2D uStrTex;
uniform vec2  uHas;        // x — есть дальний план, y — есть полосы

#define PI 3.14159265
#define TAU 6.2831853

float hash12(vec2 p){ vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
vec3  hash33(vec3 p){ p = fract(p * vec3(0.1031, 0.1030, 0.0973)); p += dot(p, p.yxz + 33.33); return fract((p.xxy + p.yxx) * p.zyx); }
float vnoise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash12(i), hash12(i + vec2(1,0)), f.x), mix(hash12(i + vec2(0,1)), hash12(i + vec2(1,1)), f.x), f.y); }
float fbm(vec2 p, int oct){ float a = 0.5, s = 0.0; for (int i = 0; i < 5; i++){ if (i >= oct) break; s += a * vnoise(p); p = p * 2.03 + 17.1; a *= 0.5; } return s; }
float ign(vec2 p){ return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))); }
mat2 rot(float a){ float c = cos(a), s = sin(a); return mat2(c, s, -s, c); }
vec3 hue2rgb(float h){ return clamp(abs(fract(h + vec3(0.0, 2.0/3.0, 1.0/3.0)) * 6.0 - 3.0) - 1.0, 0.0, 1.0); }

// звёзды: одна на заселённую ячейку; размер — в экранных px через upp
float stars(vec2 q, float cell, float density, float sigmaPx, float upp, float bright, float seed){
  vec2 g = floor(q / cell);
  float acc = 0.0;
  for (int j = -1; j <= 1; j++)
  for (int i = -1; i <= 1; i++){
    vec2 id = g + vec2(i, j);
    vec3 h = hash33(vec3(id, seed));
    if (h.x > density) continue;
    vec2 c = (id + 0.12 + 0.76 * h.yz) * cell;
    vec3 h2 = hash33(vec3(id + 71.0, seed + 3.0));
    float b = pow(h2.x, 3.2) * bright;
    float tw = 1.0 + 0.18 * sin(uTime * (0.25 + 1.1 * h2.y) + TAU * h2.z);
    float d = length(q - c) / upp;
    float s = sigmaPx * (0.7 + 0.6 * h2.z);
    acc += b * tw * (exp(-d * d / (2.0 * s * s)) + 0.04 * exp(-d / (s * 4.0)));
  }
  return acc;
}

void main(){
  vec2 fc = gl_FragCoord.xy;
  float H = uH;
  float t = uTime;
  vec2 c = uC + vec2(0.0, uScroll * H * 0.16);
  vec2 p = (fc - c) / uR;                 // радиус тени = 1
  float b = length(p);
  float ang = atan(p.y, p.x);
  float edge = (b - 1.0) * uR / H;         // расстояние от края тени в долях H (внутри < 0)
  float e = max(edge, 0.0);
  float upp = 1.0 / H;

  // ---------- курсор: лёгкая линза фона (притягивает звёзды и пыль) ----------
  vec2 mv = (fc - uMouse.xy) / H;
  float m2 = dot(mv, mv);
  vec2 mdisp = -mv * (0.0032 * uMouse.z) / (m2 + 0.010);

  // ---------- гравитационная линза ----------
  float E = uLensE;
  float lens = 1.0 - (E * E) / (b * b + 0.02);
  vec2 qs = p * lens * (uR / H) + mdisp;   // фон, доли H
  vec2 par = uPar * 0.010;
  float gain = clamp(1.0 / max(abs(lens), 0.14), 0.6, 2.6);

  // звёзды линзируются слабее, чем газ: иначе растягиваются в «дождь» (эйнштейновские дуги — только у кромки)
  float Es = min(E, 0.62);
  float lensS = 1.0 - (Es * Es) / (b * b + 0.02);
  vec2 qss = p * lensS * (uR / H) + mdisp;
  float L = 0.0;
  float uppq = upp * clamp(abs(lensS), 0.25, 3.0);
  L += stars(qss + par * 0.25 + uSeed, 0.026, 0.16, 0.75, uppq, 0.05, 1.0);
  L += stars(qss + par * 0.60 + uSeed, 0.070, 0.20, 0.95, uppq, 0.20, 2.0);
  L += stars(qss + par * 1.00 + uSeed, 0.200, 0.22, 1.30, uppq, 0.85, 3.0);
  L *= uStars * clamp(1.0 / max(abs(lensS), 0.3), 0.7, 1.8);

  // ---------- дальний план: текстура agy (пыль, дуги, сетка), линзирована вокруг дыры ----------
  if (uHas.x > 0.5) {
    vec2 rel = qss + par * 0.5;
    rel = rot(0.05 * sin(t * 0.021) / (0.6 + 0.4 * b)) * rel;       // медленная закрутка без накопления
    vec2 uv = uFar.xy + vec2(rel.x, -rel.y) / vec2(uFar.z, uFar.z / 1.7917);
    float fl = texture2D(uFarTex, uv).r;
    float inb = smoothstep(0.0, 0.05, uv.x) * smoothstep(1.0, 0.95, uv.x) * smoothstep(0.0, 0.06, uv.y) * smoothstep(1.0, 0.94, uv.y);
    L += uFar.w * fl * inb;
  }

  // ---------- туман: два слоя на разной глубине, подсвеченная часть — в оттенке платформы ----------
  float lit = exp(-e / 0.30) * smoothstep(-0.01, 0.02, edge);
  float fogL;
  {
    vec2 f1 = rot(0.5) * (qs * 1.7 + par * 0.4) + vec2(0.006, 0.0025) * t * (1.0 + uEnergy) + uSeed * 3.0;
    float n1 = fbm(f1 * 1.1, 4);
    float k1 = fbm(f1 * 0.33 + 4.2, 3);
    float neb1 = smoothstep(0.40, 0.95, n1) * smoothstep(0.30, 0.80, k1);
    vec2 f2 = rot(-0.3) * (qs * 3.4 + par * 1.3) - vec2(0.011, 0.004) * t * (1.0 + 1.5 * uEnergy) + 11.0;
    float neb2 = smoothstep(0.50, 1.02, fbm(f2, 4));
    fogL = (neb1 * 0.055 + neb2 * 0.022) * uFog * (1.0 + 6.0 * lit);
  }
  vec3 fogCol = fogL * mix(vec3(1.0), uTint * 1.35, clamp(uTintAmt * (0.35 + 0.65 * lit), 0.0, 1.0));

  // ---------- ореол: горизонт событий, раскалённо-белый ----------
  float be = 1.0 + uBeamAmt * cos(ang - uBeam);
  float breath = 1.0 + 0.03 * sin(t * TAU / 7.0) + 0.02 * uEnergy * sin(t * TAU / 2.4) + 0.65 * uPulse;
  // нити аккреции: полярный шум, вытянутый по орбите, кеплеровская скорость (внутренние быстрее);
  // шов полярных координат — справа, на тусклой стороне, и смешан по ширине 0.35 рад
  float w = (0.045 + 0.11 * uEnergy) / pow(max(b, 1.0), 1.5);
  float th = atan(p.y, -p.x) + t * w;
  float rr = b * 15.0 + uSeed * 5.0;
  float fA = fbm(vec2(th * 2.4, rr), 4);
  float fB = fbm(vec2((th - TAU * sign(th)) * 2.4, rr), 4);
  float seam = smoothstep(PI - 0.35, PI, abs(th));
  float fil = mix(fA, fB, seam * 0.5);
  fil = pow(clamp(fil * 1.2, 0.0, 1.0), 1.5) * 1.6;           // нити газа, без «винила»
  float tex = mix(1.0, 0.55 + 0.7 * fil, uFib);
  // профиль как у hero-g: раскалённое плато (HDR, упирается в белый) → быстрый спад → слабая дымка → тёмный космос
  float core = exp(-e / uHalo.x) * 5.0;
  float mid  = exp(-e / uHalo.y) * 0.55;
  float haze = exp(-e / uHalo.z) * 0.07;
  float veil = 0.012 / (1.0 + (e / 0.9) * (e / 0.9));
  float halo = (core * (0.9 + 0.2 * tex) + mid * tex + haze * mix(1.0, tex, 0.6) + veil) * be * breath * uHalo.w;

  // фотонное кольцо — бритвенная линия; вторичное — отражение обратной стороны диска
  float pw = max(0.0024, 1.15 / uR);
  float ring = exp(-pow((b - 1.0) / pw, 2.0)) * 2.4 * (0.75 + 0.35 * be);
  float ring2 = exp(-pow((edge - 0.011) / 0.0022, 2.0)) * 0.30 * be;

  // частицы: мелкая пыль на орбитах, внутренние быстрее
  float om = (0.035 + 0.09 * uEnergy) / pow(max(b, 1.0), 1.5);
  float pr = stars(rot(t * om) * p * (uR / H) + 5.0, 0.018, 0.22, 0.6, upp, 0.9, 5.0);
  pr *= smoothstep(0.0, 0.015, edge) * (1.0 - smoothstep(0.10, 0.34, edge));

  // хроматическая кайма — единственный цвет сцены: снаружи красный, к ореолу — фиолетовый
  float caPos = uHalo.x * 2.1 + 0.004;
  float caW = uHalo.x * 0.42 + 0.0022;
  float xw = (e - caPos) / caW;
  float band = exp(-xw * xw * 1.4);
  vec3 spec = mix(vec3(1.0), hue2rgb(clamp(0.02 + (0.5 - 0.5 * xw) * 0.72, 0.0, 0.78)), 0.82);
  float caMask = smoothstep(-0.35, 0.85, cos(ang - uBeam));
  vec3 ca = spec * band * caMask * uCA * (1.0 + 1.6 * uPulse) * be;

  // анаморфные полосы: текстура agy, медленный дрейф и мерцание
  float streak = 0.0;
  if (uStreak.z > 0.0) {
    float v = (uStreak.x - fc.y) / uStreak.y;
    float u = (fc.x - uStreak.w) / uRes.x * 0.82 + 0.5 + 0.015 * sin(t * 0.06);
    if (uHas.y > 0.5 && v > 0.0 && v < 1.0) streak = texture2D(uStrTex, vec2(u, v)).r;
    streak *= smoothstep(0.0, 0.10, v) * smoothstep(1.0, 0.86, v);
    streak *= uStreak.z * (0.88 + 0.12 * sin(t * 0.8 + fc.y * 0.31)) * (1.0 + 0.8 * uPulse);
  }

  // импульс «Скачать»: тонкая черта по экватору — от центра к кольцу за 0,3 с, гаснет за 0,6 с
  float eqL = 0.0;
  if (uPulse > 0.001) {
    float reach = uR * 1.05 * smoothstep(0.0, 1.0, uPulsePh);
    float dy = (fc.y - uEq) / max(1.0, H * 0.0016);
    float dx = abs(fc.x - c.x);
    eqL = exp(-dy * dy) * smoothstep(reach, reach - uR * 0.25, dx) * smoothstep(uR * 0.18, uR * 0.5, dx) * uPulse * 1.6;
  }

  // ---------- сборка ----------
  float aa = 1.0 / H;                                     // полпикселя холста — сглаженный край тени
  float sh = smoothstep(-aa, aa, edge);                  // тень: всё внутри гаснет
  vec3 col = (vec3(L) + fogCol + vec3(halo + ring + ring2 + pr)) * sh + ca * sh;
  float inner = pow(smoothstep(-0.045, 0.0, edge), 3.0) * uInner * be;
  col += vec3(inner) * (1.0 - sh);
  col += vec3(streak + eqL);

  vec2 vc = (fc / uRes - 0.5) * vec2(uRes.x / uRes.y, 1.0);
  col *= 1.0 - 0.22 * dot(vc, vc);
  col = 1.0 - exp(-col * 1.3);                             // тон-маппинг: ядро упирается в белый, полутона цветут
  col = pow(col, vec3(0.94));
  col *= 1.0 - 0.92 * smoothstep(0.12, 0.7, uScroll);         // после тон-маппинга: сцена гаснет, когда герой уходит вверх
  col += (ign(fc + fract(t) * 17.0) - 0.5) / 255.0 * 3.0;  // дизеринг против полос в градиентах
  vec3 print = mix(1.0 - col, vec3(0.955, 0.955, 0.945), 0.06);
  col = mix(col, print, uInvert);
  gl_FragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}`;

function hexToRgb(hex) {
  const n = parseInt(hex.replace('#', ''), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

// композиция по умолчанию (если CSS не задал токены) — вариант «Сингулярность»
const DEF = {
  x: 0.5, y: 0.54, r: 0.70, core: 0.04, mid: 0.10, haze: 0.30, halo: 1.0, ca: 0.55, beam: 200, beamAmt: 0.35,
  lens: 1.0, fog: 1.0, far: 0.5, farX: 0.5, farY: 0.5, farS: 2.2, stars: 0.6, inner: 0.10, fib: 0.8,
  streakTop: 0.86, streakH: 0.16, streak: 0.55, streakX: 0.5,
};
const NEUTRAL = '#5a86ff';

export function createCosmos(canvas, opts = {}) {
  const reduced = !!opts.reduced;
  let gl = null;
  try { gl = canvas.getContext('webgl', { antialias: false, alpha: false, depth: false, stencil: false, powerPreference: 'low-power' }); } catch { gl = null; }
  const api = {
    ok: false, invert: 0,
    tint: [1, 1, 1], tintT: [1, 1, 1], tintAmt: 0, tintAmtT: 0,
    energy: 0, energyT: 0, pulseAt: -1, pulseV: 0, pulsePh: 0,
    setAccent(hex, amt = 1) {
      const neutral = !hex || hex.toLowerCase() === NEUTRAL;
      if (!neutral) api.tintT = hexToRgb(hex);
      api.tintAmtT = neutral ? 0 : 0.9 * amt;
      api.wake();
    },
    setInvert(v) { api.invert = v ? 1 : 0; api.wake(); },
    setCenter() { api.readTokens(); api.wake(); },   // композиция — из CSS-токенов, аргументы не нужны
    setPointer(x, y) { api._pt = [x, y]; api._ptSeen = true; api.wake(); },
    setScroll(v) { api._scroll = v; api.wake(); },
    setEnergy(v) { api.energyT = Math.max(0, Math.min(1, v)); api.wake(); },
    pulse() { if (reduced) return; api.pulseAt = api._t; api.wake(); },
    pause() { api._paused = true; },
    resume() { api._paused = false; api.wake(); },
    wake() { if (!api._raf && api.ok && !api._paused) api._raf = requestAnimationFrame(api._frame); },
    resize() {},
    readTokens() {},
    destroy() { cancelAnimationFrame(api._raf); api._raf = 0; },
    _pt: [0, 0], _ptS: [0, 0], _ptSeen: false, _scroll: 0, _paused: false, _raf: 0, _last: 0,
    _t: reduced ? 14.0 : 14.0,   // «время постера»: волокна уже закручены, кадр без движения выглядит собранным
  };
  if (!gl) return api;
  const compile = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) { const log = gl.getShaderInfoLog(s); gl.deleteShader(s); throw new Error(log); }
    return s;
  };
  let prog;
  try {
    prog = gl.createProgram();
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
  } catch (err) { console.warn('cosmos-proto:', err.message); return api; }
  gl.useProgram(prog);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, 'p');
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const U = {};
  for (const n of ['uRes', 'uH', 'uTime', 'uPar', 'uMouse', 'uScroll', 'uC', 'uR', 'uHalo', 'uCA', 'uBeam', 'uBeamAmt', 'uLensE',
    'uTint', 'uTintAmt', 'uStreak', 'uPulse', 'uPulsePh', 'uEq', 'uEnergy', 'uFog', 'uFar', 'uStars', 'uInner', 'uFib', 'uInvert', 'uSeed',
    'uFarTex', 'uStrTex', 'uHas']) U[n] = gl.getUniformLocation(prog, n);
  const seed = opts.seed ?? 0.37;
  api.ok = true;

  // текстуры agy: грузятся лениво, до загрузки сцена рисуется без них
  const has = [0, 0];
  const texs = [gl.createTexture(), gl.createTexture()];
  const loadTex = (i, url) => {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => {
      gl.activeTexture(gl.TEXTURE0 + i);
      gl.bindTexture(gl.TEXTURE_2D, texs[i]);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, gl.LUMINANCE, gl.UNSIGNED_BYTE, img);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      has[i] = 1; api.wake();
    };
    img.onerror = () => {};
    img.src = url;
  };
  for (let i = 0; i < 2; i++) {
    gl.activeTexture(gl.TEXTURE0 + i);
    gl.bindTexture(gl.TEXTURE_2D, texs[i]);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, 1, 1, 0, gl.LUMINANCE, gl.UNSIGNED_BYTE, new Uint8Array([0]));
  }
  loadTex(0, '/static/scene/far.webp');
  loadTex(1, '/static/scene/streaks.webp');

  // токены композиции из CSS
  let tok = { ...DEF };
  let tokKey = '';
  let anchorY = null;
  const num = (cs, name, d) => { const v = parseFloat(cs.getPropertyValue(name)); return Number.isFinite(v) ? v : d; };
  api.readTokens = () => {
    const cs = getComputedStyle(canvas);
    const n = {
      x: num(cs, '--bh-x', DEF.x), y: num(cs, '--bh-y', DEF.y), r: num(cs, '--bh-r', DEF.r),
      core: num(cs, '--bh-core', DEF.core), mid: num(cs, '--bh-mid', DEF.mid), haze: num(cs, '--bh-haze', DEF.haze),
      halo: num(cs, '--bh-halo', DEF.halo), ca: num(cs, '--bh-ca', DEF.ca), beam: num(cs, '--bh-beam', DEF.beam),
      beamAmt: num(cs, '--bh-beam-amt', DEF.beamAmt), lens: num(cs, '--bh-lens', DEF.lens), fog: num(cs, '--bh-fog', DEF.fog),
      far: num(cs, '--bh-far', DEF.far), farX: num(cs, '--bh-far-x', DEF.farX), farY: num(cs, '--bh-far-y', DEF.farY),
      farS: num(cs, '--bh-far-s', DEF.farS), stars: num(cs, '--bh-stars', DEF.stars), inner: num(cs, '--bh-inner', DEF.inner),
      fib: num(cs, '--bh-fib', DEF.fib), streakTop: num(cs, '--bh-streak-top', DEF.streakTop),
      streakH: num(cs, '--bh-streak-h', DEF.streakH), streak: num(cs, '--bh-streak', DEF.streak), streakX: num(cs, '--bh-streak-x', DEF.streakX),
    };
    // якорь: центр дыры по вертикали = центр поля ссылки в покое (без превью); в рабочем режиме не пересчитывается
    if (cs.getPropertyValue('--bh-anchor').trim() === 'portal') {
      const portal = document.querySelector('.portal');
      const idle = !document.querySelector('.hero .preview');
      if (portal && idle && portal.offsetParent) {
        const pr = portal.getBoundingClientRect(), cr = canvas.getBoundingClientRect();
        if (cr.height > 0) anchorY = ((pr.top + pr.height / 2) - cr.top) / cr.height;
      }
      if (anchorY != null) n.y = anchorY;
    }
    const key = JSON.stringify(n);
    if (key !== tokKey) { tokKey = key; tok = n; return true; }
    return false;
  };
  api.readTokens();
  // черновик: CSS варианта подмешивается после загрузки — следим за <style> в <head>
  new MutationObserver(() => { if (api.readTokens()) api.wake(); }).observe(document.head, { childList: true });
  setInterval(() => { if (api.readTokens()) api.wake(); }, 400);

  // черновик: без правок app.js — импульс по #go.fire, энергия по острову
  const go = document.getElementById('go');
  if (go) new MutationObserver(() => { if (go.classList.contains('fire')) api.pulse(); }).observe(go, { attributes: true, attributeFilter: ['class'] });
  const island = document.getElementById('island');
  if (island) new MutationObserver(() => {
    const s = island.dataset.state;
    api.setEnergy(s === 'compact' || s === 'expanded' ? 1 : s === 'ask' ? 0.4 : 0);
    if (s === 'done') api.pulse();
  }).observe(island, { attributes: true, attributeFilter: ['data-state'] });

  let w = 0, hgt = 0, cssW = 1, cssH = 1;
  const RES_SCALE = opts.resScale || 0.5;
  function resize() {
    const r = canvas.getBoundingClientRect();
    cssW = Math.max(1, r.width); cssH = Math.max(1, r.height);
    const dpr = Math.min(1, devicePixelRatio || 1);
    let nw = Math.max(2, Math.round(r.width * dpr * RES_SCALE)), nh = Math.max(2, Math.round(r.height * dpr * RES_SCALE));
    const cap = 1.1e6;
    if (nw * nh > cap) { const k = Math.sqrt(cap / (nw * nh)); nw = Math.round(nw * k); nh = Math.round(nh * k); }
    if (nw !== w || nh !== hgt) { w = nw; hgt = nh; canvas.width = w; canvas.height = hgt; gl.viewport(0, 0, w, hgt); }
  }
  api.resize = () => { resize(); api.readTokens(); api.wake(); };

  const portalEl = document.querySelector('.portal');
  const eqY = (rect) => { if (!portalEl) return -1e4; const r = portalEl.getBoundingClientRect(); return hgt - ((r.top + r.height / 2) - rect.top) * hgt / cssH; };
  const draw = () => {
    resize();
    const H = hgt;
    const rect = canvas.getBoundingClientRect();
    const mxC = (api._ptS[0] * 0.5 + 0.5) * innerWidth, myC = (0.5 - api._ptS[1] * 0.5) * innerHeight;
    const mx = (mxC - rect.left) * w / cssW, my = hgt - (myC - rect.top) * hgt / cssH;
    gl.uniform2f(U.uRes, w, hgt);
    gl.uniform1f(U.uH, H);
    gl.uniform1f(U.uTime, api._t);
    gl.uniform2f(U.uPar, reduced ? 0 : api._ptS[0], reduced ? 0 : api._ptS[1]);
    gl.uniform3f(U.uMouse, mx, my, reduced || !api._ptSeen ? 0 : 1);
    gl.uniform1f(U.uScroll, api._scroll);
    gl.uniform2f(U.uC, tok.x * w, (1 - tok.y) * hgt);
    gl.uniform1f(U.uR, tok.r * H);
    gl.uniform4f(U.uHalo, tok.core, tok.mid, tok.haze, tok.halo);
    gl.uniform1f(U.uCA, tok.ca);
    gl.uniform1f(U.uBeam, tok.beam * Math.PI / 180);
    gl.uniform1f(U.uBeamAmt, tok.beamAmt);
    gl.uniform1f(U.uLensE, tok.lens);
    gl.uniform3f(U.uTint, api.tint[0], api.tint[1], api.tint[2]);
    gl.uniform1f(U.uTintAmt, api.tintAmt);
    gl.uniform4f(U.uStreak, (1 - tok.streakTop) * hgt, tok.streakH * hgt, tok.streak, tok.streakX * w);
    gl.uniform1f(U.uPulse, api.pulseV);
    gl.uniform1f(U.uPulsePh, api.pulsePh);
    gl.uniform1f(U.uEq, eqY(rect));
    gl.uniform1f(U.uEnergy, api.energy);
    gl.uniform1f(U.uFog, tok.fog);
    gl.uniform4f(U.uFar, tok.farX, tok.farY, tok.farS, tok.far);
    gl.uniform1f(U.uStars, tok.stars);
    gl.uniform1f(U.uInner, tok.inner);
    gl.uniform1f(U.uFib, tok.fib);
    gl.uniform1f(U.uInvert, api.invert);
    gl.uniform1f(U.uSeed, seed);
    gl.uniform1i(U.uFarTex, 0);
    gl.uniform1i(U.uStrTex, 1);
    gl.uniform2f(U.uHas, has[0], has[1]);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
  const ease = (cur, target, dt, tau) => cur + (target - cur) * Math.min(1, dt / tau);
  api._frame = (now) => {
    api._raf = 0;
    const dt = Math.min(0.05, api._last ? (now - api._last) / 1000 : 0.016);
    api._last = now;
    if (!reduced) api._t += dt * (1 + 0.6 * api.energy);
    api._ptS[0] = ease(api._ptS[0], api._pt[0], dt, 0.28);
    api._ptS[1] = ease(api._ptS[1], api._pt[1], dt, 0.28);
    for (let i = 0; i < 3; i++) api.tint[i] = reduced ? api.tintT[i] : ease(api.tint[i], api.tintT[i], dt, 0.35);
    api.tintAmt = reduced ? api.tintAmtT : ease(api.tintAmt, api.tintAmtT, dt, 0.35);
    api.energy = reduced ? api.energyT : ease(api.energy, api.energyT, dt, 1.2);
    if (api.pulseAt >= 0) {
      const k = api._t - api.pulseAt;
      api.pulseV = k < 0.12 ? k / 0.12 : Math.exp(-(k - 0.12) / 0.38);
      api.pulsePh = Math.min(1, k / 0.3);
      if (k > 2.5) { api.pulseAt = -1; api.pulseV = 0; api.pulsePh = 0; }
    }
    draw();
    const settled = reduced && Math.abs(api._ptS[0] - api._pt[0]) < 0.002 && Math.abs(api._ptS[1] - api._pt[1]) < 0.002;
    if (!api._paused && !settled) api._raf = requestAnimationFrame(api._frame);
    else api._last = 0;
  };
  api.wake();
  return api;
}
