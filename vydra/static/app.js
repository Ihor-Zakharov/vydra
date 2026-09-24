// выдра — логика интерфейса. Без фреймворков: состояние → точечные обновления DOM.
// Движение объектов — пружины (spring.js), фон — чёрная дыра (cosmos.js), папки — explorer.js.

import { Spring, motionOf, enter, exit, pressable, rubber, flip, REDUCED } from './spring.js';
import { createCosmos } from './cosmos.js';
import { createExplorer } from './explorer.js';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const html = document.documentElement;
const CAN_VT = typeof document.startViewTransition === 'function' && !REDUCED;
const isPhone = () => matchMedia('(max-width: 680px)').matches;

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
function plural(n, one, few, many) { const m10 = n % 10, m100 = n % 100; if (m10 === 1 && m100 !== 11) return one; if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few; return many; }
const PLATFORM = { youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram', other: 'Другой сайт', file: 'Мой файл' };
const PLATFORM_COLOR = { youtube: '#ff3b3b', tiktok: '#25f4ee', instagram: '#f36f9a', other: '#3ee0a1' };
const glyph = (p) => `#p-${PLATFORM[p] ? p : 'other'}`;
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const libUrl = (rel) => '/lib/' + String(rel).split('/').map(encodeURIComponent).join('/');
const svgUse = (id, cls = '') => `<svg${cls ? ` class="${cls}"` : ''}><use href="${id}"/></svg>`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function h(markup) { const t = document.createElement('template'); t.innerHTML = markup.trim(); return t.content.firstElementChild; }
function hash(str) { let x = 2166136261; for (const ch of String(str)) { x ^= ch.codePointAt(0); x = Math.imul(x, 16777619); } return x >>> 0; }
function rng(seed) { return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
function artHtml(seed, kind = 'MP3') {
  const x = hash(seed), h1 = x % 360, h2 = (h1 + 40 + (x >> 8) % 90) % 360;
  const r = rng(x);
  let bars = '';
  const n = 28;
  for (let i = 0; i < n; i++) { const env = Math.sin((i / (n - 1)) * Math.PI) * 0.75 + 0.25; const bh = Math.max(8, Math.round((0.25 + r() * 0.75) * env * 100)); bars += `<rect x="${i * 10 + 2}" y="${(100 - bh) / 2}" width="6" height="${bh}" rx="3"/>`; }
  return `<div class="art" style="--h1:${h1};--h2:${h2}"><span class="art-kind">${esc(kind)}</span><svg class="wave" viewBox="0 0 ${n * 10} 100" preserveAspectRatio="none">${bars}</svg></div>`;
}
/** Путь файла так, как его видит система: корень хранилища + относительный путь. */
function osPath(rel, display) {
  if (display) return display;
  const rootPath = state.info?.library?.path || state.library?.root || '';
  if (!rootPath) return String(rel).replace(/\//g, ' / ');
  const sep = rootPath.includes('\\') ? '\\' : '/';
  return `${rootPath.replace(/[\\/]+$/, '')}${sep}${String(rel).split('/').join(sep)}`;
}
function whereHtml(rel, display) {
  const full = osPath(rel, display);
  const i = Math.max(full.lastIndexOf('\\'), full.lastIndexOf('/'));
  const dir = i >= 0 ? full.slice(0, i + 1) : '', name = i >= 0 ? full.slice(i + 1) : full;
  return `<span class="where" title="${esc(full)}"><span class="p-dir">${esc(dir)}</span><span class="p-name">${esc(name)}</span></span>`;
}

/* ============================== API ============================== */

class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
function detailText(d) {
  if (!d) return '';
  if (typeof d.detail === 'string') return d.detail;
  if (Array.isArray(d.detail)) return d.detail.map((x) => String(x.msg || '').replace(/^Value error,\s*/i, '')).filter(Boolean).join('; ');
  return '';
}
async function api(path, { method = 'GET', body, form, signal } = {}) {
  let res;
  try { res = await fetch(path, { method, signal, cache: 'no-store', headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined, body: body !== undefined ? JSON.stringify(body) : form }); }
  catch (e) { if (e.name === 'AbortError') throw e; setServerDown(true); throw new ApiError('Сервер выдры не отвечает', 0); }
  if (state.down) setServerDown(false);
  const ct = res.headers.get('content-type') || '';
  const data = ct.includes('json') ? await res.json().catch(() => null) : null;
  if (!res.ok) throw new ApiError(detailText(data) || `Ошибка ${res.status}`, res.status);
  return data;
}

/* ============================== состояние ============================== */

const ACTIVE = new Set(['queued', 'downloading', 'converting', 'saving', 'waiting']);
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
  autoAccept: !!saved.autoAccept,
  controlsManual: !!saved.controlsOpen,
  dest: typeof saved.dest === 'string' ? saved.dest : '',
  jobs: [], library: null, info: null, doctor: null,
  preview: null, range: null, heights: null, down: false, speed: 0,
};
const savePrefs = () => store.set('vd.prefs', { mode: state.mode, quality: state.quality, bitrate: state.bitrate, autostart: state.autostart, autoAccept: state.autoAccept, controlsOpen: state.controlsManual, dest: state.dest });

/* ============================== тосты ============================== */

function toast(message, type = 'info', { timeout = 3800, action } = {}) {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.setAttribute('role', type === 'err' ? 'alert' : 'status');
  const icons = { ok: '#i-check', err: '#i-alert', info: '#i-info', warn: '#i-warn' };
  el.innerHTML = `<span class="ti">${svgUse(icons[type] || '#i-info')}</span><span class="tt"></span>`;
  el.querySelector('.tt').textContent = message;
  if (action) { const b = h('<button class="glass btn" type="button"></button>'); b.textContent = action.label; b.addEventListener('click', () => { action.run(); api_.close(); }); el.append(b); }
  box.append(el);
  enter(el, { dy: 22, scale: 0.92, blur: 6, response: 0.5, damping: 0.8 });
  while (box.children.length > 4) box.firstElementChild.remove();
  let timer;
  const api_ = {
    update(msg) { el.querySelector('.tt').textContent = msg; return api_; },
    close() { clearTimeout(timer); exit(el, { dy: 10, scale: 0.94 }).then(() => el.remove()); },
    type(t) { el.className = `toast ${t}`; el.querySelector('.ti').innerHTML = svgUse(icons[t] || '#i-info'); return api_; },
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
  if (down) (async () => {
    while (state.down) {
      await sleep(3500);
      try { const r = await fetch('/api/health', { cache: 'no-store' }); if (r.ok) { setServerDown(false); loadInfo(); explorer.refresh(); pollJobs(); connectEvents(); } }
      catch { /* ещё лежит */ }
    }
  })();
}

/* ============================== тема ============================== */

function applyTheme(t) {
  html.dataset.theme = t;
  try { localStorage.setItem('vd.theme', t); } catch { /* */ }
  $('meta[name="theme-color"]').content = t === 'light' ? '#f3f3f0' : '#050508';
  cosmos?.setInvert(t === 'light');
}
$('#theme').addEventListener('click', (e) => {
  const next = html.dataset.theme === 'light' ? 'dark' : 'light';
  if (!CAN_VT) { applyTheme(next); return; }
  const r = e.currentTarget.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  html.classList.add('vt-theme');
  const t = document.startViewTransition(() => applyTheme(next));
  t.finished.catch(() => {}).finally(() => html.classList.remove('vt-theme'));
  t.ready.then(() => { html.animate({ clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] }, { duration: 700, easing: 'cubic-bezier(.16,1,.3,1)', pseudoElement: '::view-transition-new(root)' }); }).catch(() => {});
});

/* ============================== стекло: блик следует за указателем; нажатие на пружине ============================== */

let glassRaf = 0, glassEv = null;
document.addEventListener('pointermove', (e) => {
  glassEv = e;
  if (glassRaf) return;
  glassRaf = requestAnimationFrame(() => {
    glassRaf = 0;
    const el = glassEv.target.closest?.('.glass');
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty('--mx', `${((glassEv.clientX - r.left) / r.width * 100).toFixed(1)}%`);
    el.style.setProperty('--my', `${((glassEv.clientY - r.top) / r.height * 100).toFixed(1)}%`);
  });
}, { passive: true });
pressable(document, '.glass, .go-btn, .btn, .chip-btn, .icon-btn, .tab, .pills button, .dept, .folder, .crumb, .text-btn, .tile-media', { scale: 0.965 });

/* ============================== космос ============================== */

let cosmos = null;
const heroEl = $('#hero');
function heroLayout() {
  if (!cosmos) return;
  const phone = isPhone();
  const scale = phone ? 0.16 : Math.min(0.14, Math.max(0.09, 0.115 * (heroEl.clientWidth / 1440)));
  cosmos.setCenter(phone ? 0.5 : 0.7, phone ? 0.24 : 0.5, scale);
  cosmos.resize();
}
function initCosmos() {
  const wrap = $('#cosmos-wrap');
  cosmos = createCosmos($('#cosmos'), { reduced: REDUCED, center: [0.7, 0.5], scale: 0.115, resScale: 0.5, accent: '#5a86ff', seed: 0.37 });
  if (!cosmos.ok) { wrap.classList.add('fallback'); cosmos = null; return; }
  cosmos.setInvert(html.dataset.theme === 'light');
  applyAccent();
  heroLayout();
  let ro = 0;
  new ResizeObserver(() => { cancelAnimationFrame(ro); ro = requestAnimationFrame(heroLayout); }).observe(heroEl);
  window.addEventListener('pointermove', (e) => { if (e.pointerType === 'mouse') cosmos.setPointer((e.clientX / innerWidth - 0.5) * 2, (e.clientY / innerHeight - 0.5) * -2); }, { passive: true });
  const orient = (e) => { if (e.gamma == null) return; cosmos.setPointer(Math.max(-1, Math.min(1, e.gamma / 30)), Math.max(-1, Math.min(1, (e.beta - 45) / -30))); };
  if ('DeviceOrientationEvent' in window && matchMedia('(pointer: coarse)').matches) {
    if (typeof DeviceOrientationEvent.requestPermission === 'function') {
      const ask = () => { DeviceOrientationEvent.requestPermission().then((r) => { if (r === 'granted') window.addEventListener('deviceorientation', orient); }).catch(() => {}); };
      heroEl.addEventListener('touchend', ask, { once: true });
    } else window.addEventListener('deviceorientation', orient);
  }
  let sRaf = 0;
  window.addEventListener('scroll', () => { cancelAnimationFrame(sRaf); sRaf = requestAnimationFrame(() => cosmos.setScroll(Math.min(1, Math.max(0, scrollY / Math.max(1, heroEl.clientHeight))))); }, { passive: true });
  new IntersectionObserver((en) => { for (const e of en) { if (e.isIntersecting) cosmos.resume(); else cosmos.pause(); } }, { threshold: 0.02 }).observe(heroEl);
  document.addEventListener('visibilitychange', () => { if (document.hidden) cosmos.pause(); else cosmos.resume(); });
}
function applyAccent() { cosmos?.setAccent(PLATFORM_COLOR[html.dataset.platform] || '#5a86ff', 1); }

/* ============================== «чернила» вкладок и пилюль — на пружинах ============================== */

const inks = new WeakMap();
function inkOf(group) {
  let s = inks.get(group);
  if (!s) {
    const ink = group.querySelector('.pill-ink, .tab-ink');
    s = { x: new Spring({ response: 0.42, damping: 0.86, epsilon: 0.2 }), w: new Spring({ response: 0.42, damping: 0.86, epsilon: 0.2 }), init: false };
    s.x.onUpdate = (v) => ink.style.setProperty('--x', `${v.toFixed(2)}px`);
    s.w.onUpdate = (v) => ink.style.setProperty('--w', `${v.toFixed(2)}px`);
    inks.set(group, s);
  }
  return s;
}
function moveInk(group, { immediate = false } = {}) {
  const on = group.querySelector('[aria-checked="true"], [aria-selected="true"]');
  if (!group.querySelector('.pill-ink, .tab-ink')) return;
  const s = inkOf(group);
  if (!on || !on.offsetWidth) { s.w.snap(0); return; }
  const imm = immediate || !s.init;
  s.init = true;
  s.x.set(on.offsetLeft, { immediate: imm });
  s.w.set(on.offsetWidth, { immediate: imm });
}
const moveAllInks = (opts) => $$('.pills, .tabs').forEach((g) => moveInk(g, opts));
function setPill(group, value) { for (const b of group.querySelectorAll('button[data-value]')) b.setAttribute('aria-checked', String(b.dataset.value === String(value))); moveInk(group); }
function initPills(group, onChange) {
  group.addEventListener('click', (e) => { const b = e.target.closest('button[data-value]'); if (!b || b.disabled) return; setPill(group, b.dataset.value); onChange(b.dataset.value); });
  group.addEventListener('keydown', (e) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    const btns = [...group.querySelectorAll('button[data-value]:not(:disabled)')];
    const i = btns.findIndex((b) => b.getAttribute('aria-checked') === 'true');
    const next = btns[(i + (e.key === 'ArrowRight' ? 1 : -1) + btns.length) % btns.length];
    if (next) { next.focus(); next.click(); e.preventDefault(); }
  });
}

/* ============================== вкладки ============================== */

function switchTab(tab, { animate = true } = {}) {
  if (tab === state.tab && animate) return;
  const from = $(`[data-panel="${state.tab}"]`), to = $(`[data-panel="${tab}"]`);
  state.tab = tab;
  for (const t of $$('.tab')) t.setAttribute('aria-selected', String(t.dataset.tab === tab));
  moveInk($('.tabs'));
  const dir = tab === 'file' ? 1 : -1;
  const show = () => { from.hidden = true; to.hidden = false; if (animate && !REDUCED) { const m = motionOf(to); m.from({ x: 26 * dir, o: 0, b: 6 }); m.to({ x: 0, o: 1, b: 0 }, { response: 0.5, damping: 0.9 }); } };
  if (animate && !REDUCED && !from.hidden) motionOf(from).to({ x: -22 * dir, o: 0, b: 5 }, { response: 0.28, damping: 1 }).then(show); else show();
  updateTuners(); updateControls();
}
$$('.tab').forEach((t) => t.addEventListener('click', () => switchTab(t.dataset.tab)));

/* ============================== формат, качество, отрезок, папка назначения ============================== */

function setMode(mode, { save = true } = {}) { state.mode = mode; setPill($('[data-name="mode"]'), mode); updateTuners(); if (save) savePrefs(); }
function updateTuners() {
  $('#quality-tuner').classList.toggle('off', state.tab !== 'link' || state.mode === 'mp3');
  $('#bitrate-tuner').classList.toggle('off', state.mode === 'mp4');
  $('#autostart-wrap').hidden = state.tab !== 'link';
  $('#clip').hidden = !!(state.tab === 'link' && state.preview?.kind === 'ready' && state.range);
  requestAnimationFrame(() => moveAllInks());
}
/** Тонкие настройки раскрываются, когда есть что настраивать: ссылка распознана, конвертер или пользователь открыл сам. */
function updateControls() {
  const open = state.tab === 'file' || !!state.preview || state.controlsManual;
  const c = $('#controls');
  $('#controls-toggle').setAttribute('aria-expanded', String(open));
  if (c.dataset.open === String(open)) return;
  c.dataset.open = String(open);
  if (open) requestAnimationFrame(() => moveAllInks({ immediate: true }));
}
$('#controls-toggle').addEventListener('click', () => { state.controlsManual = $('#controls').dataset.open !== 'true'; savePrefs(); updateControls(); });

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
  if (!effective) { const firstOn = [...group.querySelectorAll('button[data-value]:not(:disabled)')].find((b) => b.dataset.value !== 'max'); effective = firstOn ? firstOn.dataset.value : 'max'; }
  setPill(group, effective);
}
const effectiveQuality = () => $('[data-name="quality"] [aria-checked="true"]')?.dataset.value || state.quality;

