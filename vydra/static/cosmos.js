// выдра — сцена первого экрана: чёрная дыра на месте baseline, вид — одобренный пользователем гибрид 09:37,
// поверх — глубина планами и тихое движение (docs/design/DIRECTION.md §4, §5).
//
// Слои (сзади вперёд): «чистый план» (звёзды, туманность, световая плоскость-«океан» из scene/plate.webp; честно линзируется,
// «океан» — в перспективе и с волнами) → рассыпь тусклых звёзд → процедурные звёзды → пара заметных звёзд (обе — под звёзды
// плана, выдаёт их только параллакс) → средняя пыль → туман (спиральный вихрь, подсвечен кольцом,
// лучи и объёмные тени) → туман на горизонте → ореол (плато + многоступенчатый спад, нити, тонкие концентрические кольца,
// латеральная хроматическая аберрация) → плоскость-горизонт (пятно света, отражение кольца) → анаморфный блик → тон-маппинг →
// ядро-сфера (в экранных значениях) → фотонное кольцо → ближний план (боке, клочья тумана) → дизеринг.
// Параметры — CSS-токены --bh-* на .cosmos-wrap (читаются при resize и по readTokens()). Токены глубины (--bh-par, --bh-sea-persp,
// --bh-waves, --bh-mist, --bh-near, --bh-dust) и звёзд (--bh-specks, --bh-pair) в 0 возвращают эталон 09:37 попиксельно (ворота §5.6).
//
// API: createCosmos(canvas, { reduced, seed, resScale }) →
//   { ok, setAccent(hex, amt), setPointer(x, y), setScroll(v), setEnergy(v), pulse('go' | 'done'), pause(), resume(), resize(),
//     readTokens(), destroy() } (+ setInvert для светлой темы — сейчас скрыта).

const VERT = `attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }`;

