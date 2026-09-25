// выдра — ЧЕРНОВИК сцены S1b (арт-директор). API как у vydra/static/cosmos.js, плюс pulse(kind) и setEnergy(v).
// Стенд подменяет им /static/cosmos.js (.sprint/proto/rig.py). Исполнитель фундамента (S2) переносит это в cosmos.js.
//
// Решение пользователя: положение и размер дыры — как в baseline (формула baseLayout ниже), всё остальное — «Затмение».
// Замечания: дыра не чёрная (ядро 15–58 из 255, фактура), край в две ступени, мягкость, туман, «RTX»:
// кольцо светит в туман (объёмные лучи с тенями от облаков), широкая диффузия, световая плоскость с отражением кольца.
//
// Слои (сзади вперёд): «чистый план» пользователя (звёзды, туманность, плоскость; честно линзируется) → процедурные звёзды →
// туман (спиральный вихрь, подсвечен кольцом, лучи и объёмные тени) → ореол (плато + многоступенчатый спад, нити,
// тонкие концентрические кольца, латеральная хроматическая аберрация) → плоскость-горизонт (пятно света, отражение кольца) →
// анаморфный блик → тон-маппинг → ядро-сфера (в экранных значениях) → фотонное кольцо → импульс → дизеринг.

const VERT = `attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }`;