const clipOn = $('#clip-on');
function clipValues() { const sEl = $('#clip-start'), eEl = $('#clip-end'); return { s: parseTime(sEl.value), e: parseTime(eEl.value), sEl, eEl }; }
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
  out.textContent = e != null ? `= ${fmtTime(e - (s || 0))}` : `с ${fmtTime(s)} до конца`;
  const d = Math.max(e || 0, (s || 0) * 1.6, 1);
  sel.style.setProperty('--a', `${((s || 0) / d) * 100}%`); sel.style.setProperty('--b', `${((e ?? d) / d) * 100}%`);
}
clipOn.addEventListener('change', () => { renderClipRow(); if (clipOn.checked) $('#clip-start').focus(); });
$('#clip-start').addEventListener('input', renderClipRow);
$('#clip-end').addEventListener('input', renderClipRow);
function clipPayload(useRange) {
  if (useRange && state.range) { const { a, b, d } = state.range; return { start: a > 0.5 ? String(Math.round(a)) : null, end: b < d - 0.5 ? String(Math.round(b)) : null }; }
  if (!clipOn.checked) return { start: null, end: null };
  const { s, e, sEl, eEl } = clipValues();
  if (Number.isNaN(s) || Number.isNaN(e)) throw new Error('Время отрезка — в виде 1:30, 1:02:03 или 90');
  if (s != null && e != null && e <= s) throw new Error('Конец отрезка должен быть позже начала');
  return { start: sEl.value.trim() || null, end: eEl.value.trim() || null };
}

/* папка назначения: «Сохранять в …» */
function setDest(path, { quiet = false } = {}) {
  state.dest = path || '';
  savePrefs();
  renderDest();
  explorer.setDest(state.dest);
  if (!quiet) toast(state.dest ? `Новые загрузки — в «${state.dest.split('/').pop()}»` : 'Папка выбирается автоматически — по платформе', 'ok', { timeout: 2200 });
}
function renderDest() {
  const b = $('#dest-btn');
  $('#dest-value').textContent = state.dest ? state.dest.split('/').join(' / ') : 'Автоматически';
  b.classList.toggle('custom', !!state.dest);
  b.title = state.dest ? `Загрузки сохраняются в «${state.dest}». Нажмите, чтобы сменить` : 'Папка выбирается по платформе: YouTube, TikTok, Instagram… Нажмите, чтобы выбрать свою';
}
$('#dest-btn').addEventListener('click', async () => {
  const tree = explorer.state.tree || await explorer.fetchTree().catch(() => null);
  if (!tree) { toast('Папки ещё не загрузились — секунду', 'info'); return; }
  const to = await pickFolder({ title: 'Куда сохранять загрузки?', kicker: 'Папка для новых файлов', tree, current: null, allowAuto: true, selected: state.dest });
  if (to == null) return;
  setDest(to);
});

/* ============================== портал ссылок ============================== */

const urlBox = $('#url');
const portal = $('#link-form');
const URL_RE = /^(https?:\/\/)?([\w-]+\.)+[a-z]{2,}(:\d+)?(\/\S*)?$/i;
function extractUrls(text) { return String(text).split(/\s+/).map((s) => s.trim()).filter((s) => s && URL_RE.test(s)); }
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
  if (!REDUCED) { const m = motionOf(g); m.from({ s: 0.6, r: -12 }); m.to({ s: 1, r: 0 }, { response: 0.42, damping: 0.7 }); }
  $$('.pf').forEach((el) => el.classList.toggle('lit', el.dataset.p === p));
  portal.classList.toggle('lit', p !== 'none');
  applyAccent();
}
function autoGrow() { urlBox.style.height = 'auto'; urlBox.style.height = `${Math.min(urlBox.scrollHeight, 180)}px`; }
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
urlBox.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitLinks(); } });
portal.addEventListener('submit', (e) => { e.preventDefault(); submitLinks(); });
function acceptPastedText(text, { replace = true } = {}) {
  const urls = extractUrls(text);
  if (!urls.length) return false;
  urlBox.value = replace ? urls.join('\n') : `${urlBox.value.trim()}\n${urls.join('\n')}`.trim();
  if (state.tab !== 'link') switchTab('link');
  onUrlInput();
  if (state.autostart) { clearTimeout(pvTimer); submitLinks(); }
  else { urlBox.focus(); heroEl.scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'start' }); }
  return true;
}
$('#paste').addEventListener('click', async () => {
  try { const text = await navigator.clipboard.readText(); if (!acceptPastedText(text)) toast('В буфере обмена нет ссылки', 'warn'); }
  catch { urlBox.focus(); toast('Браузер не дал прочитать буфер — нажмите Ctrl+V', 'info'); }
});
document.addEventListener('paste', (e) => {
  const text = e.clipboardData?.getData('text') || '';
  const t = e.target;
  if (t === urlBox) { if (state.autostart && extractUrls(text).length) setTimeout(() => { clearTimeout(pvTimer); submitLinks(); }, 0); return; }
  if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t?.isContentEditable) return;
  if (acceptPastedText(text)) e.preventDefault();
});
const autostart = $('#autostart');
autostart.checked = state.autostart;
autostart.addEventListener('change', () => { state.autostart = autostart.checked; savePrefs(); });
const autoAccept = $('#auto-accept');
autoAccept.checked = state.autoAccept;
autoAccept.addEventListener('change', () => { state.autoAccept = autoAccept.checked; savePrefs(); toast(state.autoAccept ? 'Буду сама выбирать ближайший вариант, если нужного нет' : 'Буду спрашивать, если нужного варианта нет', 'info', { timeout: 2600 }); });

