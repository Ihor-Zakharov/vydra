// выдра — логика интерфейса. Без фреймворков: состояние → точечные обновления DOM,
// движение на пружинах (WAAPI + View Transitions), FLIP для списков.

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const html = document.documentElement;

/* ============================== движение ============================== */

// ?motion=0 — как «уменьшить движение» (для скриншотов и слабых машин)
const MOTION_OFF = new URLSearchParams(location.search).get('motion') === '0';
if (MOTION_OFF) html.classList.add('motion-off');
const REDUCED = MOTION_OFF || matchMedia('(prefers-reduced-motion: reduce)').matches;
const HAS_LINEAR = CSS.supports('transition-timing-function', 'linear(0, 1)');
const SPRING = HAS_LINEAR
  ? 'linear(0, 0.0213, 0.0775, 0.1581, 0.2541, 0.3579, 0.4635, 0.5662, 0.6624, 0.7498, 0.8268, 0.8928, 0.9476, 0.9917, 1.0258, 1.0508, 1.0679, 1.0782, 1.0828, 1.083, 1.0798, 1.0741, 1.0667, 1.0583, 1.0495, 1.0408, 1.0324, 1.0247, 1.0178, 1.0118, 1.0067, 1.0026, 0.9993, 0.9968, 0.995, 0.9939, 0.9932, 0.993, 0.9932, 0.9935, 0.9941, 0.9948, 0.9955, 0.9962, 0.9969, 0.9976, 0.9982, 0.9988, 1)'
  : 'cubic-bezier(.34, 1.4, .64, 1)';
const SPRING_SOFT = HAS_LINEAR
  ? 'linear(0, 0.0154, 0.0546, 0.109, 0.1723, 0.2399, 0.3086, 0.376, 0.4405, 0.5012, 0.5575, 0.6092, 0.6561, 0.6984, 0.7363, 0.7701, 0.8, 0.8265, 0.8497, 0.8701, 0.8879, 0.9035, 0.917, 0.9287, 0.9388, 0.9476, 0.9552, 0.9617, 0.9673, 0.9721, 0.9762, 0.9797, 0.9828, 0.9853, 0.9875, 0.9894, 0.991, 0.9924, 0.9936, 0.9945, 0.9954, 0.9961, 0.9967, 0.9972, 0.9976, 0.998, 0.9983, 0.9986, 1)'
  : 'cubic-bezier(.22, 1, .36, 1)';
const EASE_IN = 'cubic-bezier(.4, 0, 1, 1)';
const CAN_VT = typeof document.startViewTransition === 'function' && !REDUCED;

function animate(el, frames, opts) {
  if (!el || REDUCED || !el.animate) return { finished: Promise.resolve() };
  return el.animate(frames, opts);
}

/** View Transition с запасным вариантом; cls — класс на <html> на время перехода. */
function viewTransition(update, cls) {
  if (!CAN_VT) { update(); return { finished: Promise.resolve(), ready: Promise.resolve() }; }
  if (cls) html.classList.add(cls);
  const t = document.startViewTransition(update);
  t.finished.catch(() => {}).finally(() => cls && html.classList.remove(cls));
  t.ready.catch(() => {});
  return t;
}

const ENTER = [
  { opacity: 0, transform: 'translateY(14px) scale(.94)', filter: 'blur(8px)' },
  { opacity: 1, transform: 'none', filter: 'blur(0)' },
];

/** FLIP: снимаем позиции, меняем DOM, анимируем сдвиги. removed — уходящие элементы. */
function flip(container, mutate, removed = []) {
  if (REDUCED) { removed.forEach((el) => el.remove()); mutate(); return; }
  const first = new Map();
  for (const el of container.children) first.set(el, el.getBoundingClientRect());
  const box = container.getBoundingClientRect();
  for (const el of removed) {
    const r = first.get(el) || el.getBoundingClientRect();
    Object.assign(el.style, {
      position: 'absolute', left: `${r.left - box.left}px`, top: `${r.top - box.top}px`,
      width: `${r.width}px`, height: `${r.height}px`, margin: '0', pointerEvents: 'none', zIndex: '0',
    });
    container.append(el);
    el.animate([{ opacity: 1, transform: 'none', filter: 'blur(0)' }, { opacity: 0, transform: 'scale(.9)', filter: 'blur(6px)' }],
      { duration: 320, easing: EASE_IN, fill: 'forwards' }).finished.then(() => el.remove(), () => el.remove());
  }
  mutate();
  let i = 0;
  for (const el of container.children) {
    if (removed.includes(el)) continue;
    const f = first.get(el);
    const l = el.getBoundingClientRect();
    if (!f) {
      el.animate(ENTER, { duration: 650, easing: SPRING_SOFT, delay: Math.min(i++, 8) * 40, fill: 'backwards' });
      continue;
    }
    const dx = f.left - l.left, dy = f.top - l.top;
    const sx = l.width ? f.width / l.width : 1, sy = l.height ? f.height / l.height : 1;
    if (Math.abs(dx) + Math.abs(dy) > 1 || Math.abs(sx - 1) > .02 || Math.abs(sy - 1) > .02) {
      el.animate([{ transformOrigin: '0 0', transform: `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})` }, { transformOrigin: '0 0', transform: 'none' }],
        { duration: 620, easing: SPRING_SOFT });
    }
  }
}

/* появление при прокрутке */
const revealer = 'IntersectionObserver' in window
  ? new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add('in'); revealer.unobserve(e.target); }
  }, { rootMargin: '0px 0px -6% 0px' })
  : null;
function reveal(el, i = 0) {
  if (!revealer || REDUCED) return;
  el.classList.add('rv');
  el.style.setProperty('--i', i);
  revealer.observe(el);
  // Уже видимое проявляем сразу, не дожидаясь наблюдателя; и страховка — контент
  // не должен остаться невидимым, если IntersectionObserver промолчит (iframe, фоновая вкладка).
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (el.getBoundingClientRect().top < innerHeight) el.classList.add('in');
  }));
  setTimeout(() => el.classList.add('in'), 1600 + i * 70);
}

/* инерционная прокрутка перетаскиванием мышью */
function dragScroll(el) {
  let down = false, moved = false, lastX = 0, lastT = 0, v = 0, raf = 0;
  el.addEventListener('pointerdown', (e) => {
    if (e.pointerType !== 'mouse' || el.scrollWidth <= el.clientWidth) return;
    down = true; moved = false; lastX = e.clientX; lastT = performance.now(); v = 0; cancelAnimationFrame(raf);
  });
  window.addEventListener('pointermove', (e) => {
    if (!down) return;
    const dx = e.clientX - lastX, now = performance.now();
    if (Math.abs(dx) > 2) moved = true;
    el.scrollLeft -= dx; v = dx / Math.max(1, now - lastT); lastX = e.clientX; lastT = now;
  });
  window.addEventListener('pointerup', () => {
    if (!down) return; down = false;
    const step = () => { v *= 0.93; el.scrollLeft -= v * 16; if (Math.abs(v) > 0.02) raf = requestAnimationFrame(step); };
    if (!REDUCED) raf = requestAnimationFrame(step);
  });
  el.addEventListener('click', (e) => { if (moved) { e.stopPropagation(); e.preventDefault(); moved = false; } }, true);
}

/* ============================== форматирование ============================== */

const nf1 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
function fmtBytes(n) {
  if (n == null || isNaN(n)) return '';
  const u = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${(n >= 10 || i === 0 ? nf0 : nf1).format(n)} ${u[i]}`;
}
function fmtTime(sec) {
  if (sec == null || isNaN(sec)) return '';
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}
function parseTime(text) {
  const t = String(text ?? '').trim().replace(',', '.');
  if (!t) return null;
  if (/^\d+(\.\d+)?$/.test(t)) return Number(t);
  let m = t.match(/^(\d+):([0-5]?\d(?:\.\d+)?)$/);
  if (m) return Number(m[1]) * 60 + Number(m[2]);
  m = t.match(/^(\d+):([0-5]?\d):([0-5]?\d(?:\.\d+)?)$/);
  if (m) return Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]);
  return NaN;
}
function fmtAgo(unix) {
  if (!unix) return '';
  const d = new Date(unix * 1000), now = new Date(), diff = (now - d) / 1000;
  if (diff < 60) return 'только что';
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  const hm = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === now.toDateString()) return `сегодня, ${hm}`;
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return `вчера, ${hm}`;
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' });
}
const PLATFORM = {
  youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram', other: 'Другой сайт', file: 'Мой файл',
};
const glyph = (p) => `#p-${PLATFORM[p] ? p : 'other'}`;
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const CINEMA_URL = `/lib/${encodeURIComponent('Кинотеатр.html')}`;
const libUrl = (rel) => '/lib/' + String(rel).split('/').map(encodeURIComponent).join('/');
const svgUse = (id, cls = '') => `<svg${cls ? ` class="${cls}"` : ''}><use href="${id}"/></svg>`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function hash(str) {
  let h = 2166136261;
  for (const ch of String(str)) { h ^= ch.codePointAt(0); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function rng(seed) {
  return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
/** Обложка-заглушка: градиент и «волна», детерминированно от id. */
function artHtml(seed, kind = 'MP3') {
  const h = hash(seed), h1 = h % 360, h2 = (h1 + 40 + (h >> 8) % 90) % 360;
  const r = rng(h);
  let bars = '';
  const n = 28;
  for (let i = 0; i < n; i++) {
    const env = Math.sin((i / (n - 1)) * Math.PI) * 0.75 + 0.25;
    const bh = Math.max(8, Math.round((0.25 + r() * 0.75) * env * 100));
    bars += `<rect x="${i * 10 + 2}" y="${(100 - bh) / 2}" width="6" height="${bh}" rx="3"/>`;
  }
  return `<div class="art" style="--h1:${h1};--h2:${h2}"><span class="art-kind">${esc(kind)}</span><svg class="wave" viewBox="0 0 ${n * 10} 100" preserveAspectRatio="none">${bars}</svg></div>`;
}

/* ============================== API ============================== */

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}
function detailText(d) {
  if (!d) return '';
  if (typeof d.detail === 'string') return d.detail;
  if (Array.isArray(d.detail)) return d.detail.map((x) => String(x.msg || '').replace(/^Value error,\s*/i, '')).filter(Boolean).join('; ');
  return '';
}
async function api(path, { method = 'GET', body, form, signal } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method, signal, cache: 'no-store',
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : form,
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    setServerDown(true);
    throw new ApiError('Сервер выдры не отвечает', 0);
  }
  if (state.down) setServerDown(false);
  const ct = res.headers.get('content-type') || '';
  const data = ct.includes('json') ? await res.json().catch(() => null) : null;
  if (!res.ok) throw new ApiError(detailText(data) || `Ошибка ${res.status}`, res.status);
  return data;
}

/* ============================== состояние ============================== */

const ACTIVE = new Set(['queued', 'downloading', 'converting', 'saving']);
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* приватный режим */ } },
};
const saved = store.get('vd.prefs', {});
const state = {
  tab: 'link',
  mode: ['mp4', 'mp3', 'both'].includes(saved.mode) ? saved.mode : 'mp4',
  quality: saved.quality || '1080',
  bitrate: String(saved.bitrate || '192'),
  autostart: !!saved.autostart,
  libtype: 'all',
  libplatform: 'all',
  libsearch: '',
  jobs: [],
  library: null,
  info: null,
  doctor: null,
  preview: null, // {kind: loading|ready|error|multi, url, data, message, n}
  range: null, // {a, b, d}
  heights: null,
  down: false,
  fresh: new Set(),
};
const savePrefs = () => store.set('vd.prefs', { mode: state.mode, quality: state.quality, bitrate: state.bitrate, autostart: state.autostart });