const FRAG = `
precision highp float;
uniform vec2  uRes;
uniform float uH;
uniform float uTime;
uniform vec2  uPar;        // указатель −1..1, сглажен τ 0,6 с (0 при reduced)
uniform float uParAmt;     // размах параллакса при полном отклонении, доли H (1 % × --bh-par)
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
uniform float uSwell;      // вдох кольца (импульс) 0..1
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
uniform float uPersp;      // перспектива «океана» 0..1
uniform float uWaves;      // волны по «океану» 0..1
uniform float uMist;       // туман на горизонте и воздушная перспектива 0..1
uniform float uNear;       // ближний план: боке и клочья тумана 0..1
uniform float uDust;       // средняя пыль 0..1
uniform vec4  uSpecks;     // рассыпь тусклых звёзд: x — яркость, y — плотность (доля ячеек), z — оттенок 0..1, w — параллакс (доля размаха)
uniform vec4  uPairA[3];   // пара заметных звёзд: xy — место от центра дыры (доли R, вверх +), z — параллакс (доля размаха), w — яркость
uniform float uPairS[3];   // их размер: σ ядра, доли H
uniform float uInvert;
uniform float uSeed;
uniform sampler2D uFarTex;
uniform vec4  uFarMap;     // xy — точка привязки в текстуре (u вправо, v вниз), zw — размер текстуры, px холста
uniform vec2  uFarAnchor;  // куда на холсте попадает точка привязки (GL)
uniform float uHasFar;     // 0..1 — план проявляется за 0,5 с после загрузки (без скачка)
uniform float uExpo;       // появление сцены 0..1 (всё, кроме фотонного кольца)
uniform float uExpoRing;   // появление фотонного кольца (раньше остального)

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

// рассыпь тусклых звёзд: как stars(), но медленнее мерцает, а у части звёзд — розовый или голубой оттенок на грани различимости
vec3 specks(vec2 q, float cell, float density, float sigmaPx, float upp, float bright, float hue){
  vec2 g = floor(q / cell);
  vec3 acc = vec3(0.0);
  for (int j = -1; j <= 1; j++)
  for (int i = -1; i <= 1; i++){
    vec2 id = g + vec2(i, j);
    vec3 h = hash33(vec3(id, 23.0));
    if (h.x > density) continue;
    vec2 c = (id + 0.12 + 0.76 * h.yz) * cell;
    vec3 h2 = hash33(vec3(id + 37.0, 29.0));
    float b = pow(h2.x, 2.6) * bright;
    float tw = 1.0 + 0.10 * sin(uTime * (0.15 + 0.5 * h2.y) + TAU * h2.z);
    float d = length(q - c) / upp;
    float s = sigmaPx * (0.85 + 0.3 * h2.z);
    float lum = b * tw * (exp(-d * d / (2.0 * s * s)) + 0.04 * exp(-d / (s * 1.8)));
    vec3 tint = h2.y < 0.6 ? vec3(1.0) : (h2.y < 0.8 ? vec3(1.0, 0.86, 0.93) : vec3(0.86, 0.92, 1.0));   // 60 % белых, 20 % розоватых, 20 % голубоватых
    acc += lum * mix(vec3(1.0), tint, hue);
  }
  return acc;
}

// ближний план: редкие крупные расфокусированные частицы (диск с чуть более светлой кромкой), q — доли H
float bokeh(vec2 q){
  const float cell = 0.24;
  vec2 g = floor(q / cell);
  float acc = 0.0;
  for (int j = -1; j <= 1; j++)
  for (int i = -1; i <= 1; i++){
    vec2 id = g + vec2(i, j);
    vec3 h = hash33(vec3(id, 41.0));
    if (h.x > 0.40) continue;
    vec3 h2 = hash33(vec3(id + 13.0, 7.0));
    vec2 cc = (id + 0.18 + 0.64 * h.yz) * cell + 0.010 * vec2(sin(uTime * 0.11 + h2.y * 6.3), cos(uTime * 0.083 + h2.z * 6.3));
    float r = mix(0.020, 0.050, h2.x);
    float d = length(q - cc) / r;
    float disc = 1.0 - smoothstep(0.70, 1.0, d);
    float rim = exp(-pow((d - 0.86) / 0.10, 2.0));
    acc += (disc * 0.62 + rim * 0.38) * mix(0.45, 1.0, h2.y);
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

  // ---------- параллакс от мыши по планам (доли H); дыра неподвижна — это ориентир ----------
  vec2 par = uPar * uParAmt;

  // ---------- линзирование ----------
  float E = uLensE;
  float lensF = 1.0 - (E * E) / (b * b + 0.02);                    // дальний план и туман
  float Es = min(E * 0.75, 0.45);
  float lensS = 1.0 - (Es * Es) / (b * b + 0.02);                 // звёзды — слабее (без «дождя»)
  vec2 qs  = p * lensF * kRH;                                      // доли H
  vec2 qss = p * lensS * kRH;

  float be = 1.0 + uBeamAmt * cos(ang - uBeam);                    // яркая сторона
  float breath = 1.0 + 0.03 * sin(t * TAU / 7.0) + 0.05 * uEnergy + 0.10 * uSwell;
  float yPl = -uPlaneY;                                            // линия плоскости, доли R (в координатах p)
  float xpool = exp(-p.x * p.x / (2.0 * 1.7 * 1.7));

  // ---------- дальний план: «чистый план» пользователя; «океан» — в перспективе, с волнами ----------
  vec3 col = vec3(0.0);
  float far = 0.0;
  if (uHasFar > 0.001) {
    vec2 ql = p * max(lensF, -0.5);                                 // направление до линзы, доли R
    vec2 sh = par * 0.15;                                           // небо и горизонт почти стоят
    float sig = yPl - ql.y;                                         // глубина под линией горизонта, доли R (> 0 — вода)
    float waveHi = 0.0;
    if (sig > 0.0) {
      float xg = sig * 0.5;                                         // 0 у горизонта … ≈1 у нижнего края окна
      float a = 0.40 * uPersp;
      float sig2 = 2.0 * xg * (1.0 + a) / (1.0 + a * xg);          // у горизонта штрихи плотнее и мельче, у края — крупнее
      float hx = mix(1.0, 1.0 - 0.06 * min(uPersp, 1.2), clamp(xg, 0.0, 1.0));  // у края штрихи чуть длиннее (сильнее — светлеет угол с показаниями)
      if (uWaves > 0.0) {
        // волны в «глубине» воды z ~ 1/σ: к горизонту мельче, плотнее и медленнее. Зыбь (14 с) + две ряби (11 и 17 с)
        float zi = 1.0 / (sig + 0.08);
        float w0 = 12.0 * zi + 0.9 * ql.x * zi - t * TAU / 14.0;
        float w1 = 45.0 * zi + 2.6 * ql.x * zi - t * TAU / 11.0;
        float w2 = 29.0 * zi - 4.1 * ql.x * zi - t * TAU / 17.0 + 1.7;
        float amp = uWaves * min(sig, 2.4) * smoothstep(0.2, 0.9, sig);
        float rip = min(uWaves, 1.0) / uWaves;                      // рябь не сильнее эталона: выше выборка складывается (швы у гребней)
        sig2 += amp * (0.028 * sin(w0) + rip * (0.009 * sin(w1) + 0.004 * sin(w2)));
        waveHi = uWaves * smoothstep(0.3, 1.4, sig) * (0.10 * cos(w0) + 0.06 * cos(w1) + 0.03 * cos(w2));
      }
      ql = vec2(ql.x * hx, yPl - sig2);
      sh.x += par.x * 1.15 * min(xg, 1.2);                          // ближняя вода смещается сильнее всех планов фона
    }
    vec2 Pl = c + ql * uR + sh * H;
    far = plate(Pl);
    if (sig > 0.0 && uMist > 0.0) {                                 // воздушная перспектива: у горизонта штрихи мягче
      float soft = uMist * 0.022 * smoothstep(0.0, 0.08, sig) * (1.0 - smoothstep(0.15, 0.55, sig)) * uR;
      if (soft > 0.25) far = far * 0.5 + (plate(Pl + vec2(0.0, soft)) + plate(Pl - vec2(0.0, soft))) * 0.25;
    }
    far *= 1.0 + waveHi;
  }
  float ylF = p.y + uPlaneY;                                        // над линией плоскости > 0
  float L = far * uFar * uHasFar * mix(0.48, 1.0, smoothstep(-0.35, 0.05, ylF));

  // ---------- звёзды: дальние мелкие почти стоят, крупные — ближе ----------
  float upp = 1.0 / H;
  float uppq = upp * clamp(abs(lensS), 0.25, 3.0);
  float S = 0.0;
  S += stars(qss + par * 0.15 + uSeed, 0.026, 0.16, 0.75, uppq, 0.05, 1.0);
  S += stars(qss + par * 0.40 + uSeed, 0.070, 0.20, 0.95, uppq, 0.20, 2.0);
  S += stars(qss + par * 0.70 + uSeed, 0.200, 0.22, 1.30, uppq, 0.80, 3.0);
  L += S * uStars * clamp(1.0 / max(abs(lensS), 0.3), 0.7, 1.8);

  // рассыпь тусклых звёзд — дальше всех (параллакс слабее неба), под линией горизонта гаснет, в колонке текста её нет
  vec3 SP = vec3(0.0);
  if (uSpecks.x > 0.0) {
    float spM = smoothstep(0.30, 0.55, fc.x / uRes.x) * smoothstep(-0.05, 0.30, ylF);
    if (spM > 0.0) SP = specks(qss + par * uSpecks.w + uSeed * 1.7, 0.034, uSpecks.y, max(0.0015 * H, 0.65), uppq, 0.26 * uSpecks.x, uSpecks.z) * spM;
  }

  // пара заметных звёзд — как звёзды плана: мягкое ядро и короткое свечение, без лучей; у каждой своя глубина
  for (int k = 0; k < 3; k++) {
    vec4 a = uPairA[k];
    if (a.w <= 0.0) continue;
    vec2 d = P - a.xy * uR + par * a.z * H;
    float s = max(uPairS[k] * H, 0.75);
    float r2 = dot(d, d);
    float tw = 1.0 + 0.05 * sin(t * (0.21 + 0.13 * float(k)) + 1.7 * float(k));
    L += a.w * tw * (exp(-r2 / (2.0 * s * s)) + 0.06 * exp(-sqrt(r2) / (s * 2.5)));
  }

  // ---------- средний план: пыль — медленно дрейфует ----------
  if (uDust > 0.0) {
    vec2 dq = P / H + par * 0.8 + vec2(0.0022, -0.0009) * t + 3.7;       // дрейф влево-вверх, как у боке и тумана на горизонте
    L += stars(dq, 0.055, 0.10, 1.05, upp, 0.16, 11.0) * uDust * smoothstep(0.30, 0.55, fc.x / uRes.x);
  }

  // ---------- туман: спиральный вихрь вокруг дыры, два слоя ----------
  float rq = length(qs) / kRH;                                     // линзированный радиус, доли R
  float twist = 0.85 * log(max(rq, 0.6)) - t * 0.02 * (1.0 + uEnergy);
  vec2 fq = rot(twist) * qs;
  float n1 = fbm(fq * 2.3 + par * 1.6 + uSeed * 3.0, 4);
  float k1 = fbm(fq * 0.75 + 4.2, 3);
  float dens1 = smoothstep(0.36, 0.92, n1) * smoothstep(0.20, 0.72, k1);
  vec2 f2 = rot(-0.35) * (qs * 5.2 + par * 4.7) - vec2(0.010, 0.004) * t * (1.0 + 1.5 * uEnergy) + 11.0;
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
      acc += smoothstep(0.36, 0.92, fbm(fk2 * 2.3 + par * 1.6 + uSeed * 3.0, 2));
    }
    T = exp(-acc / 6.0 * min(e, 1.6) * 2.4 * uRays);
    T = mix(T, 1.0, smoothstep(2.6, 4.0, e));                     // без шва на границе марша
  }
  float lit = Iring * shafts * T;
  // пятно плоскости тоже подсвечивает туман снизу
  float fromPlane = xpool * exp(-abs(p.y - yPl) / 0.9) * mix(0.35, 1.0, smoothstep(yPl - 0.3, yPl + 0.1, p.y)) * 0.45;
  float fogL = dens * (0.028 + 1.05 * lit + fromPlane) * uFog * breath;
  vec3 ice = vec3(0.90, 0.95, 1.0);
  float tintK = clamp(uTintAmt * (0.35 + 0.65 * min(lit * 1.5, 1.0)), 0.0, 0.85);
  vec3 fogCol = fogL * mix(ice, uTint * 1.25, tintK);

  // ---------- средний план: туман на горизонте — между дырой и «океаном», стелется и дрейфует ----------
  float mistL = 0.0, mistA = 0.0;
  if (uMist > 0.0) {
    float yh = p.y - yPl;                                          // + над линией, доли R
    float bandM = exp(-yh * yh / (2.0 * 0.20 * 0.20));
    vec2 mq = vec2(p.x * 0.42 + t * 0.022, yh * 2.2) + par / kRH * vec2(0.42 * 0.6, 2.2 * 0.6) + uSeed * 7.0;
    float mn = fbm(mq, 3);
    float wideM = exp(-p.x * p.x / (2.0 * 2.4 * 2.4));
    float mden = bandM * wideM * (0.35 + 0.65 * smoothstep(0.30, 0.75, mn));
    mistL = mden * uMist * 0.045 * (0.5 + xpool) * breath;          // подсвечен пятном плоскости
    mistA = mden * uMist * 0.22;                                    // и чуть гасит низ ореола: дыра — за дымкой
  }

  // ---------- ореол: раскалённо-белый пояс, нити, многоступенчатый спад ----------
  // пояс неровный по углу: внешний край «дышит» прядями, а не ровный бублик
  float nb = fbm(dir * 1.8 + vec2(t * 0.006, uSeed * 3.0), 3);
  float band = uBand * (0.72 + 0.62 * nb);
  float w = (0.045 + 0.05 * uEnergy) / pow(max(b, 1.0), 1.5);      // у кольца: 2,6°/с, при загрузке 5,5°/с
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
  float absorb = (1.0 - 0.42 * smoke) * (1.0 - mistA);
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
  vec3 kayma = spec * bandCA * caMask * uCA * 0.8 * be * (1.0 + 0.35 * uSwell);

  // частицы на орбитах
  float om = (0.035 + 0.04 * uEnergy) / pow(max(b, 1.0), 1.5);
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
  col = vec3(L) + SP + fogCol + halo + kayma + vec3(pr) + vec3(planeL + mistL) * ice + vec3(flare) * vec3(0.92, 0.96, 1.0);
  // широкая диффузия: всё рядом с кольцом чуть приподнято (свет рассеивается в воздухе)
  col += vec3(0.020 / (1.0 + (e / 0.8) * (e / 0.8))) * be * breath * ice;

  vec2 vc = (fc / uRes - 0.5) * vec2(uRes.x / uRes.y, 1.0);
  col *= 1.0 - 0.22 * dot(vc, vc);
  col = 1.0 - exp(-col * 1.25);
  col = pow(col, vec3(0.94));
  col *= uExpo;                                                    // появление: сначала кольцо, потом всё остальное

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
  lum *= uInner * (1.0 + 0.04 * uSwell) * uExpo;
  float planeOver = (lineG * 0.85 + above * 0.30) * uPlane;         // плоскость перекрывает низ ядра
  vec3 disk = vec3(lum) * vec3(0.955, 0.975, 1.02) + vec3(1.0 - exp(-planeOver * 1.25)) * ice * 0.9 * uExpo;
  float aa = 1.3 / uR;
  float m = 1.0 - smoothstep(1.0 - aa, 1.0 + aa * 0.25, b);
  col = mix(col, disk, m);

  // фотонное кольцо — тонкая раскалённая линия поверх стыка (с той же аберрацией)
  float pw = max(1.0 / uR, 0.007);
  float ringR = exp(-pow((length(p - dCA * 0.6) - 1.0) / pw, 2.0));
  float ringG = exp(-pow((b - 1.0) / pw, 2.0));
  float ringB = exp(-pow((length(p + dCA * 0.6) - 1.0) / pw, 2.0));
  vec3 ringC = vec3(ringR, ringG, ringB) * (1.05 + 0.20 * be) * (1.0 + 0.12 * uSwell) * uExpoRing;
  col = 1.0 - (1.0 - col) * (1.0 - clamp(ringC, 0.0, 1.0));

  // ---------- ближний план: расфокусированные частицы и клочья тумана — поверх всего, сильнее всех в параллаксе ----------
  if (uNear > 0.0) {
    float mx = smoothstep(0.50, 0.66, fc.x / uRes.x) * smoothstep(0.07, 0.15, fc.y / H);   // не в колонке текста и не под показаниями
    vec2 nq = P / H + par * 1.6;
    float bk = bokeh(nq + vec2(0.0035, -0.0021) * t);
    vec2 wq = vec2(nq.x * 1.3 + t * 0.012, nq.y * 4.2) + uSeed * 5.0;
    float wn = fbm(wq * 1.7, 3);
    float wisps = smoothstep(0.55, 0.92, wn) * smoothstep(0.45, 0.08, fc.y / H);
    float light = 0.45 + 0.55 * clamp(Iring, 0.0, 1.0) + 0.60 * xpool * smoothstep(0.5, -0.8, yl);   // пыль освещена сценой
    float nearL = (bk * 0.140 + wisps * 0.060) * uNear * mx * light * uExpo;
    col = 1.0 - (1.0 - col) * (1.0 - vec3(nearL) * ice);
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

// «чистый план» пользователя (серый webp): точка привязки — середина светлого пятна на линии плоскости.
const PLATE = { url: '/static/scene/plate.webp', w: 1671, h: 941, u0: 0.69, v0: 0.797 };

// значения по умолчанию, если CSS не задал токены (= эталон 09:37 + глубина S1d)
const DEF = {
  anchor: 'baseline', x: 0.70, y: 0.54, r: 0.13,
  halo: 1.0, band: 0.22, ca: 1.0, beam: 160, beamAmt: 0.32, lens: 0.8,
  fog: 1.0, rays: 1.0, plane: 1.0, planeY: 1.45, far: 0.85, stars: 0.4, flare: 1.0, inner: 1.0, fib: 0.85,
  par: 1, persp: 1, waves: 1, mist: 1, near: 1, dust: 1,
  specks: 1, specksN: 0.24, specksHue: 1, specksPar: 0.10, pair: 1, pairPar: 1,
};
// пара заметных звёзд (их три, но третья — едва заметная): место от центра дыры в долях R (x вправо, y вверх) — правее колонки
// текста и выше «океана»; глубина — доля размаха параллакса (дыра 0, небо плана 0,15, «рассыпь» --bh-specks-par); яркость — в
// линейном свете сцены (самая яркая звезда плана ≈ 0,5); размер — σ ядра в долях H (звезда плана — σ ≈ 0,0015–0,002 H; меньше 0,75 px холста не бывает — иначе мерцает при параллаксе).
// Сила всей пары — --bh-pair, размах глубин — --bh-pair-par.
const PAIR = [
  { x: 2.30, y: 1.90, depth: 0.90, amp: 0.55, size: 0.0016 },
  { x: -1.55, y: 2.95, depth: 0.55, amp: 0.42, size: 0.0015 },
  { x: 3.30, y: -0.35, depth: 0.30, amp: 0.32, size: 0.0014 },
];
const OPEN = 1.8;          // появление сцены, с: кольцо — за первые 40 %, всё остальное — плавно к концу (§4.2)
const POSTER_T = 14.0;     // «время постера»: нити уже закручены, кадр без движения выглядит собранным (§4.5)
const PACE = 1.15;         // темп сцены: волны, нити, кольцо — на 15 % живее эталона (просьба пользователя); кадр постера не меняется
const SLOW_FRAME = 0.020;  // кадр дольше 20 ms три раза подряд — без ближнего плана и пыли (§5.4)

// положение и размер дыры «как в baseline» (решение пользователя, §5.1), CSS px холста
export function baseLayout(vw, vh) {
  const heroH = Math.max(674, 0.94 * vh);
  const k = Math.min(0.14, Math.max(0.09, 0.115 * vw / 1440));
  return { cx: 0.70 * vw, cy: 68 + heroH / 2, R: k * heroH };
}

export function createCosmos(canvas, opts = {}) {
  const reduced = !!opts.reduced;
  let gl = null;
  try { gl = canvas.getContext('webgl', { antialias: false, alpha: false, depth: false, stencil: false, powerPreference: 'low-power' }); } catch { gl = null; }
  const clock = () => performance.now() / 1000;
  const api = {
    ok: false, invert: 0,
    tint: [1, 1, 1], tintT: [1, 1, 1], tintAmt: 0, tintAmtT: 0,
    energy: 0, energyT: 0,
    swell: 0, _attack: 0, _peak: 1, lastPulse: -10,
    /** Оттенок тумана и каймы по цвету источника; null — нейтральный белый. */
    setAccent(hex, amt = 1) {
      if (hex) api.tintT = hexToRgb(hex);
      api.tintAmtT = hex ? 0.55 * amt : 0;
      api.wake();
    },
    setInvert(v) { api.invert = v ? 1 : 0; api.wake(); },
    /** Указатель −1..1 → только параллакс планов (линзы у курсора нет); при reduced не двигает ничего. */
    setPointer(x, y) { if (reduced) return; api._pt = [x, y]; api.wake(); },
    setScroll(v) { if (v === api._scroll) return; api._scroll = v; api.wake(); },
    setEnergy(v) { const e = Math.max(0, Math.min(1, v)); if (e === api.energyT) return; api.energyT = e; api.wake(); },
    /** Мягкий вдох кольца: 'go' — старт загрузки, 'done' — готово (слабее). Не чаще 3 раз в секунду;
        повтор во время вдоха продлевает его, а не начинает заново — яркость не скачет. */
    pulse(kind = 'go') {
      if (reduced) return;
      const now = clock();
      if (now - api.lastPulse < 0.34) return;
      api.lastPulse = now;
      api._attack = kind === 'go' ? 0.45 : 0.35;
      api._peak = kind === 'go' ? 1 : 0.7;
      api.wake();
    },
    pause() { api._paused = true; },
    resume() { if (!api._paused) return; api._paused = false; api._last = 0; api.wake(); },
    wake() { if (!api._raf && api.ok && !api._paused) api._raf = requestAnimationFrame(api._frame); },
    // один кадр и на паузе: холст сменил размер (буфер очищен), пришла текстура, сменились токены
    _redraw() { if (!api._raf && api.ok) api._raf = requestAnimationFrame(api._frame); },
    resize() {},
    readTokens() { return false; },
    destroy() { cancelAnimationFrame(api._raf); api._raf = 0; api.ok = false; },
    _pt: [0, 0], _ptS: [0, 0], _scroll: 0, _paused: false, _raf: 0, _last: 0,
    _t: POSTER_T, _age: 0, _expo: reduced ? 1 : 0, _expoRing: reduced ? 1 : 0, _far: 0,
    _odd: false, _slow: 0, _lite: false,
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
  } catch { return api; }   // без WebGL-шейдера — CSS-запасной вид (.cosmos-wrap.fallback)
  gl.useProgram(prog);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, 'p');
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const U = {};
  for (const n of ['uRes', 'uH', 'uTime', 'uPar', 'uParAmt', 'uScroll', 'uC', 'uR', 'uHalo', 'uBand', 'uCA', 'uBeam', 'uBeamAmt', 'uLensE',
    'uTint', 'uTintAmt', 'uSwell', 'uEnergy', 'uFog', 'uRays', 'uPlane', 'uPlaneY', 'uFar', 'uStars', 'uFlare',
    'uInner', 'uFib', 'uPersp', 'uWaves', 'uMist', 'uNear', 'uDust', 'uSpecks', 'uPairA', 'uPairS', 'uInvert', 'uSeed', 'uFarTex', 'uFarMap', 'uFarAnchor', 'uHasFar',
    'uExpo', 'uExpoRing']) U[n] = gl.getUniformLocation(prog, n);
  const seed = opts.seed ?? 0.37;
  api.ok = true;

  // текстура дальнего плана: грузится асинхронно (index.html её предзагружает), до загрузки сцена рисуется без неё,
  // план проявляется за 0,5 с — без скачка
  let hasFar = 0;
  const tex = gl.createTexture();
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, 1, 1, 0, gl.LUMINANCE, gl.UNSIGNED_BYTE, new Uint8Array([0]));
  {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => {
      if (!api.ok) return;
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, gl.LUMINANCE, gl.UNSIGNED_BYTE, img);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      hasFar = 1; api._redraw();
    };
    img.src = PLATE.url;
  }

  // токены композиции и вида из CSS (.cosmos-wrap)
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
      par: num(cs, '--bh-par', DEF.par), persp: num(cs, '--bh-sea-persp', DEF.persp), waves: num(cs, '--bh-waves', DEF.waves),
      mist: num(cs, '--bh-mist', DEF.mist), near: num(cs, '--bh-near', DEF.near), dust: num(cs, '--bh-dust', DEF.dust),
      specks: num(cs, '--bh-specks', DEF.specks), specksN: num(cs, '--bh-specks-n', DEF.specksN),
      specksHue: num(cs, '--bh-specks-hue', DEF.specksHue), specksPar: num(cs, '--bh-specks-par', DEF.specksPar),
      pair: num(cs, '--bh-pair', DEF.pair), pairPar: num(cs, '--bh-pair-par', DEF.pairPar),
    };
    const key = JSON.stringify(n);
    if (key === tokKey) return false;
    tokKey = key; tok = n; api._redraw();
    return true;
  };

  // холст: 0,5 от CSS px (devicePixelRatio ограничен 1), не больше 1,1 Мпикс (§5.4); размер — только по resize()
  let w = 0, hgt = 0, cssW = 1, cssH = 1;
  const RES_SCALE = opts.resScale || 0.5;
  function measure() {
    const r = canvas.getBoundingClientRect();
    cssW = Math.max(1, r.width); cssH = Math.max(1, r.height);
    const dpr = Math.min(1, devicePixelRatio || 1);
    let nw = Math.max(2, Math.round(r.width * dpr * RES_SCALE)), nh = Math.max(2, Math.round(r.height * dpr * RES_SCALE));
    const cap = 1.1e6;
    if (nw * nh > cap) { const k = Math.sqrt(cap / (nw * nh)); nw = Math.round(nw * k); nh = Math.round(nh * k); }
    if (nw === w && nh === hgt) return false;
    w = nw; hgt = nh; canvas.width = w; canvas.height = hgt; gl.viewport(0, 0, w, hgt);
    return true;
  }
  // смена размера очищает буфер: кадр рисуется сразу, в том же кадре браузера — иначе, если кадр сцены в этом кадре
  // уже прошёл (появилась полоса прокрутки под превью), на экран уходит пустой, чёрный холст
  api.resize = () => { const cleared = measure(); api.readTokens(); if (cleared) draw(); api._redraw(); };

  const draw = () => {
    const sx = w / cssW, sy = hgt / cssH;
    // композиция в CSS px холста (холст = окно, 100svh)
    const comp = tok.anchor === 'baseline' ? baseLayout(cssW, cssH) : { cx: tok.x * cssW, cy: tok.y * cssH, R: tok.r * cssH };
    // «чистый план»: пятно плоскости — под дырой, линия плоскости — на planeY·R ниже центра; масштаб — чтобы закрыть холст
    const ax = comp.cx, ay = comp.cy + tok.planeY * comp.R;
    const s = Math.max(ax / (PLATE.u0 * PLATE.w), (cssW - ax) / ((1 - PLATE.u0) * PLATE.w), ay / (PLATE.v0 * PLATE.h),
      (cssH * 0.92 - ay) / ((1 - PLATE.v0) * PLATE.h));
    gl.uniform2f(U.uRes, w, hgt);
    gl.uniform1f(U.uH, hgt);
    gl.uniform1f(U.uTime, api._t);
    gl.uniform2f(U.uPar, reduced ? 0 : api._ptS[0], reduced ? 0 : api._ptS[1]);
    gl.uniform1f(U.uParAmt, 0.01 * tok.par);
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
    gl.uniform1f(U.uPersp, tok.persp);
    gl.uniform1f(U.uWaves, tok.waves);
    gl.uniform1f(U.uMist, tok.mist);
    gl.uniform1f(U.uNear, api._lite ? 0 : tok.near);
    gl.uniform1f(U.uDust, api._lite ? 0 : tok.dust);
    gl.uniform4f(U.uSpecks, api._lite ? 0 : tok.specks, tok.specksN, tok.specksHue, tok.specksPar);
    gl.uniform4fv(U.uPairA, PAIR.flatMap((s) => [s.x, s.y, s.depth * tok.pairPar, s.amp * tok.pair]));
    gl.uniform1fv(U.uPairS, PAIR.map((s) => s.size));
    gl.uniform1f(U.uInvert, api.invert);
    gl.uniform1f(U.uSeed, seed);
    gl.uniform1i(U.uFarTex, 0);
    gl.uniform4f(U.uFarMap, PLATE.u0, PLATE.v0, PLATE.w * s * sx, PLATE.h * s * sy);
    gl.uniform2f(U.uFarAnchor, ax * sx, hgt - ay * sy);
    gl.uniform1f(U.uHasFar, api._far);
    gl.uniform1f(U.uExpo, api._expo);
    gl.uniform1f(U.uExpoRing, api._expoRing);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
  const ease = (cur, target, dt, tau) => cur + (target - cur) * Math.min(1, dt / tau);
  const smooth = (x) => x * x * (3 - 2 * x);
  const near = (a, b, eps) => Math.abs(a - b) < eps;
  api._frame = (now) => {
    api._raf = 0;
    const raw = api._last ? (now - api._last) / 1000 : 0.016;
    const dt = Math.min(0.05, raw);
    api._last = now;
    if (!reduced) {
      api._t += dt * PACE * (1 + 0.3 * api.energy);                  // при загрузке время сцены ×1,3
      // слабая машина: три долгих кадра подряд — сцена остаётся эталонной, без ближнего плана и пыли
      api._slow = raw > SLOW_FRAME && raw < 0.1 ? api._slow + 1 : 0;           // разовый долгий кадр (>100 ms) — не видеокарта
      if (api._slow >= 3) api._lite = true;
    }
    // появление: кольцо за первые 40 % (0,72 с), всё остальное — плавная S-кривая к 1,8 с; план — за 0,5 с после загрузки
    // на паузе (экраны данных) кадр рисуется только по событию — сразу в установившемся виде
    const still = reduced || api._paused;
    api._age = still ? Math.max(api._age + dt, OPEN) : api._age + dt;
    const x = Math.min(1, api._age / OPEN);
    api._expo = still ? 1 : smooth(x);
    api._expoRing = still ? 1 : smooth(Math.min(1, x / 0.4));
    api._far = still ? hasFar : Math.min(hasFar, api._far + dt / 0.5);
    // параллакс: указатель сглажен τ 0,6 с — сцена «догоняет» мышь лениво, без рывков
    if (!reduced) { api._ptS[0] = ease(api._ptS[0], api._pt[0], dt, 0.6); api._ptS[1] = ease(api._ptS[1], api._pt[1], dt, 0.6); }
    for (let i = 0; i < 3; i++) api.tint[i] = reduced ? api.tintT[i] : ease(api.tint[i], api.tintT[i], dt, 0.5);
    api.tintAmt = reduced ? api.tintAmtT : ease(api.tintAmt, api.tintAmtT, dt, 0.5);
    api.energy = reduced ? api.energyT : ease(api.energy, api.energyT, dt, 1.2);
    // вдох кольца: пока идёт атака — к пику τ 0,2 с, потом выдох τ 0,7 с (виден ≈ 1,5 с, пик ореола +9 %)
    if (api._attack > 0) { api._attack -= dt; api.swell = ease(api.swell, api._peak, dt, 0.2); }
    else api.swell = api.swell < 0.002 ? 0 : ease(api.swell, 0, dt, 0.7);
    // 60 fps — пока идёт появление, вдох, параллакс не осел, оттенок или энергия меняются; в покое — 30 fps
    const busy = api._expo < 1 || api._far < hasFar || api.swell > 0 || api._attack > 0
      || !near(api._ptS[0], api._pt[0], 0.001) || !near(api._ptS[1], api._pt[1], 0.001)
      || !near(api.tintAmt, api.tintAmtT, 0.002) || !near(api.energy, api.energyT, 0.002);
    api._odd = !api._odd;
    if (busy || api._odd || reduced || api._paused) draw();
    if (reduced) { api._last = 0; return; }                          // перерисовка — только по событию
    if (!api._paused) api._raf = requestAnimationFrame(api._frame);
    else api._last = 0;
  };
  measure();
  api.readTokens();
  api.wake();
  return api;
}
