// выдра — фон героя: чёрная дыра в реальном времени (WebGL, один фрагментный шейдер).
// Оригинальная работа: гравитационное линзирование звёздного поля, тонкий аккреционный диск
// с доплеровским усилением, слабый релятивистский джет, холодный газ и объёмное свечение.
// Дисциплина: монохромная сцена; цвет — только в эмиссии диска и джета (uAccent).
// Рендер в ½ разрешения, пауза, когда вкладка скрыта или герой ушёл с экрана; без WebGL — CSS-подложка.

const VERT = `attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }`;

const FRAG = `
precision highp float;
uniform vec2  uRes;
uniform float uTime;
uniform vec2  uPointer;    // -1..1 параллакс
uniform float uScroll;     // 0..1 прокрутка героя
uniform vec2  uCenter;     // центр дыры в долях холста
uniform float uScale;      // радиус тени в долях min(res)
uniform vec3  uAccent;     // цвет эмиссии
uniform float uAccentAmt;  // 0..1 — сколько цвета допускаем
uniform float uInvert;     // 1 — светлая тема (отпечаток)
uniform float uSeed;

#define PI 3.14159265
#define TAU 6.2831853

float hash12(vec2 p){ vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
vec3  hash33(vec3 p){ p = fract(p * vec3(0.1031, 0.1030, 0.0973)); p += dot(p, p.yxz + 33.33); return fract((p.xxy + p.yxx) * p.zyx); }
float vnoise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash12(i), hash12(i + vec2(1,0)), f.x), mix(hash12(i + vec2(0,1)), hash12(i + vec2(1,1)), f.x), f.y); }
float fbm(vec2 p, int oct){ float a = 0.5, s = 0.0; for (int i = 0; i < 5; i++){ if (i >= oct) break; s += a * vnoise(p); p = p * 2.03 + 17.1; a *= 0.5; } return s; }
float ign(vec2 p){ return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))); }

// Звёздный слой: одна звезда на заселённую ячейку; размер в экранных пикселях через upp.
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
    float tw = 1.0 + 0.16 * sin(uTime * (0.25 + 1.1 * h2.y) + TAU * h2.z);   // медленное мерцание, никогда до нуля
    float d = length(q - c) / upp;
    float s = sigmaPx * (0.7 + 0.6 * h2.z);
    acc += b * tw * (exp(-d * d / (2.0 * s * s)) + 0.05 * exp(-d / (s * 4.0)));
  }
  return acc;
}

void main(){
  vec2 res = uRes;
  float m = min(res.x, res.y);
  vec2 ctr = res * uCenter + vec2(0.0, uScroll * res.y * 0.18);
  vec2 p = (gl_FragCoord.xy - ctr) / (m * uScale);   // радиус тени = 1
  float b = length(p);
  float ang = atan(p.y, p.x);
  float t = uTime;

  // ---------- линзирование фона (тонкая линза, эйнштейновский радиус = 1) ----------
  float lens = 1.0 - 1.0 / (b * b + 0.02);
  vec2 q = p * lens;
  vec2 par = uPointer * 0.06;
  float upp = 1.0 / (m * uScale);
  float uppq = upp * clamp(abs(lens), 0.08, 4.0);

  float L = 0.0;
  L += stars(q + par * 0.25 + uSeed, 0.16, 0.16, 0.85, uppq, 0.07, 1.0);
  L += stars(q + par * 0.6  + uSeed, 0.42, 0.20, 1.05, uppq, 0.24, 2.0);
  L += stars(q + par * 1.0  + uSeed, 1.30, 0.22, 1.5,  uppq, 1.0,  3.0);
  L *= clamp(1.0 / max(abs(lens), 0.12), 0.6, 3.0);

  // ---------- холодный газ, ≤ 5 % яркости, пустота у дыры ----------
  {
    vec2 nq = q * 0.9 + par * 0.4 + vec2(0.015, 0.006) * t + uSeed * 3.0;
    nq = mat2(0.9, 0.44, -0.44, 0.9) * nq;
    float n = fbm(nq * 0.55, 4);
    float mk = fbm(nq * 0.16 + 4.2, 3);
    float neb = smoothstep(0.42, 0.95, n) * smoothstep(0.38, 0.8, mk);
    L += neb * 0.05 * smoothstep(0.6, 2.4, b);
  }

  // ---------- аккреционный диск ----------
  float cosI = 0.26;
  vec2 pd = vec2(p.x, p.y / cosI);
  float rd = length(pd);
  float ad = atan(pd.y, pd.x);
  float energy = 0.8;
  float w = 0.5 / max(pow(rd, 1.5), 0.2);                         // кеплеровское вращение
  float streaks = fbm(vec2(ad * 2.2 - t * w, rd * 3.4 - t * 0.02), 4);
  float streaks2 = fbm(vec2(ad * 5.0 + t * w * 1.7, rd * 7.0), 3);
  float tex = 0.45 + 0.75 * streaks + 0.35 * streaks2;
  float rIn = 1.04, rOut = 3.9;
  float prof = smoothstep(rIn - 0.03, rIn + 0.05, rd) * (1.0 - smoothstep(rOut - 1.4, rOut, rd)) * pow(rIn / max(rd, rIn), 2.4);
  float vlos = -0.36 * cos(ad);                                    // доплер: приближающаяся сторона ярче
  float beam = pow(1.0 + vlos, 2.4);
  float near = smoothstep(-0.3, 0.3, p.y);
  float disk = prof * tex * beam * (0.55 + 0.45 * near) * smoothstep(0.98, 1.02, b);

  // ---------- дальняя сторона диска, перекинутая над и под тенью ----------
  float arch;
  {
    float ra = 1.11 + 0.035 * sin(ang * 3.0 + t * 0.2);
    float da = (b - ra) / (0.05 + 0.045 * abs(sin(ang)));
    float archTex = 0.55 + 0.8 * fbm(vec2(ang * 3.0 - t * 0.35, b * 6.0), 3);
    float top = smoothstep(0.05, 0.75, -p.y / max(b, 0.01));
    float bottom = smoothstep(0.1, 0.9, p.y / max(b, 0.01)) * 0.4;
    arch = exp(-da * da) * archTex * (top + bottom) * pow(1.0 - 0.36 * cos(ang), 2.0) * 1.9 * smoothstep(0.99, 1.03, b);
  }

  // ---------- фотонное кольцо, джет, лучи, свечение ----------
  float ring = exp(-pow((b - 1.025) / 0.014, 2.0)) * 0.9;
  float ringHalo = exp(-pow((b - 1.03) / 0.09, 2.0)) * 0.10;
  float jetX = exp(-p.x * p.x / (0.010 + 0.06 * abs(p.y)));
  float jetY = smoothstep(1.0, 1.9, abs(p.y)) * exp(-abs(p.y) * 0.3) * (p.y > 0.0 ? 1.0 : 0.55);
  float jetTex = 0.75 + 0.35 * fbm(vec2(p.x * 6.0, abs(p.y) * 2.5 - t * 1.4), 3);
  float jet = jetX * jetY * jetTex * 0.09;
  float rays = 0.5 + 0.5 * fbm(vec2(ang * 7.0 + uSeed, b * 0.6 - t * 0.04), 3);
  float glowWide = pow(rIn / max(rd, rIn), 1.6) * (0.5 + 0.5 * beam) * smoothstep(0.9, 1.2, b) * (1.0 - smoothstep(rOut, rOut + 3.0, rd));
  float halo = (0.035 / (b * b + 0.35)) * (0.6 + 0.8 * rays) * smoothstep(0.95, 1.1, b);
  float bloom = glowWide * 0.16 * (0.7 + 0.3 * rays) + halo;

  float diskL = (disk * 1.35 + arch * 1.1) * energy;
  L += diskL + ring * 1.6 + ringHalo + jet + bloom * energy;

  // ---------- цвет: сцена монохромная, эмиссия — с оттенком ----------
  float emis = clamp((diskL + arch * energy + jet * 3.0 + ringHalo + bloom * energy * 2.0) / max(L, 1e-4), 0.0, 1.0);
  float mid = smoothstep(0.03, 0.5, L) * (1.0 - smoothstep(1.1, 2.4, L));
  vec3 tint = mix(vec3(1.0), uAccent, uAccentAmt * (1.0 - uInvert) * emis * mid * 0.9);
  vec3 col = L * tint;
  col *= smoothstep(0.985, 1.0, b);                                 // тень
  vec2 vc = (gl_FragCoord.xy / res - 0.5) * vec2(res.x / res.y, 1.0);
  col *= 1.0 - 0.35 * dot(vc, vc);                                 // виньетка
  col = 1.0 - exp(-col * 1.35);                                    // тон-маппинг
  col = pow(col, vec3(0.92));
  col += (ign(gl_FragCoord.xy + fract(t) * 17.0) - 0.5) / 255.0 * 2.5;
  vec3 print = mix(1.0 - col, vec3(0.955, 0.955, 0.945), 0.06);   // светлая тема — негатив, как отпечаток
  col = mix(col, print, uInvert);
  gl_FragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}`;