/* ============================== тосты ============================== */

function toast(message, type = 'info', { timeout = 3800 } = {}) {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.setAttribute('role', type === 'err' ? 'alert' : 'status');
  const icon = { ok: '#i-check', err: '#i-alert', info: '#i-info', warn: '#i-warn' }[type] || '#i-info';
  el.innerHTML = `<span class="ti">${svgUse(icon)}</span><span class="tt"></span>`;
  el.querySelector('.tt').textContent = message;
  box.append(el);
  while (box.children.length > 4) box.firstElementChild.remove();
  let timer;
  const api_ = {
    update(msg) { el.querySelector('.tt').textContent = msg; return api_; },
    close() { clearTimeout(timer); el.classList.add('out'); setTimeout(() => el.remove(), REDUCED ? 0 : 360); },
    type(t) { el.className = `toast ${t}`; return api_; },
    later(ms) { clearTimeout(timer); timer = setTimeout(api_.close, ms); return api_; },
  };
  if (timeout) api_.later(timeout);
  return api_;
}

/* ============================== сервер недоступен ============================== */

function setServerDown(down) {
  if (state.down === down) return;
  state.down = down;
  $('#offline').hidden = !down;
  if (down) {
    (async () => {
      while (state.down) {
        await sleep(3500);
        try {
          const r = await fetch('/api/health', { cache: 'no-store' });
          if (r.ok) { setServerDown(false); loadInfo(); loadLibrary(); pollJobs(); }
        } catch { /* ещё лежит */ }
      }
    })();
  }
}

/* ============================== тема ============================== */

function applyTheme(t) {
  html.dataset.theme = t;
  store.set('vd.theme', t);
  try { localStorage.setItem('vd.theme', t); } catch { /* */ }
  $('meta[name="theme-color"]').content = t === 'light' ? '#f3eee4' : '#07070c';
}
$('#theme').addEventListener('click', (e) => {
  const next = html.dataset.theme === 'light' ? 'dark' : 'light';
  if (!CAN_VT) { applyTheme(next); return; }
  const r = e.currentTarget.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  const t = viewTransition(() => applyTheme(next), 'vt-theme');
  t.ready.then(() => {
    html.animate({ clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
      { duration: 750, easing: SPRING_SOFT, pseudoElement: '::view-transition-new(root)' });
  });
});

/* ============================== «чернила» вкладок и пилюль ============================== */

function moveInk(group) {
  const on = group.querySelector('[aria-checked="true"], [aria-selected="true"]');
  const ink = group.querySelector('.pill-ink, .tab-ink');
  if (!ink) return;
  if (!on || !on.offsetWidth) { ink.style.setProperty('--w', '0px'); return; }
  ink.style.setProperty('--x', `${on.offsetLeft}px`);
  ink.style.setProperty('--w', `${on.offsetWidth}px`);
}
const moveAllInks = () => $$('.pills, .tabs').forEach(moveInk);

function setPill(group, value) {
  for (const b of group.querySelectorAll('button[data-value]')) b.setAttribute('aria-checked', String(b.dataset.value === String(value)));
  moveInk(group);
}
function initPills(group, onChange) {
  group.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-value]');
    if (!b || b.disabled) return;
    setPill(group, b.dataset.value);
    onChange(b.dataset.value);
  });
  group.addEventListener('keydown', (e) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    const btns = [...group.querySelectorAll('button[data-value]:not(:disabled)')];
    const i = btns.findIndex((b) => b.getAttribute('aria-checked') === 'true');
    const next = btns[(i + (e.key === 'ArrowRight' ? 1 : -1) + btns.length) % btns.length];
    if (next) { next.focus(); next.click(); e.preventDefault(); }
  });
}

/* ============================== вкладки ============================== */

function switchTab(tab, { animate: anim = true } = {}) {
  if (tab === state.tab && anim) return;
  const forward = tab === 'file';
  const update = () => {
    state.tab = tab;
    for (const t of $$('.tab')) t.setAttribute('aria-selected', String(t.dataset.tab === tab));
    $('#panel-link').hidden = tab !== 'link';
    $('#panel-file').hidden = tab !== 'file';
    updateTuners();
    moveAllInks();
  };
  if (!anim) { update(); return; }
  const consoleEl = $('.console');
  consoleEl.classList.add('vt-panel');
  const t = viewTransition(update, forward ? 'vt-tab-fwd' : 'vt-tab-back');
  t.finished.finally(() => consoleEl.classList.remove('vt-panel'));
}
$$('.tab').forEach((t) => t.addEventListener('click', () => switchTab(t.dataset.tab)));

/* ============================== формат, качество, отрезок ============================== */

function setMode(mode, { save = true } = {}) {
  state.mode = mode;
  for (const b of $$('.fmt')) b.setAttribute('aria-checked', String(b.dataset.mode === mode));
  updateTuners();
  if (save) savePrefs();
}
$$('.fmt').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
$('.formats').addEventListener('keydown', (e) => {
  if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
  const order = ['mp4', 'mp3', 'both'];
  const next = order[(order.indexOf(state.mode) + (e.key === 'ArrowRight' ? 1 : 2)) % 3];
  setMode(next);
  $(`.fmt[data-mode="${next}"]`).focus();
  e.preventDefault();
});

function updateTuners() {
  $('#quality-tuner').classList.toggle('off', state.tab !== 'link' || state.mode === 'mp3');
  $('#bitrate-tuner').classList.toggle('off', state.mode === 'mp4');
  $('#autostart-wrap').hidden = state.tab !== 'link';
  const previewHasRange = state.tab === 'link' && state.preview?.kind === 'ready' && state.range;
  $('#clip').hidden = !!previewHasRange;
  requestAnimationFrame(moveAllInks);
}

/** Какие «высоты» есть у видео: недоступное качество гасим, «Макс» всегда можно. */
function applyHeights(heights) {
  state.heights = heights && heights.length ? heights : null;
  const max = state.heights ? Math.max(...state.heights) : Infinity;
  const group = $('[data-name="quality"]');
  let effective = state.quality;
  for (const b of group.querySelectorAll('button[data-value]')) {
    const q = b.dataset.value;
    const off = q !== 'max' && max < Number(q) * 0.9;
    b.disabled = off;
    b.title = off ? 'В этом видео такого качества нет' : (q === 'max' ? 'Самое высокое, вплоть до 4K. Может перекодироваться дольше' : '');
    if (off && q === effective) effective = null;
  }
  if (!effective) {
    const firstOn = [...group.querySelectorAll('button[data-value]:not(:disabled)')].find((b) => b.dataset.value !== 'max');
    effective = firstOn ? firstOn.dataset.value : 'max';
  }
  setPill(group, effective);
}
const effectiveQuality = () => $('[data-name="quality"] [aria-checked="true"]')?.dataset.value || state.quality;

/* «Только отрезок» без превью (несколько ссылок, конвертер, превью не загрузилось) */
const clipOn = $('#clip-on');
function clipValues() {
  const sEl = $('#clip-start'), eEl = $('#clip-end');
  const s = parseTime(sEl.value), e = parseTime(eEl.value);
  return { s, e, sEl, eEl };
}
function renderClipRow() {
  $('#clip-fields').hidden = !clipOn.checked;
  const { s, e, sEl, eEl } = clipValues();
  sEl.parentElement.classList.toggle('invalid', Number.isNaN(s));
  eEl.parentElement.classList.toggle('invalid', Number.isNaN(e));
  const out = $('#clip-len');
  out.classList.remove('bad');
  const sel = $('#clip-mini-sel');
  sel.style.setProperty('--a', '0%'); sel.style.setProperty('--b', '100%');
  if (Number.isNaN(s) || Number.isNaN(e)) { out.textContent = 'формат: 1:30 или 90'; out.classList.add('bad'); return; }
  if (s != null && e != null && e <= s) { out.textContent = 'конец раньше начала'; out.classList.add('bad'); return; }
  if (s == null && e == null) { out.textContent = 'весь ролик'; return; }
  if (e != null) out.textContent = `= ${fmtTime(e - (s || 0))}`;
  else out.textContent = `с ${fmtTime(s)} до конца`;
  const d = Math.max(e || 0, (s || 0) * 1.6, 1);
  sel.style.setProperty('--a', `${((s || 0) / d) * 100}%`);
  sel.style.setProperty('--b', `${((e ?? d) / d) * 100}%`);
}
clipOn.addEventListener('change', () => { renderClipRow(); if (clipOn.checked) $('#clip-start').focus(); });
$('#clip-start').addEventListener('input', renderClipRow);
$('#clip-end').addEventListener('input', renderClipRow);

