const CACHE = 'managerws-static-v3';
const STATIC_ASSETS = ['/static/manifest.json', '/static/icon-192.png', '/static/icon-512.png'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== self.location.origin) return;
  const url = new URL(req.url);
  // Never cache application pages, dashboards, forms, or API-like routes containing private data.
  if (url.pathname === '/' || !url.pathname.startsWith('/static/')) return;
  event.respondWith(caches.match(req).then(cached => cached || fetch(req)));
});