function hexToRgb(hex) {
  const n = parseInt(hex.replace('#', ''), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export function createCosmos(canvas, opts = {}) {
  const reduced = !!opts.reduced;
  let gl = null;
  try { gl = canvas.getContext('webgl', { antialias: false, alpha: false, depth: false, stencil: false, powerPreference: 'low-power' }); } catch { gl = null; }
  const api = {
    ok: false, accent: hexToRgb(opts.accent || '#5a86ff'), accentAmt: 1, invert: 0,
    center: opts.center || [0.68, 0.55], scale: opts.scale || 0.11,
    setAccent(hex, amt = 1) { api.accent = hexToRgb(hex); api.accentAmt = amt; api.wake(); },
    setInvert(v) { api.invert = v ? 1 : 0; api.wake(); },
    setCenter(x, y, scale) { api.center = [x, y]; if (scale) api.scale = scale; api.wake(); },
    setPointer(x, y) { api._pt = [x, y]; if (reduced) api._ptS = [x, y]; api.wake(); },
    setScroll(v) { api._scroll = v; api.wake(); },
    pause() { api._paused = true; },
    resume() { api._paused = false; api.wake(); },
    wake() { if (!api._raf && api.ok && !api._paused) api._raf = requestAnimationFrame(api._frame); },
    resize() {},
    destroy() { cancelAnimationFrame(api._raf); api._raf = 0; },
    _pt: [0, 0], _ptS: [0, 0], _scroll: 0, _paused: false, _raf: 0, _last: 0, _t: 0,
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
  } catch { return api; } // шейдер не собрался — остаёмся на CSS-подложке, без шума в консоли
  gl.useProgram(prog);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, 'p');
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const U = {};
  for (const n of ['uRes', 'uTime', 'uPointer', 'uScroll', 'uCenter', 'uScale', 'uAccent', 'uAccentAmt', 'uInvert', 'uSeed']) U[n] = gl.getUniformLocation(prog, n);
  const seed = opts.seed ?? 0.37;
  api.ok = true;

  let w = 0, hgt = 0;
  const RES_SCALE = opts.resScale || 0.5;
  function resize() {
    const r = canvas.getBoundingClientRect();
    const dpr = Math.min(1, devicePixelRatio || 1);
    let nw = Math.max(2, Math.round(r.width * dpr * RES_SCALE)), nh = Math.max(2, Math.round(r.height * dpr * RES_SCALE));
    const cap = 1.1e6;                                              // потолок: ~1.1 Мп на кадр
    if (nw * nh > cap) { const k = Math.sqrt(cap / (nw * nh)); nw = Math.round(nw * k); nh = Math.round(nh * k); }
    if (nw !== w || nh !== hgt) { w = nw; hgt = nh; canvas.width = w; canvas.height = hgt; gl.viewport(0, 0, w, hgt); }
  }
  api.resize = () => { resize(); api.wake(); };

  const draw = () => {
    resize();
    gl.uniform2f(U.uRes, w, hgt);
    gl.uniform1f(U.uTime, api._t);
    gl.uniform2f(U.uPointer, api._ptS[0], api._ptS[1]);
    gl.uniform1f(U.uScroll, api._scroll);
    gl.uniform2f(U.uCenter, api.center[0], 1 - api.center[1]);
    gl.uniform1f(U.uScale, api.scale);
    gl.uniform3f(U.uAccent, api.accent[0], api.accent[1], api.accent[2]);
    gl.uniform1f(U.uAccentAmt, api.accentAmt);
    gl.uniform1f(U.uInvert, api.invert);
    gl.uniform1f(U.uSeed, seed);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
  api._frame = (now) => {
    api._raf = 0;
    const dt = Math.min(0.05, api._last ? (now - api._last) / 1000 : 0.016);
    api._last = now;
    if (!reduced) api._t += dt;
    // reduced (?motion=0 / prefers-reduced-motion): указатель сразу в целевой точке — без
    // сглаживания по реальному dt, иначе кадры стенда не будут детерминированы между прогонами
    // (сходимость по реальному времени отличается на доли процента от прогона к прогону).
    if (reduced) { api._ptS[0] = api._pt[0]; api._ptS[1] = api._pt[1]; }
    else {
      api._ptS[0] += (api._pt[0] - api._ptS[0]) * Math.min(1, dt * 3.5);
      api._ptS[1] += (api._pt[1] - api._ptS[1]) * Math.min(1, dt * 3.5);
    }
    draw();
    const settled = reduced && Math.abs(api._ptS[0] - api._pt[0]) < 0.002 && Math.abs(api._ptS[1] - api._pt[1]) < 0.002;
    if (!api._paused && !settled) api._raf = requestAnimationFrame(api._frame);
    else api._last = 0;
  };
  api.wake();
  return api;
}