/** start/end для отправки или ошибка. */
function clipPayload(useRange) {
  if (useRange && state.range) {
    const { a, b, d } = state.range;
    const start = a > 0.5 ? String(Math.round(a)) : null;
    const end = b < d - 0.5 ? String(Math.round(b)) : null;
    return { start, end };
  }
  if (!clipOn.checked) return { start: null, end: null };
  const { s, e, sEl, eEl } = clipValues();
  if (Number.isNaN(s) || Number.isNaN(e)) throw new Error('Время отрезка — в виде 1:30, 1:02:03 или 90');
  if (s != null && e != null && e <= s) throw new Error('Конец отрезка должен быть позже начала');
  return { start: sEl.value.trim() || null, end: eEl.value.trim() || null };
}

/* ============================== портал ссылок ============================== */

const urlBox = $('#url');
const URL_RE = /^(https?:\/\/)?([\w-]+\.)+[a-z]{2,}(:\d+)?(\/\S*)?$/i;
function extractUrls(text) {
  return String(text).split(/\s+/).map((s) => s.trim()).filter((s) => s && URL_RE.test(s));
}
function detectPlatform(url) {
  if (!url) return 'none';
  let host = '';
  try { host = new URL(/^https?:/i.test(url) ? url : `https://${url}`).hostname.toLowerCase(); } catch { return 'none'; }
  const is = (d) => host === d || host.endsWith(`.${d}`);
  if (is('youtube.com') || is('youtu.be')) return 'youtube';
  if (is('tiktok.com')) return 'tiktok';
  if (is('instagram.com')) return 'instagram';
  return 'other';
}
function setPlatform(p) {
  if (html.dataset.platform === p) return;
  html.dataset.platform = p;
  const g = $('#portal-glyph');
  g.dataset.p = p;
  g.innerHTML = svgUse(p === 'none' ? '#i-link' : glyph(p));
  g.classList.remove('pop'); void g.offsetWidth; g.classList.add('pop');
  $$('.pf').forEach((el) => el.classList.toggle('lit', el.dataset.p === p));
}
function autoGrow() {
  urlBox.style.height = 'auto';
  urlBox.style.height = `${Math.min(urlBox.scrollHeight, 180)}px`;
}

let pvTimer = 0;
function onUrlInput() {
  autoGrow();
  const urls = extractUrls(urlBox.value);
  setPlatform(urls.length ? detectPlatform(urls[0]) : 'none');
  clearTimeout(pvTimer);
  if (!urls.length) { setPreview(null); return; }
  if (urls.length > 1) { setPreview({ kind: 'multi', n: urls.length }); return; }
  const url = urls[0];
  if (state.preview && state.preview.url === url && state.preview.kind !== 'multi') return;
  pvTimer = setTimeout(() => loadPreview(url), 400);
}
urlBox.addEventListener('input', onUrlInput);
urlBox.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitLinks(); }
});
$('#link-form').addEventListener('submit', (e) => { e.preventDefault(); submitLinks(); });

function acceptPastedText(text, { replace = true } = {}) {
  const urls = extractUrls(text);
  if (!urls.length) return false;
  urlBox.value = replace ? urls.join('\n') : `${urlBox.value.trim()}\n${urls.join('\n')}`.trim();
  if (state.tab !== 'link') switchTab('link');
  onUrlInput();
  if (state.autostart) { clearTimeout(pvTimer); submitLinks(); }
  else urlBox.focus();
  return true;
}
$('#paste').addEventListener('click', async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (!acceptPastedText(text)) toast('В буфере обмена нет ссылки', 'warn');
  } catch {
    urlBox.focus();
    toast('Браузер не дал прочитать буфер — нажмите Ctrl+V', 'info');
  }
});
document.addEventListener('paste', (e) => {
  const text = e.clipboardData?.getData('text') || '';
  const t = e.target;
  if (t === urlBox) {
    if (state.autostart && extractUrls(text).length) setTimeout(() => { clearTimeout(pvTimer); submitLinks(); }, 0);
    return;
  }
  if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t?.isContentEditable) return;
  if (acceptPastedText(text)) e.preventDefault();
});
const autostart = $('#autostart');
autostart.checked = state.autostart;
autostart.addEventListener('change', () => { state.autostart = autostart.checked; savePrefs(); });

async function submitLinks() {
  const urls = extractUrls(urlBox.value);
  const form = $('#link-form');
  if (!urls.length) {
    animate(form, [{ transform: 'translateX(0)' }, { transform: 'translateX(-10px)' }, { transform: 'translateX(9px)' }, { transform: 'translateX(-5px)' }, { transform: 'none' }], { duration: 420, easing: 'ease-out' });
    toast(urlBox.value.trim() ? 'Это не похоже на ссылку' : 'Сначала вставьте ссылку на видео', 'warn');
    urlBox.focus();
    return;
  }
  let clip;
  try {
    clip = clipPayload(urls.length === 1 && state.preview?.kind === 'ready' && state.preview.url === urls[0]);
  } catch (err) { toast(err.message, 'err'); return; }
  const go = $('#go');
  go.disabled = true;
  go.classList.remove('fire'); void go.offsetWidth; go.classList.add('fire');
  try {
    const body = { urls, mode: state.mode, quality: effectiveQuality(), bitrate: Number(state.bitrate) };
    if (clip.start) body.start = clip.start;
    if (clip.end) body.end = clip.end;
    const jobs = await api('/api/jobs', { method: 'POST', body });
    urlBox.value = '';
    onUrlInput();
    toast(jobs.length > 1 ? `В очереди: ${jobs.length} ссылки` : 'Поехали! Ссылка в очереди', 'ok', { timeout: 2400 });
    pollJobs();
  } catch (err) {
    toast(err.message, 'err', { timeout: 6000 });
  } finally {
    go.disabled = false;
  }
}

/* ============================== превью ссылки ============================== */

const pvCache = new Map();
let pvAbort = null;

async function loadPreview(url) {
  if (state.autostart) return;
  if (pvCache.has(url)) { setPreview({ kind: 'ready', url, data: pvCache.get(url) }); return; }
  pvAbort?.abort();
  pvAbort = new AbortController();
  setPreview({ kind: 'loading', url });
  try {
    const data = await api('/api/preview', { method: 'POST', body: { url }, signal: pvAbort.signal });
    pvCache.set(url, data);
    if (extractUrls(urlBox.value)[0] === url) setPreview({ kind: 'ready', url, data });
  } catch (err) {
    if (err.name === 'AbortError') return;
    if (extractUrls(urlBox.value)[0] === url) setPreview({ kind: 'error', url, message: err.message });
  }
}

