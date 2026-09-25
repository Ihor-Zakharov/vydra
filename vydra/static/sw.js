// выдра — service worker. Оболочка открывается даже при выключенном сервере
// (тогда интерфейс покажет баннер «Сервер не запущен»). API никогда не кэшируется.
const SHELL = 'vydra-shell-v5';
const POSTERS = 'vydra-posters-v1';
const PRECACHE = [
  '/',
  '/static/css/core.css',
  '/static/css/shell.css',
  '/static/css/hero.css',
  '/static/css/jobs.css',
  '/static/css/library.css',
  '/static/css/dialogs.css',
  '/static/app.js',
  '/static/spring.js',
  '/static/cosmos.js',
  '/static/scene/plate.webp',
  '/static/explorer.js',
  '/static/fonts/fonts.css',
  '/static/manifest.webmanifest',
  '/static/icons/icon.svg',
  '/static/icons/icon-192.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(PRECACHE)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keep = new Set([SHELL, POSTERS]);
    for (const key of await caches.keys()) if (!keep.has(key)) await caches.delete(key);
    await self.clients.claim();
  })());
});

async function networkFirst(request) {
  const cache = await caches.open(SHELL);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(request, { ignoreSearch: request.mode === 'navigate' });
    if (cached) return cached;
    if (request.mode === 'navigate') return (await cache.match('/')) || Response.error();
    throw err;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(POSTERS);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) cache.put(request, response.clone());
  return response;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;
  if (url.pathname.startsWith('/lib/.vydra/posters/')) { event.respondWith(cacheFirst(request)); return; }
  if (request.mode === 'navigate' || url.pathname === '/' || url.pathname.startsWith('/static/')) {
    event.respondWith(networkFirst(request));
  }
});