async function submitLinks(extra = {}) {
  const urls = extractUrls(urlBox.value);
  if (!urls.length) {
    if (!REDUCED) { const m = motionOf(portal); m.from({ x: -9 }); m.to({ x: 0 }, { response: 0.28, damping: 0.35 }); }
    toast(urlBox.value.trim() ? 'Это не похоже на ссылку' : 'Сначала вставьте ссылку на видео', 'warn');
    urlBox.focus();
    return;
  }
  let clip;
  try { clip = clipPayload(urls.length === 1 && state.preview?.kind === 'ready' && state.preview.url === urls[0]); }
  catch (err) { toast(err.message, 'err'); return; }
  const go = $('#go');
  go.disabled = true;
  go.classList.remove('fire'); void go.offsetWidth; go.classList.add('fire');
  try {
    const body = { urls, mode: state.mode, quality: effectiveQuality(), bitrate: Number(state.bitrate), auto_accept: state.autoAccept, ...extra };
    if (clip.start) body.start = clip.start;
    if (clip.end) body.end = clip.end;
    if (state.dest) body.folder = state.dest;
    const jobs = await api('/api/jobs', { method: 'POST', body });
    urlBox.value = '';
    onUrlInput();
    toast(jobs.length > 1 ? `В очереди: ${jobs.length} ${plural(jobs.length, 'ссылка', 'ссылки', 'ссылок')}` : 'Поехали! Ссылка в очереди', 'ok', { timeout: 2400 });
    portal.classList.add('busy');
    pollJobs();
  } catch (err) {
    if (err.status === 422 && /confirm_playlist/i.test(err.message)) {
      const msg = err.message.replace(/\s*\(confirm_playlist\)\s*$/i, '');
      const ok = await confirmDialog({ title: 'Это плейлист', text: `${msg}. Скачать все ролики?`, yes: 'Скачать все' });
      if (ok === 'yes') return submitLinks({ ...extra, confirm_playlist: true });
      return;
    }
    if (err.status === 400 && /папк/i.test(err.message) && state.dest) {
      toast(`${err.message}. Сохраняю автоматически`, 'warn', { timeout: 5000 });
      setDest('', { quiet: true });
      return submitLinks(extra);
    }
    toast(err.message, 'err', { timeout: 6000 });
  } finally { go.disabled = false; }
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
  state.preview = p;
  state.range = null;
  if (!p) {
    pvAbort?.abort();
    applyHeights(null);
    const card = slot.firstElementChild;
    if (card) exit(card, { dy: -6, scale: 0.97 }).then(() => { if (!state.preview && card.isConnected) card.remove(); });
    updateTuners(); updateControls();
    return;
  }
  let card;
  if (p.kind === 'multi') { applyHeights(null); card = h(`<div class="preview compact">${svgUse('#i-layers')}<span><b>${p.n} ${plural(p.n, 'ссылка', 'ссылки', 'ссылок')}</b> — превью покажу для одной, а скачаю все сразу</span></div>`); }
  else if (p.kind === 'loading') card = h('<div class="preview loading" aria-busy="true"><div class="pv-media skel"></div><div class="pv-body"><div class="skel skel-line" style="width:82%;height:18px"></div><div class="skel skel-line" style="width:48%"></div><div class="skel" style="height:46px;margin-top:auto;border-radius:12px"></div></div></div>');
  else if (p.kind === 'error') { applyHeights(null); card = h(`<div class="preview compact error">${svgUse('#i-warn')}<span><b>Превью не загрузилось.</b> ${esc(p.message)} Скачать всё равно можно — жмите стрелку.</span></div>`); }
  else card = previewCard(p.data);
  slot.replaceChildren(card);
  if (!REDUCED) {
    const m = motionOf(card, { origin: 'top center' });
    if (p.kind === 'ready' && prevKind === 'loading') { m.from({ o: 0.4, b: 14, s: 0.99 }); m.to({ o: 1, b: 0, s: 1 }, { response: 0.7, damping: 0.95 }); }
    else if (p.kind !== prevKind) { m.from({ o: 0, y: -8, s: 0.97, b: 8 }); m.to({ o: 1, y: 0, s: 1, b: 0 }, { response: 0.55, damping: 0.85 }); }
  }
  updateTuners(); updateControls();
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
    <div class="pv-media">${thumb}${video}<span class="pv-badge pf-${esc(d.platform)}">${svgUse(glyph(d.platform))}</span>${dur ? `<span class="pv-dur">${fmtTime(dur)}</span>` : ''}${video ? `<button class="pv-play" type="button" aria-label="Смотреть превью">${svgUse('#i-play')}</button>` : ''}</div>
    <div class="pv-body"><h3 class="pv-title"></h3><p class="pv-meta">${chips.join('')}</p>
      ${dur ? `<div class="range" style="--a:0%;--b:100%"><div class="range-ticks"></div><div class="range-track${d.thumbnail ? '' : ' no-strip'}"></div><div class="range-shade l"></div><div class="range-shade r"></div><div class="range-sel"></div><div class="range-playhead"></div>
        <div class="range-handle a" role="slider" tabindex="0" aria-label="Начало отрезка"><span class="range-bubble"></span></div><div class="range-handle b" role="slider" tabindex="0" aria-label="Конец отрезка"><span class="range-bubble"></span></div></div>
      <div class="range-legend"><label class="time-field glass"><span>с</span><input class="pv-start" inputmode="decimal" placeholder="0:00" aria-label="Начало отрезка"></label><label class="time-field glass"><span>по</span><input class="pv-end" inputmode="decimal" placeholder="${fmtTime(dur)}" aria-label="Конец отрезка"></label><output class="clip-len"></output><button class="range-reset" type="button" hidden>Весь ролик</button></div>` : ''}
    </div></div>`);
  card.querySelector('.pv-title').textContent = d.title || d.url;
  card.querySelector('.pv-title').title = d.title || '';
  const img = card.querySelector('.pv-media img');
  if (img) {
    img.addEventListener('load', () => { if (img.naturalHeight > img.naturalWidth * 1.1) card.classList.add('portrait'); const track = card.querySelector('.range-track'); if (track) track.style.setProperty('--strip', `url("${d.thumbnail.replace(/"/g, '%22')}")`); });
    img.addEventListener('error', () => { img.replaceWith(h(artHtml(d.url, PLATFORM[d.platform] || ''))); card.querySelector('.range-track')?.classList.add('no-strip'); });
  }
  if (dur) initRange(card, dur);
  return card;
}
function initRange(card, d) {
  state.range = { a: 0, b: d, d };
  const range = card.querySelector('.range');
  const ha = range.querySelector('.range-handle.a'), hb = range.querySelector('.range-handle.b');
  const inA = card.querySelector('.pv-start'), inB = card.querySelector('.pv-end');
  const out = card.querySelector('.clip-len'), reset = card.querySelector('.range-reset');
  const media = card.querySelector('.pv-media'), video = media.querySelector('video');
  const minGap = Math.min(1, d / 50);
  let videoOk = false;
  const render = (skipInput) => {
    const { a, b } = state.range;
    range.style.setProperty('--a', `${(a / d) * 100}%`); range.style.setProperty('--b', `${(b / d) * 100}%`);
    ha.querySelector('.range-bubble').textContent = fmtTime(a); hb.querySelector('.range-bubble').textContent = fmtTime(b);
    for (const [el, v, label] of [[ha, a, 'Начало'], [hb, b, 'Конец']]) { el.setAttribute('aria-valuemin', '0'); el.setAttribute('aria-valuemax', String(Math.round(d))); el.setAttribute('aria-valuenow', String(Math.round(v))); el.setAttribute('aria-valuetext', `${label}: ${fmtTime(v)}`); }
    if (skipInput !== inA) inA.value = a > 0.5 ? fmtTime(a) : '';
    if (skipInput !== inB) inB.value = b < d - 0.5 ? fmtTime(b) : '';
    inA.parentElement.classList.remove('invalid'); inB.parentElement.classList.remove('invalid');
    const full = a <= 0.5 && b >= d - 0.5;
    out.textContent = full ? 'весь ролик' : `= ${fmtTime(b - a)}`;
    reset.hidden = full;
  };
  const seek = (t) => { if (!videoOk) return; cancelAnimationFrame(seek.raf); seek.raf = requestAnimationFrame(() => { try { if (video.fastSeek) video.fastSeek(t); else video.currentTime = t; } catch { /* */ } range.style.setProperty('--ph', `${(t / d) * 100}%`); range.classList.add('has-playhead'); }); };
  const setA = (t) => { state.range.a = Math.max(0, Math.min(t, state.range.b - minGap)); };
  const setB = (t) => { state.range.b = Math.min(d, Math.max(t, state.range.a + minGap)); };
  const sa = new Spring({ response: 0.5, damping: 1, epsilon: 0.01 }), sb = new Spring({ response: 0.5, damping: 1, epsilon: 0.01 });
  sa.onUpdate = (v) => { setA(v); render(); }; sb.onUpdate = (v) => { setB(v); render(); };
  let drag = null, lastT = 0, vel = 0, over = 0, lastRaw = 0;
  const timeAt = (clientX) => { const r = range.getBoundingClientRect(); return ((clientX - r.left) / r.width) * d; };
  range.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const raw = timeAt(e.clientX), t = Math.max(0, Math.min(d, raw));
    const handle = e.target.closest('.range-handle') || (Math.abs(t - state.range.a) <= Math.abs(t - state.range.b) ? ha : hb);
    drag = handle; handle.classList.add('dragging');
    range.setPointerCapture(e.pointerId);
    (handle === ha ? sa : sb).stop();
    lastT = performance.now(); vel = 0; over = 0; lastRaw = raw;
    if (!e.target.closest('.range-handle')) { (handle === ha ? setA : setB)(t); render(); seek(handle === ha ? state.range.a : state.range.b); }
    video?.pause();
    e.preventDefault();
  });
  range.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const raw = timeAt(e.clientX), now = performance.now(), dt = Math.max(1, now - lastT);
    vel = ((raw - lastRaw) / dt) * 1000 * 0.5 + vel * 0.5; lastRaw = raw; lastT = now;
    over = raw < 0 ? rubber(raw, d * 0.08) : raw > d ? rubber(raw - d, d * 0.08) : 0;
    const t = Math.max(0, Math.min(d, raw));
    if (drag === ha) setA(t); else setB(t);
    render();
    seek(drag === ha ? state.range.a : state.range.b);
  });
  const end = () => {
    if (!drag) return;
    drag.classList.remove('dragging');
    const s = drag === ha ? sa : sb, cur = drag === ha ? state.range.a : state.range.b;
    s.snap(cur);
    if (Math.abs(vel) > d * 0.15 || over) s.set(Math.max(0, Math.min(d, cur + vel * 0.12)), { velocity: vel * 0.5 });
    drag = null; over = 0;
  };
  range.addEventListener('pointerup', end); range.addEventListener('pointercancel', end);
  for (const [el, s, key] of [[ha, sa, 'a'], [hb, sb, 'b']]) {
    el.addEventListener('keydown', (e) => {
      const step = e.shiftKey ? 10 : 1;
      let t = state.range[key];
      if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') t -= step; else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') t += step; else if (e.key === 'Home') t = 0; else if (e.key === 'End') t = d; else return;
      e.preventDefault();
      s.snap(state.range[key]); s.set(Math.max(0, Math.min(d, t)));
      seek(t);
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
  reset.addEventListener('click', () => { sa.snap(state.range.a); sb.snap(state.range.b); sa.set(0); sb.set(d); });
  render();
  if (video) {
    video.addEventListener('loadeddata', () => { videoOk = true; media.classList.add('video-ready'); }, { once: true });
    video.addEventListener('error', () => { videoOk = false; video.remove(); media.querySelector('.pv-play')?.remove(); });
    video.addEventListener('timeupdate', () => { if (video.paused) return; const { a, b } = state.range; if (video.currentTime >= b - 0.05) video.currentTime = a; range.style.setProperty('--ph', `${(video.currentTime / d) * 100}%`); range.classList.add('has-playhead'); });
    video.addEventListener('play', () => media.classList.add('playing'));
    video.addEventListener('pause', () => media.classList.remove('playing'));
    media.addEventListener('click', () => { if (!videoOk) return; if (video.paused) { const { a, b } = state.range; if (video.currentTime < a || video.currentTime >= b - 0.1) video.currentTime = a; video.play().catch(() => {}); } else video.pause(); });
  }
}

/* ============================== конвертер ============================== */

const fileInput = $('#file-input');
fileInput.addEventListener('change', () => { if (fileInput.files.length) uploadFiles([...fileInput.files]); fileInput.value = ''; });
function uploadFiles(files, folder = null) {
  let clip;
  try { clip = clipPayload(false); } catch (err) { toast(err.message, 'err'); return; }
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.name);
  fd.append('mode', state.mode);
  fd.append('bitrate', state.bitrate);
  fd.append('auto_accept', state.autoAccept ? 'true' : 'false');
  if (clip.start) fd.append('start', clip.start);
  if (clip.end) fd.append('end', clip.end);
  const target = folder ?? (state.dest || null);
  if (target) fd.append('folder', target);
  const total = files.reduce((s, f) => s + f.size, 0);
  const t = toast(`Загружаю ${files.length > 1 ? `${files.length} ${plural(files.length, 'файл', 'файла', 'файлов')}` : `«${files[0].name}»`}…`, 'info', { timeout: 0 });
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/convert');
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) t.update(`Загружаю… ${Math.round((e.loaded / e.total) * 100)}% из ${fmtBytes(total)}`); };
  xhr.onload = () => {
    let data = null;
    try { data = JSON.parse(xhr.responseText); } catch { /* */ }
    if (xhr.status >= 200 && xhr.status < 300) { t.type('ok').update(files.length > 1 ? `В очереди на конвертацию: ${files.length}` : 'Файл в очереди на конвертацию').later(2600); pollJobs(); }
    else t.type('err').update(detailText(data) || `Ошибка ${xhr.status}`).later(6000);
  };
  xhr.onerror = () => { t.type('err').update('Сервер не отвечает — файл не загружен').later(6000); setServerDown(true); };
  xhr.send(fd);
}
let dragDepth = 0;
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
window.addEventListener('dragenter', (e) => { if (!hasFiles(e)) return; dragDepth++; $('#dragveil').hidden = false; });
window.addEventListener('dragleave', (e) => { if (!hasFiles(e)) return; dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) { $('#dragveil').hidden = true; explorer.clearDropHighlight(); } });
window.addEventListener('dragover', (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  const folder = explorer.highlightDrop(e.clientX, e.clientY);
  $('#dragveil-sub').textContent = folder != null ? `в папку «${folder.split('/').pop() || 'Хранилище'}»` : (state.dest ? `в «${state.dest.split('/').pop()}»` : '');
  $('#dragveil').style.opacity = folder != null ? '0' : '';
});
window.addEventListener('drop', (e) => {
  if (!hasFiles(e)) { const text = e.dataTransfer?.getData('text'); if (text && e.target !== urlBox && acceptPastedText(text)) e.preventDefault(); return; }
  e.preventDefault();
  dragDepth = 0;
  $('#dragveil').hidden = true; $('#dragveil').style.opacity = '';
  const folder = explorer.folderAt(e.clientX, e.clientY);
  explorer.clearDropHighlight();
  const files = [...e.dataTransfer.files];
  if (!files.length) return;
  if (folder == null && state.tab !== 'file') switchTab('file');
  uploadFiles(files, folder);
});