function setPreview(p) {
  const slot = $('#preview-slot');
  const prevKind = state.preview?.kind;
  const sameUrl = p && state.preview && p.url === state.preview.url;
  state.preview = p;
  state.range = null;
  if (!p) {
    pvAbort?.abort();
    applyHeights(null);
    const card = slot.firstElementChild;
    if (card) animate(card, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(-6px) scale(.97)', filter: 'blur(6px)' }], { duration: 220, easing: EASE_IN }).finished.then(() => { if (!state.preview) slot.replaceChildren(); });
    updateTuners();
    return;
  }
  let card;
  if (p.kind === 'multi') {
    applyHeights(null);
    card = h(`<div class="preview compact">${svgUse('#i-layers')}<span><b>${p.n} ссылки</b> — превью покажу для одной, а скачаю все сразу</span></div>`);
  } else if (p.kind === 'loading') {
    card = h(`<div class="preview loading" aria-busy="true">
      <div class="pv-media skel"></div>
      <div class="pv-body">
        <div class="skel skel-line" style="width:82%;height:18px"></div>
        <div class="skel skel-line" style="width:48%"></div>
        <div class="skel" style="height:46px;margin-top:auto;border-radius:12px"></div>
      </div></div>`);
  } else if (p.kind === 'error') {
    applyHeights(null);
    card = h(`<div class="preview compact error">${svgUse('#i-warn')}<span><b>Превью не загрузилось.</b> ${esc(p.message)} Скачать всё равно можно — жмите стрелку.</span></div>`);
  } else {
    card = previewCard(p.data);
  }
  slot.replaceChildren(card);
  if (!(sameUrl && prevKind && prevKind !== 'multi' && p.kind === 'ready' && prevKind === 'loading') || p.kind !== 'ready') {
    if (p.kind !== prevKind) card.classList.add('enter');
  } else {
    animate(card, [{ opacity: .6, filter: 'blur(4px)' }, { opacity: 1, filter: 'blur(0)' }], { duration: 400, easing: 'ease-out' });
  }
  updateTuners();
}

function h(markup) {
  const t = document.createElement('template');
  t.innerHTML = markup.trim();
  return t.content.firstElementChild;
}

function previewCard(d) {
  const dur = d.duration && d.duration > 0 ? d.duration : null;
  applyHeights(d.heights);
  if (d.has_video === false && state.mode !== 'mp3') { setMode('mp3', { save: false }); toast('Здесь только звук — переключила на MP3', 'info'); }
  const chips = [];
  if (d.uploader) chips.push(`<span class="meta-chip">${esc(d.uploader)}</span>`);
  chips.push(`<span class="meta-chip">${svgUse(glyph(d.platform))}${PLATFORM[d.platform] || 'Сайт'}</span>`);
  if (dur) chips.push(`<span class="meta-chip mono">${fmtTime(dur)}</span>`);
  if (d.playlist) chips.push(`<span class="meta-chip accent">${svgUse('#i-layers')}Плейлист${d.count ? ` · ${d.count} видео` : ''}</span>`);
  if (d.has_video === false) chips.push(`<span class="meta-chip accent">${svgUse('#i-wave')}только звук</span>`);
  if (d.heights?.length) chips.push(`<span class="meta-chip mono">до ${Math.max(...d.heights)}p</span>`);
  const thumb = d.thumbnail ? `<img src="${esc(d.thumbnail)}" alt="" referrerpolicy="no-referrer" decoding="async">` : artHtml(d.url, PLATFORM[d.platform] || '');
  const video = d.preview_url && dur ? `<video muted playsinline preload="metadata" src="${esc(d.preview_url)}"></video>` : '';
  const card = h(`<div class="preview">
    <div class="pv-media">
      ${thumb}${video}
      <span class="pv-badge pf-${esc(d.platform)}">${svgUse(glyph(d.platform))}</span>
      ${dur ? `<span class="pv-dur">${fmtTime(dur)}</span>` : ''}
      ${video ? `<button class="pv-play" type="button" aria-label="Смотреть превью">${svgUse('#i-play')}</button>` : ''}
    </div>
    <div class="pv-body">
      <h3 class="pv-title"></h3>
      <p class="pv-meta">${chips.join('')}</p>
      ${dur ? `<div class="range" style="--a:0%;--b:100%">
        <div class="range-track${d.thumbnail ? '' : ' no-strip'}"></div>
        <div class="range-shade l"></div><div class="range-shade r"></div>
        <div class="range-sel"></div>
        <div class="range-playhead"></div>
        <div class="range-handle a" role="slider" tabindex="0" aria-label="Начало отрезка"><span class="range-bubble"></span></div>
        <div class="range-handle b" role="slider" tabindex="0" aria-label="Конец отрезка"><span class="range-bubble"></span></div>
      </div>
      <div class="range-legend">
        <label class="time-field"><span>с</span><input class="pv-start" inputmode="decimal" placeholder="0:00" aria-label="Начало отрезка"></label>
        <label class="time-field"><span>по</span><input class="pv-end" inputmode="decimal" placeholder="${fmtTime(dur)}" aria-label="Конец отрезка"></label>
        <output class="clip-len"></output>
        <button class="range-reset" type="button" hidden>Весь ролик</button>
      </div>` : ''}
    </div></div>`);
  card.querySelector('.pv-title').textContent = d.title || d.url;
  card.querySelector('.pv-title').title = d.title || '';
  const img = card.querySelector('.pv-media img');
  if (img) {
    img.addEventListener('load', () => {
      if (img.naturalHeight > img.naturalWidth * 1.1) card.classList.add('portrait');
      const track = card.querySelector('.range-track');
      if (track) track.style.setProperty('--strip', `url("${d.thumbnail.replace(/"/g, '%22')}")`);
    });
    img.addEventListener('error', () => {
      img.replaceWith(h(artHtml(d.url, PLATFORM[d.platform] || '')));
      card.querySelector('.range-track')?.classList.add('no-strip');
    });
  }
  if (dur) initRange(card, dur);
  return card;
}

function initRange(card, d) {
  state.range = { a: 0, b: d, d };
  const range = card.querySelector('.range');
  const ha = range.querySelector('.range-handle.a');
  const hb = range.querySelector('.range-handle.b');
  const inA = card.querySelector('.pv-start');
  const inB = card.querySelector('.pv-end');
  const out = card.querySelector('.clip-len');
  const reset = card.querySelector('.range-reset');
  const media = card.querySelector('.pv-media');
  const video = media.querySelector('video');
  const minGap = Math.min(1, d / 50);
  let videoOk = false;

  const render = (skipInput) => {
    const { a, b } = state.range;
    range.style.setProperty('--a', `${(a / d) * 100}%`);
    range.style.setProperty('--b', `${(b / d) * 100}%`);
    ha.querySelector('.range-bubble').textContent = fmtTime(a);
    hb.querySelector('.range-bubble').textContent = fmtTime(b);
    for (const [el, v, label] of [[ha, a, 'Начало'], [hb, b, 'Конец']]) {
      el.setAttribute('aria-valuemin', '0');
      el.setAttribute('aria-valuemax', String(Math.round(d)));
      el.setAttribute('aria-valuenow', String(Math.round(v)));
      el.setAttribute('aria-valuetext', `${label}: ${fmtTime(v)}`);
    }
    if (skipInput !== inA) inA.value = a > 0.5 ? fmtTime(a) : '';
    if (skipInput !== inB) inB.value = b < d - 0.5 ? fmtTime(b) : '';
    inA.parentElement.classList.remove('invalid');
    inB.parentElement.classList.remove('invalid');
    const full = a <= 0.5 && b >= d - 0.5;
    out.textContent = full ? 'весь ролик' : `= ${fmtTime(b - a)}`;
    reset.hidden = full;
  };
  const seek = (t) => {
    if (!videoOk) return;
    cancelAnimationFrame(seek.raf);
    seek.raf = requestAnimationFrame(() => {
      try { if (video.fastSeek) video.fastSeek(t); else video.currentTime = t; } catch { /* */ }
      range.style.setProperty('--ph', `${(t / d) * 100}%`);
      range.classList.add('has-playhead');
    });
  };
  const setA = (t) => { state.range.a = Math.max(0, Math.min(t, state.range.b - minGap)); };
  const setB = (t) => { state.range.b = Math.min(d, Math.max(t, state.range.a + minGap)); };

  let drag = null;
  const timeAt = (clientX) => {
    const r = range.getBoundingClientRect();
    return Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * d;
  };
  range.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const t = timeAt(e.clientX);
    const handle = e.target.closest('.range-handle')
      || (Math.abs(t - state.range.a) <= Math.abs(t - state.range.b) ? ha : hb);
    drag = handle;
    handle.classList.add('dragging');
    range.setPointerCapture(e.pointerId);
    if (!e.target.closest('.range-handle')) { (handle === ha ? setA : setB)(t); render(); seek(handle === ha ? state.range.a : state.range.b); }
    video?.pause();
    e.preventDefault();
  });
  range.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const t = timeAt(e.clientX);
    if (drag === ha) setA(t); else setB(t);
    render();
    seek(drag === ha ? state.range.a : state.range.b);
  });
  const end = () => { if (drag) { drag.classList.remove('dragging'); drag = null; } };
  range.addEventListener('pointerup', end);
  range.addEventListener('pointercancel', end);
  for (const [el, set, key] of [[ha, setA, 'a'], [hb, setB, 'b']]) {
    el.addEventListener('keydown', (e) => {
      const step = e.shiftKey ? 10 : 1;
      let t = state.range[key];
      if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') t -= step;
      else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') t += step;
      else if (e.key === 'Home') t = 0;
      else if (e.key === 'End') t = d;
      else return;
      e.preventDefault();
      set(t); render(); seek(state.range[key]);
    });
  }
  const onInput = (input, set, fallback) => () => {
    const v = parseTime(input.value);
    const bad = Number.isNaN(v) || (v != null && (v < 0 || v > d + 0.5));
    input.parentElement.classList.toggle('invalid', bad);
    if (bad) { out.textContent = Number.isNaN(v) ? 'формат: 1:30 или 90' : `ролик длится ${fmtTime(d)}`; out.classList.add('bad'); return; }
    out.classList.remove('bad');
    set(v == null ? fallback : v);
    render(input);
  };
  inA.addEventListener('input', onInput(inA, setA, 0));
  inB.addEventListener('input', onInput(inB, setB, d));
  reset.addEventListener('click', () => { state.range.a = 0; state.range.b = d; render(); });
  render();

  if (video) {
    video.addEventListener('loadeddata', () => { videoOk = true; media.classList.add('video-ready'); }, { once: true });
    video.addEventListener('error', () => { videoOk = false; video.remove(); media.querySelector('.pv-play')?.remove(); });
    video.addEventListener('timeupdate', () => {
      if (video.paused) return;
      const { a, b } = state.range;
      if (video.currentTime >= b - 0.05) video.currentTime = a;
      range.style.setProperty('--ph', `${(video.currentTime / d) * 100}%`);
      range.classList.add('has-playhead');
    });
    video.addEventListener('play', () => media.classList.add('playing'));
    video.addEventListener('pause', () => media.classList.remove('playing'));
    media.addEventListener('click', () => {
      if (!videoOk) return;
      if (video.paused) {
        const { a, b } = state.range;
        if (video.currentTime < a || video.currentTime >= b - 0.1) video.currentTime = a;
        video.play().catch(() => {});
      } else video.pause();
    });
  }
}

/* ============================== конвертер ============================== */

const fileInput = $('#file-input');
fileInput.addEventListener('change', () => { if (fileInput.files.length) uploadFiles([...fileInput.files]); fileInput.value = ''; });

function uploadFiles(files) {
  let clip;
  try { clip = clipPayload(false); } catch (err) { toast(err.message, 'err'); return; }
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.name);
  fd.append('mode', state.mode);
  fd.append('bitrate', state.bitrate);
  if (clip.start) fd.append('start', clip.start);
  if (clip.end) fd.append('end', clip.end);
  const total = files.reduce((s, f) => s + f.size, 0);
  const t = toast(`Загружаю ${files.length > 1 ? `${files.length} файла` : `«${files[0].name}»`}…`, 'info', { timeout: 0 });
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/convert');
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) t.update(`Загружаю… ${Math.round((e.loaded / e.total) * 100)}% из ${fmtBytes(total)}`); };
  xhr.onload = () => {
    let data = null;
    try { data = JSON.parse(xhr.responseText); } catch { /* */ }
    if (xhr.status >= 200 && xhr.status < 300) {
      t.type('ok').update(files.length > 1 ? `В очереди на конвертацию: ${files.length}` : 'Файл в очереди на конвертацию').later(2600);
      pollJobs();
    } else {
      t.type('err').update(detailText(data) || `Ошибка ${xhr.status}`).later(6000);
    }
  };
  xhr.onerror = () => { t.type('err').update('Сервер не отвечает — файл не загружен').later(6000); setServerDown(true); };
  xhr.send(fd);
}

/* перетаскивание файлов в любое место окна */
let dragDepth = 0;
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
window.addEventListener('dragenter', (e) => { if (!hasFiles(e)) return; dragDepth++; $('#dragveil').hidden = false; });
window.addEventListener('dragleave', (e) => { if (!hasFiles(e)) return; dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('#dragveil').hidden = true; });
window.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener('drop', (e) => {
  if (!hasFiles(e)) {
    const text = e.dataTransfer?.getData('text');
    if (text && e.target !== urlBox && acceptPastedText(text)) e.preventDefault();
    return;
  }
  e.preventDefault();
  dragDepth = 0;
  $('#dragveil').hidden = true;
  const files = [...e.dataTransfer.files];
  if (!files.length) return;
  if (state.tab !== 'file') switchTab('file');
  uploadFiles(files);
});

/* ============================== очередь ============================== */

const jobEls = new Map();
let pollTimer = 0;
let lastOrder = '';
let seenDone = null;

async function pollJobs() {
  clearTimeout(pollTimer);
  let jobs = null;
  try { jobs = await api('/api/jobs'); } catch { /* баннер покажет api() */ }
  if (jobs) onJobs(jobs);
  const active = (jobs || state.jobs).some((j) => ACTIVE.has(j.status));
  pollTimer = setTimeout(pollJobs, active ? 700 : 5000);
}