const FRAG = `
precision highp float;
uniform vec2  uRes;
uniform float uH;
uniform float uTime;
uniform vec2  uPar;        // параллакс -1..1 (сглажен)
uniform vec3  uMouse;      // xy — курсор, px холста (GL), z — сила линзы 0..1
uniform float uScroll;
uniform vec2  uC;          // центр дыры, px холста (GL)
uniform float uR;          // радиус тени, px холста
uniform float uHalo;       // сила ореола
uniform float uBand;       // толщина раскалённого пояса, доли R
uniform float uCA;         // хроматическая кайма
uniform float uBeam;       // направление яркой стороны, рад
uniform float uBeamAmt;    // асимметрия 0..1
uniform float uLensE;      // эйнштейновский радиус для дальнего плана, доли R
uniform vec3  uTint;       // оттенок тумана и каймы (платформа)
uniform float uTintAmt;
uniform float uSwell;      // импульс: вспухание ореола 0..1
uniform float uLine;       // импульс «Скачать»: яркость черты 0..1
uniform float uLinePh;     // положение головы черты 0..1 (от поля к кольцу)
uniform vec2  uEq;         // x — правый край поля ссылки, y — его центр, px холста (GL)
uniform float uEnergy;     // активность загрузок 0..1
uniform float uFog;
uniform float uRays;
uniform float uPlane;
uniform float uPlaneY;     // линия плоскости ниже центра, доли R
uniform float uFar;
uniform float uStars;
uniform float uFlare;
uniform float uInner;
uniform float uFib;
uniform float uInvert;
uniform float uSeed;
uniform sampler2D uFarTex;
uniform vec4  uFarMap;     // xy — точка привязки в текстуре (u вправо, v вниз), zw — размер текстуры, px холста
uniform vec2  uFarAnchor;  // куда на холсте попадает точка привязки (GL)
uniform float uHasFar;

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
    float tw = 1.0 + 0.16 * sin(uTime * (0.25 + 1.1 * h2.y) + TAU * h2.z);
    float d = length(q - c) / upp;
    float s = sigmaPx * (0.7 + 0.6 * h2.z);
    acc += b * tw * (exp(-d * d / (2.0 * s * s)) + 0.04 * exp(-d / (s * 4.0)));
  }
  return acc;
}

// «чистый план» (текстура пользователя): точка P в px холста (GL)
float plate(vec2 P){
  vec2 d = (P - uFarAnchor) / uFarMap.zw;
  vec2 uv = uFarMap.xy + vec2(d.x, -d.y);
  float inb = smoothstep(0.0, 0.03, uv.x) * smoothstep(1.0, 0.97, uv.x) * smoothstep(0.0, 0.03, uv.y) * smoothstep(1.0, 0.985, uv.y);
  return texture2D(uFarTex, clamp(uv, 0.001, 0.999)).r * inb;
}

// профиль ореола снаружи тени; e — расстояние от края тени в долях R.
// ядро — раскалённый пояс и быстрый спад (всегда белые); рассеяние — длинный спад в тумане (берёт оттенок источника)
float haloCore(float e, float band){
  float plateau = smoothstep(0.012, 0.07, e) * (1.0 - smoothstep(band * 0.45, band * 1.25, e)) * 1.55;
  float x = max(e - band * 0.7, 0.0);
  float fast = 0.80 * exp(-x / 0.11) * smoothstep(0.0, 0.07, e);
  float groove = 1.0 - 0.22 * exp(-pow((e - 0.030) / 0.035, 2.0));        // мягкая ложбина у кромки — «контактная тень»
  return (plateau + fast) * groove;
}
float haloScatter(float e, float band){
  float x = max(e - band * 0.7, 0.0);
  float slow = (0.38 * exp(-x / 0.34) + 0.11 * exp(-x / 1.0)) * smoothstep(0.0, 0.07, e);
  float veil = 0.032 / (1.0 + (e / 1.8) * (e / 1.8));
  return slow + veil;
}
// тонкие концентрические кольца (вторичные изображения), убывающая непрозрачность
float ringsProfile(float e, float band){
  float s = 0.0;
  s += 0.24 * exp(-pow((e - band - 0.08) / 0.011, 2.0));
  s += 0.12 * exp(-pow((e - band - 0.21) / 0.013, 2.0));
  s += 0.06 * exp(-pow((e - band - 0.40) / 0.016, 2.0));
  return s;
}

void main(){
  vec2 fc = gl_FragCoord.xy;
  float H = uH;
  float t = uTime;
  vec2 c = uC + vec2(0.0, uScroll * H * 0.16);
  vec2 P = fc - c;
  vec2 p = P / uR;                              // радиус тени = 1
  float b = length(p);
  float ang = atan(p.y, p.x);
  float e = max(b - 1.0, 0.0);
  vec2 dir = p / max(b, 1e-3);
  float kRH = uR / H;

  // ---------- курсор: лёгкая гравитационная линза фона ----------
  vec2 mv = (fc - uMouse.xy) / H;
  vec2 mdisp = -mv * (0.0007 * uMouse.z) / (dot(mv, mv) + 0.004);   // ≤ ~5 CSS px смещения

  // ---------- линзирование ----------
  float E = uLensE;
  float lensF = 1.0 - (E * E) / (b * b + 0.02);                    // дальний план и туман
  float Es = min(E * 0.75, 0.45);
  float lensS = 1.0 - (Es * Es) / (b * b + 0.02);                 // звёзды — слабее (без «дождя»)
  vec2 par = uPar * 0.010;
  vec2 qs  = p * lensF * kRH + mdisp;                              // доли H
  vec2 qss = p * lensS * kRH + mdisp;

  float be = 1.0 + uBeamAmt * cos(ang - uBeam);                    // яркая сторона
  float breath = 1.0 + 0.03 * sin(t * TAU / 7.0) + 0.05 * uEnergy + 0.30 * uSwell;

  // ---------- дальний план: «чистый план» пользователя ----------
  vec3 col = vec3(0.0);
  float far = 0.0;
  if (uHasFar > 0.5) {
    vec2 Pl = c + p * max(lensF, -0.5) * uR + mdisp * H + uPar * H * 0.006;
    far = plate(Pl);
  }
  float ylF = p.y + uPlaneY;                                        // над линией плоскости > 0
  float L = far * uFar * mix(0.48, 1.0, smoothstep(-0.35, 0.05, ylF));

  // ---------- звёзды ----------
  float upp = 1.0 / H;
  float uppq = upp * clamp(abs(lensS), 0.25, 3.0);
  float S = 0.0;
  S += stars(qss + par * 0.25 + uSeed, 0.026, 0.16, 0.75, uppq, 0.05, 1.0);
  S += stars(qss + par * 0.60 + uSeed, 0.070, 0.20, 0.95, uppq, 0.20, 2.0);
  S += stars(qss + par * 1.00 + uSeed, 0.200, 0.22, 1.30, uppq, 0.80, 3.0);
  L += S * uStars * clamp(1.0 / max(abs(lensS), 0.3), 0.7, 1.8);

  // ---------- туман: спиральный вихрь вокруг дыры, два слоя ----------
  float rq = length(qs) / kRH;                                     // линзированный радиус, доли R
  float twist = 0.85 * log(max(rq, 0.6)) - t * 0.02 * (1.0 + uEnergy);
  vec2 fq = rot(twist) * qs;
  float n1 = fbm(fq * 2.3 + par * 0.5 + uSeed * 3.0, 4);
  float k1 = fbm(fq * 0.75 + 4.2, 3);
  float dens1 = smoothstep(0.36, 0.92, n1) * smoothstep(0.20, 0.72, k1);
  vec2 f2 = rot(-0.35) * (qs * 5.2 + par * 1.3) - vec2(0.010, 0.004) * t * (1.0 + 1.5 * uEnergy) + 11.0;
  float dens2 = smoothstep(0.50, 1.02, fbm(f2, 3));
  float dens = (dens1 * 0.85 + dens2 * 0.32) * mix(0.5, 1.0, exp(-abs(p.y) * 0.45));

  // свет кольца, падающий на туман: мягкий спад + лучи + объёмные тени облаков на пути к кольцу
  float Iring = be / (1.0 + (e / 0.8) * (e / 0.8));
  float sh1 = fbm(dir * 3.1 + vec2(uSeed * 5.0, t * 0.008), 3);
  float shafts = mix(1.0, 0.18 + 1.7 * smoothstep(0.30, 0.78, sh1), uRays);
  float T = 1.0;
  if (e > 0.05 && e < 4.0) {
    float acc = 0.0;
    for (int k = 0; k < 6; k++) {
      float fk = (float(k) + 0.5) / 6.0;
      float bk = 1.08 + (b - 1.08) * fk;
      vec2 qk = dir * bk * kRH;
      float rk = bk;
      vec2 fk2 = rot(0.85 * log(max(rk, 0.6)) - t * 0.02 * (1.0 + uEnergy)) * qk;
      acc += smoothstep(0.36, 0.92, fbm(fk2 * 2.3 + par * 0.5 + uSeed * 3.0, 2));
    }
    T = exp(-acc / 6.0 * min(e, 1.6) * 2.4 * uRays);
    T = mix(T, 1.0, smoothstep(2.6, 4.0, e));                     // без шва на границе марша
  }
  float lit = Iring * shafts * T;
  // пятно плоскости тоже подсвечивает туман снизу
  float yPl = -uPlaneY;                                            // линия плоскости, доли R (в координатах p)
  float xpool = exp(-p.x * p.x / (2.0 * 1.7 * 1.7));
  float fromPlane = xpool * exp(-abs(p.y - yPl) / 0.9) * mix(0.35, 1.0, smoothstep(yPl - 0.3, yPl + 0.1, p.y)) * 0.45;
  float fogL = dens * (0.028 + 1.05 * lit + fromPlane) * uFog * breath;
  vec3 ice = vec3(0.90, 0.95, 1.0);
  float tintK = clamp(uTintAmt * (0.35 + 0.65 * min(lit * 1.5, 1.0)), 0.0, 0.85);
  vec3 fogCol = fogL * mix(ice, uTint * 1.25, tintK);

  // ---------- ореол: раскалённо-белый пояс, нити, многоступенчатый спад ----------
  // пояс неровный по углу: внешний край «дышит» прядями, а не ровный бублик
  float nb = fbm(dir * 1.8 + vec2(t * 0.006, uSeed * 3.0), 3);
  float band = uBand * (0.72 + 0.62 * nb);
  float w = (0.045 + 0.11 * uEnergy) / pow(max(b, 1.0), 1.5);
  float th = atan(p.y, -p.x) + t * w + 1.5 * log(max(b, 1.0));      // спираль: пряди закручиваются наружу
  float rr = b * 12.0 + uSeed * 5.0;
  float fA = fbm(vec2(th * 3.2, rr), 4);
  float fB = fbm(vec2((th - TAU * sign(th)) * 3.2, rr), 4);
  float seam = smoothstep(PI - 0.35, PI, abs(th));
  float fil = mix(fA, fB, seam * 0.5);
  fil = pow(clamp(fil * 1.2, 0.0, 1.0), 1.4) * 1.6;
  float tex = mix(1.0, 0.50 + 0.8 * fil, uFib * exp(-e / 0.40));   // нити — только в поясе и ближнем спаде
  float wisp = fil * exp(-max(e - band, 0.0) / 0.22) * smoothstep(band * 0.6, band * 1.1, e) * 0.35 * uFib;   // пряди за поясом
  // тёмная дымка вихря поверх свечения (силуэты на фоне ореола)
  float smoke = dens1 * smoothstep(band * 0.6, band * 1.6, e) * (1.0 - smoothstep(1.2, 2.6, e));
  float absorb = 1.0 - 0.42 * smoke;
  // латеральная хроматическая аберрация: красный канал смещён вправо, синий — влево
  vec2 dCA = vec2(0.013 * uCA, 0.0);
  float eR = max(length(p - dCA) - 1.0, 0.0);
  float eB = max(length(p + dCA) - 1.0, 0.0);
  vec3 hal = vec3(haloCore(eR, band), haloCore(e, band), haloCore(eB, band));
  vec3 sca = vec3(haloScatter(eR, band), haloScatter(e, band), haloScatter(eB, band));
  vec3 rng = vec3(ringsProfile(eR, uBand), ringsProfile(e, uBand), ringsProfile(eB, uBand));
  vec3 scaCol = mix(ice, uTint * 1.2, uTintAmt * 0.38);
  vec3 halo = ((hal * tex + rng + wisp) + sca * mix(1.0, tex, 0.4) * scaCol) * be * breath * uHalo * absorb;

  // радужная кайма на внешнем краю пояса — только на дуге, обращённой к форме (сверху-слева)
  float xw = (e - band * 1.05) / (0.030 + band * 0.12);
  float bandCA = exp(-xw * xw * 1.3);
  vec3 spec = mix(vec3(1.0), hue2rgb(clamp(0.02 + (0.5 - 0.5 * xw) * 0.72, 0.0, 0.78)), 0.85);
  spec = mix(spec, uTint * 1.3, uTintAmt * 0.45);
  float caMask = smoothstep(-0.2, 0.9, cos(ang - 2.25));
  vec3 kayma = spec * bandCA * caMask * uCA * 0.8 * be * (1.0 + 1.4 * uSwell);

  // частицы на орбитах
  float om = (0.035 + 0.09 * uEnergy) / pow(max(b, 1.0), 1.5);
  float pr = stars(rot(t * om) * p * kRH + 5.0, 0.018, 0.22, 0.6, upp, 0.9, 5.0);
  pr *= smoothstep(0.0, 0.02, e) * (1.0 - smoothstep(0.15, 0.6, e));

  // ---------- плоскость-горизонт: пятно света под дырой, отражение кольца ----------
  float yl = p.y - yPl;                                           // + над линией, − под ней (доли R)
  float xfar = exp(-abs(p.x) / 5.5);
  float lineG = exp(-abs(yl) / 0.045) * (0.30 + 0.70 * xpool) * xfar * 0.85;
  float above = exp(-max(yl, 0.0) / 0.26) * step(0.0, yl) * xpool;
  float below = exp(-max(-yl, 0.0) / 0.55) * step(yl, 0.0) * (0.35 + 0.65 * xpool) * xfar * 0.45;
  vec2 pm = vec2(p.x, 2.0 * yPl - p.y);                           // зеркало относительно линии
  float em = max(length(pm) - 1.0, 0.0);
  float refl = (haloCore(em * 0.7, uBand * 1.6) + haloScatter(em * 0.7, uBand * 1.6)) * exp(-max(-yl, 0.0) / 0.35) * step(yl, 0.0) * 0.10;
  float planeL = (lineG * 0.85 + above * 0.30 + below * 0.28 + refl) * uPlane * breath;

  // ---------- анаморфный блик ----------
  float dyF = p.y;
  float flare = (exp(-pow(dyF / 0.035, 2.0)) * exp(-abs(p.x) / 1.8) * 0.20 + exp(-pow(dyF / 0.12, 2.0)) * exp(-abs(p.x) / 1.2) * 0.05)
              * uFlare * (0.85 + 0.15 * sin(t * 0.37));

  // ---------- сборка (HDR) ----------
  col = vec3(L) + fogCol + halo + kayma + vec3(pr) + vec3(planeL) * ice + vec3(flare) * vec3(0.92, 0.96, 1.0);
  // широкая диффузия: всё рядом с кольцом чуть приподнято (свет рассеивается в воздухе)
  col += vec3(0.020 / (1.0 + (e / 0.8) * (e / 0.8))) * be * breath * ice;

  vec2 vc = (fc / uRes - 0.5) * vec2(uRes.x / uRes.y, 1.0);
  col *= 1.0 - 0.22 * dot(vc, vc);
  col = 1.0 - exp(-col * 1.25);
  col = pow(col, vec3(0.94));

  // ---------- ядро: сфера в контровом свете, не #000 (экранные значения) ----------
  float d = clamp(1.0 - b, 0.0, 1.0);
  float limb = pow(1.0 - clamp(d / 0.95, 0.0, 1.0), 1.6);
  float rim = exp(-max(d - 0.02, 0.0) / 0.21);
  float fast = exp(-d / 0.012);
  float beIn = 1.0 + 0.55 * uBeamAmt * cos(ang - uBeam);
  float lum = 0.060 + (0.150 * limb + 0.20 * rim + 0.26 * fast) * beIn;
  vec2 lq = rot(t * 0.030) * p;
  float liq = fbm(lq * 2.4 + uSeed * 9.0, 3) - 0.5;
  float liq2 = fbm(rot(-t * 0.017) * p * 5.5 + 3.0, 2) - 0.5;
  lum += (liq * 0.07 + liq2 * 0.028) * (0.35 + 0.65 * limb);
  lum += (hash12(floor(fc) + floor(t * 12.0)) - 0.5) * 0.012;             // зерно внутри ядра
  float part = stars(p * kRH + vec2(t * 0.004, 0.0) + 9.0, 0.03, 0.22, 0.7, upp, 0.30, 7.0);
  lum += part * 0.35;
  lum *= uInner * (1.0 + 0.10 * uSwell);
  float planeOver = (lineG * 0.85 + above * 0.30) * uPlane;         // плоскость перекрывает низ ядра
  vec3 disk = vec3(lum) * vec3(0.955, 0.975, 1.02) + vec3(1.0 - exp(-planeOver * 1.25)) * ice * 0.9;
  float aa = 1.3 / uR;
  float m = 1.0 - smoothstep(1.0 - aa, 1.0 + aa * 0.25, b);
  col = mix(col, disk, m);

  // фотонное кольцо — тонкая раскалённая линия поверх стыка (с той же аберрацией)
  float pw = max(1.0 / uR, 0.007);
  float ringR = exp(-pow((length(p - dCA * 0.6) - 1.0) / pw, 2.0));
  float ringG = exp(-pow((b - 1.0) / pw, 2.0));
  float ringB = exp(-pow((length(p + dCA * 0.6) - 1.0) / pw, 2.0));
  vec3 ringC = vec3(ringR, ringG, ringB) * (1.05 + 0.20 * be) * (1.0 + 0.4 * uSwell);
  col = 1.0 - (1.0 - col) * (1.0 - clamp(ringC, 0.0, 1.0));

  // ---------- импульс «Скачать»: тонкая черта от поля ссылки к кольцу ----------
  if (uLine > 0.001) {
    float x0 = uEq.x, x1 = c.x - uR * 1.08;
    float xh = mix(x0, x1, uLinePh);
    float tail = (x1 - x0) * 0.38;
    float along = smoothstep(xh - tail, xh, fc.x) * (1.0 - smoothstep(xh, xh + 3.0, fc.x)) * step(x0, fc.x);
    float dy = fc.y - uEq.y;
    float lineL = (exp(-dy * dy / 0.8) + 0.18 * exp(-dy * dy / 18.0)) * along * uLine * 0.75;
    col = 1.0 - (1.0 - col) * (1.0 - vec3(lineL) * ice);
  }

  col *= 1.0 - 0.92 * smoothstep(0.12, 0.7, uScroll);             // сцена гаснет, когда герой уходит вверх
  col += (ign(fc + fract(t) * 17.0) - 0.5) / 255.0 * 3.0;          // дизеринг против полос
  vec3 print = mix(1.0 - col, vec3(0.955, 0.955, 0.945), 0.06);
  col = mix(col, print, uInvert);
  gl_FragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}`;

