// выдра — пружинная физика для интерфейса.
// Настоящая модель «масса–пружина–демпфер», интегрируется на каждом кадре. Параметры как у Apple:
// response (период, с) и dampingFraction (0..1). Любая пружина прерываема: новая цель подхватывает
// текущую скорость, поэтому движение никогда не «дёргается» при перенацеливании.

const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches || new URLSearchParams(location.search).get('motion') === '0';

const active = new Set();
let raf = 0;
let last = 0;

function tick(now) {
  raf = 0;
  const dt = Math.min(0.048, Math.max(0.001, (now - last) / 1000));
  last = now;
  for (const s of active) s._step(dt);
  if (active.size) raf = requestAnimationFrame(tick);
}
function schedule(s) {
  active.add(s);
  if (!raf) { last = performance.now(); raf = requestAnimationFrame(tick); }
}

/** Пружина одного числа. */
export class Spring {
  /**
   * @param {object} o
   * @param {number} [o.response=0.42] период, с (меньше — резче)
   * @param {number} [o.damping=0.86] доля критического демпфирования (1 — без отскока)
   * @param {number} [o.value=0] стартовое значение
   * @param {number} [o.epsilon=0.001] порог покоя (в единицах значения)
   * @param {(v:number)=>void} [o.onUpdate]
   * @param {()=>void} [o.onRest]
   */
  constructor(o = {}) {
    this.response = o.response ?? 0.42;
    this.damping = o.damping ?? 0.86;
    this.value = o.value ?? 0;
    this.target = this.value;
    this.velocity = 0;
    this.epsilon = o.epsilon ?? 0.001;
    this.onUpdate = o.onUpdate || null;
    this.onRest = o.onRest || null;
    this._resolvers = [];
  }
  get moving() { return active.has(this); }
  /** Новая цель; текущая скорость сохраняется (или задаётся явно — velocity handoff после жеста). */
  set(target, { velocity, immediate } = {}) {
    if (velocity != null) this.velocity = velocity;
    this.target = target;
    if (immediate || REDUCED) { this.snap(target); return this; }
    if (Math.abs(this.value - target) < this.epsilon && Math.abs(this.velocity) < this.epsilon) { this._settle(); return this; }
    schedule(this);
    return this;
  }
  snap(v) {
    this.value = this.target = v; this.velocity = 0;
    active.delete(this);
    this.onUpdate?.(v);
    this._settle();
    return this;
  }
  /** Промис, который разрешается, когда пружина остановится. */
  get settled() { return active.has(this) ? new Promise((r) => this._resolvers.push(r)) : Promise.resolve(); }
  stop() { active.delete(this); this.velocity = 0; return this; }
  retune(response, damping) { if (response) this.response = response; if (damping != null) this.damping = damping; return this; }
  _settle() { const rs = this._resolvers; this._resolvers = []; rs.forEach((r) => r()); this.onRest?.(); }
  _step(dt) {
    // жёсткость и демпфирование из response / dampingFraction (масса 1)
    const w = (2 * Math.PI) / this.response;
    const k = w * w, c = 2 * this.damping * w;
    // полунеявный Эйлер с подшагами: устойчиво и при резких пружинах
    const n = Math.max(1, Math.ceil(dt / 0.004));
    const h = dt / n;
    for (let i = 0; i < n; i++) {
      const a = -k * (this.value - this.target) - c * this.velocity;
      this.velocity += a * h;
      this.value += this.velocity * h;
    }
    const scale = Math.max(1, Math.abs(this.target) * 0.001);
    if (Math.abs(this.velocity) < this.epsilon * 12 * scale && Math.abs(this.value - this.target) < this.epsilon * scale) {
      this.value = this.target; this.velocity = 0;
      active.delete(this);
      this.onUpdate?.(this.value);
      this._settle();
      return;
    }
    this.onUpdate?.(this.value);
  }
}

/**
 * Набор пружин, пишущих в transform/opacity элемента. Все свойства — только transform/opacity/filter,
 * чтобы браузер не делал layout. Пример: springs.to({ x: 0, y: 0, s: 1, o: 1 }).
 */