/* ============================== очередь ============================== */

const jobEls = new Map();
let pollTimer = 0, lastOrder = '', seenDone = null, pollBusy = false;
async function pollJobs() {
  clearTimeout(pollTimer);
  if (pollBusy) { pollTimer = setTimeout(pollJobs, 300); return; }
  pollBusy = true;
  let jobs = null;
  try { jobs = await api('/api/jobs'); } catch { /* баннер покажет api() */ }
  pollBusy = false;
  if (jobs) onJobs(jobs);
  const active = (jobs || state.jobs).some((j) => ACTIVE.has(j.status) && j.status !== 'waiting');
  pollTimer = setTimeout(pollJobs, active ? 700 : (events.ok ? 15000 : 5000));
}
function onJobs(jobs) {
  const prev = new Map(state.jobs.map((j) => [j.id, j.status]));
  state.jobs = jobs;
  if (seenDone === null) seenDone = new Set(jobs.filter((j) => !ACTIVE.has(j.status)).map((j) => j.id));
  let finished = null;
  for (const j of jobs) {
    if (j.status === 'done' && !seenDone.has(j.id)) { seenDone.add(j.id); if (prev.has(j.id) || !finished) finished = j; }
    else if (!ACTIVE.has(j.status)) seenDone.add(j.id);
  }
  renderJobs(jobs);
  if (!islandDemo) updateIsland(jobs, finished);
  state.speed = jobs.filter((j) => j.status === 'downloading').reduce((s, j) => s + (j.speed || 0), 0);
  if (finished) explorer.refresh();
  if (!jobs.some((j) => ACTIVE.has(j.status))) portal.classList.remove('busy');
  updateReadout();
}
function renderJobs(jobs) {
  const list = $('#jobs'), section = $('#queue');
  if (section.hidden && jobs.length) { section.hidden = false; section.querySelector('.rv')?.classList.add('in'); enter(section, { dy: 16, scale: 0.985, blur: 6, response: 0.7, damping: 0.95 }); }
  const ids = new Set(jobs.map((j) => j.id));
  const removed = [];
  for (const [id, el] of jobEls) if (!ids.has(id)) { removed.push(el); jobEls.delete(id); clearInterval(countdowns.get(id)); }
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
  if (order !== lastOrder || removed.length) flip(list, mutate, removed); else mutate();
  lastOrder = order;
  if (!jobs.length && !removed.length) section.hidden = true;
  else if (!jobs.length) setTimeout(() => { if (!state.jobs.length) section.hidden = true; }, 420);
  const active = jobs.filter((j) => ACTIVE.has(j.status));
  const waiting = jobs.filter((j) => j.status === 'waiting');
  const known = active.filter((j) => j.progress != null && j.status !== 'waiting');
  document.title = waiting.length ? '(?) выдра' : active.length ? `(${known.length ? Math.round(known.reduce((s, j) => s + j.progress, 0) / known.length) : '…'}${known.length ? '%' : ''}) выдра` : 'выдра';
  const done = jobs.filter((j) => j.status === 'done').length;
  $('#queue-readout').textContent = [active.length ? `активно ${active.length}` : '', waiting.length ? `ждут ответа ${waiting.length}` : '', done ? `готово ${done}` : ''].filter(Boolean).join(' · ');
  $('#clear-jobs').hidden = !jobs.some((j) => !ACTIVE.has(j.status));
}
function createJobEl(j) {
  const el = $('#job-tpl').content.firstElementChild.cloneNode(true);
  el.dataset.id = j.id;
  el.querySelector('.job-bar').append(h('<div class="job-glow"></div>'));
  el.querySelector('.job-cancel').addEventListener('click', async () => { try { await api(`/api/jobs/${j.id}/cancel`, { method: 'POST' }); pollJobs(); } catch (err) { toast(err.message, 'err'); } });
  el.querySelector('.job-retry').addEventListener('click', async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try { await api(`/api/jobs/${j.id}/retry`, { method: 'POST' }); toast('Пробую ещё раз', 'info', { timeout: 2000 }); pollJobs(); }
    catch (err) { toast(err.status === 404 ? 'Повтор появится после обновления выдры' : err.message, err.status === 404 ? 'warn' : 'err'); }
    finally { b.disabled = false; }
  });
  return el;
}
function jobMeta(j) {
  const chips = [`<span class="meta-chip">${svgUse(glyph(j.platform))}${PLATFORM[j.platform] || 'Сайт'}</span>`];
  const mode = j.mode === 'mp3' ? `MP3 · ${j.bitrate}` : j.mode === 'both' ? 'MP4 + MP3' : 'MP4';
  const q = j.kind === 'url' && j.mode !== 'mp3' ? (j.quality === 'max' ? ' · макс' : ` · ${j.quality}p`) : '';
  chips.push(`<span class="meta-chip mono">${mode}${q}</span>`);
  if (j.clip) { const [s, e] = j.clip; const label = e == null ? `с ${fmtTime(s)}` : (s ? `${fmtTime(s)}–${fmtTime(e)}` : `до ${fmtTime(e)}`); chips.push(`<span class="meta-chip accent mono">${svgUse('#i-scissors')}${label}</span>`); }
  if (j.count > 1) chips.push(`<span class="meta-chip mono">${j.index}/${j.count}</span>`);
  if (j.uploader) chips.push(`<span class="meta-chip">${esc(j.uploader)}</span>`);
  if (j.folder) chips.push(`<span class="meta-chip">${svgUse('#i-folder')}${esc(String(j.folder).split('/').pop())}</span>`);
  return chips.join('');
}
function jobTitle(j) {
  if (j.title) return j.title;
  if (j.kind === 'file') return j.source;
  try { const u = new URL(j.source); return `${u.hostname.replace(/^www\./, '')}${u.pathname.length > 1 ? u.pathname : ''}${u.search}`; } catch { return j.source; }
}
/** Что делать с ошибкой: понятная кнопка рядом с текстом. */
function errorAction(text) {
  if (/cookie|войти|вход|18\+|бот|login|sign in|private|закрыт/i.test(text)) return { label: 'Загрузить cookies', run: () => openSettings('cookies') };
  if (/ffmpeg|yt-dlp|deno|node|обнов|не найден|not found/i.test(text)) return { label: 'Проверить систему', run: () => { openDrawer(healthDlg); runDoctor(); } };
  if (/сет|network|timeout|соедин|connection|resolve/i.test(text)) return { label: 'Проверить связь', run: () => { openDrawer(healthDlg); runDoctor(); } };
  return null;
}
const countdowns = new Map();
const seenQuestions = new Set();
function updateJobEl(el, j) {
  const active = ACTIVE.has(j.status);
  const indet = active && j.status !== 'waiting' && (j.progress == null || j.status === 'queued' || j.status === 'saving');
  el.className = `job st-${j.status}${active ? ' active' : ''}${indet ? ' indet' : ''}`;
  el.dataset.p = j.platform;
  const badge = el.querySelector('.job-badge');
  if (badge.dataset.p !== j.platform) { badge.dataset.p = j.platform; badge.className = `job-badge pf-${j.platform}`; badge.innerHTML = svgUse(glyph(j.platform)); el.querySelector('.job-thumb-icon use').setAttribute('href', glyph(j.platform)); }
  const title = jobTitle(j);
  const t = el.querySelector('.job-title');
  if (t.textContent !== title) { t.textContent = title; t.title = title; }
  const meta = jobMeta(j);
  const metaEl = el.querySelector('.job-meta');
  if (metaEl.dataset.sig !== meta) { metaEl.dataset.sig = meta; metaEl.innerHTML = meta; }
  const p = j.status === 'done' ? 1 : (j.progress ?? 0) / 100;
  el.querySelector('.job-bar').style.setProperty('--p', String(Math.max(0, Math.min(1, p))));
  const stage = el.querySelector('.job-stage');
  clearInterval(countdowns.get(j.id));
  if (j.retry_at && j.status === 'queued') {
    const tick = () => {
      const left = Math.max(0, Math.ceil(j.retry_at - Date.now() / 1000));
      const attempt = j.attempt && j.max_attempts ? ` (попытка ${Math.min(j.attempt + 1, j.max_attempts)} из ${j.max_attempts})` : '';
      stage.innerHTML = `Повтор через <span class="countdown">${left} с</span>${attempt}`;
      if (!left) { clearInterval(countdowns.get(j.id)); pollJobs(); }
    };
    tick(); countdowns.set(j.id, setInterval(tick, 1000));
  } else stage.textContent = j.status === 'waiting' ? (j.question?.title || 'Нужно ваше решение') : (j.count > 1 && active ? `${j.stage} · ${j.index} из ${j.count}` : j.stage);
  let nums = '';
  if (j.status === 'downloading' && j.progress != null) nums = [`${Math.floor(j.progress)}%`, j.speed ? `${fmtBytes(j.speed)}/с` : '', j.eta != null ? `ещё ${fmtTime(j.eta)}` : ''].filter(Boolean).join(' · ');
  else if (j.status === 'converting' && j.progress != null) nums = `${Math.floor(j.progress)}%`;
  else if (j.status === 'done') nums = fmtBytes((j.files || []).reduce((s, f) => s + (f.size || 0), 0));
  else if (j.attempt > 1 && active) nums = `попытка ${j.attempt}${j.max_attempts ? ` из ${j.max_attempts}` : ''}`;
  el.querySelector('.job-nums').textContent = nums;
  // заметки: «формат 251 недоступен — взял 140» и т. п.
  const notes = el.querySelector('.job-notes');
  const notesSig = (j.notes || []).join('|');
  if (notes.dataset.sig !== notesSig) { notes.dataset.sig = notesSig; notes.hidden = !j.notes?.length; notes.replaceChildren(...(j.notes || []).map((n) => { const li = document.createElement('li'); li.textContent = n; return li; })); }
  renderAsk(el, j);
  const note = el.querySelector('.job-note');
  const noteText = j.error || j.warning || (j.resumed && active ? 'Продолжено после перезапуска выдры' : '');
  if (note.dataset.text !== noteText) {
    note.dataset.text = noteText;
    note.hidden = !noteText;
    note.className = `job-note ${j.error ? 'err' : j.warning ? 'warn' : 'info'}`;
    note.innerHTML = noteText ? `${svgUse(j.error ? '#i-alert' : j.warning ? '#i-warn' : '#i-info')}<span>${esc(noteText)}</span>` : '';
    const act = j.error ? errorAction(j.error) : null;
    if (act) { const b = h('<button class="glass btn" type="button"></button>'); b.textContent = act.label; b.addEventListener('click', act.run); note.append(b); }
  }
  el.querySelector('.job-retry').hidden = !(j.status === 'error' || j.status === 'cancelled');
  if (j.thumb && !el.querySelector('.job-thumb img')) { const img = new Image(); img.alt = ''; img.decoding = 'async'; img.onload = () => el.querySelector('.job-thumb').append(img); img.src = `/api/jobs/${j.id}/thumbnail`; }
  const files = el.querySelector('.job-files');
  const sig = (j.files || []).map((f) => `${f.id || f.name}:${f.display_path || ''}`).join('|') + `|${state.info?.library?.path || ''}`;
  if (files.dataset.sig !== sig) { files.dataset.sig = sig; files.replaceChildren(...(j.files || []).map(fileRow)); }
}
/** Карточка решения: что не вышло, какой есть вариант, продолжить? */
function renderAsk(el, j) {
  const box = el.querySelector('.job-ask');
  const q = j.status === 'waiting' ? j.question : null;
  if (!q) { if (!box.hidden) { box.hidden = true; box.dataset.qid = ''; box.replaceChildren(); } return; }
  const qid = String(q.id ?? q.code ?? j.id);
  if (box.dataset.qid === qid && !box.hidden) return;
  box.dataset.qid = qid;
  box.hidden = false;
  box.tabIndex = -1;
  box.setAttribute('role', 'group');
  box.setAttribute('aria-label', q.title || 'Нужно решение');
  const options = q.options || [];
  const primary = options.find((o) => o.primary) || options.find((o) => o.id === q.default) || options[0];
  const secondary = options.find((o) => o !== primary && !o.primary) || null;
  box.innerHTML = `<div class="ask-head">${svgUse('#i-question')}<b></b></div><p class="ask-msg"></p><div class="ask-options"></div>${primary?.hint ? '<small class="ask-hint"></small>' : ''}`;
  box.querySelector('.ask-head b').textContent = q.title || 'Так не выходит — есть другой вариант';
  box.querySelector('.ask-msg').textContent = q.message || '';
  if (primary?.hint) box.querySelector('.ask-hint').textContent = primary.hint;
  const opts = box.querySelector('.ask-options');
  for (const o of options) {
    const b = h(`<button class="${o === primary ? 'btn primary' : 'glass btn'}" type="button" data-opt="${esc(o.id)}"></button>`);
    b.textContent = o.label;
    if (o.hint) b.title = o.hint;
    b.addEventListener('click', () => answer(j.id, o.id, box));
    opts.append(b);
  }
  box.onkeydown = (e) => {
    if (e.key === 'Escape' && secondary) { e.preventDefault(); e.stopPropagation(); answer(j.id, secondary.id, box); }
    else if (e.key === 'Enter' && primary && !e.target.closest('button')) { e.preventDefault(); answer(j.id, primary.id, box); }
  };
  if (!REDUCED) enter(box, { dy: 8, scale: 0.98, blur: 5, response: 0.5, damping: 0.85 });
  const focusedTyping = document.activeElement?.closest?.('input, textarea, dialog');
  if (!seenQuestions.has(qid)) { seenQuestions.add(qid); if (!focusedTyping && primary) setTimeout(() => opts.querySelector('.primary')?.focus({ preventScroll: true }), 80); }
}
async function answer(jobId, option, box) {
  const btns = [...box.querySelectorAll('button')];
  btns.forEach((b) => { b.disabled = true; });
  const chosen = btns.find((b) => b.dataset.opt === option);
  if (chosen) chosen.classList.add('busy');
  try {
    await api(`/api/jobs/${jobId}/answer`, { method: 'POST', body: { option } });
    toast('Продолжаю', 'ok', { timeout: 1600 });
    box.hidden = true; box.dataset.qid = '';
    pollJobs();
  } catch (err) {
    btns.forEach((b) => { b.disabled = false; b.classList.remove('busy'); });
    toast(err.status === 404 ? 'Ответы на вопросы появятся после обновления выдры' : err.message, err.status === 404 ? 'warn' : 'err');
  }
}
/** Готовый файл: где лежит + Смотреть / Открыть / Показать в папке. */
function fileRow(f) {
  const isAudio = f.type === 'mp3';
  const row = h(`<div class="job-file">
    <span class="job-file-kind">${svgUse(isAudio ? '#i-wave' : '#i-film')}${esc(String(f.type || '').toUpperCase())} · ${fmtBytes(f.size)}</span>
    ${f.path ? whereHtml(f.path, f.display_path) : ''}
    <span class="job-file-actions">
      <button class="glass btn" type="button" data-a="play" title="${isAudio ? 'Слушать прямо здесь' : 'Смотреть прямо здесь'}">${svgUse('#i-play')}<span>${isAudio ? 'Слушать' : 'Смотреть'}</span></button>
      <button class="glass btn" type="button" data-a="open" title="Открыть в вашем плеере">${svgUse('#i-external')}<span>Открыть</span></button>
      <button class="glass btn" type="button" data-a="reveal" title="Показать файл в Проводнике">${svgUse('#i-folder')}<span>Показать в папке</span></button>
    </span></div>`);
  row.addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-a]');
    if (!b || !f.id) return;
    if (b.dataset.a === 'play') {
      let item = state.library?.items.find((i) => i.id === f.id);
      if (!item) { state.library = await explorer.loadLibrary(); item = state.library?.items.find((i) => i.id === f.id); }
      if (item) openPlayer(item, $(`.tile[data-id="${CSS.escape(f.id)}"] .tile-media`)); else toast('Файл ещё индексируется — секунду', 'info');
      return;
    }
    fileAction(f.id, b.dataset.a);
  });
  return row;
}
async function fileAction(id, act) {
  const t = toast(act === 'reveal' ? 'Показываю в папке…' : 'Открываю в плеере…', 'info', { timeout: 2200 });
  try { await api(`/api/library/${encodeURIComponent(id)}/${act}`, { method: 'POST' }); }
  catch (err) { t.type('err').update(err.message || (act === 'reveal' ? 'Не получилось показать файл' : 'Не получилось открыть файл')).later(5000); }
}
$('#clear-jobs').addEventListener('click', async () => { try { await api('/api/jobs/clear', { method: 'POST' }); pollJobs(); } catch (err) { toast(err.message, 'err'); } });

