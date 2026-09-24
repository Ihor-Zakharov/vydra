/* выдра · офлайн-кинотеатр.
 * Обычный скрипт без модулей и fetch: страница должна открываться двойным кликом (file://).
 * Данные приходят из .vydra/library.js → window.VYDRA_LIBRARY. */
(function () {
  'use strict';

  var HTTP = location.protocol === 'http:' || location.protocol === 'https:';
  var APP_URL = HTTP ? '/' : 'http://localhost:8765/';
  var ROW_LIMIT = 40;
  var SVGNS = 'http://www.w3.org/2000/svg';
  var mqReduced = window.matchMedia ? matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  var mqSheet = window.matchMedia ? matchMedia('(max-width: 980px)') : { matches: false };
  function reduced() { return mqReduced.matches; }
  function sheetMode() { return mqSheet.matches; }
  function canMorph() { return !!document.startViewTransition && !reduced() && !sheetMode(); }

  var PLATFORM = {
    youtube: { label: 'YouTube', row: 'YouTube', glyph: 'p-youtube' },
    tiktok: { label: 'TikTok', row: 'TikTok', glyph: 'p-tiktok' },
    instagram: { label: 'Instagram', row: 'Instagram', glyph: 'p-instagram' },
    other: { label: 'Сайт', row: 'Другие сайты', glyph: 'p-other' },
    file: { label: 'Мой файл', row: 'Мои файлы', glyph: 'p-file' },
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function pf(p) { return PLATFORM[p] || PLATFORM.other; }

  // ——— localStorage: в приватном режиме и на file:// может бросать ———

  var store = {
    get: function (key, fallback) {
      try {
        var raw = localStorage.getItem(key);
        return raw == null ? fallback : JSON.parse(raw);
      } catch (e) { return fallback; }
    },
    set: function (key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* нет хранилища — живём без него */ }
    },
  };

  // ——— данные ———

  function num(v) { var n = Number(v); return isFinite(n) && n > 0 ? n : null; }
  function stem(path) { var name = String(path).split('/').pop(); return name.replace(/\.[^.]+$/, ''); }

  function normalize(raw) {
    var data = raw && typeof raw === 'object' ? raw : {};
    var items = (Array.isArray(data.items) ? data.items : [])
      .filter(function (it) { return it && it.id && it.path; })
      .map(function (it) {
        return {
          id: String(it.id),
          path: String(it.path),
          type: it.type === 'audio' ? 'audio' : 'video',
          title: String(it.title || stem(it.path)),
          platform: PLATFORM[it.platform] ? it.platform : 'other',
          source: typeof it.source === 'string' && /^https?:\/\//i.test(it.source) ? it.source : null,
          uploader: it.uploader ? String(it.uploader) : null,
          duration: num(it.duration),
          width: num(it.width),
          height: num(it.height),
          size: num(it.size) || 0,
          added: num(it.added) || 0,
          poster: it.poster ? String(it.poster) : null,
        };
      });
    items.sort(function (a, b) { return b.added - a.added; });
    return { generated: data.generated || 0, root: data.root ? String(data.root) : '', items: items };
  }

  // Путь «Видео/YouTube/Название.mp4» → URL относительно страницы (работает и на file://, и по http)
  function urlOf(rel) { return String(rel).split('/').map(encodeURIComponent).join('/'); }

  function orientation(item) {
    if (item.type === 'audio') return 'is-square';
    return item.width && item.height && item.height > item.width ? 'is-portrait' : 'is-landscape';
  }

  var lib = normalize(window.VYDRA_LIBRARY);
  var positions = store.get('vydra.pos', {}) || {};
  var state = { q: '', type: store.get('vydra.type', 'all'), view: null };
  if (['all', 'video', 'audio'].indexOf(state.type) < 0) state.type = 'all';

  function filtered() {
    if (state.type === 'all') return lib.items;
    return lib.items.filter(function (it) { return it.type === state.type; });
  }

  function progressOf(item) {
    var p = positions[item.id];
    if (!p || p.w || !(p.t > 5)) return null;
    if (p.d && p.t > p.d - 3) return null;
    return p;
  }

  function continueList(items) {
    return items
      .filter(function (it) { return progressOf(it); })
      .sort(function (a, b) { return (positions[b.id].at || 0) - (positions[a.id].at || 0); });
  }

  // ——— форматирование ———

  function pad(n) { return n < 10 ? '0' + n : String(n); }
  function fmtTime(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return h ? h + ':' + pad(m) + ':' + pad(s) : m + ':' + pad(s);
  }
  function fmtSize(bytes) {
    var units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ'];
    var i = 0;
    bytes = bytes || 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
    var v = bytes >= 10 || i === 0 ? Math.round(bytes) : bytes.toFixed(1).replace('.', ',');
    return v + ' ' + units[i];
  }
  function plural(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
    return many;
  }
  function dayStart(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); }
  function fmtAdded(ts) {
    if (!ts) return '';
    var d = new Date(ts * 1000);
    var days = Math.round((dayStart(new Date()) - dayStart(d)) / 864e5);
    if (days <= 0) return 'сегодня';
    if (days === 1) return 'вчера';
    if (days < 7) return days + ' ' + plural(days, 'день', 'дня', 'дней') + ' назад';
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
  }

  // ——— DOM ———

  function h(tag, props) {
    var el = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (k) {
        var v = props[k];
        if (v == null || v === false) return;
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else if (k === 'style') Object.keys(v).forEach(function (s) { el.style.setProperty(s, v[s]); });
        else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? '' : v);
      });
    }
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el, child) {
    if (child == null || child === false) return;
    if (Array.isArray(child)) { child.forEach(function (c) { append(el, c); }); return; }
    el.appendChild(child.nodeType ? child : document.createTextNode(String(child)));
  }
  function icon(id, cls) {
    var svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('class', 'ico' + (cls ? ' ' + cls : ''));
    svg.setAttribute('aria-hidden', 'true');
    var use = document.createElementNS(SVGNS, 'use');
    use.setAttribute('href', '#' + id);
    svg.appendChild(use);
    return svg;
  }
  function cssEscape(s) { return window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&'); }

  // Детерминированная «обложка» из id: два оттенка + волна
  function hash(str) {
    var x = 2166136261;
    for (var i = 0; i < str.length; i++) { x ^= str.charCodeAt(i); x = Math.imul(x, 16777619); }
    return x >>> 0;
  }
  function rng(seed) {
    return function () {
      seed = (seed + 0x6d2b79f5) | 0;
      var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function artFor(item) {
    var x = hash(item.id);
    var h1 = x % 360, h2 = (h1 + 45 + ((x >>> 9) % 100)) % 360;
    var wrap = h('div', { class: 'art', style: { '--h1': String(h1), '--h2': String(h2) } });
    var svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('class', 'art-wave');
    svg.setAttribute('viewBox', '0 0 120 40');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('aria-hidden', 'true');
    var rand = rng(x), bars = 28;
    for (var i = 0; i < bars; i++) {
      var env = Math.sin(Math.PI * (i + 0.5) / bars);
      var height = 3 + (0.25 + rand() * 0.75) * 36 * (0.35 + env * 0.65);
      var r = document.createElementNS(SVGNS, 'rect');
      r.setAttribute('x', String(i * (120 / bars) + 0.8));
      r.setAttribute('y', String(40 - height));
      r.setAttribute('width', String(120 / bars - 1.6));
      r.setAttribute('height', String(height));
      r.setAttribute('rx', '1.2');
      svg.appendChild(r);
    }
    wrap.appendChild(svg);
    wrap.appendChild(icon(item.type === 'audio' ? 'i-music' : 'i-film', 'art-glyph'));
    return wrap;
  }

  // Картинка постера с подложкой; нет постера или он битый — генерируем обложку
  function mediaFor(item) {
    var box = h('div', { class: 'media' });
    if (!item.poster) { box.appendChild(artFor(item)); return box; }
    var src = urlOf(item.poster);
    var needsBg = item.type !== 'video' || orientation(item) === 'is-portrait';
    var bg = needsBg ? h('img', { class: 'media-bg', src: src, alt: '', loading: 'lazy', decoding: 'async', draggable: 'false' }) : null;
    var img = h('img', { class: 'media-img', src: src, alt: '', loading: 'lazy', decoding: 'async', draggable: 'false' });
    img.addEventListener('error', function () {
      if (bg) bg.remove();
      img.replaceWith(artFor(item));
    }, { once: true });
    if (bg) box.appendChild(bg);
    box.appendChild(img);
    return box;
  }
  function backdropFor(item) {
    if (!item.poster) return artFor(item);
    var img = h('img', { src: urlOf(item.poster), alt: '', decoding: 'async' });
    img.addEventListener('error', function () { img.replaceWith(artFor(item)); }, { once: true });
    return img;
  }

  function subline(item) {
    var parts = [item.uploader || pf(item.platform).label];
    if (item.type === 'audio') parts.push('аудио');
    parts.push(fmtSize(item.size));
    if (item.added) parts.push(fmtAdded(item.added));
    return parts.join(' · ');
  }

  function card(item, queue) {
    var media = mediaFor(item);
    var pos = positions[item.id];
    var prog = progressOf(item);
    append(media, [
      h('span', { class: 'badge-pf', title: pf(item.platform).row }, icon(pf(item.platform).glyph)),
      item.duration ? h('span', { class: 'badge-dur' }, fmtTime(item.duration)) : null,
      h('span', { class: 'card-play', 'aria-hidden': 'true' }, icon('i-play')),
    ]);
    if (pos && pos.w) append(media, h('span', { class: 'badge-watched' }, icon('i-check'), 'просмотрено'));
    else if (prog && prog.d) {
      media.classList.add('has-progress');
      append(media, h('span', { class: 'card-progress' }, h('i', { style: { width: Math.min(100, (prog.t / prog.d) * 100).toFixed(1) + '%' } })));
    }
    var el = h('button', {
      class: 'card ' + orientation(item),
      type: 'button',
      'data-id': item.id,
      'data-platform': item.platform,
      title: item.title,
    }, h('span', { class: 'lift' }, h('span', { class: 'glow', 'aria-hidden': 'true' }), media),
    h('span', { class: 'card-text' },
      h('span', { class: 'card-title', text: item.title }),
      h('span', { class: 'card-sub', text: subline(item) })));
    el.addEventListener('click', function () { openTheater(item, queue, { from: media }); });
    return el;
  }

  // ——— фон-аврора: два слоя, новый цвет проявляется кроссфейдом (только opacity) ———

  var ambient = { current: 'none', front: $('#aurora-a'), back: $('#aurora-b') };
  function setAmbient(platform) {
    document.documentElement.setAttribute('data-platform', platform);
    if (platform === ambient.current) return;
    ambient.current = platform;
    var next = ambient.back;
    next.setAttribute('data-platform', platform);
    next.classList.add('is-on');
    ambient.front.classList.remove('is-on');
    ambient.back = ambient.front;
    ambient.front = next;
  }

  // ——— появление рядов при прокрутке ———

  var revealBatch = 0;
  var revealer = 'IntersectionObserver' in window
    ? new IntersectionObserver(function (entries) {
      var n = 0;
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.style.setProperty('--i', String(n++));
        e.target.classList.add('in');
        revealer.unobserve(e.target);
      });
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0.12 })
    : null;
  function reveal(el, animate) {
    el.classList.add('reveal');
    if (!animate || !revealer || reduced()) { el.classList.add('in'); el.style.setProperty('--i', '0'); return; }
    revealer.observe(el);
  }

  // ——— отрисовка ———

  var arrowUpdaters = [];
  var trackScroll = {};

  function renderStats() {
    var v = 0, a = 0, total = 0;
    lib.items.forEach(function (it) { if (it.type === 'audio') a++; else v++; total += it.size; });
    var parts = [];
    if (v) parts.push(v + ' видео');
    if (a) parts.push(a + ' ' + plural(a, 'трек', 'трека', 'треков'));
    parts.push(fmtSize(total));
    $('#stats').textContent = lib.items.length ? parts.join(' · ') : 'пока пусто';
    var seg = $('#seg');
    var index = ['all', 'video', 'audio'].indexOf(state.type);
    seg.style.setProperty('--seg-i', String(Math.max(0, index)));
    Array.prototype.forEach.call(seg.querySelectorAll('button'), function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.type === state.type));
    });
    $('#foot-root').textContent = lib.root || 'Папка с загрузками';
    $('#foot-time').textContent = lib.generated
      ? 'обновлено ' + new Date(lib.generated * 1000).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
      : '';
  }

  function heroLayer(pick, queue, prog) {
    var p = pf(pick.platform);
    var verb = pick.type === 'audio' ? 'Слушать' : 'Смотреть';

    var frameMedia = mediaFor(pick);
    append(frameMedia, h('span', { class: 'hero-play', 'aria-hidden': 'true' }, icon('i-play')));
    if (prog && prog.d) append(frameMedia, h('span', { class: 'hero-progress' }, h('i', { style: { width: (prog.t / prog.d * 100).toFixed(1) + '%' } })));

    var chips = [h('span', { class: 'chip chip-pf' }, icon(p.glyph), p.label)];
    if (pick.duration) chips.push(h('span', { class: 'chip chip-num' }, icon('i-clock'), fmtTime(pick.duration)));
    if (pick.uploader) chips.push(h('span', { class: 'chip', text: pick.uploader }));
    if (pick.added) chips.push(h('span', { class: 'chip', text: 'добавлено ' + fmtAdded(pick.added) }));

    var actions = [
      h('button', { class: 'btn btn-primary', type: 'button', onclick: function () { openTheater(pick, queue, { from: frameMedia }); } },
        icon('i-play'), prog ? 'Продолжить с ' + fmtTime(prog.t) : verb),
    ];
    if (prog) {
      actions.push(h('button', { class: 'btn btn-glass', type: 'button', onclick: function () { openTheater(pick, queue, { from: frameMedia, fromStart: true }); } },
        icon('i-restart'), 'Сначала'));
    }
    if (pick.source) {
      actions.push(h('a', { class: 'btn btn-glass', href: pick.source, target: '_blank', rel: 'noopener noreferrer' }, icon('i-external'), 'Открыть источник'));
    }

    return h('div', { class: 'hero-layer', 'data-id': pick.id, 'data-platform': pick.platform },
      h('div', { class: 'hero-bg', 'aria-hidden': 'true' }, backdropFor(pick)),
      h('div', { class: 'hero-shade', 'aria-hidden': 'true' }),
      h('div', { class: 'hero-text' },
        h('p', { class: 'eyebrow', text: prog ? 'Продолжить просмотр' : (pick.type === 'audio' ? 'Новое в фонотеке' : 'Новое в коллекции') }),
        h('h1', { class: 'hero-title', text: pick.title }),
        h('div', { class: 'chips' }, chips),
        h('div', { class: 'hero-actions' }, actions)),
      h('button', {
        class: 'hero-frame ' + orientation(pick),
        type: 'button',
        'data-id': pick.id,
        'aria-label': verb + ': ' + pick.title,
        onclick: function () { openTheater(pick, queue, { from: frameMedia }); },
      }, frameMedia));
  }

  function renderHero(items) {
    var hero = $('#hero');
    var cont = continueList(items);
    var pick = cont[0] || items.filter(function (it) { return it.type === 'video'; })[0] || items[0];
    if (!pick) { hero.hidden = true; hero.textContent = ''; return; }
    var prog = progressOf(pick);
    var layer = heroLayer(pick, prog ? cont : items, prog);
    var wasHidden = hero.hidden;
    hero.hidden = false;
    setAmbient(pick.platform);

    // старые слои: тот же ролик — меняем молча, другой — плавный кроссфейд
    var old = Array.prototype.slice.call(hero.querySelectorAll('.hero-layer'));
    var current = old.filter(function (l) { return !l.classList.contains('is-leaving'); })[0];
    var same = current && current.dataset.id === pick.id;
    old.forEach(function (l) {
      if (same || reduced() || l.classList.contains('is-leaving')) { l.remove(); return; }
      l.classList.add('is-leaving');
      l.setAttribute('aria-hidden', 'true');
      setTimeout(function () { l.remove(); }, 700);
    });
    if (!same && !reduced()) layer.classList.add('is-entering');
    if (wasHidden && !reduced()) layer.classList.add('is-entering');
    hero.appendChild(layer);
  }

  var ROWS = [
    { key: 'continue', title: 'Продолжить просмотр', glyph: 'i-clock', pick: continueList },
    { key: 'recent', title: 'Недавно добавленные', glyph: 'i-spark', pick: function (items) { return items; } },
    { key: 'youtube', platform: 'youtube' },
    { key: 'tiktok', platform: 'tiktok' },
    { key: 'instagram', platform: 'instagram' },
    { key: 'other', platform: 'other' },
    { key: 'file', platform: 'file' },
    { key: 'music', title: 'Музыка', glyph: 'i-music', pick: function (items) { return items.filter(function (it) { return it.type === 'audio'; }); } },
  ];
  ROWS.forEach(function (r) {
    if (!r.platform) return;
    r.title = pf(r.platform).row;
    r.glyph = pf(r.platform).glyph;
    r.pick = function (items) {
      return items.filter(function (it) { return it.type === 'video' && it.platform === r.platform; });
    };
  });

  function renderRows(items, animate) {
    var box = $('#rows');
    // запоминаем горизонтальную прокрутку, чтобы перерисовка не сбрасывала ряды в начало
    Array.prototype.forEach.call(box.querySelectorAll('.row'), function (r) {
      var t = r.querySelector('.track');
      if (t) trackScroll[r.dataset.key] = t.scrollLeft;
    });
    box.textContent = '';
    arrowUpdaters = [];
    ROWS.forEach(function (def) {
      var list = def.pick(items);
      if (!list.length) return;
      var el = row(def, list);
      box.appendChild(el);
      var track = el.querySelector('.track');
      if (trackScroll[def.key]) track.scrollLeft = trackScroll[def.key];
      reveal(el, animate);
    });
  }

  function row(def, list) {
    var cards = list.slice(0, ROW_LIMIT).map(function (it, i) {
      var c = card(it, list);
      if (i < 10) c.style.setProperty('--ci', String(i));
      return c;
    });
    var track = h('div', { class: 'track' }, cards);
    var step = function (dir) {
      track.scrollBy({ left: dir * track.clientWidth * 0.85, behavior: reduced() ? 'auto' : 'smooth' });
    };
    var prev = h('button', { class: 'row-arrow', type: 'button', 'aria-label': 'Прокрутить назад', onclick: function () { step(-1); } }, icon('i-chev-l'));
    var next = h('button', { class: 'row-arrow', type: 'button', 'aria-label': 'Прокрутить вперёд', onclick: function () { step(1); } }, icon('i-chev-r'));
    var update = function () {
      prev.disabled = track.scrollLeft < 8;
      next.disabled = track.scrollLeft + track.clientWidth > track.scrollWidth - 8;
    };
    track.addEventListener('scroll', update, { passive: true });
    arrowUpdaters.push(update);
    requestAnimationFrame(update);

    var tools = [];
    if (list.length > 4) {
      tools.push(h('button', { class: 'row-all', type: 'button', onclick: function () { showView(def); } }, 'Все', icon('i-chev-r')));
    }
    tools.push(prev, next);
    return h('section', { class: 'row', 'data-key': def.key, 'data-platform': def.platform || null, 'aria-label': def.title },
      h('div', { class: 'row-head' },
        h('h2', { class: 'row-title' }, icon(def.glyph, 'row-glyph'), h('span', { text: def.title }), h('span', { class: 'row-count', text: String(list.length) })),
        h('div', { class: 'row-tools' }, tools)),
      track);
  }

  function matches(item, q) {
    var hay = (item.title + ' ' + (item.uploader || '') + ' ' + pf(item.platform).row + ' ' + item.path).toLowerCase();
    return q.split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
  }

  function renderResults(title, list, emptyText, animate) {
    var box = $('#results');
    box.textContent = '';
    box.hidden = false;
    var back = h('button', { class: 'results-back', type: 'button', 'aria-label': 'Назад', onclick: resetView }, icon('i-arrow-l'));
    append(box, h('div', { class: 'results-head' }, back,
      h('h2', { class: 'results-title', text: title }),
      h('span', { class: 'row-count', text: String(list.length) })));
    if (!list.length) {
      append(box, h('p', { class: 'nothing' }, emptyText));
      return;
    }
    append(box, h('div', { class: animate ? 'grid' : 'grid no-reveal' }, list.map(function (it, i) {
      var c = card(it, list);
      if (i < 24) c.style.setProperty('--ci', String(i));
      return c;
    })));
  }

  function showView(def) {
    state.view = def;
    render(true);
    window.scrollTo({ top: 0, behavior: reduced() ? 'auto' : 'smooth' });
  }
  function resetView() {
    state.view = null;
    state.q = '';
    $('#q').value = '';
    render(true);
  }

  // animate: заново проиграть появление рядов (первый показ, смена фильтра); при фоновых обновлениях — нет
  var lastMode = '';
  function render(animate) {
    renderStats();
    var mode = state.q.trim() ? 'search' : state.view ? 'view:' + state.view.key : 'home';
    var fresh = mode !== lastMode;
    lastMode = mode;
    var empty = lib.items.length === 0;
    $('#empty').hidden = !empty;
    var hero = $('#hero'), rows = $('#rows'), results = $('#results');
    if (empty) {
      hero.hidden = true;
      hero.textContent = '';
      rows.textContent = '';
      results.hidden = true;
      setAmbient('none');
      return;
    }
    var items = filtered();
    var q = state.q.trim().toLowerCase();
    if (q) {
      hero.hidden = true;
      rows.textContent = '';
      var found = items.filter(function (it) { return matches(it, q); });
      var nothing = h('span', null, 'Ничего не нашлось по запросу «', h('b', { text: state.q.trim() }), '»');
      renderResults('Поиск', found, nothing, animate && fresh);
    } else if (state.view) {
      hero.hidden = true;
      rows.textContent = '';
      renderResults(state.view.title, state.view.pick(items), 'Здесь пока пусто', animate && fresh);
    } else {
      results.hidden = true;
      results.textContent = '';
      if (!items.length) {
        hero.hidden = true;
        hero.textContent = '';
        rows.textContent = '';
        append(rows, h('p', { class: 'nothing', text: state.type === 'audio' ? 'Музыки пока нет — выберите MP3 при скачивании.' : 'Видео пока нет.' }));
        return;
      }
      renderHero(items);
      renderRows(items, animate);
    }
  }

  // ——— кинозал ———

  var T = { open: false, queue: [], index: 0, item: null, media: null, pushed: false, lastSave: 0, chipTimer: 0, hideTimer: 0 };
  var auto = store.get('vydra.autonext', true) !== false;
  var theater = $('#theater');
  var layout = $('#th-layout');

  // Самая заметная на экране карточка этого ролика — к ней «улетает» плеер при закрытии
  function visibleMediaFor(id) {
    var best = null, bestArea = 0;
    var nodes = document.querySelectorAll('.card[data-id="' + cssEscape(id) + '"] .media, .hero-frame[data-id="' + cssEscape(id) + '"] .media');
    Array.prototype.forEach.call(nodes, function (m) {
      if (m.closest('.hero-layer.is-leaving')) return;
      var r = m.getBoundingClientRect();
      var w = Math.min(r.right, innerWidth) - Math.max(r.left, 0);
      var hgt = Math.min(r.bottom, innerHeight) - Math.max(r.top, 0);
      var area = w > 0 && hgt > 0 ? w * hgt : 0;
      if (area > bestArea) { best = m; bestArea = area; }
    });
    return best;
  }

  function waitImage(img, ms) {
    return new Promise(function (resolve) {
      if (!img || (img.complete && img.naturalWidth)) { resolve(); return; }
      var done = function () { resolve(); };
      img.addEventListener('load', done, { once: true });
      img.addEventListener('error', done, { once: true });
      setTimeout(done, ms);
    });
  }

  function openTheater(item, queue, opts) {
    opts = opts || {};
    T.queue = queue && queue.length ? queue.slice() : [item];
    T.index = -1;
    for (var i = 0; i < T.queue.length; i++) if (T.queue[i].id === item.id) { T.index = i; break; }
    if (T.index < 0) { T.queue.unshift(item); T.index = 0; }

    if (T.open) { loadCurrent(!!opts.fromStart); return; }
    T.open = true;
    T.returnFocus = document.activeElement;
    clearTimeout(T.hideTimer);
    try {
      history.pushState({ vydra: 'theater' }, '', '#v=' + encodeURIComponent(item.id));
      T.pushed = true;
    } catch (e) { T.pushed = false; }

    var source = opts.from && document.contains(opts.from) ? opts.from : null;
    if (canMorph() && source) {
      // общий элемент: постер карточки перетекает в экран плеера
      source.style.viewTransitionName = 'vd-hero';
      theater.classList.add('instant');
      var vt = document.startViewTransition(function () {
        source.style.viewTransitionName = '';
        showTheater(true);
        loadCurrent(!!opts.fromStart);
        var screen = $('#th-screen');
        screen.style.viewTransitionName = 'vd-hero';
        return waitImage(screen.querySelector('.th-poster'), 180);
      });
      vt.finished.finally(function () {
        $('#th-screen').style.viewTransitionName = '';
        theater.classList.remove('instant');
      });
    } else {
      showTheater(false);
      loadCurrent(!!opts.fromStart);
    }
    theater.focus({ preventScroll: true });
  }

  function showTheater(instant) {
    theater.hidden = false;
    document.body.classList.add('no-scroll');
    if (sheetMode()) {
      var shell = $('#shell');
      shell.style.transformOrigin = '50% ' + (window.scrollY + innerHeight / 2) + 'px';
      document.body.classList.add('sheet-open');
    }
    if (instant) { theater.classList.add('is-open'); return; }
    void theater.offsetWidth; // зафиксировать стартовое состояние, иначе переход не сыграет
    theater.classList.add('is-open');
  }

  function loadCurrent(fromStart) {
    savePosition(true);
    stopMedia();
    var item = T.queue[T.index];
    T.item = item;
    theater.setAttribute('data-platform', item.platform);
    theater.classList.remove('is-playing');

    var backdrop = $('#th-backdrop');
    backdrop.textContent = '';
    backdrop.appendChild(backdropFor(item));

    var p = pf(item.platform);
    var heading = $('.th-heading', theater);
    heading.classList.remove('swap');
    $('#th-eyebrow').textContent = item.type === 'audio' ? 'Сейчас играет' : 'Сейчас на экране';
    $('#th-title').textContent = item.title;
    var meta = $('#th-meta');
    meta.textContent = '';
    append(meta, [
      h('span', { class: 'chip chip-pf' }, icon(p.glyph), p.label),
      item.duration ? h('span', { class: 'chip chip-num' }, icon('i-clock'), fmtTime(item.duration)) : null,
      item.uploader ? h('span', { class: 'chip', text: item.uploader }) : null,
      h('span', { class: 'chip chip-num', text: fmtSize(item.size) }),
    ]);
    if (!theater.classList.contains('instant') && !reduced()) {
      void heading.offsetWidth;
      heading.classList.add('swap');
    }

    var screen = $('#th-screen');
    screen.textContent = '';
    screen.className = 'th-screen ' + (item.type === 'audio' ? 'is-audio' : orientation(item));
    var media;
    if (item.type === 'audio') {
      media = h('audio', { controls: true, preload: 'metadata', src: urlOf(item.path) });
      var eq = h('div', { class: 'eq', 'aria-hidden': 'true' });
      for (var i = 0; i < 24; i++) eq.appendChild(document.createElement('span'));
      append(screen, h('div', { class: 'th-audio' }, h('div', { class: 'th-cover' }, mediaFor(item)), eq,
        h('p', { class: 'th-audio-title', text: item.title }),
        item.uploader ? h('p', { class: 'th-audio-sub', text: item.uploader }) : null,
        media));
    } else {
      media = h('video', { controls: true, playsinline: true, preload: 'metadata', src: urlOf(item.path) });
      append(screen, h('div', { class: 'th-screen-bg', 'aria-hidden': 'true' }, backdropFor(item)));
      append(screen, media);
      if (item.poster) {
        var poster = h('img', { class: 'th-poster', src: urlOf(item.poster), alt: '', decoding: 'async' });
        poster.addEventListener('error', function () { poster.remove(); }, { once: true });
        append(screen, poster);
        media.addEventListener('playing', function () { poster.classList.add('is-gone'); }, { once: true });
        media.addEventListener('seeked', function () { poster.classList.add('is-gone'); }, { once: true });
      }
    }
    T.media = media;
    T.lastSave = Date.now();
    bindMedia(media, item, fromStart);

    var src = $('#th-source');
    if (item.source) { src.hidden = false; src.href = item.source; } else { src.hidden = true; src.removeAttribute('href'); }
    $('#th-prev').disabled = T.index <= 0;
    $('#th-next').disabled = T.index >= T.queue.length - 1;
    renderQueue();
    try { history.replaceState(history.state, '', '#v=' + encodeURIComponent(item.id)); } catch (e) { /* file:// без истории */ }

    var playing = media.play();
    if (playing && playing.catch) playing.catch(function () { /* автозапуск запрещён — пользователь нажмёт сам */ });
  }

  function bindMedia(media, item, fromStart) {
    media.addEventListener('loadedmetadata', function () {
      var p = positions[item.id];
      if (fromStart) { clearPosition(item.id); return; }
      if (p && !p.w && p.t > 5 && isFinite(media.duration) && p.t < media.duration - 3) {
        media.currentTime = p.t;
        showResumeChip(p.t);
      }
    });
    media.addEventListener('timeupdate', function () { savePosition(false); });
    media.addEventListener('play', function () { theater.classList.add('is-playing'); });
    media.addEventListener('pause', function () { theater.classList.remove('is-playing'); savePosition(true); });
    media.addEventListener('ended', function () {
      theater.classList.remove('is-playing');
      markWatched(item, media.duration);
      if (auto && T.index < T.queue.length - 1) { T.index++; loadCurrent(false); } else renderQueue();
    });
  }

  function showResumeChip(t) {
    var screen = $('#th-screen');
    var chip = h('div', { class: 'resume-chip', role: 'status' },
      h('span', null, 'Продолжаем с ', h('b', { text: fmtTime(t) })),
      h('button', {
        type: 'button',
        onclick: function () {
          if (T.media) { T.media.currentTime = 0; T.media.play().catch(function () {}); }
          chip.classList.add('is-gone');
        },
      }, icon('i-restart'), 'Сначала'));
    screen.appendChild(chip);
    clearTimeout(T.chipTimer);
    T.chipTimer = setTimeout(function () { chip.classList.add('is-gone'); }, 6000);
  }

  function renderQueue() {
    var list = $('#th-list');
    list.textContent = '';
    var current = null;
    T.queue.forEach(function (it, i) {
      var media = mediaFor(it);
      var pos = positions[it.id];
      if (it.duration) append(media, h('span', { class: 'badge-dur' }, fmtTime(it.duration)));
      if (pos && pos.w) append(media, h('span', { class: 'badge-watched', title: 'просмотрено' }, icon('i-check')));
      var sub = h('span', { class: 'q-sub' });
      if (i === T.index) append(sub, h('span', { class: 'q-now', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')));
      else append(sub, icon(pf(it.platform).glyph));
      append(sub, h('span', { text: it.uploader || pf(it.platform).row }));
      var btn = h('button', {
        class: 'q-item ' + orientation(it) + (i === T.index ? ' is-current' : ''),
        type: 'button',
        'data-platform': it.platform,
        'aria-current': i === T.index ? 'true' : null,
        onclick: function () { if (i !== T.index) { T.index = i; loadCurrent(false); } },
      }, media, h('span', { class: 'q-text' }, h('span', { class: 'q-title', text: it.title }), sub));
      if (i === T.index) current = btn;
      list.appendChild(btn);
    });
    $('#th-count').textContent = (T.index + 1) + ' из ' + T.queue.length;
    if (current && !sheetMode()) current.scrollIntoView({ block: 'nearest' });
  }

  function stopMedia() {
    if (!T.media) return;
    try {
      T.media.pause();
      T.media.removeAttribute('src');
      T.media.load();
    } catch (e) { /* уже выгружено */ }
    T.media = null;
  }

  function finishHide() {
    theater.hidden = true;
    theater.classList.remove('is-open', 'is-playing', 'is-dragging');
    layout.style.transform = '';
    $('#th-dim').style.opacity = '';
    $('#th-screen').textContent = '';
    $('#th-list').textContent = '';
  }

  function closeTheater(fromPop) {
    if (!T.open) return;
    savePosition(true);
    clearTimeout(T.chipTimer);
    T.open = false;
    var closing = T.item;
    document.body.classList.remove('no-scroll');
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(function () {});

    if (!fromPop && T.pushed) {
      T.pushed = false;
      try { history.back(); } catch (e) { /* ничего */ }
    } else {
      T.pushed = false;
      try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { /* ничего */ }
    }

    var screen = $('#th-screen');
    if (canMorph() && closing && !theater.classList.contains('is-dragging')) {
      // обратный морфинг: экран «влетает» в карточку, если она на экране
      screen.style.viewTransitionName = 'vd-hero';
      theater.classList.add('instant');
      var target = null;
      var vt = document.startViewTransition(function () {
        screen.style.viewTransitionName = '';
        stopMedia();
        finishHide();
        render(false);
        target = visibleMediaFor(closing.id);
        if (target) target.style.viewTransitionName = 'vd-hero';
      });
      vt.finished.finally(function () {
        if (target) target.style.viewTransitionName = '';
        theater.classList.remove('instant');
      });
    } else {
      stopMedia();
      theater.classList.remove('is-open', 'is-playing');
      document.body.classList.remove('sheet-open');
      clearTimeout(T.hideTimer);
      T.hideTimer = setTimeout(function () { if (!T.open) finishHide(); }, reduced() ? 0 : 480);
      render(false);
    }
    document.body.classList.remove('sheet-open');
    if (T.returnFocus && document.contains(T.returnFocus)) T.returnFocus.focus({ preventScroll: true });
  }

  function step(dir) {
    var i = T.index + dir;
    if (i < 0 || i >= T.queue.length) return;
    T.index = i;
    loadCurrent(false);
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) { document.exitFullscreen().catch(function () {}); return; }
    var target = T.media && T.media.tagName === 'VIDEO' ? T.media : $('#th-screen');
    if (target.requestFullscreen) target.requestFullscreen().catch(function () {});
    else if (target.webkitEnterFullscreen) target.webkitEnterFullscreen();
  }

  // ——— шторка на телефоне: тянешь вниз — закрывается ———

  var drag = null;
  function dragAllowed(target) {
    if (!sheetMode() || !T.open) return false;
    if (target.closest('.th-grab, .th-head')) return true;
    // из остальной шторки — только когда она прокручена к самому верху и не по плееру/кнопкам
    return layout.scrollTop <= 0 && !target.closest('.th-screen, .th-controls, input, a');
  }
  layout.addEventListener('touchstart', function (e) {
    if (e.touches.length !== 1 || !dragAllowed(e.target)) { drag = null; return; }
    var y = e.touches[0].clientY;
    drag = { y0: y, last: y, lastT: performance.now(), v: 0, dy: 0, active: false, fromHandle: !!e.target.closest('.th-grab, .th-head') };
  }, { passive: true });
  layout.addEventListener('touchmove', function (e) {
    if (!drag) return;
    var y = e.touches[0].clientY;
    var dy = y - drag.y0;
    if (!drag.active) {
      if (dy > 6 && (drag.fromHandle || layout.scrollTop <= 0)) {
        drag.active = true;
        drag.y0 = y;
        dy = 0;
        theater.classList.add('is-dragging');
        document.body.classList.add('is-dragging');
      } else if (Math.abs(dy) > 6) { drag = null; return; } else return;
    }
    e.preventDefault();
    var now = performance.now();
    drag.v = (y - drag.last) / Math.max(1, now - drag.lastT);
    drag.last = y;
    drag.lastT = now;
    drag.dy = Math.max(0, dy);
    var height = layout.getBoundingClientRect().height || innerHeight;
    var progress = Math.min(1, drag.dy / height);
    layout.style.transform = 'translate3d(0,' + drag.dy + 'px,0)';
    $('#th-dim').style.opacity = String(1 - progress * 0.9);
    $('#shell').style.transform = 'scale(' + (0.94 + 0.06 * progress) + ')';
  }, { passive: false });
  function endDrag() {
    if (!drag) return;
    var d = drag;
    drag = null;
    if (!d.active) return;
    theater.classList.remove('is-dragging');
    document.body.classList.remove('is-dragging');
    $('#shell').style.transform = '';
    var height = layout.getBoundingClientRect().height || innerHeight;
    if (d.dy > Math.min(160, height * 0.25) || d.v > 0.55) {
      // уезжает вниз с текущей скорости, затем закрываемся без повторной анимации
      layout.style.transform = '';
      $('#th-dim').style.opacity = '';
      closeTheater(false);
    } else {
      layout.style.transform = '';
      $('#th-dim').style.opacity = '';
    }
  }
  layout.addEventListener('touchend', endDrag);
  layout.addEventListener('touchcancel', endDrag);

  // ——— позиции просмотра ———

  function persistPositions() {
    var ids = Object.keys(positions);
    if (ids.length > 600) {
      ids.sort(function (a, b) { return (positions[a].at || 0) - (positions[b].at || 0); });
      ids.slice(0, ids.length - 500).forEach(function (id) { delete positions[id]; });
    }
    store.set('vydra.pos', positions);
  }

  function savePosition(force) {
    var m = T.media, item = T.item;
    if (!m || !item || !isFinite(m.duration) || m.duration <= 0) return;
    var now = Date.now();
    if (!force && now - T.lastSave < 3000) return;
    T.lastSave = now;
    var t = m.currentTime, d = m.duration;
    positions = store.get('vydra.pos', positions) || positions; // другая вкладка могла что-то записать
    if (t / d > 0.92) positions[item.id] = { t: 0, d: d, at: now / 1000, w: 1 };
    else if (t > 5) positions[item.id] = { t: t, d: d, at: now / 1000, w: 0 };
    else return;
    persistPositions();
  }

  function markWatched(item, d) {
    positions[item.id] = { t: 0, d: d || item.duration || 0, at: Date.now() / 1000, w: 1 };
    persistPositions();
  }

  function clearPosition(id) {
    if (!positions[id]) return;
    delete positions[id];
    persistPositions();
  }

  // ——— события ———

  function isTyping(target) {
    return target && target.closest && target.closest('input[type="search"], input[type="text"], textarea, [contenteditable]');
  }

  window.addEventListener('keydown', function (e) {
    if (!T.open) {
      if (e.key === '/' && !isTyping(e.target)) { e.preventDefault(); $('#q').focus(); }
      else if (e.key === 'Escape' && e.target === $('#q') && state.q) { resetView(); }
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var m = T.media;
    switch (e.code) {
      case 'Space':
      case 'KeyK':
        // как в плеерах: пробел всегда пауза, даже если фокус остался на кнопке «Следующее»
        if (isTyping(e.target)) return;
        e.preventDefault();
        if (m) { if (m.paused) m.play().catch(function () {}); else m.pause(); }
        break;
      case 'ArrowLeft':
      case 'ArrowRight':
        if (isTyping(e.target)) return;
        e.preventDefault();
        var dir = e.code === 'ArrowLeft' ? -1 : 1;
        if (e.shiftKey) step(dir);
        else if (m && isFinite(m.duration)) m.currentTime = Math.min(Math.max(0, m.currentTime + dir * 5), m.duration);
        break;
      case 'KeyF':
        e.preventDefault();
        toggleFullscreen();
        break;
      case 'Escape':
        if (!document.fullscreenElement) { e.preventDefault(); closeTheater(false); }
        break;
    }
  }, true);

  window.addEventListener('popstate', function () {
    if (T.open) closeTheater(true);
  });

  window.addEventListener('storage', function (e) {
    if (e.key === 'vydra.pos') {
      positions = store.get('vydra.pos', {}) || {};
      if (!T.open) render(false);
    }
  });

  window.addEventListener('pagehide', function () { savePosition(true); });
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') savePosition(true);
    else if (HTTP) reloadLibrary();
  });

  window.addEventListener('resize', function () { arrowUpdaters.forEach(function (fn) { fn(); }); }, { passive: true });
  window.addEventListener('scroll', function () {
    $('#bar').classList.toggle('is-stuck', window.scrollY > 4);
  }, { passive: true });

  $('#q').addEventListener('input', function (e) {
    state.q = e.target.value;
    if (state.q) state.view = null;
    render(true);
  });
  $('#seg').addEventListener('click', function (e) {
    var b = e.target.closest('button[data-type]');
    if (!b || b.dataset.type === state.type) return;
    state.type = b.dataset.type;
    store.set('vydra.type', state.type);
    render(true);
  });
  $('#th-close').addEventListener('click', function () { closeTheater(false); });
  $('#th-dim').addEventListener('click', function () { closeTheater(false); });
  $('#th-prev').addEventListener('click', function () { step(-1); });
  $('#th-next').addEventListener('click', function () { step(1); });
  $('#th-full').addEventListener('click', toggleFullscreen);
  var autoBox = $('#th-auto');
  autoBox.checked = auto;
  autoBox.addEventListener('change', function () { auto = autoBox.checked; store.set('vydra.autonext', auto); });

  // Ссылка обратно в приложение: по http — тот же сервер, с диска — локальный адрес выдры
  [$('#back'), $('#empty-go')].forEach(function (a) {
    a.href = APP_URL;
    if (!HTTP) a.title = 'Откроется, если выдра запущена (ярлык «выдра» на рабочем столе)';
  });

  // ——— живое обновление по http: новые загрузки появляются сами ———

  var reloading = false;
  function reloadLibrary() {
    if (!HTTP || reloading) return;
    reloading = true;
    var s = document.createElement('script');
    s.src = '.vydra/library.js?t=' + Date.now();
    s.onload = function () {
      reloading = false;
      s.remove();
      var next = window.VYDRA_LIBRARY;
      if (next && next.generated !== lib.generated) {
        lib = normalize(next);
        if (!T.open) render(false);
      }
    };
    s.onerror = function () { reloading = false; s.remove(); };
    document.head.appendChild(s);
  }
  if (HTTP) setInterval(reloadLibrary, 10000);

  // ——— старт ———

  render(true);
  var deep = /^#v=(.+)$/.exec(location.hash);
  if (deep) {
    var id = decodeURIComponent(deep[1]);
    var found = lib.items.filter(function (it) { return it.id === id; })[0];
    if (found) {
      try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { /* ничего */ }
      openTheater(found, filtered());
    }
  }
})();
