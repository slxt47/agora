// Service Worker (Vorlage; vite.config.js setzt Version und Vorab-Liste ein und legt ihn als /sw.js ab, Bereich „/“).
// Cacht NUR die App-Hülle und statische Dateien: die Offline-Seite samt ihren Dateien (vorab) und gehashte /assets/
// bzw. /icons/ (beim ersten Abruf). Nie: /api, /rtc, /vendor (MediaPipe, 9 MB – das macht der HTTP-Cache), Aufnahmen,
// Range-Anfragen, fremde Herkünfte. Seitenaufrufe gehen immer ins Netz; ohne Netz kommt die Seite „Keine Verbindung“.
// Ein neuer Service Worker wartet, bis die Seite ihn bittet ({typ:'aktivieren'} nach „Neu laden“) – kein Wechsel mitten
// im Meeting. Alte Caches („agora-…“) räumt die Aktivierung weg.
// Push (opt-in im Konto): zeigt die Benachrichtigung (Inhalt ist für dieses Gerät verschlüsselt, der Browser entschlüsselt)
// und öffnet beim Antippen die passende Seite – nur Pfade der eigenen Herkunft.
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

// ---------------------------------------------------------------- Push-Benachrichtigungen
/** Nur Pfade dieser Herkunft („/r/…“, „/konto#…“) – nie fremde Adressen aus der Nachricht. */
function zielPfad(url) {
  return typeof url === 'string' && url.startsWith('/') && !url.startsWith('//') && !url.includes('\\') ? url : '/';
}

self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { /* kein JSON */ }
  const titel = typeof d.titel === 'string' && d.titel ? d.titel.slice(0, 120) : 'Agora';
  e.waitUntil(self.registration.showNotification(titel, {
    body: typeof d.text === 'string' ? d.text.slice(0, 300) : '',
    tag: typeof d.tag === 'string' && d.tag ? d.tag.slice(0, 64) : undefined,
    data: { url: zielPfad(d.url) },
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const ziel = new URL(zielPfad(e.notification.data?.url), self.location.origin);
  e.waitUntil((async () => {
    const fenster = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of fenster) {
      const u = new URL(c.url);
      if (u.origin === ziel.origin && u.pathname === ziel.pathname && 'focus' in c) return c.focus();
    }
    return self.clients.openWindow(ziel.href);
  })());
});

// Der Push-Dienst hat das Abo erneuert: neues Abo mit demselben Schlüssel holen und im Konto ersetzen (Cookie der Seite).
self.addEventListener('pushsubscriptionchange', (e) => {
  e.waitUntil((async () => {
    const schluessel = e.oldSubscription?.options?.applicationServerKey;
    const neu = e.newSubscription || (schluessel ? await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: schluessel }) : null);
    if (!neu) return;
    await fetch('/api/push/abo', {
      method: 'POST', credentials: 'same-origin', headers: { 'X-Agora': '1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...neu.toJSON(), alt: e.oldSubscription?.endpoint || null }),
    }).catch(() => {});
  })());
});
