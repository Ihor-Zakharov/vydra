// выдра — папки хранилища: отделы по платформам, свои папки, выбор папки для загрузок,
// переименование/удаление, перетаскивание в папку, контекстное меню, клавиатура, живая синхронизация.
// Без /api/fs работает поверх /api/library (виртуальные папки по платформе и типу, только чтение).

import { motionOf, enter, exit, flip, REDUCED } from './spring.js';

const PLAT_ORDER = ['youtube', 'tiktok', 'instagram', 'other', 'file'];
const PLAT_NAME = { youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram', other: 'Другие сайты', file: 'Мои файлы' };
const NAME_PLAT = Object.fromEntries(Object.entries(PLAT_NAME).map(([k, v]) => [v, k]));
const TYPE_NAME = { video: 'Видео', audio: 'Аудио' };

export function createExplorer(ctx) {
  const { $, api, toast, esc, h, svgUse, glyph, fmtBytes, fmtTime, fmtAgo, libUrl, openPlayer, confirmDialog, pickFolder, artHtml, attachHoverPreview, fileAction, onDest } = ctx;
  const root = $('#explorer');
  const main = $('#ex-main');
  const deptsEl = $('#ex-departments');
  const foldersEl = $('#ex-folders');
  const filesEl = $('#ex-files');
  const treeEl = $('#ex-tree');
  const crumbsEl = $('#crumbs');
  const emptyEl = $('#lib-empty');
  const selbar = $('#selbar');
  const ctxMenu = $('#ctx');
  const saveHereBtn = $('#save-here');

  const ex = {
    supported: null, path: '', data: null, tree: null, rev: null,
    view: 'grid', sort: 'date', search: '', everywhere: false,
    selected: new Set(), focus: null, anchor: null,
    items: [], library: null, dest: '',
    fileEls: new Map(), grid: null, pending: 0,
  };
  const listeners = { navigate: [], change: [], tree: [] };
  const on = (ev, fn) => listeners[ev].push(fn);
  const emit = (ev, ...a) => listeners[ev].forEach((fn) => fn(...a));
  try { const v = localStorage.getItem('vd.view'); if (v === 'list' || v === 'grid') ex.view = v; } catch { /* */ }
  try { const s = localStorage.getItem('vd.sort'); if (s) ex.sort = s; } catch { /* */ }
  root.dataset.view = ex.view;

  /* ---------- данные ---------- */

  const keyOfFile = (f) => `i:${f.id}`;
  const keyOfFolder = (f) => `f:${f.path}`;

  async function fetchFs(path) {
    if (ex.supported === false) return virtualFs(path);
    try { const d = await api(`/api/fs?path=${encodeURIComponent(path)}`); ex.supported = true; return d; }
    catch (err) {
      if (err.status === 404 && ex.supported !== true) { ex.supported = false; return virtualFs(path); }
      if (err.status === 404) throw new Error('Папка не найдена');
      throw err;
    }
  }
  async function fetchTree() {
    if (ex.supported === false) return virtualTree();
    try { const d = await api('/api/fs/tree'); ex.supported = true; return d.tree || d; }
    catch (err) { if (err.status === 404) { if (ex.supported !== true) ex.supported = false; return virtualTree(); } throw err; }
  }
  async function loadLibrary() {
    try { ex.library = await api('/api/library'); } catch { /* баннер уже показан */ }
    return ex.library;
  }
  function itemFolder(it) { return it.folder != null ? String(it.folder) : `${PLAT_NAME[it.platform] || PLAT_NAME.other}/${TYPE_NAME[it.type] || 'Видео'}`; }
  function virtualFs(path) {
    const items = ex.library?.items || [];
    const folders = new Map(); const files = [];
    for (const it of items) {
      const f = itemFolder(it);
      if (f === path) { files.push({ ...it, name: it.path.split('/').pop(), folder: f }); continue; }
      const prefix = path ? `${path}/` : '';
      if (!f.startsWith(prefix)) continue;
      const name = f.slice(prefix.length).split('/')[0];
      if (!name) continue;
      const p = prefix + name;
      const cur = folders.get(p) || { name, path: p, platform: path ? null : (NAME_PLAT[name] || null), system: !path || !!TYPE_NAME.video === name || name === TYPE_NAME.audio, count: 0, size: 0, modified: 0 };
      cur.count += 1; cur.size += it.size || 0; cur.modified = Math.max(cur.modified, it.added || 0);
      folders.set(p, cur);
    }
    if (!path) for (const k of PLAT_ORDER) if (!folders.has(PLAT_NAME[k])) folders.set(PLAT_NAME[k], { name: PLAT_NAME[k], path: PLAT_NAME[k], platform: k, system: true, count: 0, size: 0, modified: 0 });
    const crumbs = []; let acc = '';
    for (const part of path.split('/').filter(Boolean)) { acc = acc ? `${acc}/${part}` : part; crumbs.push({ name: part, path: acc }); }
    return { path, name: path.split('/').pop() || '', breadcrumbs: crumbs, folders: [...folders.values()], files, rev: ex.library?.stats?.count ?? 0 };
  }
  function virtualTree() {
    const build = (path) => virtualFs(path).folders.map((f) => ({ ...f, children: build(f.path) }));
    return { name: 'Хранилище', path: '', platform: null, system: true, count: ex.library?.stats?.count || 0, size: ex.library?.stats?.size || 0, children: build('') };
  }

  async function load(path = ex.path, { soft = false } = {}) {
    const seq = ++ex.pending;
    try {
      if (!ex.library || !soft) await loadLibrary();
      const data = await fetchFs(path);
      if (seq !== ex.pending) return;
      ex.path = path; ex.data = data; ex.rev = data.rev;
      render({ soft });
      emit('change', data);
      fetchTree().then((t) => { if (seq === ex.pending) { ex.tree = t; renderTree(); emit('tree', t); } }).catch(() => {});
    } catch (err) {
      if (seq !== ex.pending) return;
      if (path) { toast(err.message || 'Папка недоступна', 'warn'); return navigate('', { soft: true }); }
      toast(err.message, 'err');
    }
  }
  function navigate(path, opts = {}) {
    if (path === ex.path && !opts.force) return Promise.resolve();
    ex.selected.clear(); ex.focus = null; ex.anchor = null;
    ex.search = ''; $('#lib-search').value = '';
    emit('navigate', path);
    return load(path, { soft: true });
  }
  const refresh = () => load(ex.path, { soft: true });

  /* ---------- сортировка и фильтр ---------- */

  function sortItems(list) {
    const by = { date: (a, b) => (b.added || 0) - (a.added || 0), name: (a, b) => (a.title || '').localeCompare(b.title || '', 'ru'), size: (a, b) => (b.size || 0) - (a.size || 0), duration: (a, b) => (b.duration || 0) - (a.duration || 0) }[ex.sort] || (() => 0);
    return [...list].sort(by);
  }
  function visibleFiles() {
    const q = ex.search.trim().toLocaleLowerCase('ru');
    if (q && ex.everywhere) {
      const all = (ex.library?.items || []).map((it) => ({ ...it, name: it.path.split('/').pop(), folder: itemFolder(it) }));
      return sortItems(all.filter((it) => `${it.title} ${it.uploader || ''} ${it.path}`.toLocaleLowerCase('ru').includes(q)));
    }
    let list = ex.data?.files || [];
    if (q) list = list.filter((it) => `${it.title} ${it.uploader || ''} ${it.name || ''}`.toLocaleLowerCase('ru').includes(q));
    return sortItems(list);
  }

  /* ---------- отрисовка ---------- */

  function render({ soft = false } = {}) {
    const d = ex.data;
    if (!d) return;
    const searching = !!(ex.search.trim() && ex.everywhere);
    renderCrumbs(d);
    const folders = searching ? [] : (d.folders || []);
    const depts = !d.path ? folders.filter((f) => f.system && f.platform) : [];
    const plain = !d.path ? folders.filter((f) => !(f.system && f.platform)) : folders;
    depts.sort((a, b) => PLAT_ORDER.indexOf(a.platform) - PLAT_ORDER.indexOf(b.platform));
    plain.sort((a, b) => (a.system === b.system ? a.name.localeCompare(b.name, 'ru') : a.system ? -1 : 1));
    replaceKeyed(deptsEl, depts, keyOfFolder, deptEl, soft);
    replaceKeyed(foldersEl, plain, keyOfFolder, folderEl, soft);
    ex.items = visibleFiles();
    let grid = ex.grid;
    if (!grid) { grid = h('<div class="grid" role="list"></div>'); filesEl.append(grid); ex.grid = grid; }
    const ids = new Set(ex.items.map(keyOfFile));
    const removed = [];
    for (const [k, el] of ex.fileEls) if (!ids.has(k)) { removed.push(el); ex.fileEls.delete(k); }
    const first = !soft || !ex.fileEls.size;
    const mutate = () => {
      ex.items.forEach((it, i) => {
        const k = keyOfFile(it);
        let el = ex.fileEls.get(k);
        const sig = tileSig(it);
        if (el && el.dataset.sig !== sig) { const fresh = tileEl(it); el.replaceWith(fresh); el = fresh; }
        if (!el) { el = tileEl(it); if (first && !REDUCED) enter(el, { delay: Math.min(i, 12) * 28 }); }
        ex.fileEls.set(k, el);
        el.classList.toggle('selected', ex.selected.has(k));
        el.classList.toggle('focused', ex.focus === k);
        const at = grid.children[i];
        if (at !== el) grid.insertBefore(el, at || null);
      });
    };
    if (!first && (removed.length || orderChanged(grid))) flip(grid, mutate, removed); else { removed.forEach((el) => el.remove()); mutate(); }
    for (const k of [...ex.selected]) if (!ids.has(k) && !k.startsWith('f:')) ex.selected.delete(k);
    const nothing = !ex.items.length && !depts.length && !plain.length;
    emptyEl.hidden = !nothing;
    if (nothing) {
      const all = ex.library?.items?.length || 0;
      if (ex.search.trim()) { $('#lib-empty-title').textContent = 'Ничего не нашлось'; $('#lib-empty-sub').textContent = 'Попробуйте другой запрос или переключите поиск на «везде»'; }
      else if (!all) { $('#lib-empty-title').textContent = 'Здесь появятся ваши видео и музыка'; $('#lib-empty-sub').textContent = 'Вставьте ссылку выше — файл сохранится в хранилище и будет доступен без интернета'; }
      else { $('#lib-empty-title').textContent = 'В этой папке пусто'; $('#lib-empty-sub').textContent = 'Перетащите сюда файлы или нажмите «Сохранять сюда» — новые загрузки лягут в неё'; }
    }
    updateSelbar();
    $('#new-folder').disabled = ex.supported === false;
    $('#new-folder').title = ex.supported === false ? 'Сервер ещё не умеет папки — обновите выдру' : 'Новая папка (Ctrl+Shift+N)';
    renderSaveHere();
  }
  function renderSaveHere() {
    saveHereBtn.hidden = !ex.path;
    const on = !!ex.path && ex.dest === ex.path;
    saveHereBtn.setAttribute('aria-pressed', String(on));
    saveHereBtn.querySelector('span').textContent = on ? 'Сохраняется сюда' : 'Сохранять сюда';
    saveHereBtn.title = on ? 'Новые загрузки идут в эту папку. Нажмите, чтобы вернуть автоматический выбор' : 'Сохранять новые загрузки в эту папку';
  }
  function orderChanged(grid) { return [...grid.children].map((el) => el.dataset.key).join(',') !== ex.items.map(keyOfFile).join(','); }
  function replaceKeyed(container, list, keyFn, make, soft) {
    const existing = new Map([...container.children].map((el) => [el.dataset.key, el]));
    const keys = new Set(list.map(keyFn));
    const removed = [...existing].filter(([k]) => !keys.has(k)).map(([, el]) => el);
    const mutate = () => {
      list.forEach((f, i) => {
        const k = keyFn(f);
        let el = existing.get(k);
        const sig = `${f.count}|${f.size}|${f.name}`;
        if (el && el.dataset.sig !== sig) { const fresh = make(f); el.replaceWith(fresh); el = fresh; }
        if (!el) { el = make(f); if (!soft && !REDUCED) enter(el, { delay: Math.min(i, 8) * 30 }); }
        el.classList.toggle('selected', ex.selected.has(k));
        el.classList.toggle('focused', ex.focus === k);
        const at = container.children[i];
        if (at !== el) container.insertBefore(el, at || null);
      });
    };
    if (soft && existing.size && (removed.length || list.length !== existing.size)) flip(container, mutate, removed); else { removed.forEach((el) => el.remove()); mutate(); }
  }
  function renderCrumbs(d) {
    const parts = [{ name: 'Хранилище', path: '', home: true }, ...(d.breadcrumbs || [])];
    crumbsEl.replaceChildren(...parts.flatMap((c, i) => {
      const last = i === parts.length - 1;
      const el = h(`<button class="crumb" type="button" data-path="${esc(c.path)}"${last ? ' aria-current="page"' : ''}>${c.home ? svgUse('#i-home') : ''}<span></span></button>`);
      el.querySelector('span').textContent = c.name;
      el.addEventListener('click', () => { if (!last) navigate(c.path); });
      return last ? [el] : [el, h(`<span class="crumb-sep">${svgUse('#i-chev-r')}</span>`)];
    }));
  }
  const treeOpen = new Set();
  function renderTree() {
    if (!ex.tree) return;
    const openPath = new Set(ex.path.split('/').filter(Boolean).reduce((acc, p) => { acc.push(acc.length ? `${acc[acc.length - 1]}/${p}` : p); return acc; }, []));
    treeEl.replaceChildren(treeNode(ex.tree, 0, openPath));
  }
  function treeNode(node, depth, openPath) {
    const kids = node.children || [];
    const isOpen = depth === 0 || treeOpen.has(node.path) || openPath.has(node.path);
    const frag = document.createDocumentFragment();
    const el = h(`<div class="tnode${isOpen ? ' open' : ''}" role="treeitem" data-path="${esc(node.path)}"${node.platform ? ` data-platform="${esc(node.platform)}"` : ''}${node.path === ex.path ? ' aria-current="true"' : ''}>
      <span class="tw${kids.length ? '' : ' leaf'}">${svgUse('#i-chev-r')}</span>${svgUse(node.platform ? glyph(node.platform) : depth === 0 ? '#i-drive' : '#i-folder', 'ti')}<span class="tn"></span><span class="tc">${node.count ? node.count : ''}</span></div>`);
    el.querySelector('.tn').textContent = node.name || 'Хранилище';
    el.style.paddingLeft = `${8 + depth * 6}px`;
    el.addEventListener('click', (e) => { if (e.target.closest('.tw') && kids.length) { toggleTree(el, node.path); return; } navigate(node.path); });
    frag.append(el);
    if (kids.length) {
      const box = h('<div class="tkids"></div>');
      box.hidden = !isOpen;
      for (const k of kids) box.append(treeNode(k, depth + 1, openPath));
      frag.append(box);
    }
    return frag;
  }
  function toggleTree(el, path) { const box = el.nextElementSibling; const open = el.classList.toggle('open'); if (open) treeOpen.add(path); else treeOpen.delete(path); if (box) box.hidden = !open; }

  function deptEl(f) {
    const el = h(`<button class="dept" type="button" data-key="${esc(keyOfFolder(f))}" data-path="${esc(f.path)}" data-platform="${esc(f.platform || '')}" data-sig="${f.count}|${f.size}|${esc(f.name)}" data-folder>
      <span class="dept-glyph">${svgUse(glyph(f.platform))}</span><b></b><small></small>${svgUse('#i-chev-r', 'dept-arrow')}</button>`);
    el.querySelector('b').textContent = f.name;
    el.querySelector('small').textContent = f.count ? `${f.count} ${plural(f.count, 'файл', 'файла', 'файлов')} · ${fmtBytes(f.size)}` : 'пусто';
    return el;
  }
  function folderEl(f) {
    const el = h(`<button class="folder" type="button" data-key="${esc(keyOfFolder(f))}" data-path="${esc(f.path)}" data-sig="${f.count}|${f.size}|${esc(f.name)}" data-folder${f.system ? ' data-system' : ''}>${svgUse('#i-folder')}<span class="fn"></span><span class="fc"></span></button>`);
    el.querySelector('.fn').textContent = f.name;
    el.querySelector('.fc').textContent = f.count ? String(f.count) : '';
    el.title = f.name;
    return el;
  }
  const tileSig = (it) => [it.poster, it.title, it.duration, it.size, it.width, it.height, it.type, it.folder].join('|');
  function tileAr(it) { if (it.type === 'audio') return 1; const ar = it.width && it.height ? it.width / it.height : 16 / 9; return Math.max(0.72, Math.min(1.9, ar)); }
  function tileEl(it) {
    const isAudio = it.type === 'audio';
    const poster = it.poster ? `<img src="${esc(libUrl(it.poster))}" alt="" loading="lazy" decoding="async" draggable="false">` : artHtml(it.id, isAudio ? 'MP3' : 'MP4');
    const where = ex.search.trim() && ex.everywhere ? (it.folder || '') : '';
    const meta = [PLAT_NAME[it.platform] || '', fmtBytes(it.size), fmtAgo(it.added)].filter(Boolean).join(' · ');
    const el = h(`<article class="tile" role="listitem" style="--ar:${tileAr(it)}" data-key="${esc(keyOfFile(it))}" data-id="${esc(it.id)}" data-sig="${esc(tileSig(it))}" tabindex="-1">
      <span class="tile-check">${svgUse('#i-check')}</span>
      <div class="tile-media">${poster}<span class="tile-scrim"></span><span class="tile-pf pf-${esc(it.platform)}">${svgUse(glyph(it.platform))}</span>${it.duration ? `<span class="tile-dur">${fmtTime(it.duration)}</span>` : ''}<span class="tile-play">${svgUse('#i-play')}</span></div>
      <span class="tile-info"><p class="tile-title"></p><p class="tile-meta">${esc(where ? `${where} · ${meta}` : meta)}</p></span>
      <span class="tile-col dur">${it.duration ? fmtTime(it.duration) : ''}</span><span class="tile-col size">${fmtBytes(it.size)}</span>
      <button class="tile-more" type="button" aria-label="Действия">${svgUse('#i-dots')}</button>
    </article>`);
    el.querySelector('.tile-title').textContent = it.title;
    el.setAttribute('aria-label', `${isAudio ? 'Слушать' : 'Смотреть'}: ${it.title}`);
    const img = el.querySelector('img');
    if (img) img.addEventListener('error', () => img.replaceWith(h(artHtml(it.id, isAudio ? 'MP3' : 'MP4'))));
    if (!isAudio) attachHoverPreview(el.querySelector('.tile-media'), it);
    return el;
  }
  function plural(n, one, few, many) { const m10 = n % 10, m100 = n % 100; if (m10 === 1 && m100 !== 11) return one; if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few; return many; }

  /* ---------- выделение ---------- */

  const itemOf = (key) => (key.startsWith('i:') ? ex.items.find((it) => keyOfFile(it) === key) : null);
  const folderOf = (key) => (ex.data?.folders || []).find((f) => keyOfFolder(f) === key);
  const elOf = (key) => (key.startsWith('i:') ? ex.fileEls.get(key) : root.querySelector(`.ex-main [data-key="${CSS.escape(key)}"]`));
  const orderedKeys = () => [...deptsEl.children, ...foldersEl.children, ...(ex.grid?.children || [])].map((el) => el.dataset.key).filter(Boolean);
  function select(key, { toggle = false, range = false } = {}) {
    if (range && ex.anchor) {
      const keys = orderedKeys(); const a = keys.indexOf(ex.anchor), b = keys.indexOf(key);
      if (a >= 0 && b >= 0) { ex.selected.clear(); for (let i = Math.min(a, b); i <= Math.max(a, b); i++) ex.selected.add(keys[i]); }
    } else if (toggle) { if (ex.selected.has(key)) ex.selected.delete(key); else ex.selected.add(key); ex.anchor = key; }
    else { ex.selected.clear(); ex.selected.add(key); ex.anchor = key; }
    setFocus(key);
    paintSelection();
  }
  function clearSelection() { ex.selected.clear(); paintSelection(); }
  function selectAll() { orderedKeys().forEach((k) => ex.selected.add(k)); paintSelection(); }
  function setFocus(key) {
    if (ex.focus) elOf(ex.focus)?.classList.remove('focused');
    ex.focus = key;
    const el = key ? elOf(key) : null;
    if (el) { el.classList.add('focused'); el.scrollIntoView?.({ block: 'nearest' }); }
  }
  function paintSelection() { for (const el of root.querySelectorAll('.ex-main [data-key]')) el.classList.toggle('selected', ex.selected.has(el.dataset.key)); updateSelbar(); }
  function updateSelbar() {
    const n = ex.selected.size;
    selbar.hidden = n === 0;
    if (!n) return;
    $('#selbar-count').textContent = `${n} ${plural(n, 'объект', 'объекта', 'объектов')}`;
    const onlyFiles = [...ex.selected].every((k) => k.startsWith('i:'));
    selbar.querySelector('[data-sel="rename"]').hidden = n !== 1;
    selbar.querySelector('[data-sel="open"]').hidden = !(n === 1 && onlyFiles);
    selbar.querySelector('[data-sel="move"]').disabled = ex.supported === false;
  }

  /* ---------- действия ---------- */

  function openKey(key) {
    if (key.startsWith('f:')) { const f = folderOf(key); if (f) navigate(f.path); return; }
    const it = itemOf(key);
    if (it) openPlayer(it, elOf(key)?.querySelector('.tile-media'));
  }
  async function renameKey(key) {
    if (ex.supported === false) { toast('Сервер ещё не умеет переименовывать — обновите выдру', 'warn'); return; }
    const el = elOf(key); if (!el) return;
    const isFolder = key.startsWith('f:');
    const f = isFolder ? folderOf(key) : itemOf(key);
    if (!f) return;
    if (isFolder && f.system) { toast('Это системная папка — её имя менять нельзя', 'warn'); return; }
    const oldName = isFolder ? f.name : (f.name || f.path.split('/').pop());
    const input = h('<input class="rename" type="text" aria-label="Новое имя">');
    input.value = oldName;
    const target = isFolder ? el.querySelector('.fn') : el.querySelector('.tile-info');
    target.style.visibility = 'hidden';
    el.append(input);
    input.focus();
    const dot = isFolder ? -1 : oldName.lastIndexOf('.');
    input.setSelectionRange(0, dot > 0 ? dot : oldName.length);
    let done = false;
    const finish = async (commit) => {
      if (done) return; done = true;
      const name = input.value.trim();
      input.remove(); target.style.visibility = '';
      if (!commit || !name || name === oldName) return;
      const label = target.querySelector?.('.tile-title') || target;
      const prevText = label.textContent;
      label.textContent = isFolder ? name : name.replace(/\.[^.]+$/, '');
      try { await api('/api/fs/rename', { method: 'POST', body: { path: f.path, name } }); toast('Переименовано', 'ok', { timeout: 1800 }); refresh(); }
      catch (err) { label.textContent = prevText; toast(err.message || 'Не получилось переименовать', 'err'); }
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); finish(true); } else if (e.key === 'Escape') { e.preventDefault(); finish(false); } e.stopPropagation(); });
    input.addEventListener('blur', () => finish(true));
    input.addEventListener('click', (e) => e.stopPropagation());
    input.addEventListener('pointerdown', (e) => e.stopPropagation());
  }
  async function deleteKeys(keys) {
    keys = [...keys];
    const files = keys.filter((k) => k.startsWith('i:')).map(itemOf).filter(Boolean);
    const folders = keys.filter((k) => k.startsWith('f:')).map(folderOf).filter(Boolean);
    if (!files.length && !folders.length) return;
    if (folders.some((f) => f.system)) { toast('Системные папки удалить нельзя', 'warn'); return; }
    if (folders.length && ex.supported === false) { toast('Сервер ещё не умеет удалять папки', 'warn'); return; }
    const n = files.length + folders.length;
    const what = n === 1 ? (files[0] ? `«${files[0].title}»` : `папку «${folders[0].name}»`) : `${n} ${plural(n, 'объект', 'объекта', 'объектов')}`;
    const ok = await confirmDialog({ title: 'В Корзину?', text: `${what} — можно будет восстановить из Корзины.`, yes: 'Удалить', danger: true });
    if (ok !== 'yes') return;
    const els = keys.map(elOf).filter(Boolean);
    await Promise.all(els.map((el) => exit(el, { dy: 0, scale: 0.9, blur: 4 })));
    const removedKeys = new Set(keys);
    if (ex.data) { ex.data.files = ex.data.files.filter((it) => !removedKeys.has(keyOfFile(it))); ex.data.folders = ex.data.folders.filter((f) => !removedKeys.has(keyOfFolder(f))); }
    ex.selected.clear();
    render({ soft: true });
    const results = await Promise.allSettled([
      ...files.map((f) => api(`/api/library/${encodeURIComponent(f.id)}`, { method: 'DELETE' })),
      ...folders.map((f) => api(`/api/fs?path=${encodeURIComponent(f.path)}&recursive=true`, { method: 'DELETE' })),
    ]);
    const failed = results.filter((r) => r.status === 'rejected');
    if (failed.length) toast(`Не всё удалилось: ${failed[0].reason?.message || 'ошибка сервера'}`, 'err');
    else toast(n === 1 ? 'Перемещено в Корзину' : `В Корзине: ${n}`, 'ok', { timeout: 2200 });
    if (ex.library) ex.library.items = ex.library.items.filter((it) => !files.some((f) => f.id === it.id));
    await refresh();
  }
  async function moveKeys(keys, to) {
    keys = [...keys].filter((k) => !(k.startsWith('f:') && folderOf(k)?.path === to));
    if (!keys.length) return;
    if (ex.supported === false) { toast('Сервер ещё не умеет перемещать — обновите выдру', 'warn'); return; }
    const paths = keys.map((k) => (k.startsWith('f:') ? folderOf(k)?.path : itemOf(k)?.path)).filter(Boolean);
    if (paths.some((p) => to === p || to.startsWith(`${p}/`))) { toast('Папку нельзя переместить в саму себя', 'warn'); return; }
    const set = new Set(keys);
    const backup = { files: ex.data.files, folders: ex.data.folders };
    ex.data.files = ex.data.files.filter((it) => !set.has(keyOfFile(it)));
    ex.data.folders = ex.data.folders.filter((f) => !set.has(keyOfFolder(f)));
    ex.selected.clear();
    render({ soft: true });
    try {
      const r = await api('/api/fs/move', { method: 'POST', body: { paths, to } });
      const n = (r?.moved || paths).length;
      toast(`Перемещено: ${n} → ${to.split('/').pop() || 'Хранилище'}`, 'ok', { timeout: 2200 });
      refresh();
    } catch (err) { ex.data.files = backup.files; ex.data.folders = backup.folders; render({ soft: true }); toast(err.message || 'Не получилось переместить', 'err'); }
  }
  async function newFolder() {
    if (ex.supported === false) { toast('Сервер ещё не умеет папки — обновите выдру', 'warn'); return; }
    if (foldersEl.querySelector('[data-key="f:__new"]')) return;
    const chip = h(`<span class="folder" data-key="f:__new">${svgUse('#i-folder')}<input class="rename" type="text" placeholder="Новая папка" aria-label="Имя новой папки"></span>`);
    foldersEl.prepend(chip);
    const input = chip.querySelector('input');
    input.focus();
    let done = false;
    const finish = async (commit) => {
      if (done) return; done = true;
      const name = input.value.trim();
      chip.remove();
      if (!commit || !name) return;
      try {
        const f = await api('/api/fs/folder', { method: 'POST', body: { parent: ex.path, name } });
        toast(`Папка «${f?.name || name}» создана`, 'ok', { timeout: 2000 });
        await refresh();
        select(`f:${f?.path || (ex.path ? `${ex.path}/${name}` : name)}`);
      } catch (err) { toast(err.message || 'Не получилось создать папку', 'err'); }
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); finish(true); } else if (e.key === 'Escape') { e.preventDefault(); finish(false); } e.stopPropagation(); });
    input.addEventListener('blur', () => finish(true));
  }
  async function moveViaDialog(keys) {
    if (ex.supported === false) { toast('Сервер ещё не умеет перемещать — обновите выдру', 'warn'); return; }
    const tree = ex.tree || await fetchTree().catch(() => null);
    if (!tree) return;
    const to = await pickFolder({ title: 'Куда переместить?', kicker: 'Переместить', tree, current: ex.path, disabled: keys.filter((k) => k.startsWith('f:')).map((k) => folderOf(k)?.path).filter(Boolean) });
    if (to == null) return;
    moveKeys(keys, to);
  }

  /* ---------- контекстное меню ---------- */

  function openMenu(x, y, key) {
    if (key && !ex.selected.has(key)) select(key);
    const keys = key ? [...ex.selected] : [];
    const one = keys.length === 1 ? keys[0] : null;
    const it = one ? itemOf(one) : null;
    const f = one ? folderOf(one) : null;
    const items = [];
    if (it) {
      items.push({ icon: '#i-play', label: it.type === 'audio' ? 'Слушать' : 'Смотреть', kbd: 'Enter', run: () => openKey(one) });
      items.push({ icon: '#i-external', label: 'Открыть в плеере', run: () => fileAction(it.id, 'open') });
      items.push({ icon: '#i-folder', label: 'Показать в папке', run: () => fileAction(it.id, 'reveal') });
      items.push({ sep: true });
    } else if (f) {
      items.push({ icon: '#i-folder-open', label: 'Открыть', kbd: 'Enter', run: () => openKey(one) });
      items.push({ icon: '#i-pin', label: ex.dest === f.path ? 'Не сохранять сюда' : 'Сохранять загрузки сюда', run: () => onDest(ex.dest === f.path ? '' : f.path) });
      items.push({ sep: true });
    }
    if (key && matchMedia('(pointer: coarse)').matches) items.push({ icon: '#i-select', label: ex.selected.size > 1 ? 'Снять выделение' : 'Выделить', run: () => { if (ex.selected.size > 1) clearSelection(); else select(key, { toggle: false }); } });
    if (keys.length) {
      if (one) items.push({ icon: '#i-pencil', label: 'Переименовать', kbd: 'F2', run: () => renameKey(one), disabled: ex.supported === false || !!f?.system });
      items.push({ icon: '#i-move', label: 'Переместить в…', run: () => moveViaDialog(keys), disabled: ex.supported === false || !!f?.system });
      items.push({ icon: '#i-trash', label: 'Удалить в Корзину', kbd: 'Del', danger: true, run: () => deleteKeys(keys), disabled: !!f?.system });
      items.push({ sep: true });
    }
    items.push({ icon: '#i-folder-plus', label: 'Новая папка', kbd: 'Ctrl+Shift+N', run: newFolder, disabled: ex.supported === false });
    items.push({ icon: '#i-refresh', label: 'Обновить', run: () => refresh() });
    ctxMenu.replaceChildren(...items.map((m) => {
      if (m.sep) return h('<hr>');
      const b = h(`<button type="button" role="menuitem"${m.danger ? ' class="danger"' : ''}${m.disabled ? ' disabled' : ''}>${svgUse(m.icon)}<span></span>${m.kbd ? `<kbd>${m.kbd}</kbd>` : ''}</button>`);
      b.querySelector('span').textContent = m.label;
      b.addEventListener('click', () => { closeMenu(); m.run(); });
      return b;
    }));
    ctxMenu.hidden = false;
    const r = ctxMenu.getBoundingClientRect();
    ctxMenu.style.left = `${Math.max(8, Math.min(x, innerWidth - r.width - 8))}px`;
    ctxMenu.style.top = `${Math.max(8, Math.min(y, innerHeight - r.height - 8))}px`;
    if (!REDUCED) { const m = motionOf(ctxMenu, { origin: 'top left' }); m.from({ s: 0.94, o: 0 }); m.to({ s: 1, o: 1 }, { response: 0.32, damping: 0.85 }); }
    ctxMenu.querySelector('button:not([disabled])')?.focus();
  }
  function closeMenu() { if (ctxMenu.hidden) return; ctxMenu.hidden = true; }
  document.addEventListener('pointerdown', (e) => { if (!ctxMenu.hidden && !e.target.closest('#ctx')) closeMenu(); });
  window.addEventListener('scroll', closeMenu, { passive: true });
  ctxMenu.addEventListener('keydown', (e) => {
    const btns = [...ctxMenu.querySelectorAll('button:not([disabled])')];
    const i = btns.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); btns[(i + 1) % btns.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); btns[(i - 1 + btns.length) % btns.length]?.focus(); }
    else if (e.key === 'Escape') { e.preventDefault(); closeMenu(); main.focus({ preventScroll: true }); }
  });
  main.addEventListener('contextmenu', (e) => { const el = e.target.closest('[data-key]'); e.preventDefault(); openMenu(e.clientX, e.clientY, el?.dataset.key || null); });

  /* ---------- клики и клавиатура ---------- */

  main.addEventListener('click', (e) => {
    if (e.target.closest('.rename')) return;
    const more = e.target.closest('.tile-more');
    if (more) { const el = more.closest('[data-key]'); const r = more.getBoundingClientRect(); openMenu(r.left, r.bottom + 4, el.dataset.key); e.stopPropagation(); return; }
    const el = e.target.closest('[data-key]');
    if (!el) { if (!e.shiftKey && !e.ctrlKey && !e.metaKey) clearSelection(); return; }
    if (dragState.moved) { dragState.moved = false; return; }
    const key = el.dataset.key;
    const coarse = matchMedia('(pointer: coarse)').matches;
    if (e.ctrlKey || e.metaKey) { select(key, { toggle: true }); return; }
    if (e.shiftKey) { select(key, { range: true }); return; }
    if (coarse || e.detail >= 2 || key.startsWith('f:')) { setFocus(key); openKey(key); return; }
    select(key);
  });
  main.addEventListener('keydown', (e) => {
    if (e.target.closest('input, textarea')) return;
    const keys = orderedKeys();
    const i = ex.focus ? keys.indexOf(ex.focus) : -1;
    const move = (j) => { const k = keys[Math.max(0, Math.min(keys.length - 1, j))]; if (!k) return; if (e.shiftKey) { if (!ex.anchor) ex.anchor = ex.focus || k; select(k, { range: true }); } else select(k); };
    switch (e.key) {
      case 'ArrowRight': e.preventDefault(); move(i + 1); break;
      case 'ArrowLeft': e.preventDefault(); move(i - 1); break;
      case 'ArrowDown': e.preventDefault(); move(i + columnsAt(i)); break;
      case 'ArrowUp': e.preventDefault(); move(i - columnsAt(i)); break;
      case 'Home': e.preventDefault(); move(0); break;
      case 'End': e.preventDefault(); move(keys.length - 1); break;
      case 'Enter': if (ex.focus) { e.preventDefault(); openKey(ex.focus); } break;
      case ' ': if (ex.focus) { e.preventDefault(); select(ex.focus, { toggle: true }); } break;
      case 'Backspace': if (ex.path) { e.preventDefault(); navigate(ex.path.split('/').slice(0, -1).join('/')); } break;
      case 'Escape': e.preventDefault(); clearSelection(); break;
      case 'Delete': if (ex.selected.size) { e.preventDefault(); deleteKeys(ex.selected); } break;
      case 'F2': if (ex.focus) { e.preventDefault(); renameKey(ex.focus); } break;
      case 'a': case 'A': case 'ф': case 'Ф': if (e.ctrlKey || e.metaKey) { e.preventDefault(); selectAll(); } break;
      default: break;
    }
  });
  function columnsAt(i) {
    const keys = orderedKeys();
    const el = elOf(keys[Math.max(0, i)]);
    if (!el) return 1;
    return Math.max(1, [...el.parentElement.children].filter((c) => c.offsetTop === el.offsetTop).length);
  }

  /* ---------- перетаскивание в папку (мышь и палец) ---------- */

  const dragState = { active: false, moved: false, keys: [], ghost: null, gm: null, target: null, pointerId: null, startX: 0, startY: 0, el: null };
  main.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || e.target.closest('.rename, .tile-more')) return;
    const el = e.target.closest('[data-key]');
    if (!el || (el.dataset.folder != null && el.dataset.system != null && !el.classList.contains('dept'))) { if (!el) return; }
    if (!el) return;
    dragState.startX = e.clientX; dragState.startY = e.clientY; dragState.pointerId = e.pointerId; dragState.el = el; dragState.moved = false; dragState.touch = e.pointerType === 'touch';
  });
  window.addEventListener('pointermove', (e) => {
    if (dragState.pointerId !== e.pointerId) return;
    const dx = e.clientX - dragState.startX, dy = e.clientY - dragState.startY;
    if (!dragState.active) {
      if (Math.hypot(dx, dy) < 8) return;
      if (dragState.touch) { dragState.pointerId = null; return; } // палец прокручивает страницу; перенос — через «Переместить в…»
      beginDrag(e);
    }
    if (dragState.active) moveDrag(e);
  }, { passive: true });
  const endPointer = (e) => { if (dragState.pointerId !== e.pointerId) return; if (dragState.active) endDrag(); dragState.pointerId = null; };
  window.addEventListener('pointerup', endPointer);
  window.addEventListener('pointercancel', endPointer);
  function beginDrag(e) {
    const key = dragState.el.dataset.key;
    if (!ex.selected.has(key)) select(key);
    dragState.keys = [...ex.selected].filter((k) => !(k.startsWith('f:') && folderOf(k)?.system));
    if (!dragState.keys.length) { dragState.pointerId = null; return; }
    dragState.active = true; dragState.moved = true;
    const first = dragState.keys[0];
    const it = itemOf(first);
    const ghost = h(`<div class="ghost">${it?.poster ? `<img src="${esc(libUrl(it.poster))}" alt="">` : svgUse(first.startsWith('f:') ? '#i-folder' : '#i-film')}<span class="gname"></span>${dragState.keys.length > 1 ? `<span class="gcount">${dragState.keys.length}</span>` : ''}</div>`);
    ghost.querySelector('.gname').textContent = it ? it.title : folderOf(first)?.name || '';
    document.body.append(ghost);
    dragState.ghost = ghost;
    const m = motionOf(ghost, { response: 0.22, damping: 1 });
    m.from({ x: e.clientX + 14, y: e.clientY + 14, s: 0.9, o: 0 });
    m.to({ s: 1, o: 1 }, { response: 0.3 });
    dragState.gm = m;
    for (const k of dragState.keys) elOf(k)?.classList.add('dragging');
  }
  function dropTargetAt(x, y) {
    const t = document.elementFromPoint(x, y)?.closest('.dept, .folder, .tnode, .crumb');
    if (!t || t.dataset.path == null) return null;
    const path = t.dataset.path;
    if (dragState.keys.some((k) => k === `f:${path}`)) return null;
    if (path === ex.path && !t.classList.contains('crumb')) return null;
    return { el: t, path };
  }
  function moveDrag(e) {
    const t = dropTargetAt(e.clientX, e.clientY);
    if (dragState.target?.el !== t?.el) { dragState.target?.el.classList.remove('drop-target'); t?.el.classList.add('drop-target'); dragState.target = t; }
    dragState.gm.to({ x: e.clientX + 14, y: e.clientY + 14 }, { response: 0.2, damping: 1 });
  }
  function endDrag() {
    dragState.active = false;
    for (const k of dragState.keys) elOf(k)?.classList.remove('dragging');
    const ghost = dragState.ghost, m = dragState.gm, t = dragState.target;
    dragState.target?.el.classList.remove('drop-target');
    dragState.target = null; dragState.ghost = null;
    if (t) { const r = t.el.getBoundingClientRect(); m.to({ x: r.left + r.width / 2 - 20, y: r.top + r.height / 2 - 12, s: 0.4, o: 0 }, { response: 0.32, damping: 1 }).then(() => ghost.remove()); moveKeys(dragState.keys, t.path); }
    else m.to({ s: 0.9, o: 0 }, { response: 0.28, damping: 1 }).then(() => ghost.remove());
    dragState.keys = [];
  }

  /* ---------- панель выделения и инструменты ---------- */

  selbar.addEventListener('click', (e) => {
    const b = e.target.closest('[data-sel]'); if (!b) return;
    const keys = [...ex.selected];
    switch (b.dataset.sel) {
      case 'open': openKey(keys[0]); break;
      case 'move': moveViaDialog(keys); break;
      case 'rename': renameKey(keys[0]); break;
      case 'delete': deleteKeys(keys); break;
      case 'clear': clearSelection(); break;
      default: break;
    }
  });
  let searchTimer = 0;
  $('#lib-search').addEventListener('input', (e) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { ex.search = e.target.value; render({ soft: true }); }, 140); });
  $('#lib-search').addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.target.value = ''; ex.search = ''; render({ soft: true }); e.target.blur(); } });
  $('#search-scope').addEventListener('click', (e) => { ex.everywhere = !ex.everywhere; e.currentTarget.setAttribute('aria-pressed', String(ex.everywhere)); e.currentTarget.textContent = ex.everywhere ? 'везде' : 'здесь'; render({ soft: true }); });
  $('#sort-select').value = ex.sort;
  $('#sort-select').addEventListener('change', (e) => { ex.sort = e.target.value; try { localStorage.setItem('vd.sort', ex.sort); } catch { /* */ } render({ soft: true }); });
  $('#new-folder').addEventListener('click', newFolder);
  saveHereBtn.addEventListener('click', () => onDest(ex.dest === ex.path ? '' : ex.path));
  function setView(v) { ex.view = v; root.dataset.view = v; try { localStorage.setItem('vd.view', v); } catch { /* */ } }
  function setDest(path) { ex.dest = path || ''; renderSaveHere(); }

  /* нативное перетаскивание файлов ОС на папку — цель для загрузки конвертера */
  function folderAt(x, y) { const t = document.elementFromPoint(x, y)?.closest('.dept, .folder, .tnode, .crumb'); return t?.dataset.path ?? null; }
  function highlightDrop(x, y) {
    const under = document.elementFromPoint(x, y)?.closest('.dept, .folder, .tnode, .crumb');
    for (const el of root.querySelectorAll('.os-drop')) if (el !== under) el.classList.remove('os-drop', 'drop-target');
    if (under && under.dataset.path != null) under.classList.add('os-drop', 'drop-target');
    return under?.dataset.path ?? null;
  }
  function clearDropHighlight() { for (const el of root.querySelectorAll('.os-drop')) el.classList.remove('os-drop', 'drop-target'); }

  return {
    state: ex, load, refresh, navigate, setView, setDest, newFolder, on, folderAt, highlightDrop, clearDropHighlight, loadLibrary, fetchTree,
    currentPath: () => ex.path, currentName: () => ex.data?.name || '',
  };
}