/* ============================== Dynamic Island ============================== */

const island = $('#island');
const islandExp = new Spring({ response: 0.5, damping: 0.8, epsilon: 0.002 });
const islandIdle = new Spring({ response: 0.45, damping: 0.95, epsilon: 0.002, value: 1 });
islandExp.onUpdate = (v) => island.style.setProperty('--exp', v.toFixed(4));
islandIdle.onUpdate = (v) => island.style.setProperty('--idle', v.toFixed(4));
let islandHover = false, islandPinned = false, doneTimer = 0;
const islandDemo = new URLSearchParams(location.search).get('island') === 'demo';
function setIslandState(s) {
  island.dataset.state = s;
  islandIdle.set(s === 'idle' ? 1 : 0);
  islandExp.set(s === 'expanded' ? 1 : 0);
  $('#island-hit').tabIndex = s === 'idle' ? -1 : 0;
  $('#island-hit').setAttribute('aria-label', s === 'ask' ? 'Нужен ваш ответ — открыть очередь' : s === 'done' ? 'Готово — открыть хранилище' : 'Статус загрузок');
}
function updateIsland(jobs, finished) {
  const waiting = jobs.find((j) => j.status === 'waiting');
  const active = jobs.filter((j) => ACTIVE.has(j.status) && j.status !== 'waiting');
  if (waiting && !active.some((j) => j.status === 'downloading' || j.status === 'converting')) { clearTimeout(doneTimer); setIslandState('ask'); return; }
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
    if (cur.thumb && thumb.dataset.id !== cur.id) { thumb.dataset.id = cur.id; thumb.innerHTML = `<img alt="" src="/api/jobs/${cur.id}/thumbnail">`; }
    else if (!cur.thumb && thumb.dataset.id !== `g-${cur.platform}`) { thumb.dataset.id = `g-${cur.platform}`; thumb.innerHTML = svgUse(glyph(cur.platform)); }
    setIslandState(islandHover || islandPinned ? 'expanded' : 'compact');
  } else if (finished) {
    islandPinned = false;
    $('#island-done-text').textContent = 'Готово';
    setIslandState('done');
    clearTimeout(doneTimer);
    doneTimer = setTimeout(() => { if (!state.jobs.some((j) => ACTIVE.has(j.status))) setIslandState('idle'); }, 2400);
  } else if (island.dataset.state !== 'done') setIslandState('idle');
}
const islandHit = $('#island-hit');
islandHit.addEventListener('pointerenter', (e) => { if (e.pointerType !== 'mouse') return; islandHover = true; if (island.dataset.state === 'compact') setIslandState('expanded'); });
islandHit.addEventListener('pointerleave', () => { islandHover = false; if (island.dataset.state === 'expanded' && !islandPinned) setIslandState('compact'); });
islandHit.addEventListener('click', () => {
  const s = island.dataset.state;
  if (s === 'expanded') { islandPinned = false; setIslandState('compact'); $('#queue').scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'start' }); }
  else if (s === 'compact') { islandPinned = true; setIslandState('expanded'); }
  else if (s === 'done') $('#library').scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'start' });
  else if (s === 'ask') { const w = state.jobs.find((j) => j.status === 'waiting'); const el = w && jobEls.get(w.id); if (el) { el.scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'center' }); setTimeout(() => el.querySelector('.job-ask .primary')?.focus({ preventScroll: true }), 500); } }
});
island.style.setProperty('--exp', '0'); island.style.setProperty('--idle', '1');