function onJobs(jobs) {
  const prev = new Map(state.jobs.map((j) => [j.id, j.status]));
  state.jobs = jobs;
  if (seenDone === null) seenDone = new Set(jobs.filter((j) => !ACTIVE.has(j.status)).map((j) => j.id));
  let finished = null;
  for (const j of jobs) {
    if (j.status === 'done' && !seenDone.has(j.id)) {
      seenDone.add(j.id);
      if (prev.has(j.id) || !finished) finished = j;
      for (const f of j.files || []) if (f.id) state.fresh.add(f.id);
    } else if (!ACTIVE.has(j.status)) seenDone.add(j.id);
  }
  renderJobs(jobs);
  if (!islandDemo) updateIsland(jobs, finished);
  if (finished) loadLibrary();
}

function renderJobs(jobs) {
  const list = $('#jobs');
  const section = $('#queue');
  if (section.hidden && jobs.length) { section.hidden = false; animate(section, ENTER, { duration: 600, easing: SPRING_SOFT }); }
  const ids = new Set(jobs.map((j) => j.id));
  const removed = [];
  for (const [id, el] of jobEls) if (!ids.has(id)) { removed.push(el); jobEls.delete(id); }
  const order = jobs.map((j) => j.id).join(',');
  const mutate = () => {
    jobs.forEach((j, i) => {
      let el = jobEls.get(j.id);
      if (!el) { el = createJobEl(j); jobEls.set(j.id, el); }
      updateJobEl(el, j);
      const at = list.children[i];
      if (at !== el) list.insertBefore(el, at || null);
    });
  };
  if (order !== lastOrder || removed.length) flip(list, mutate, removed);
  else mutate();
  lastOrder = order;
  if (!jobs.length && !removed.length) section.hidden = true;
  else if (!jobs.length) setTimeout(() => { if (!state.jobs.length) section.hidden = true; }, 360);
  const active = jobs.filter((j) => ACTIVE.has(j.status));
  const known = active.filter((j) => j.progress != null);
  document.title = active.length
    ? `(${known.length ? Math.round(known.reduce((s, j) => s + j.progress, 0) / known.length) : '…'}${known.length ? '%' : ''}) выдра`
    : 'выдра';
}

function createJobEl(j) {
  const el = $('#job-tpl').content.firstElementChild.cloneNode(true);
  el.dataset.id = j.id;
  el.querySelector('.job-cancel').addEventListener('click', async () => {
    try { await api(`/api/jobs/${j.id}/cancel`, { method: 'POST' }); pollJobs(); } catch (err) { toast(err.message, 'err'); }
  });
  return el;
}

function jobMeta(j) {
  const chips = [`<span class="meta-chip">${svgUse(glyph(j.platform))}${PLATFORM[j.platform] || 'Сайт'}</span>`];
  const mode = j.mode === 'mp3' ? `MP3 · ${j.bitrate}` : j.mode === 'both' ? 'MP4 + MP3' : 'MP4';
  const q = j.kind === 'url' && j.mode !== 'mp3' ? (j.quality === 'max' ? ' · макс' : ` · ${j.quality}p`) : '';
  chips.push(`<span class="meta-chip mono">${mode}${q}</span>`);
  if (j.clip) {
    const [s, e] = j.clip;
    const label = e == null ? `с ${fmtTime(s)}` : (s ? `${fmtTime(s)}–${fmtTime(e)}` : `до ${fmtTime(e)}`);
    chips.push(`<span class="meta-chip accent mono">${svgUse('#i-scissors')}${label}</span>`);
  }
  if (j.count > 1) chips.push(`<span class="meta-chip mono">${j.index}/${j.count}</span>`);
  if (j.uploader) chips.push(`<span class="meta-chip">${esc(j.uploader)}</span>`);
  return chips.join('');
}

function jobTitle(j) {
  if (j.title) return j.title;
  if (j.kind === 'file') return j.source;
  try { const u = new URL(j.source); return `${u.hostname.replace(/^www\./, '')}${u.pathname.length > 1 ? u.pathname : ''}${u.search}`; } catch { return j.source; }
}

function updateJobEl(el, j) {
  const active = ACTIVE.has(j.status);
  const indet = active && (j.progress == null || j.status === 'queued' || j.status === 'saving');
  el.className = `job st-${j.status}${active ? ' active' : ''}${indet ? ' indet' : ''}`;
  el.dataset.p = j.platform;
  const badge = el.querySelector('.job-badge');
  if (badge.dataset.p !== j.platform) {
    badge.dataset.p = j.platform;
    badge.className = `job-badge pf-${j.platform}`;
    badge.innerHTML = svgUse(glyph(j.platform));
    el.querySelector('.job-thumb-icon use').setAttribute('href', glyph(j.platform));
  }
  const title = jobTitle(j);
  const t = el.querySelector('.job-title');
  if (t.textContent !== title) { t.textContent = title; t.title = title; }
  const meta = jobMeta(j);
  const metaEl = el.querySelector('.job-meta');
  if (metaEl.dataset.sig !== meta) { metaEl.dataset.sig = meta; metaEl.innerHTML = meta; }
  const p = j.status === 'done' ? 1 : (j.progress ?? 0) / 100;
  el.querySelector('.job-fill').style.setProperty('--p', String(Math.max(0, Math.min(1, p))));
  el.querySelector('.job-stage').textContent = j.count > 1 && active ? `${j.stage} · ${j.index} из ${j.count}` : j.stage;
  let nums = '';
  if (j.status === 'downloading' && j.progress != null) {
    nums = [`${Math.floor(j.progress)}%`, j.speed ? `${fmtBytes(j.speed)}/с` : '', j.eta != null ? `ещё ${fmtTime(j.eta)}` : ''].filter(Boolean).join(' · ');
  } else if (j.status === 'converting' && j.progress != null) nums = `${Math.floor(j.progress)}%`;
  else if (j.status === 'done') nums = fmtBytes((j.files || []).reduce((s, f) => s + (f.size || 0), 0));
  el.querySelector('.job-nums').textContent = nums;
  const note = el.querySelector('.job-note');
  const noteText = j.error || j.warning || '';
  if (note.dataset.text !== noteText) {
    note.dataset.text = noteText;
    note.hidden = !noteText;
    note.className = `job-note ${j.error ? 'err' : 'warn'}`;
    note.innerHTML = noteText ? `${svgUse(j.error ? '#i-alert' : '#i-warn')}<span>${esc(noteText)}</span>` : '';
  }
  if (j.thumb && !el.querySelector('.job-thumb img')) {
    const img = new Image();
    img.alt = '';
    img.decoding = 'async';
    img.onload = () => el.querySelector('.job-thumb').append(img);
    img.src = `/api/jobs/${j.id}/thumbnail`;
  }
  const files = el.querySelector('.job-files');
  const sig = (j.files || []).map((f) => f.id || f.name).join('|');
  if (files.dataset.sig !== sig) {
    files.dataset.sig = sig;
    files.replaceChildren(...(j.files || []).map(fileChip));
  }
}

function fileChip(f) {
  const isAudio = f.type === 'mp3';
  const chip = h(`<span class="file-chip">
    <span class="file-chip-label">${svgUse(isAudio ? '#i-wave' : '#i-film')}${esc(String(f.type || '').toUpperCase())} · ${fmtBytes(f.size)}</span>
    <button class="icon-btn" type="button" data-a="play" title="${isAudio ? 'Слушать' : 'Смотреть'}" aria-label="${isAudio ? 'Слушать' : 'Смотреть'}">${svgUse('#i-play')}</button>
    <button class="icon-btn" type="button" data-a="reveal" title="Показать в папке" aria-label="Показать в папке">${svgUse('#i-folder')}</button>
    <button class="icon-btn" type="button" data-a="open" title="Открыть в системном плеере" aria-label="Открыть в системном плеере">${svgUse('#i-external')}</button>
  </span>`);
  chip.addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-a]');
    if (!b || !f.id) return;
    if (b.dataset.a === 'play') {
      let item = state.library?.items.find((i) => i.id === f.id);
      if (!item) { await loadLibrary(); item = state.library?.items.find((i) => i.id === f.id); }
      if (item) openPlayer(item, $(`.tile[data-id="${CSS.escape(f.id)}"] .tile-media`));
      else toast('Файл ещё индексируется — секунду', 'info');
      return;
    }
    libraryAction(f.id, b.dataset.a);
  });
  return chip;
}

async function libraryAction(id, act) {
  try {
    await api(`/api/library/${encodeURIComponent(id)}/${act}`, { method: 'POST' });
    toast(act === 'reveal' ? 'Открываю папку с файлом' : 'Открываю в системном плеере', 'info', { timeout: 2000 });
  } catch (err) { toast(err.message, 'err'); }
}

$('#clear-jobs').addEventListener('click', async () => {
  try { await api('/api/jobs/clear', { method: 'POST' }); pollJobs(); } catch (err) { toast(err.message, 'err'); }
});

/* ============================== Dynamic Island ============================== */

