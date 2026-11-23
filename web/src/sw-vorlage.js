// Service Worker (Vorlage; vite.config.js setzt Version und Vorab-Liste ein und legt ihn als /sw.js ab, Bereich „/“).
// Cacht NUR die App-Hülle und statische Dateien: die Offline-Seite samt ihren Dateien (vorab) und gehashte /assets/
// bzw. /icons/ (beim ersten Abruf). Nie: /api, /rtc, /vendor (MediaPipe, 9 MB – das macht der HTTP-Cache), Aufnahmen,
// Range-Anfragen, fremde Herkünfte. Seitenaufrufe gehen immer ins Netz; ohne Netz kommt die Seite „Keine Verbindung“.
// Ein neuer Service Worker wartet, bis die Seite ihn bittet ({typ:'aktivieren'} nach „Neu laden“) – kein Wechsel mitten
// im Meeting. Alte Caches („agora-…“) räumt die Aktivierung weg.
const CACHE = 'agora-__VERSION__';
const VORAB = __VORAB__;
const OFFLINE = '/offline.html';
const NIE = /^\/(api|rtc|vendor)(\/|$)/;
const STATISCH = /^\/(assets|icons)\//;

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(VORAB.map((u) => new Request(u, { cache: 'reload' })))));
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith('agora-') && k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener('message', (e) => {
  if (e.data && e.data.typ === 'aktivieren') self.skipWaiting();
});

self.addEventListener('fetch', (e) => {
  const r = e.request;
  if (r.method !== 'GET' || r.headers.has('range')) return;
  const u = new URL(r.url);
  if (u.origin !== self.location.origin || NIE.test(u.pathname)) return;
  if (r.mode === 'navigate') {
    e.respondWith(fetch(r).catch(async () => (await caches.match(OFFLINE, { cacheName: CACHE })) || Response.error()));
    return;
  }
  const vorab = VORAB.includes(u.pathname + u.search);
  if (!vorab && !STATISCH.test(u.pathname)) return;
  e.respondWith((async () => {
    const c = await caches.open(CACHE);
    const treffer = await c.match(r);
    if (treffer) return treffer;
    const antwort = await fetch(r);
    if (antwort.ok && antwort.type === 'basic' && STATISCH.test(u.pathname)) c.put(r, antwort.clone()).catch(() => {});
    return antwort;
  })());
});