/* ============================== показания — только реальные данные ============================== */

function updateReadout() {
  const st = state.library?.stats;
  $('#ro-count').textContent = st ? String(st.count ?? 0) : '—';
  $('#ro-size').textContent = st ? fmtBytes(st.size || 0) : '—';
  const active = state.jobs.filter((j) => ACTIVE.has(j.status)).length;
  const a = $('#ro-active'); a.textContent = String(active); a.classList.toggle('live', active > 0);
  const s = $('#ro-speed'); s.textContent = state.speed ? `${fmtBytes(state.speed)}/с` : '—'; s.classList.toggle('live', state.speed > 0);
  if (st) $('#lib-readout').textContent = `${st.count ?? 0} ${plural(st.count ?? 0, 'файл', 'файла', 'файлов')} · ${fmtBytes(st.size || 0)}${st.videos != null ? ` · видео ${st.videos} · аудио ${st.audios ?? 0}` : ''}`;
}

/* ============================== плеер ============================== */

const player = $('#player');
let playerItem = null, playerOrigin = null;
function openPlayer(item, originEl) {
  playerItem = item; playerOrigin = originEl || null;
  const fill = () => { fillPlayer(item); if (!player.open) player.showModal(); };
  const src = originEl?.querySelector('img, .art');
  if (CAN_VT && src && !player.open) { src.style.viewTransitionName = 'player-media'; const t = document.startViewTransition(() => { src.style.viewTransitionName = ''; fill(); }); t.finished.catch(() => {}); }
  else { fill(); if (!REDUCED) { const m = motionOf(player.querySelector('.player-box')); m.from({ y: 20, s: 0.96, o: 0, b: 8 }); m.to({ y: 0, s: 1, o: 1, b: 0 }, { response: 0.55, damping: 0.88 }); } }
}
function fillPlayer(item) {
  const stage = $('#player-stage');
  const isAudio = item.type === 'audio';
  const media = libUrl(item.path);
  const poster = item.poster ? libUrl(item.poster) : '';
  if (isAudio) {
    stage.innerHTML = `<div class="audio-view">${poster ? `<div class="bg" style="background-image:url('${esc(poster)}')"></div>` : ''}<div class="audio-cover">${poster ? `<img src="${esc(poster)}" alt="">` : artHtml(item.id)}</div><div class="eq paused" aria-hidden="true">${'<i></i>'.repeat(18)}</div><audio controls autoplay preload="auto" src="${esc(media)}"></audio></div>`;
    const audio = stage.querySelector('audio'), eq = stage.querySelector('.eq');
    audio.addEventListener('play', () => eq.classList.remove('paused'));
    audio.addEventListener('pause', () => eq.classList.add('paused'));
  } else stage.innerHTML = `<video controls autoplay playsinline preload="auto" ${poster ? `poster="${esc(poster)}"` : ''} src="${esc(media)}"></video>`;
  $('#player-title').textContent = item.title;
  const chips = [`<span class="meta-chip">${svgUse(glyph(item.platform))}${PLATFORM[item.platform] || ''}</span>`];
  if (item.duration) chips.push(`<span class="meta-chip mono">${fmtTime(item.duration)}</span>`);
  if (item.width && item.height) chips.push(`<span class="meta-chip mono">${item.width}×${item.height}</span>`);
  chips.push(`<span class="meta-chip mono">${fmtBytes(item.size)}</span>`);
  if (item.added) chips.push(`<span class="meta-chip">${fmtAgo(item.added)}</span>`);
  if (item.uploader) chips.push(`<span class="meta-chip">${esc(item.uploader)}</span>`);
  $('#player-meta').innerHTML = chips.join('');
  $('#player-where').innerHTML = whereHtml(item.path, item.display_path);
  const dl = player.querySelector('[data-act="download"]');
  dl.href = media; dl.setAttribute('download', item.path.split('/').pop());
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
  if (CAN_VT && target && visible) { const t = document.startViewTransition(() => { player.close(); target.style.viewTransitionName = 'player-media'; }); await t.finished.catch(() => {}); target.style.viewTransitionName = ''; }
  else { player.classList.add('closing'); await exit(player.querySelector('.player-box'), { dy: 16, scale: 0.96 }); player.close(); player.classList.remove('closing'); motionOf(player.querySelector('.player-box')).from({ y: 0, s: 1, o: 1, b: 0 }); }
  stage.replaceChildren();
}
player.addEventListener('cancel', (e) => { e.preventDefault(); closePlayer(); });
player.addEventListener('click', (e) => { if (e.target === player || e.target.closest('[data-close]')) closePlayer(); });
player.querySelector('.player-actions').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-act]');
  if (!b || !playerItem) return;
  const act = b.dataset.act;
  if (act === 'open' || act === 'reveal') { fileAction(playerItem.id, act); return; }
  if (act === 'delete') { b.hidden = true; const c = player.querySelector('.confirm'); c.hidden = false; enter(c, { dy: 0, scale: 0.9, blur: 0, response: 0.4, damping: 0.75 }); return; }
  if (act === 'delete-no') { player.querySelector('.confirm').hidden = true; player.querySelector('[data-act="delete"]').hidden = false; return; }
  if (act === 'delete-yes') {
    try { await api(`/api/library/${encodeURIComponent(playerItem.id)}`, { method: 'DELETE' }); playerOrigin = null; await closePlayer(); toast('Файл перемещён в Корзину', 'ok'); explorer.refresh(); }
    catch (err) { toast(err.message, 'err'); }
  }
});
function attachHoverPreview(btn, it) {
  let timer = 0, video = null;
  btn.addEventListener('pointerenter', (e) => {
    if (e.pointerType !== 'mouse' || REDUCED) return;
    timer = setTimeout(() => {
      if (!video) { video = document.createElement('video'); video.muted = true; video.loop = true; video.playsInline = true; video.preload = 'auto'; video.addEventListener('playing', () => btn.classList.add('previewing')); btn.insertBefore(video, btn.querySelector('.tile-scrim')); }
      video.src = `${libUrl(it.path)}#t=${(it.duration ? Math.min(it.duration * 0.15, 20) : 0).toFixed(1)}`;
      video.play().catch(() => {});
    }, 420);
  });
  btn.addEventListener('pointerleave', () => { clearTimeout(timer); btn.classList.remove('previewing'); if (video) { video.pause(); video.removeAttribute('src'); video.load(); } });
}

/* ============================== шторки и модальные окна ============================== */