export class Motion {
  constructor(el, { response = 0.42, damping = 0.86, origin } = {}) {
    this.el = el;
    this.props = {};
    this.defaults = { response, damping };
    this._dirty = false;
    this._blur = 0;
    if (origin) el.style.transformOrigin = origin;
  }
  _spring(name, init) {
    if (!this.props[name]) {
      const s = new Spring({ ...this.defaults, value: init, epsilon: name === 'o' || name === 's' || name === 'sx' || name === 'sy' ? 0.0015 : 0.05 });
      s.onUpdate = () => this._apply();
      this.props[name] = s;
    }
    return this.props[name];
  }
  /** Мгновенно выставить значения (без анимации). */
  from(values) {
    for (const [k, v] of Object.entries(values)) this._spring(k, v).snap(v);
    this._apply();
    return this;
  }
  /** Анимировать к значениям; opts.velocity — {x: px/s, ...}; opts.response/damping — переопределение. */
  to(values, opts = {}) {
    const ps = [];
    for (const [k, v] of Object.entries(values)) {
      const s = this._spring(k, k === 'o' || k === 's' || k === 'sx' || k === 'sy' ? 1 : 0);
      if (opts.response || opts.damping != null) s.retune(opts.response, opts.damping);
      s.set(v, { velocity: opts.velocity?.[k], immediate: opts.immediate });
      ps.push(s.settled);
    }
    return Promise.all(ps);
  }
  get(name) { return this.props[name]?.value ?? (name === 'o' || name === 's' ? 1 : 0); }
  velocity(name) { return this.props[name]?.velocity ?? 0; }
  stop() { for (const s of Object.values(this.props)) s.stop(); }
  _apply() {
    if (this._dirty) return;
    this._dirty = true;
    // собираем transform один раз за кадр даже если обновились несколько пружин
    queueMicrotask(() => {
      this._dirty = false;
      const p = this.props, st = this.el.style;
      const x = p.x?.value ?? 0, y = p.y?.value ?? 0;
      const s = p.s?.value ?? 1, sx = p.sx?.value ?? 1, sy = p.sy?.value ?? 1;
      const r = p.r?.value ?? 0;
      const parts = [];
      if (x || y) parts.push(`translate3d(${x.toFixed(2)}px, ${y.toFixed(2)}px, 0)`);
      if (r) parts.push(`rotate(${r.toFixed(2)}deg)`);
      if (s !== 1 || sx !== 1 || sy !== 1) parts.push(`scale(${(s * sx).toFixed(4)}, ${(s * sy).toFixed(4)})`);
      st.transform = parts.length ? parts.join(' ') : '';
      if (p.o) st.opacity = String(Math.max(0, Math.min(1, p.o.value)));
      if (p.b) { const b = Math.max(0, p.b.value); st.filter = b > 0.05 ? `blur(${b.toFixed(2)}px)` : ''; }
    });
  }
}

const motions = new WeakMap();
/** Единственный Motion на элемент — чтобы новые анимации подхватывали текущие скорости. */
export function motionOf(el, opts) {
  let m = motions.get(el);
  if (!m) { m = new Motion(el, opts); motions.set(el, m); }
  return m;
}

/** Появление: снизу, размытым, прозрачным → на место. */
export function enter(el, { dy = 14, scale = 0.96, blur = 8, delay = 0, response = 0.6, damping = 0.9 } = {}) {
  if (REDUCED) return Promise.resolve();
  const m = motionOf(el);
  m.from({ y: dy, s: scale, o: 0, b: blur });
  const go = () => m.to({ y: 0, s: 1, o: 1, b: 0 }, { response, damping });
  if (delay) return new Promise((r) => setTimeout(() => go().then(r), delay));
  return go();
}
/** Исчезновение: чуть вниз/вверх, размыть, растворить. */
export function exit(el, { dy = 8, scale = 0.97, blur = 6, response = 0.3, damping = 1 } = {}) {
  if (REDUCED) return Promise.resolve();
  const m = motionOf(el);
  return m.to({ y: dy, s: scale, o: 0, b: blur }, { response, damping });
}

/** Нажатие, следующее за указателем: сразу вниз при pointerdown, пружиной обратно при отпускании. */
export function pressable(root, selector, { scale = 0.97 } = {}) {
  root.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const el = e.target.closest(selector);
    if (!el || el.disabled || el.getAttribute('aria-disabled') === 'true') return;
    if (REDUCED) return;
    const m = motionOf(el);
    m.to({ s: scale }, { response: 0.12, damping: 1 });
    const up = () => {
      m.to({ s: 1 }, { response: 0.38, damping: 0.8 });
      el.removeEventListener('pointerup', up); el.removeEventListener('pointercancel', up); el.removeEventListener('pointerleave', up);
    };
    el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up); el.addEventListener('pointerleave', up);
  }, { passive: true });
}

/** Резиновое сопротивление за границей (как у списков в iOS). */
export function rubber(offset, limit = 120, coef = 0.55) {
  const s = Math.sign(offset), d = Math.abs(offset);
  return s * ((1 - 1 / (d * coef / limit + 1)) * limit);
}

/** FLIP на пружинах: снять позиции, поменять DOM, анимировать сдвиги; removed — уходящие элементы. */
export function flip(container, mutate, removed = [], { stagger = 32, response = 0.55, damping = 0.9 } = {}) {
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
    exit(el, { dy: 0, scale: 0.94 }).then(() => el.remove());
  }
  mutate();
  let i = 0;
  for (const el of container.children) {
    if (removed.includes(el)) continue;
    const f = first.get(el);
    const l = el.getBoundingClientRect();
    const m = motionOf(el);
    if (!f) { enter(el, { delay: Math.min(i++, 8) * stagger }); continue; }
    const dx = f.left - l.left, dy = f.top - l.top;
    if (Math.abs(dx) + Math.abs(dy) > 1) {
      // продолжаем из текущего положения, если элемент ещё в движении
      m.from({ x: m.get('x') + dx, y: m.get('y') + dy });
      m.to({ x: 0, y: 0 }, { response, damping });
    }
  }
}

export { REDUCED };