const island = $('#island');
let islandHover = false, islandPinned = false, doneTimer = 0;
const islandDemo = new URLSearchParams(location.search).get('island') === 'demo';
function updateIsland(jobs, finished) {
  const active = jobs.filter((j) => ACTIVE.has(j.status));
  if (active.length) {
    clearTimeout(doneTimer);
    const cur = active.find((j) => j.status === 'downloading' || j.status === 'converting') || active[active.length - 1];
    const known = active.filter((j) => j.progress != null && j.status !== 'queued');
    const pct = known.length ? known.reduce((s, j) => s + j.progress, 0) / known.length : null;
    island.toggleAttribute('data-indet', pct == null);
    island.style.setProperty('--p', String(pct ?? 0));
    $('#island-pct').textContent = pct == null ? '···' : `${Math.floor(pct)}%`;
    $('#island-title').textContent = jobTitle(cur);
    $('#island-stage').textContent = [cur.stage, cur.status === 'downloading' && cur.speed ? `${fmtBytes(cur.speed)}/с` : '', cur.eta != null && cur.status === 'downloading' ? `ещё ${fmtTime(cur.eta)}` : ''].filter(Boolean).join(' · ');
    $('#island-bar').style.setProperty('--s', String((cur.progress ?? 0) / 100));
    $('#island-count').textContent = active.length > 1 ? `+${active.length - 1}` : '';
    const thumb = $('#island-thumb');
    if (cur.thumb && thumb.dataset.id !== cur.id) {
      thumb.dataset.id = cur.id;
      thumb.innerHTML = `<img alt="" src="/api/jobs/${cur.id}/thumbnail">`;
    } else if (!cur.thumb && thumb.dataset.id !== `g-${cur.platform}`) {
      thumb.dataset.id = `g-${cur.platform}`;
      thumb.innerHTML = svgUse(glyph(cur.platform));
    }
    island.dataset.state = islandHover || islandPinned ? 'expanded' : 'compact';
    $('#island-hit').tabIndex = 0;
  } else if (finished) {
    islandPinned = false;
    $('#island-done-text').textContent = 'Готово';
    island.dataset.state = 'done';
    clearTimeout(doneTimer);
    doneTimer = setTimeout(() => { if (!state.jobs.some((j) => ACTIVE.has(j.status))) island.dataset.state = 'idle'; }, 2200);
  } else if (island.dataset.state !== 'done') {
    island.dataset.state = 'idle';
    $('#island-hit').tabIndex = -1;
  }
}
const islandHit = $('#island-hit');
islandHit.addEventListener('pointerenter', (e) => { if (e.pointerType !== 'mouse') return; islandHover = true; if (island.dataset.state === 'compact') island.dataset.state = 'expanded'; });
islandHit.addEventListener('pointerleave', () => { islandHover = false; if (island.dataset.state === 'expanded' && !islandPinned) island.dataset.state = 'compact'; });
islandHit.addEventListener('click', () => {
  if (island.dataset.state === 'expanded') { islandPinned = false; island.dataset.state = 'compact'; $('#queue').scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'start' }); }
  else if (island.dataset.state === 'compact') { islandPinned = true; island.dataset.state = 'expanded'; }
  else if (island.dataset.state === 'done') { $('.library').scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'start' }); }
});

/* ============================== хранилище ============================== */

const tileEls = new Map();
let libLoaded = false;
let libOrder = '';

async function loadLibrary() {
  try {
    state.library = await api('/api/library');
  } catch { return; }
  renderLibrary();
  $('#foot-path').textContent = state.library.root || '';
}

function filteredItems() {
  const items = state.library?.items || [];
  const q = state.libsearch.trim().toLocaleLowerCase('ru');
  return items.filter((it) => (state.libtype === 'all' || it.type === state.libtype)
    && (state.libplatform === 'all' || it.platform === state.libplatform)
    && (!q || `${it.title} ${it.uploader || ''} ${it.path}`.toLocaleLowerCase('ru').includes(q)));
}

function renderLibrary() {
  const lib = state.library;
  if (!lib) return;
  const items = lib.items || [];
  const st = lib.stats || {};
  // статистика
  const stats = $('#stats');
  stats.hidden = !items.length;
  if (items.length) {
    const v = st.size_by_type?.video || 0, a = st.size_by_type?.audio || 0, total = v + a || 1;
    $('#sb-video').style.flex = `${v / total} 1 0`;
    $('#sb-audio').style.flex = `${a / total} 1 0`;
    $('#sb-video').hidden = !v; $('#sb-audio').hidden = !a;
    $('#stats-legend').innerHTML = `<span><b>${st.count ?? items.length}</b> ${plural(st.count ?? items.length, 'файл', 'файла', 'файлов')} · <b>${fmtBytes(st.size)}</b></span>`
      + `<span><i style="background:linear-gradient(90deg,var(--c1),var(--c2))"></i>видео ${st.videos ?? 0} · ${fmtBytes(v)}</span>`
      + `<span><i style="background:var(--c3)"></i>аудио ${st.audios ?? 0} · ${fmtBytes(a)}</span>`;
    $('#lib-sub').textContent = 'Всё хранится у вас и смотрится без интернета';
  }
  renderPlatformChips(items);
  // плитки
  const grid = $('#library');
  const shown = filteredItems();
  const ids = new Set(shown.map((i) => i.id));
  const removed = [];
  for (const [id, el] of tileEls) if (!ids.has(id)) { removed.push(el); tileEls.delete(id); }
  const order = shown.map((i) => i.id).join(',');
  const initial = !libLoaded;
  const mutate = () => {
    shown.forEach((it, i) => {
      let el = tileEls.get(it.id);
      const sig = tileSig(it);
      if (el && el.dataset.sig !== sig) { const fresh = tileEl(it); el.replaceWith(fresh); el = fresh; }
      if (!el) { el = tileEl(it); if (initial) reveal(el, Math.min(i, 10)); }
      tileEls.set(it.id, el);
      el.classList.toggle('fresh', state.fresh.has(it.id));
      const at = grid.children[i];
      if (at !== el) grid.insertBefore(el, at || null);
    });
  };
  if (!initial && (order !== libOrder || removed.length)) flip(grid, mutate, removed);
  else { removed.forEach((el) => el.remove()); mutate(); }
  libOrder = order;
  libLoaded = true;
  if (state.fresh.size) setTimeout(() => { state.fresh.clear(); $$('.tile.fresh').forEach((t) => t.classList.remove('fresh')); }, 5000);
  // пустые состояния
  const empty = $('#lib-empty');
  empty.hidden = shown.length > 0;
  if (!items.length) {
    $('#lib-empty-title').textContent = 'Здесь появятся ваши видео и музыка';
    $('#lib-empty-sub').textContent = 'Вставьте ссылку выше — файл сохранится в хранилище и будет доступен без интернета';
  } else if (!shown.length) {
    $('#lib-empty-title').textContent = 'Ничего не нашлось';
    $('#lib-empty-sub').textContent = 'Попробуйте другой запрос или сбросьте фильтры';
  }
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

function renderPlatformChips(items) {
  const box = $('#platform-chips');
  const counts = {};
  for (const it of items) counts[it.platform] = (counts[it.platform] || 0) + 1;
  const keys = ['youtube', 'tiktok', 'instagram', 'other', 'file'].filter((k) => counts[k]);
  if (state.libplatform !== 'all' && !counts[state.libplatform]) state.libplatform = 'all';
  const sig = keys.map((k) => `${k}:${counts[k]}`).join(',') + `|${state.libplatform}`;
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.hidden = keys.length < 2;
  const chip = (k, label, n, icon) => `<button type="button" class="pchip" role="radio" data-p="${k}" aria-checked="${state.libplatform === k}">${icon || ''}${label}${n != null ? ` <span class="n">${n}</span>` : ''}</button>`;
  box.innerHTML = chip('all', 'Все платформы', null) + keys.map((k) => chip(k, k === 'other' ? 'Другие' : k === 'file' ? 'Мои файлы' : PLATFORM[k], counts[k], svgUse(glyph(k)))).join('');
}
$('#platform-chips').addEventListener('click', (e) => {
  const b = e.target.closest('.pchip');
  if (!b) return;
  state.libplatform = b.dataset.p;
  $$('.pchip').forEach((c) => c.setAttribute('aria-checked', String(c === b)));
  $('#platform-chips').dataset.sig = '';
  renderLibrary();
});
let searchTimer = 0;
$('#lib-search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.libsearch = e.target.value; renderLibrary(); }, 140);
});

const tileSig = (it) => [it.poster, it.title, it.duration, it.size, it.width, it.height, it.type].join('|');
function tileAr(it) {
  if (it.type === 'audio') return 1;
  const ar = it.width && it.height ? it.width / it.height : 16 / 9;
  return Math.max(0.72, Math.min(1.9, ar));
}
function tileEl(it) {
  const isAudio = it.type === 'audio';
  const poster = it.poster
    ? `<img src="${esc(libUrl(it.poster))}" alt="" loading="lazy" decoding="async">`
    : artHtml(it.id, isAudio ? 'MP3' : 'MP4');
  const meta = [PLATFORM[it.platform] || '', fmtBytes(it.size), fmtAgo(it.added)].filter(Boolean).join(' · ');
  const el = h(`<article class="tile" style="--ar:${tileAr(it)}">
    <button class="tile-media" type="button">
      ${poster}
      <span class="tile-scrim"></span>
      <span class="tile-pf pf-${esc(it.platform)}">${svgUse(glyph(it.platform))}</span>
      ${it.duration ? `<span class="tile-dur">${fmtTime(it.duration)}</span>` : ''}
      <span class="tile-play">${svgUse('#i-play')}</span>
      <span class="tile-info"><p class="tile-title"></p><p class="tile-meta">${esc(meta)}</p></span>
    </button>
  </article>`);
  el.dataset.id = it.id;
  el.dataset.sig = tileSig(it);
  const btn = el.querySelector('.tile-media');
  btn.setAttribute('aria-label', `${isAudio ? 'Слушать' : 'Смотреть'}: ${it.title}`);
  el.querySelector('.tile-title').textContent = it.title;
  const img = el.querySelector('img');
  if (img) img.addEventListener('error', () => img.replaceWith(h(artHtml(it.id, isAudio ? 'MP3' : 'MP4'))));
  btn.addEventListener('click', () => {
    const cur = state.library?.items.find((i) => i.id === it.id) || it;
    openPlayer(cur, btn);
  });
  if (!isAudio) attachHoverPreview(btn, it);
  return el;
}

function attachHoverPreview(btn, it) {
  let timer = 0, video = null;
  btn.addEventListener('pointerenter', (e) => {
    if (e.pointerType !== 'mouse' || REDUCED) return;
    timer = setTimeout(() => {
      if (!video) {
        video = document.createElement('video');
        video.muted = true; video.loop = true; video.playsInline = true; video.preload = 'auto';
        video.addEventListener('playing', () => btn.classList.add('previewing'));
        btn.insertBefore(video, btn.querySelector('.tile-scrim'));
      }
      const start = it.duration ? Math.min(it.duration * 0.15, 20) : 0;
      video.src = `${libUrl(it.path)}#t=${start.toFixed(1)}`;
      video.play().catch(() => {});
    }, 380);
  });
  btn.addEventListener('pointerleave', () => {
    clearTimeout(timer);
    btn.classList.remove('previewing');
    if (video) { video.pause(); video.removeAttribute('src'); video.load(); }
  });
}

/* ============================== плеер ============================== */

const player = $('#player');
let playerItem = null, playerOrigin = null;