function openDrawer(dlg) {
  if (dlg.open) return;
  dlg.showModal();
  const box = dlg.querySelector('.drawer-box');
  const m = motionOf(box);
  if (REDUCED) { m.from({ x: 0, y: 0, o: 1 }); return; }
  if (isPhone()) { m.from({ y: box.offsetHeight || innerHeight, x: 0, o: 1 }); m.to({ y: 0 }, { response: 0.6, damping: 0.9 }); }
  else { m.from({ x: box.offsetWidth + 24, y: 0, o: 0.7 }); m.to({ x: 0, o: 1 }, { response: 0.6, damping: 0.88 }); }
}
async function closeDrawer(dlg, { velocity } = {}) {
  if (!dlg.open) return;
  const box = dlg.querySelector('.drawer-box');
  const m = motionOf(box);
  dlg.classList.add('closing');
  if (!REDUCED) { if (isPhone()) await m.to({ y: box.offsetHeight + 40 }, { response: 0.42, damping: 1, velocity: { y: velocity || 0 } }); else await m.to({ x: box.offsetWidth + 24, o: 0.6 }, { response: 0.36, damping: 1 }); }
  dlg.close(); dlg.classList.remove('closing');
  m.from({ x: 0, y: 0, o: 1 });
}
for (const dlg of $$('dialog.drawer')) {
  dlg.addEventListener('cancel', (e) => { e.preventDefault(); closeDrawer(dlg); });
  dlg.addEventListener('click', (e) => { if (e.target === dlg || e.target.closest('[data-close]')) closeDrawer(dlg); });
  const box = dlg.querySelector('.drawer-box'), handle = dlg.querySelector('.drawer-head');
  let startY = 0, lastY = 0, lastT = 0, v = 0, dragging = false;
  handle.addEventListener('pointerdown', (e) => { if (!isPhone() || e.target.closest('button')) return; dragging = true; startY = lastY = e.clientY; lastT = performance.now(); v = 0; motionOf(box).stop(); handle.setPointerCapture(e.pointerId); });
  handle.addEventListener('pointermove', (e) => { if (!dragging) return; const dy = e.clientY - startY, now = performance.now(); v = ((e.clientY - lastY) / Math.max(1, now - lastT)) * 1000 * 0.5 + v * 0.5; lastY = e.clientY; lastT = now; motionOf(box).from({ y: dy > 0 ? dy : rubber(dy, 80) }); });
  const release = (e) => { if (!dragging) return; dragging = false; const dy = e.clientY - startY; if (dy > 110 || v > 600) { closeDrawer(dlg, { velocity: v }); return; } motionOf(box).to({ y: 0 }, { response: 0.5, damping: 0.8, velocity: { y: v } }); };
  handle.addEventListener('pointerup', release); handle.addEventListener('pointercancel', release);
}
function openModal(dlg) {
  if (dlg.open) return;
  dlg.showModal();
  if (!REDUCED) { const m = motionOf(dlg.querySelector('.modal-box')); m.from({ y: isPhone() ? 40 : 14, s: 0.97, o: 0, b: 6 }); m.to({ y: 0, s: 1, o: 1, b: 0 }, { response: 0.45, damping: 0.86 }); }
}
async function closeModal(dlg) {
  if (!dlg.open) return;
  dlg.classList.add('closing');
  await exit(dlg.querySelector('.modal-box'), { dy: 10, scale: 0.97 });
  dlg.close(); dlg.classList.remove('closing');
  motionOf(dlg.querySelector('.modal-box')).from({ y: 0, s: 1, o: 1, b: 0 });
}
for (const dlg of $$('dialog.modal')) {
  dlg.addEventListener('cancel', (e) => { e.preventDefault(); closeModal(dlg); });
  dlg.addEventListener('click', (e) => { if (e.target === dlg || e.target.closest('[data-close]')) closeModal(dlg); });
}
/** Вопрос с одной или двумя кнопками; вернёт 'yes' | 'no' | null. */
function confirmDialog({ title, text, yes = 'Да', no = null, danger = false }) {
  const dlg = $('#confirm-dialog');
  const yesBtn = $('#confirm-yes'), noBtn = $('#confirm-no');
  $('#confirm-title').textContent = title; $('#confirm-text').textContent = text; yesBtn.textContent = yes;
  yesBtn.className = danger ? 'btn danger solid' : 'btn primary';
  noBtn.hidden = !no; if (no) noBtn.textContent = no;
  return new Promise((resolve) => {
    let result = null;
    const done = (v) => { result = v; closeModal(dlg); };
    const onYes = () => done('yes'), onNo = () => done('no');
    yesBtn.addEventListener('click', onYes); noBtn.addEventListener('click', onNo);
    dlg.addEventListener('close', () => { yesBtn.removeEventListener('click', onYes); noBtn.removeEventListener('click', onNo); resolve(result); }, { once: true });
    openModal(dlg);
    setTimeout(() => yesBtn.focus(), 30);
  });
}
/** Выбор папки из дерева; вернёт путь ('' — автоматически) или null. */
function pickFolder({ title, kicker, tree, current = null, allowAuto = false, selected = null, disabled = [] }) {
  const dlg = $('#folder-dialog');
  const box = $('#folder-tree'), btn = $('#folder-confirm');
  $('#folder-title').textContent = title; $('#folder-kicker').textContent = kicker || 'Папка';
  let chosen = selected;
  btn.disabled = chosen == null;
  const mark = () => { box.querySelectorAll('[aria-current]').forEach((x) => x.removeAttribute('aria-current')); box.querySelector(`[data-path="${CSS.escape(chosen ?? '\u0000')}"]`)?.setAttribute('aria-current', 'true'); };
  const node = (n, depth) => {
    const off = n.path === current || disabled.some((p) => n.path === p || n.path.startsWith(`${p}/`));
    const el = h(`<div class="tnode" role="treeitem" data-path="${esc(n.path)}"${n.platform ? ` data-platform="${esc(n.platform)}"` : ''}${off ? ' aria-disabled="true"' : ''}><span class="tw leaf">${svgUse('#i-chev-r')}</span>${svgUse(n.platform ? glyph(n.platform) : depth === 0 ? '#i-drive' : '#i-folder', 'ti')}<span class="tn"></span><span class="tc">${n.count || ''}</span></div>`);
    el.querySelector('.tn').textContent = n.name || 'Хранилище';
    el.style.paddingLeft = `${8 + depth * 14}px`;
    if (!off) el.addEventListener('click', () => { chosen = n.path; mark(); btn.disabled = false; });
    const frag = document.createDocumentFragment(); frag.append(el);
    for (const k of n.children || []) frag.append(node(k, depth + 1));
    return frag;
  };
  box.replaceChildren();
  if (allowAuto) { const auto = h(`<div class="tnode" role="treeitem" data-path=""><span class="tw leaf">${svgUse('#i-chev-r')}</span>${svgUse('#i-layers', 'ti')}<span class="tn">Автоматически — по платформе</span></div>`); auto.addEventListener('click', () => { chosen = ''; mark(); btn.disabled = false; }); box.append(auto); }
  for (const k of tree.children || []) box.append(node(k, 1));
  if (!allowAuto) box.prepend(node({ ...tree, children: [] }, 0));
  mark();
  return new Promise((resolve) => {
    let result = null;
    const done = () => { result = chosen; closeModal(dlg); };
    btn.addEventListener('click', done);
    dlg.addEventListener('close', () => { btn.removeEventListener('click', done); resolve(result); }, { once: true });
    openModal(dlg);
  });
}

/* ---------- настройки: папка для загрузок, cookies ---------- */