function hexToRgb(hex) {
  const n = parseInt(hex.replace('#', ''), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

// «чистый план» пользователя (.sprint/art/user/chatgpt-plate-1.png → webp, оттенки серого):
// точка привязки — середина светлого пятна на линии плоскости.
const PLATE = { url: '/static/scene/plate.webp', w: 1671, h: 941, u0: 0.69, v0: 0.797 };

// значения по умолчанию, если CSS не задал токены
const DEF = {
  anchor: 'baseline', x: 0.70, y: 0.54, r: 0.13,
  halo: 1.0, band: 0.22, ca: 1.0, beam: 160, beamAmt: 0.32, lens: 0.8,
  fog: 1.0, rays: 1.0, plane: 1.0, planeY: 1.45, far: 0.85, stars: 0.4, flare: 1.0, inner: 1.0, fib: 0.85,
};
const NEUTRAL = '#5a86ff';

// положение и размер дыры «как в baseline» (app.js: heroLayout + высота героя baseline), CSS px окна
export function baseLayout(vw, vh) {
  const heroH = Math.max(674, 0.94 * vh);
  const k = Math.min(0.14, Math.max(0.09, 0.115 * vw / 1440));
  return { cx: 0.70 * vw, cy: 68 + heroH / 2, R: k * heroH };
}

export function createCosmos(canvas, opts = {}) {
  const reduced = !!opts.reduced;
  let gl = null;
  try { gl = canvas.getContext('webgl', { antialias: false, alpha: false, depth: false, stencil: false, powerPreference: 'low-power' }); } catch { gl = null; }
  const api = {
    ok: false, invert: 0,
    tint: [1, 1, 1], tintT: [1, 1, 1], tintAmt: 0, tintAmtT: 0,
    energy: 0, energyT: 0,
    pulseAt: -1, pulseKind: 0, lastPulse: -10, swell: 0, line: 0, linePh: 0,
    setAccent(hex, amt = 1) {
      const neutral = !hex || hex.toLowerCase() === NEUTRAL;
      if (!neutral) api.tintT = hexToRgb(hex);
      api.tintAmtT = neutral ? 0 : 0.55 * amt;
      api.wake();
    },
    setInvert(v) { api.invert = v ? 1 : 0; api.wake(); },
    setCenter() { api.readTokens(); api.wake(); },   // композиция — из CSS-токенов и baseLayout
    setPointer(x, y) { api._pt = [x, y]; api._ptSeen = true; api.wake(); },
    setScroll(v) { api._scroll = v; api.wake(); },
    setEnergy(v) { api.energyT = Math.max(0, Math.min(1, v)); api.wake(); },
    // импульс: 'go' — черта от поля к кольцу и вспухание ореола; 'done' — только вспухание. Не чаще 3 раз в секунду.
    pulse(kind = 'go') {
      if (reduced) return;
      if (api._t - api.lastPulse < 0.34) return;
      api.lastPulse = api._t; api.pulseAt = api._t; api.pulseKind = kind === 'go' ? 1 : 0; api.wake();
    },
    pause() { api._paused = true; },
    resume() { api._paused = false; api.wake(); },
    wake() { if (!api._raf && api.ok && !api._paused) api._raf = requestAnimationFrame(api._frame); },
    resize() {},
    readTokens() {},
    destroy() { cancelAnimationFrame(api._raf); api._raf = 0; },
    _pt: [0, 0], _ptS: [0, 0], _ptSeen: false, _mz: 0, _scroll: 0, _paused: false, _raf: 0, _last: 0,
    _t: 14.0,   // «время постера»: нити уже закручены, кадр без движения выглядит собранным
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
  for (const n of ['uRes', 'uH', 'uTime', 'uPar', 'uMouse', 'uScroll', 'uC', 'uR', 'uHalo', 'uBand', 'uCA', 'uBeam', 'uBeamAmt', 'uLensE',
    'uTint', 'uTintAmt', 'uSwell', 'uLine', 'uLinePh', 'uEq', 'uEnergy', 'uFog', 'uRays', 'uPlane', 'uPlaneY', 'uFar', 'uStars', 'uFlare',
    'uInner', 'uFib', 'uInvert', 'uSeed', 'uFarTex', 'uFarMap', 'uFarAnchor', 'uHasFar']) U[n] = gl.getUniformLocation(prog, n);
  const seed = opts.seed ?? 0.37;
  api.ok = true;

  // текстура дальнего плана: грузится лениво, до загрузки сцена рисуется без неё
  let hasFar = 0;
  const tex = gl.createTexture();
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, 1, 1, 0, gl.LUMINANCE, gl.UNSIGNED_BYTE, new Uint8Array([0]));
  {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => {
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, gl.LUMINANCE, gl.UNSIGNED_BYTE, img);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      hasFar = 1; api.wake();
    };
    img.onerror = () => {};
    img.src = PLATE.url;
  }

  // токены композиции и вида из CSS (.hero)
  let tok = { ...DEF };
  let tokKey = '';
  const num = (cs, name, d) => { const v = parseFloat(cs.getPropertyValue(name)); return Number.isFinite(v) ? v : d; };
  api.readTokens = () => {
    const cs = getComputedStyle(canvas);
    const n = {
      anchor: (cs.getPropertyValue('--bh-anchor').trim() || DEF.anchor),
      x: num(cs, '--bh-x', DEF.x), y: num(cs, '--bh-y', DEF.y), r: num(cs, '--bh-r', DEF.r),
      halo: num(cs, '--bh-halo', DEF.halo), band: num(cs, '--bh-band', DEF.band), ca: num(cs, '--bh-ca', DEF.ca),
      beam: num(cs, '--bh-beam', DEF.beam), beamAmt: num(cs, '--bh-beam-amt', DEF.beamAmt), lens: num(cs, '--bh-lens', DEF.lens),
      fog: num(cs, '--bh-fog', DEF.fog), rays: num(cs, '--bh-rays', DEF.rays), plane: num(cs, '--bh-plane', DEF.plane),
      planeY: num(cs, '--bh-plane-y', DEF.planeY), far: num(cs, '--bh-far', DEF.far), stars: num(cs, '--bh-stars', DEF.stars),
      flare: num(cs, '--bh-flare', DEF.flare), inner: num(cs, '--bh-inner', DEF.inner), fib: num(cs, '--bh-fib', DEF.fib),
    };
    const key = JSON.stringify(n);
    if (key !== tokKey) { tokKey = key; tok = n; return true; }
    return false;
  };
  api.readTokens();
  // черновик: CSS варианта подмешивается после загрузки — следим за <style> в <head>
  new MutationObserver(() => { if (api.readTokens()) api.wake(); }).observe(document.head, { childList: true });
  setInterval(() => { if (api.readTokens()) api.wake(); }, 400);

  // черновик: без правок app.js — импульс по #go.fire, энергия и «готово» по острову
  const go = document.getElementById('go');
  if (go) new MutationObserver(() => { if (go.classList.contains('fire')) api.pulse('go'); }).observe(go, { attributes: true, attributeFilter: ['class'] });
  const island = document.getElementById('island');
  if (island) new MutationObserver(() => {
    const s = island.dataset.state;
    api.setEnergy(s === 'compact' || s === 'expanded' ? 1 : s === 'ask' ? 0.4 : 0);
    if (s === 'done') api.pulse('done');
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
  const draw = () => {
    resize();
    const sx = w / cssW, sy = hgt / cssH;
    const rect = canvas.getBoundingClientRect();
    // композиция в CSS px холста (холст = первый экран, верх — верх страницы)
    const comp = tok.anchor === 'baseline' ? baseLayout(cssW, cssH) : { cx: tok.x * cssW, cy: tok.y * cssH, R: tok.r * cssH };
    const mxC = (api._ptS[0] * 0.5 + 0.5) * innerWidth, myC = (0.5 - api._ptS[1] * 0.5) * innerHeight;
    const mx = (mxC - rect.left) * sx, my = hgt - (myC - rect.top) * sy;
    // «чистый план»: пятно плоскости — под дырой, линия плоскости — на planeY·R ниже центра; масштаб — чтобы закрыть холст
    const ax = comp.cx, ay = comp.cy + tok.planeY * comp.R;
    const s = Math.max(ax / (PLATE.u0 * PLATE.w), (cssW - ax) / ((1 - PLATE.u0) * PLATE.w), ay / (PLATE.v0 * PLATE.h),
      (cssH * 0.92 - ay) / ((1 - PLATE.v0) * PLATE.h));
    let eqX = -1e4, eqY = -1e4;
    if (portalEl) { const pr = portalEl.getBoundingClientRect(); eqX = (pr.right - rect.left) * sx; eqY = hgt - ((pr.top + pr.height / 2) - rect.top) * sy; }
    gl.uniform2f(U.uRes, w, hgt);
    gl.uniform1f(U.uH, hgt);
    gl.uniform1f(U.uTime, api._t);
    gl.uniform2f(U.uPar, reduced ? 0 : api._ptS[0], reduced ? 0 : api._ptS[1]);
    gl.uniform3f(U.uMouse, mx, my, reduced ? 0 : api._mz);
    gl.uniform1f(U.uScroll, api._scroll);
    gl.uniform2f(U.uC, comp.cx * sx, hgt - comp.cy * sy);
    gl.uniform1f(U.uR, comp.R * sy);
    gl.uniform1f(U.uHalo, tok.halo);
    gl.uniform1f(U.uBand, tok.band);
    gl.uniform1f(U.uCA, tok.ca);
    gl.uniform1f(U.uBeam, tok.beam * Math.PI / 180);
    gl.uniform1f(U.uBeamAmt, tok.beamAmt);
    gl.uniform1f(U.uLensE, tok.lens);
    gl.uniform3f(U.uTint, api.tint[0], api.tint[1], api.tint[2]);
    gl.uniform1f(U.uTintAmt, api.tintAmt);
    gl.uniform1f(U.uSwell, api.swell);
    gl.uniform1f(U.uLine, api.line);
    gl.uniform1f(U.uLinePh, api.linePh);
    gl.uniform2f(U.uEq, eqX, eqY);
    gl.uniform1f(U.uEnergy, api.energy);
    gl.uniform1f(U.uFog, tok.fog);
    gl.uniform1f(U.uRays, tok.rays);
    gl.uniform1f(U.uPlane, tok.plane);
    gl.uniform1f(U.uPlaneY, tok.planeY);
    gl.uniform1f(U.uFar, tok.far);
    gl.uniform1f(U.uStars, tok.stars);
    gl.uniform1f(U.uFlare, tok.flare);
    gl.uniform1f(U.uInner, tok.inner);
    gl.uniform1f(U.uFib, tok.fib);
    gl.uniform1f(U.uInvert, api.invert);
    gl.uniform1f(U.uSeed, seed);
    gl.uniform1i(U.uFarTex, 0);
    gl.uniform4f(U.uFarMap, PLATE.u0, PLATE.v0, PLATE.w * s * sx, PLATE.h * s * sy);
    gl.uniform2f(U.uFarAnchor, ax * sx, hgt - ay * sy);
    gl.uniform1f(U.uHasFar, hasFar);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
  const ease = (cur, target, dt, tau) => cur + (target - cur) * Math.min(1, dt / tau);
  const smooth = (x) => x * x * (3 - 2 * x);
  api._frame = (now) => {
    api._raf = 0;
    const dt = Math.min(0.05, api._last ? (now - api._last) / 1000 : 0.016);
    api._last = now;
    if (!reduced) api._t += dt * (1 + 0.6 * api.energy);
    api._ptS[0] = ease(api._ptS[0], api._pt[0], dt, 0.35);
    api._ptS[1] = ease(api._ptS[1], api._pt[1], dt, 0.35);
    api._mz = ease(api._mz, api._ptSeen ? 1 : 0, dt, 0.6);
    for (let i = 0; i < 3; i++) api.tint[i] = reduced ? api.tintT[i] : ease(api.tint[i], api.tintT[i], dt, 0.5);
    api.tintAmt = reduced ? api.tintAmtT : ease(api.tintAmt, api.tintAmtT, dt, 0.5);
    api.energy = reduced ? api.energyT : ease(api.energy, api.energyT, dt, 1.2);
    // импульсы: огибающие без рывков (вход 0,18 с, спад τ 0,9 с)
    if (api.pulseAt >= 0) {
      const k = api._t - api.pulseAt;
      const lineDur = 0.45;
      if (api.pulseKind === 1) {
        api.linePh = smooth(Math.min(1, k / lineDur));
        api.line = k < lineDur ? Math.sin(Math.PI * Math.min(1, k / lineDur)) ** 0.7 : 0;
      } else { api.line = 0; }
      const k0 = api.pulseKind === 1 ? k - lineDur * 0.85 : k;
      api.swell = k0 < 0 ? 0 : k0 < 0.18 ? smooth(k0 / 0.18) : Math.exp(-(k0 - 0.18) / 0.9);
      if (k > 4.5) { api.pulseAt = -1; api.swell = 0; api.line = 0; }
    }
    draw();
    const settled = reduced && Math.abs(api._ptS[0] - api._pt[0]) < 0.002 && Math.abs(api._ptS[1] - api._pt[1]) < 0.002;
    if (!api._paused && !settled) api._raf = requestAnimationFrame(api._frame);
    else api._last = 0;
  };
  api.wake();
  if (new URLSearchParams(location.search).has('scene-debug')) window.__cosmos = api;   // только стенд: раскадровка движения
  return api;
}