function openPlayer(item, originEl) {
  playerItem = item;
  playerOrigin = originEl || null;
  const fill = () => {
    fillPlayer(item);
    if (!player.open) player.showModal();
  };
  const src = originEl?.querySelector('img, .art');
  if (CAN_VT && src && !player.open) {
    src.style.viewTransitionName = 'player-media';
    const t = document.startViewTransition(() => { src.style.viewTransitionName = ''; fill(); });
    t.finished.catch(() => {});
  } else {
    fill();
    animate(player.querySelector('.player-box'), [{ opacity: 0, transform: 'translateY(20px) scale(.96)', filter: 'blur(8px)' }, { opacity: 1, transform: 'none', filter: 'blur(0)' }], { duration: 600, easing: SPRING_SOFT });
  }
}

function fillPlayer(item) {
  const stage = $('#player-stage');
  const isAudio = item.type === 'audio';
  const media = libUrl(item.path);
  const poster = item.poster ? libUrl(item.poster) : '';
  if (isAudio) {
    stage.innerHTML = `<div class="audio-view">
      ${poster ? `<div class="bg" style="background-image:url('${esc(poster)}')"></div>` : ''}
      <div class="audio-cover">${poster ? `<img src="${esc(poster)}" alt="">` : artHtml(item.id)}</div>
      <div class="eq paused" aria-hidden="true">${'<i></i>'.repeat(18)}</div>
      <audio controls autoplay preload="auto" src="${esc(media)}"></audio>
    </div>`;
    const audio = stage.querySelector('audio');
    const eq = stage.querySelector('.eq');
    audio.addEventListener('play', () => eq.classList.remove('paused'));
    audio.addEventListener('pause', () => eq.classList.add('paused'));
  } else {
    stage.innerHTML = `<video controls autoplay playsinline preload="auto" ${poster ? `poster="${esc(poster)}"` : ''} src="${esc(media)}"></video>`;
  }
  $('#player-title').textContent = item.title;
  const chips = [`<span class="meta-chip">${svgUse(glyph(item.platform))}${PLATFORM[item.platform] || ''}</span>`];
  if (item.duration) chips.push(`<span class="meta-chip mono">${fmtTime(item.duration)}</span>`);
  if (item.width && item.height) chips.push(`<span class="meta-chip mono">${item.width}×${item.height}</span>`);
  chips.push(`<span class="meta-chip mono">${fmtBytes(item.size)}</span>`);
  if (item.added) chips.push(`<span class="meta-chip">${fmtAgo(item.added)}</span>`);
  if (item.uploader) chips.push(`<span class="meta-chip">${esc(item.uploader)}</span>`);
  $('#player-meta').innerHTML = chips.join('');
  player.querySelector('[data-act="cinema"]').href = `${CINEMA_URL}#v=${encodeURIComponent(item.id)}`;
  const dl = player.querySelector('[data-act="download"]');
  dl.href = media;
  dl.setAttribute('download', item.path.split('/').pop());
  const source = player.querySelector('[data-act="source"]');
  source.hidden = !item.source;
  if (item.source) source.href = item.source;
  player.querySelector('.confirm').hidden = true;
  player.querySelector('[data-act="delete"]').hidden = false;
}

async function closePlayer() {
  if (!player.open) return;
  const stage = $('#player-stage');
  stage.querySelectorAll('video, audio').forEach((m) => m.pause());
  const target = playerOrigin?.isConnected ? playerOrigin.querySelector('img, .art') : null;
  const r = target?.getBoundingClientRect();
  const visible = r && r.bottom > 0 && r.top < innerHeight;
  if (CAN_VT && target && visible) {
    const t = document.startViewTransition(() => { player.close(); target.style.viewTransitionName = 'player-media'; });
    await t.finished.catch(() => {});
    target.style.viewTransitionName = '';
  } else {
    player.classList.add('closing');
    await animate(player.querySelector('.player-box'), [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(16px) scale(.96)', filter: 'blur(6px)' }], { duration: 260, easing: EASE_IN }).finished;
    player.close();
    player.classList.remove('closing');
  }
  stage.replaceChildren();
}
player.addEventListener('cancel', (e) => { e.preventDefault(); closePlayer(); });
player.addEventListener('click', (e) => { if (e.target === player || e.target.closest('[data-close]')) closePlayer(); });
player.querySelector('.player-actions').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-act]');
  if (!b || !playerItem) return;
  const act = b.dataset.act;
  if (act === 'cinema') { $('#player-stage').querySelectorAll('video, audio').forEach((m) => m.pause()); return; }
  if (act === 'open' || act === 'reveal') { libraryAction(playerItem.id, act); return; }
  if (act === 'delete') { b.hidden = true; player.querySelector('.confirm').hidden = false; return; }
  if (act === 'delete-no') { player.querySelector('.confirm').hidden = true; player.querySelector('[data-act="delete"]').hidden = false; return; }
  if (act === 'delete-yes') {
    try {
      await api(`/api/library/${encodeURIComponent(playerItem.id)}`, { method: 'DELETE' });
      const id = playerItem.id;
      playerOrigin = null;
      await closePlayer();
      if (state.library) state.library.items = state.library.items.filter((i) => i.id !== id);
      renderLibrary();
      toast('Файл перемещён в Корзину', 'ok');
      loadLibrary();
    } catch (err) { toast(err.message, 'err'); }
  }
});

/* ============================== шторки ============================== */

const isPhone = () => matchMedia('(max-width: 680px)').matches;
function openDrawer(dlg) {
  if (dlg.open) return;
  dlg.showModal();
  const box = dlg.querySelector('.drawer-box');
  box.style.transform = '';
  animate(box, isPhone()
    ? [{ transform: 'translateY(100%)' }, { transform: 'none' }]
    : [{ transform: 'translateX(calc(100% + 24px))', opacity: .7 }, { transform: 'none', opacity: 1 }],
  { duration: 680, easing: SPRING_SOFT });
}
async function closeDrawer(dlg, fromY = null) {
  if (!dlg.open) return;
  const box = dlg.querySelector('.drawer-box');
  dlg.classList.add('closing');
  const from = fromY != null ? `translateY(${fromY}px)` : 'none';
  await animate(box, isPhone()
    ? [{ transform: from }, { transform: 'translateY(100%)' }]
    : [{ transform: 'none', opacity: 1 }, { transform: 'translateX(calc(100% + 24px))', opacity: .6 }],
  { duration: 300, easing: EASE_IN, fill: 'forwards' }).finished;
  dlg.close();
  dlg.classList.remove('closing');
  box.getAnimations?.().forEach((a) => a.cancel());
  box.style.transform = '';
}
for (const dlg of $$('dialog.drawer')) {
  dlg.addEventListener('cancel', (e) => { e.preventDefault(); closeDrawer(dlg); });
  dlg.addEventListener('click', (e) => { if (e.target === dlg || e.target.closest('[data-close]')) closeDrawer(dlg); });
  // смахнуть вниз, чтобы закрыть (на телефоне)
  const box = dlg.querySelector('.drawer-box');
  const handle = dlg.querySelector('.drawer-head');
  let startY = 0, lastY = 0, lastT = 0, v = 0, dragging = false;
  handle.addEventListener('pointerdown', (e) => {
    if (!isPhone() || e.target.closest('button')) return;
    dragging = true; startY = lastY = e.clientY; lastT = performance.now(); v = 0;
    handle.setPointerCapture(e.pointerId);
  });
  handle.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const dy = e.clientY - startY;
    const now = performance.now();
    v = (e.clientY - lastY) / Math.max(1, now - lastT); lastY = e.clientY; lastT = now;
    box.style.transform = `translateY(${dy > 0 ? dy : dy * 0.2}px)`;
  });
  const release = (e) => {
    if (!dragging) return;
    dragging = false;
    const dy = e.clientY - startY;
    if (dy > 110 || v > 0.6) { closeDrawer(dlg, Math.max(0, dy)); return; }
    animate(box, [{ transform: `translateY(${dy}px)` }, { transform: 'none' }], { duration: 560, easing: SPRING });
    box.style.transform = '';
  };
  handle.addEventListener('pointerup', release);
  handle.addEventListener('pointercancel', release);
}

/* ---------- настройки ---------- */