const settingsDlg = $('#settings');
$('#open-settings').addEventListener('click', () => openSettings());
async function openSettings(focusBlock) {
  openDrawer(settingsDlg);
  if (focusBlock === 'cookies') setTimeout(() => { const b = $('#cookies-block'); b.scrollIntoView({ block: 'center', behavior: REDUCED ? 'auto' : 'smooth' }); b.classList.remove('flash'); void b.offsetWidth; b.classList.add('flash'); }, 350);
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
  $('#path-input').placeholder = state.info?.os === 'mac' || state.info?.os === 'linux' ? '…или впишите путь: ~/Movies/выдра' : '…или впишите путь: D:\\Видео';
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
async function afterLibraryChanged(s, { oldCount, oldPath }) {
  renderSettings(s);
  loadInfo();
  explorer.navigate('', { force: true });
  const newPath = s.library?.path;
  if (oldCount > 0 && newPath && newPath !== oldPath) {
    const r = await confirmDialog({ title: 'Перенести уже скачанное?', text: `В прежней папке ${oldCount} ${plural(oldCount, 'файл', 'файла', 'файлов')}. Перенести их в новую папку, чтобы всё было в одном месте?`, yes: 'Перенести', no: 'Оставить' });
    if (r === 'yes') {
      const t = toast('Переношу файлы…', 'info', { timeout: 0 });
      try {
        await api('/api/settings/library', { method: 'POST', body: { path: newPath, move: true } });
        await explorer.navigate('', { force: true });
        const now = explorer.state.library?.stats?.count || 0;
        if (now >= oldCount) t.type('ok').update('Файлы перенесены в новую папку').later(3200);
        else t.type('warn').update('Перенос пока не поддерживается этой версией — файлы остались в прежней папке').later(6000);
      } catch (err) { t.type('err').update(`Перенести не получилось: ${err.message}`).later(6000); }
    }
  }
}
async function applyLibrary(body, okText) {
  showPathError('');
  const oldCount = state.library?.stats?.count || 0, oldPath = state.info?.library?.path;
  try {
    const s = await api('/api/settings/library', { method: 'POST', body });
    toast(okText, 'ok');
    await afterLibraryChanged(s, { oldCount, oldPath });
    return true;
  } catch (err) { showPathError(err.message); return false; }
}
$('#path-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const v = $('#path-input').value.trim();
  if (!v) { showPathError('Впишите путь к папке'); return; }
  if (await applyLibrary({ path: v }, 'Папка для загрузок изменена')) $('#path-input').value = '';
});
$('#reset-folder').addEventListener('click', () => applyLibrary({ reset: true }, 'Вернула папку по умолчанию'));
$('#pick-folder').addEventListener('click', async (e) => {
  const b = e.currentTarget, label = b.querySelector('span');
  b.disabled = true; b.classList.add('busy'); b.querySelector('use').setAttribute('href', '#i-refresh');
  label.textContent = 'Окно выбора папки открыто на компьютере…';
  showPathError('');
  const oldCount = state.library?.stats?.count || 0, oldPath = state.info?.library?.path;
  try {
    const r = await api('/api/settings/library/pick', { method: 'POST' });
    if (r?.cancelled) toast('Выбор папки отменён', 'info');
    else { toast('Папка для загрузок изменена', 'ok'); await afterLibraryChanged(r, { oldCount, oldPath }); }
  } catch (err) { showPathError(err.status === 501 ? 'На этой системе нет окна выбора — впишите путь ниже' : err.message); }
  finally { b.disabled = false; b.classList.remove('busy'); b.querySelector('use').setAttribute('href', '#i-folder-open'); label.textContent = 'Выбрать…'; }
});
const openFolder = async () => { const t = toast('Открываю папку загрузок…', 'info', { timeout: 2200 }); try { await api('/api/folder/open', { method: 'POST' }); } catch (err) { t.type('err').update(err.message).later(5000); } };
$('#open-folder').addEventListener('click', openFolder);
$('#open-folder-2').addEventListener('click', openFolder);
$('#cookie-input').addEventListener('change', async (e) => {
  const f = e.target.files[0]; e.target.value = '';
  if (!f) return;
  const fd = new FormData(); fd.append('file', f, f.name);
  try { renderSettings(await api('/api/settings/cookies', { method: 'POST', form: fd })); toast('Cookies подключены — можно повторить загрузку', 'ok'); loadInfo(); }
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
  if (!state.doctor) { $('#checks').innerHTML = Array.from({ length: 6 }, () => '<li class="check skeleton skel"></li>').join(''); $('#health-summary').innerHTML = '<span class="sum-chip">Проверяю…</span>'; $('#fix-all').disabled = true; }
  try { state.doctor = await api('/api/doctor'); renderHealth(); }
  catch (err) { if (healthDlg.open) toast(err.message, 'err'); }
  finally { doctorBusy = false; btn.classList.remove('busy'); btn.disabled = false; }
}
function summarize(checks) { const s = { ok: 0, warn: 0, fail: 0 }; for (const c of checks) s[c.status] = (s[c.status] || 0) + 1; return s; }
function renderHealth() {
  const d = state.doctor;
  if (!d) return;
  const s = d.summary || summarize(d.checks);
  $('#health-summary').innerHTML = [`<span class="sum-chip"><i style="background:var(--ok)"></i>в порядке: ${s.ok || 0}</span>`, s.warn ? `<span class="sum-chip"><i style="background:var(--warn)"></i>внимание: ${s.warn}</span>` : '', s.fail ? `<span class="sum-chip"><i style="background:var(--err)"></i>сломано: ${s.fail}</span>` : ''].join('');
  const fixable = d.checks.filter((c) => c.status !== 'ok' && c.fix);
  $('#fix-all').disabled = !fixable.length;
  $('#fix-all span').textContent = fixable.length ? `Починить всё (${fixable.length})` : 'Всё в порядке';
  const icon = { ok: '#i-check', warn: '#i-warn', fail: '#i-x' };
  const list = $('#checks');
  list.innerHTML = d.checks.map((c) => `<li class="check ${c.status}" data-id="${esc(c.id)}"><span class="check-icon">${svgUse(icon[c.status] || '#i-info')}</span><p class="check-title">${esc(c.title)}</p>${c.fix && c.status !== 'ok' ? `<button class="glass btn" type="button" data-fix="${esc(c.id)}">${svgUse('#i-wrench')}<span>${esc(c.fix)}</span></button>` : ''}<p class="check-detail">${esc(c.detail || '')}</p>${c.hint ? `<p class="check-hint">${esc(c.hint)}</p>` : ''}</li>`).join('');
  if (!REDUCED) [...list.children].forEach((li, i) => enter(li, { dy: 10, scale: 0.985, blur: 4, delay: i * 40 }));
  updateBadge();
}
$('#checks').addEventListener('click', (e) => { const b = e.target.closest('[data-fix]'); if (b) fix(b.dataset.fix, b); });
async function fix(id, btn) {
  const label = btn.querySelector('span'), old = label.textContent;
  btn.disabled = true; btn.classList.add('busy'); label.textContent = 'Чиню…';
  const allBtns = $$('#health [data-fix], #fix-all');
  allBtns.forEach((b) => { b.disabled = true; });
  try {
    const r = await api('/api/doctor/fix', { method: 'POST', body: { id } });
    for (const res of r.results || []) toast(res.message, res.ok ? 'ok' : 'err', { timeout: res.ok ? 4200 : 8000 });
    if (r.checks) { state.doctor = { checks: r.checks, summary: summarize(r.checks) }; renderHealth(); }
    loadInfo(); explorer.refresh();
  } catch (err) { toast(err.message, 'err', { timeout: 7000 }); label.textContent = old; }
  finally { btn.classList.remove('busy'); allBtns.forEach((b) => { if (b.isConnected) b.disabled = false; }); if (state.doctor) renderHealth(); }
}
function updateBadge() {
  const d = state.doctor, badge = $('#health-badge');
  if (!d) { badge.hidden = true; return; }
  const s = d.summary || summarize(d.checks);
  badge.hidden = !(s.warn || s.fail);
  badge.dataset.level = s.fail ? 'fail' : 'warn';
  $('#open-health').title = s.fail ? `Состояние системы: есть проблемы (${s.fail})` : s.warn ? 'Состояние системы: есть замечания' : 'Состояние системы: всё в порядке';
}

/* ============================== клавиши ============================== */

$('#open-cheats').addEventListener('click', () => openModal($('#cheats')));
document.addEventListener('keydown', (e) => {
  const typing = e.target.closest?.('input, textarea, select, [contenteditable]');
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && ['N', 'n', 'Т', 'т'].includes(e.key)) { e.preventDefault(); explorer.newFolder(); return; }
  if (typing) return;
  if (e.key === '?') { e.preventDefault(); openModal($('#cheats')); return; }
  if (e.key === '/') { e.preventDefault(); $('#lib-search').focus(); }
});

/* ============================== живые события с сервера ============================== */

const events = { es: null, ok: false };
let syncTimer = 0, refreshTimer = 0;
function blinkSync() { const el = $('#sync'); el.classList.add('on'); clearTimeout(syncTimer); syncTimer = setTimeout(() => el.classList.remove('on'), 2200); }
function connectEvents() {
  if (events.es || !('EventSource' in window)) return;
  try {
    const es = new EventSource('/api/events');
    events.es = es;
    es.addEventListener('open', () => { events.ok = true; });
    es.addEventListener('library', () => { clearTimeout(refreshTimer); refreshTimer = setTimeout(() => explorer.refresh().then(blinkSync), 250); });
    es.addEventListener('jobs', () => pollJobs());
    es.addEventListener('error', () => { if (es.readyState === EventSource.CLOSED) { es.close(); events.es = null; events.ok = false; } });
  } catch { events.es = null; }
}
let lastRev = null;
async function pollRev() {
  if (events.ok || document.hidden || state.down) return;
  try { const r = await api('/api/library/rev'); if (lastRev != null && r?.rev !== lastRev) explorer.refresh().then(blinkSync); lastRev = r?.rev ?? lastRev; }
  catch { /* старый сервер — без живой синхронизации */ }
}

/* ============================== общее ============================== */

async function loadInfo() {
  try { state.info = await api('/api/info'); } catch { return; }
  const i = state.info;
  $('#foot-meta').textContent = [i.version ? `выдра ${i.version}` : '', i.ytdlp ? `yt-dlp ${i.ytdlp}` : ''].filter(Boolean).join(' · ');
  if (i.library?.path) $('#foot-path').textContent = i.library.path;
  renderAbout();
  for (const [id, el] of jobEls) { const j = state.jobs.find((x) => x.id === id); if (j) updateJobEl(el, j); }
}
let scrollRaf = 0;
window.addEventListener('scroll', () => { cancelAnimationFrame(scrollRaf); scrollRaf = requestAnimationFrame(() => $('#topbar').classList.toggle('scrolled', scrollY > 8)); }, { passive: true });
window.addEventListener('resize', () => requestAnimationFrame(() => moveAllInks({ immediate: true })));
document.addEventListener('visibilitychange', () => { if (!document.hidden) { pollJobs(); explorer.refresh(); } });
function initReveal() {
  const all = $$('.rv');
  if (REDUCED) { all.forEach((el) => el.classList.add('in')); return; }
  $$('.hero .rv').forEach((el, i) => { el.style.setProperty('--i', el.dataset.i ?? i); requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('in'))); });
  const io = new IntersectionObserver((entries) => { for (const e of entries) if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }, { rootMargin: '0px 0px -8% 0px' });
  $$('.rv:not(.hero .rv)').forEach((el) => io.observe(el));
  setTimeout(() => all.forEach((el) => el.classList.add('in')), 2500);
}

const explorer = createExplorer({ $, api, toast, esc, h, svgUse, glyph, fmtBytes, fmtTime, fmtAgo, libUrl, openPlayer, confirmDialog, pickFolder, artHtml, attachHoverPreview, fileAction, onDest: (p) => setDest(p) });
explorer.on('change', () => { state.library = explorer.state.library; updateReadout(); if (!state.info?.library?.path && state.library?.root) $('#foot-path').textContent = state.library.root; });
explorer.on('tree', (tree) => {
  if (!state.dest) return;
  const exists = (n) => n.path === state.dest || (n.children || []).some(exists);
  if (!exists(tree)) { toast(`Папки «${state.dest.split('/').pop()}» больше нет — сохраняю автоматически`, 'warn', { timeout: 5000 }); setDest('', { quiet: true }); }
});
initPills($('.view-toggle'), (v) => explorer.setView(v));

function init() {
  initCosmos();
  setMode(state.mode, { save: false });
  initPills($('[data-name="mode"]'), (v) => setMode(v));
  initPills($('[data-name="quality"]'), (v) => { state.quality = v; savePrefs(); });
  initPills($('[data-name="bitrate"]'), (v) => { state.bitrate = v; savePrefs(); });
  setPill($('[data-name="quality"]'), state.quality);
  setPill($('[data-name="bitrate"]'), state.bitrate);
  setPill($('.view-toggle'), explorer.state.view);
  switchTab('link', { animate: false });
  renderClipRow();
  updateControls();
  renderDest();
  explorer.setDest(state.dest);
  requestAnimationFrame(() => moveAllInks({ immediate: true }));
  document.fonts?.ready.then(() => moveAllInks({ immediate: true }));
  initReveal();

  loadInfo();
  explorer.load('');
  pollJobs();
  connectEvents();
  setTimeout(runDoctor, 1500);
  setInterval(pollRev, 10000);
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});

  const q = new URLSearchParams(location.search);
  if (q.get('panel') === 'settings') openSettings();
  if (q.get('panel') === 'health') { openDrawer(healthDlg); runDoctor(); }
  if (q.get('tab') === 'file') switchTab('file', { animate: false });
  if (q.get('url')) { urlBox.value = q.get('url'); onUrlInput(); }
  if (q.get('folder') != null) explorer.navigate(q.get('folder'));
  if (q.get('island') === 'demo') {
    islandPinned = true;
    const demo = { id: 'demo', status: 'downloading', stage: 'Скачивание', progress: 62, speed: 4.2e6, eta: 7, platform: 'youtube', title: 'NASA Moon Base Update (Aug. 4, 2026)', thumb: false };
    const tick = () => updateIsland([demo, { ...demo, id: 'demo2', status: 'queued', progress: null }], null);
    tick(); setInterval(tick, 1000);
  }
  if (q.get('player') === 'first') {
    const wait = setInterval(() => { const first = explorer.state.items?.[0]; if (first) { clearInterval(wait); openPlayer(first, $(`.tile[data-id="${CSS.escape(first.id)}"] .tile-media`)); } }, 300);
    setTimeout(() => clearInterval(wait), 8000);
  }
}
init();