const settingsDlg = $('#settings');
$('#open-settings').addEventListener('click', () => openSettings());
async function openSettings() {
  openDrawer(settingsDlg);
  try { renderSettings(await api('/api/settings')); } catch (err) { toast(err.message, 'err'); }
}
function renderSettings(s) {
  const lib = s.library || {};
  $('#lib-path').textContent = lib.path || '—';
  const tag = $('#lib-tag');
  tag.textContent = lib.fixed ? 'задана снаружи' : lib.is_default ? 'по умолчанию' : 'своя папка';
  tag.className = `tag ${lib.is_default ? '' : 'accent'}`;
  $('#lib-fixed').hidden = !lib.fixed;
  $('#path-form').hidden = !!lib.fixed;
  $('#pick-folder').hidden = !!lib.fixed || state.info?.can_pick_folder === false;
  $('#reset-folder').hidden = !!lib.fixed;
  $('#reset-folder').disabled = !!lib.is_default;
  $('#path-input').placeholder = state.info?.os === 'mac' || state.info?.os === 'linux' ? 'Например, ~/Movies/выдра' : 'Например, D:\\Видео';
  $('#tree-root').textContent = (lib.path || 'VideoDownloader').split(/[\\/]/).filter(Boolean).pop() || lib.path;
  const c = s.cookies || {};
  const ct = $('#cookie-tag');
  ct.textContent = c.present ? `подключены${c.updated ? ` · ${fmtAgo(c.updated)}` : ''}` : 'не нужны, пока не просят';
  ct.className = `tag ${c.present ? 'ok' : ''}`;
  $('#cookie-remove').hidden = !c.present;
  renderAbout();
}
function renderAbout() {
  const i = state.info;
  if (!i) return;
  const os = { windows: 'Windows', wsl: 'Windows (WSL)', mac: 'macOS', linux: 'Linux' }[i.os] || i.os;
  $('#about').innerHTML = `<dt>Версия</dt><dd>${esc(i.version || '—')}</dd><dt>yt-dlp</dt><dd>${esc(i.ytdlp || '—')}</dd><dt>Система</dt><dd>${esc(os || '—')}</dd><dt>Адрес</dt><dd>${esc(location.origin)}</dd>`;
}
function showPathError(msg) { const e = $('#path-error'); e.textContent = msg || ''; e.hidden = !msg; }
async function applyLibrary(body, okText) {
  showPathError('');
  try {
    const s = await api('/api/settings/library', { method: 'POST', body });
    renderSettings(s);
    toast(okText, 'ok');
    libLoaded = false; tileEls.clear(); $('#library').replaceChildren();
    loadInfo(); loadLibrary();
    return true;
  } catch (err) { showPathError(err.message); return false; }
}
$('#path-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const v = $('#path-input').value.trim();
  if (!v) { showPathError('Впишите путь к папке'); return; }
  if (await applyLibrary({ path: v }, 'Хранилище переехало в новую папку')) $('#path-input').value = '';
});
$('#reset-folder').addEventListener('click', () => applyLibrary({ reset: true }, 'Вернула папку по умолчанию'));
$('#pick-folder').addEventListener('click', async (e) => {
  const b = e.currentTarget;
  const label = b.querySelector('span');
  b.disabled = true; b.classList.add('busy');
  b.querySelector('use').setAttribute('href', '#i-refresh');
  label.textContent = 'Окно выбора папки открыто на компьютере…';
  showPathError('');
  try {
    const r = await api('/api/settings/library/pick', { method: 'POST' });
    if (r?.cancelled) toast('Выбор папки отменён', 'info');
    else {
      renderSettings(r); toast('Хранилище переехало в выбранную папку', 'ok');
      libLoaded = false; tileEls.clear(); $('#library').replaceChildren();
      loadInfo(); loadLibrary();
    }
  } catch (err) { showPathError(err.message); }
  finally {
    b.disabled = false; b.classList.remove('busy');
    b.querySelector('use').setAttribute('href', '#i-folder');
    label.textContent = 'Выбрать…';
  }
});
const openFolder = async () => { try { await api('/api/folder/open', { method: 'POST' }); } catch (err) { toast(err.message, 'err'); } };
$('#open-folder').addEventListener('click', openFolder);
$('#open-folder-2').addEventListener('click', openFolder);
$('#cookie-input').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  e.target.value = '';
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f, f.name);
  try { renderSettings(await api('/api/settings/cookies', { method: 'POST', form: fd })); toast('Cookies подключены', 'ok'); loadInfo(); }
  catch (err) { toast(err.message, 'err'); }
});
$('#cookie-remove').addEventListener('click', async () => {
  try { renderSettings(await api('/api/settings/cookies', { method: 'DELETE' })); toast('Cookies удалены', 'ok'); loadInfo(); }
  catch (err) { toast(err.message, 'err'); }
});

/* ---------- состояние системы ---------- */

const healthDlg = $('#health');
$('#open-health').addEventListener('click', () => { openDrawer(healthDlg); if (!state.doctor) runDoctor(); else renderHealth(); });
$('#recheck').addEventListener('click', () => runDoctor());
$('#fix-all').addEventListener('click', (e) => fix('all', e.currentTarget));

let doctorBusy = false;
async function runDoctor() {
  if (doctorBusy) return;
  doctorBusy = true;
  const btn = $('#recheck');
  btn.classList.add('busy'); btn.disabled = true;
  if (!state.doctor) {
    $('#checks').innerHTML = Array.from({ length: 6 }, () => '<li class="check skeleton skel"></li>').join('');
    $('#health-summary').innerHTML = '<span class="sum-chip">Проверяю…</span>';
    $('#fix-all').disabled = true;
  }
  try {
    state.doctor = await api('/api/doctor');
    renderHealth();
  } catch (err) {
    if (healthDlg.open) toast(err.message, 'err');
  } finally {
    doctorBusy = false;
    btn.classList.remove('busy'); btn.disabled = false;
  }
}
function summarize(checks) {
  const s = { ok: 0, warn: 0, fail: 0 };
  for (const c of checks) s[c.status] = (s[c.status] || 0) + 1;
  return s;
}
function renderHealth() {
  const d = state.doctor;
  if (!d) return;
  const s = d.summary || summarize(d.checks);
  $('#health-summary').innerHTML = [
    `<span class="sum-chip"><i style="background:var(--ok)"></i>в порядке: ${s.ok || 0}</span>`,
    s.warn ? `<span class="sum-chip"><i style="background:var(--warn)"></i>внимание: ${s.warn}</span>` : '',
    s.fail ? `<span class="sum-chip"><i style="background:var(--err)"></i>сломано: ${s.fail}</span>` : '',
  ].join('');
  const fixable = d.checks.filter((c) => c.status !== 'ok' && c.fix);
  $('#fix-all').disabled = !fixable.length;
  $('#fix-all span').textContent = fixable.length ? `Починить всё (${fixable.length})` : 'Всё в порядке';
  const icon = { ok: '#i-check', warn: '#i-warn', fail: '#i-x' };
  $('#checks').innerHTML = d.checks.map((c, i) => `<li class="check ${c.status}" style="--i:${i}" data-id="${esc(c.id)}">
    <span class="check-icon">${svgUse(icon[c.status] || '#i-info')}</span>
    <p class="check-title">${esc(c.title)}</p>
    ${c.fix && c.status !== 'ok' ? `<button class="btn" type="button" data-fix="${esc(c.id)}">${svgUse('#i-wrench')}<span>${esc(c.fix)}</span></button>` : ''}
    <p class="check-detail">${esc(c.detail || '')}</p>
    ${c.hint ? `<p class="check-hint">${esc(c.hint)}</p>` : ''}
  </li>`).join('');
  updateBadge();
}
$('#checks').addEventListener('click', (e) => { const b = e.target.closest('[data-fix]'); if (b) fix(b.dataset.fix, b); });
async function fix(id, btn) {
  const label = btn.querySelector('span');
  const old = label.textContent;
  btn.disabled = true; btn.classList.add('busy');
  label.textContent = 'Чиню…';
  const allBtns = $$('#health [data-fix], #fix-all');
  allBtns.forEach((b) => { b.disabled = true; });
  try {
    const r = await api('/api/doctor/fix', { method: 'POST', body: { id } });
    for (const res of r.results || []) toast(res.message, res.ok ? 'ok' : 'err', { timeout: res.ok ? 4200 : 8000 });
    if (r.checks) { state.doctor = { checks: r.checks, summary: summarize(r.checks) }; renderHealth(); }
    loadInfo(); loadLibrary();
  } catch (err) {
    toast(err.message, 'err', { timeout: 7000 });
    label.textContent = old;
  } finally {
    btn.classList.remove('busy');
    allBtns.forEach((b) => { if (b.isConnected) b.disabled = false; });
    if (state.doctor) renderHealth();
  }
}
function updateBadge() {
  const d = state.doctor;
  const badge = $('#health-badge');
  if (!d) { badge.hidden = true; return; }
  const s = d.summary || summarize(d.checks);
  badge.hidden = !(s.warn || s.fail);
  badge.dataset.level = s.fail ? 'fail' : 'warn';
  $('#open-health').title = s.fail ? `Состояние системы: есть проблемы (${s.fail})` : s.warn ? 'Состояние системы: есть замечания' : 'Состояние системы: всё в порядке';
}

/* ============================== общее ============================== */

async function loadInfo() {
  try { state.info = await api('/api/info'); } catch { return; }
  const i = state.info;
  $('#foot-meta').textContent = [i.version ? `выдра ${i.version}` : '', i.ytdlp ? `yt-dlp ${i.ytdlp}` : ''].filter(Boolean).join(' · ');
  if (i.library?.path) $('#foot-path').textContent = i.library.path;
  renderAbout();
}

let scrollRaf = 0;
window.addEventListener('scroll', () => {
  cancelAnimationFrame(scrollRaf);
  scrollRaf = requestAnimationFrame(() => $('.topbar').classList.toggle('scrolled', scrollY > 8));
}, { passive: true });
window.addEventListener('resize', () => requestAnimationFrame(moveAllInks));
document.addEventListener('visibilitychange', () => { if (!document.hidden) { pollJobs(); loadLibrary(); } });

function init() {
  document.body.classList.add('no-anim');
  setMode(state.mode, { save: false });
  initPills($('[data-name="quality"]'), (v) => { state.quality = v; savePrefs(); });
  initPills($('[data-name="bitrate"]'), (v) => { state.bitrate = v; savePrefs(); });
  initPills($('[data-name="libtype"]'), (v) => { state.libtype = v; renderLibrary(); });
  setPill($('[data-name="quality"]'), state.quality);
  setPill($('[data-name="bitrate"]'), state.bitrate);
  setPill($('[data-name="libtype"]'), 'all');
  switchTab('link', { animate: false });
  renderClipRow();
  $$('.pills, .platform-chips').forEach(dragScroll);
  requestAnimationFrame(() => { moveAllInks(); requestAnimationFrame(() => document.body.classList.remove('no-anim')); });
  document.fonts?.ready.then(moveAllInks);

  // въезд героя и консоли
  [...$$('.hero > *'), $('.console'), $('.library')].forEach((el, i) => reveal(el, i));

  loadInfo();
  loadLibrary();
  pollJobs();
  setTimeout(runDoctor, 1200);
  setInterval(() => { if (!document.hidden) loadLibrary(); }, 15000);

  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});

  // крючки для скриншотов и прямых ссылок: ?panel=settings|health, ?player=first
  const q = new URLSearchParams(location.search);
  if (q.get('panel') === 'settings') openSettings();
  if (q.get('panel') === 'health') { openDrawer(healthDlg); runDoctor(); }
  if (q.get('tab') === 'file') switchTab('file', { animate: false });
  if (q.get('url')) { urlBox.value = q.get('url'); onUrlInput(); }
  if (q.get('island') === 'demo') {
    // показ острова без реальной загрузки (для скриншотов)
    islandPinned = true;
    const demo = { id: 'demo', status: 'downloading', stage: 'Скачивание', progress: 62, speed: 4.2e6, eta: 7, platform: 'youtube', title: 'NASA Moon Base Update (Aug. 4, 2026)', thumb: false };
    const tick = () => updateIsland([demo, { ...demo, id: 'demo2', status: 'queued', progress: null }], null);
    tick(); setInterval(tick, 1000);
  }
  if (q.get('player') === 'first') {
    const wait = setInterval(() => {
      const first = state.library?.items?.[0];
      if (first) { clearInterval(wait); openPlayer(first, $(`.tile[data-id="${CSS.escape(first.id)}"] .tile-media`)); }
    }, 300);
    setTimeout(() => clearInterval(wait), 8000);
  }
}

init();
