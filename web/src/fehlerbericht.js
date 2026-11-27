// Fehler-Monitoring im Browser (selbst gehostet, statt Sentry): window.onerror, unhandledrejection, LiveKit-Trennungsgründe
// (raum/meeting.js) und API-Aufrufe mit Status ≥ 500 (common.js) gehen an POST /api/fehlerbericht.
// Datenschutz: nur Pfad der Seite (location.pathname – nie href, also nie das Fragment mit dem E2EE-Schlüssel #k=…),
// Query und Fragment werden auch aus Nachricht und Stack entfernt, keine Namen/E-Mail-Adressen; der Server säubert noch
// einmal (app/fehlerbericht.py). Höchstens 10 Berichte je Seitenaufruf, jeder Fehler nur einmal.
// Ohne DOM testbar (test/fehlerbericht.test.js): bereinigen(), berichtBauen().

const MAX_JE_SEITE = 10;
const VERSION = typeof __AGORA_VERSION__ !== 'undefined' ? __AGORA_VERSION__ : 'dev';   // vite.config.js (package.json)
const gesendet = new Set();
let anzahl = 0;

/** Query, Fragment (#k=…), Herkunft, E-Mail-Adressen und Meeting-Codes entfernen; kürzen. */
export function bereinigen(text, maximal = 500) {
  return String(text ?? '')
    .slice(0, maximal * 4)
    .replace(/\b(?:https?|wss?):\/\/[^/\s'"<>()]+/gi, '')
    .replace(/(?:[?#]|%23|%3[Ff])[^\s'"<>()[\]{}]*/g, '')
    .replace(/\bk=[A-Za-z0-9_-]{8,}/g, '')
    .replace(/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g, '‹e-mail›')
    .replace(/\b[a-z]{3}-[a-z]{4}-[a-z]{3}\b/g, '‹code›')
    .trim()
    .slice(0, maximal);
}

/** Seite: nur der Pfad, Codes/Token in /r/, /w/, /a/, /e/ ersetzt. */
export function seiteBereinigen(pfad) {
  return bereinigen(String(pfad || '/').replace(/^\/(r|w|a|e)\/[^/?#]+/, '/$1/‹code›'), 200);
}

/** Bericht für /api/fehlerbericht (art: fehler | promise | livekit | api). */
export function berichtBauen(art, nachricht, stapel = '', seite = '/') {
  return {
    art, version: VERSION, seite: seiteBereinigen(seite),
    nachricht: bereinigen(nachricht) || '(ohne Text)',
    stapel: String(stapel || '').split('\n').slice(0, 20).map((z) => bereinigen(z, 300)).filter(Boolean).join('\n').slice(0, 4000),
  };
}

/** Senden (fire and forget, nie selbst einen Fehler werfen, nie sich selbst melden). */
export function fehlerMelden(art, nachricht, stapel = '') {
  try {
    if (!globalThis.fetch || !globalThis.location) return;
    const b = berichtBauen(art, nachricht, stapel, location.pathname);
    const schluessel = `${b.art}|${b.nachricht}|${b.stapel.split('\n')[0] || ''}`;
    if (gesendet.has(schluessel) || anzahl >= MAX_JE_SEITE) return;
    gesendet.add(schluessel);
    anzahl += 1;
    fetch('/api/fehlerbericht', {
      method: 'POST', credentials: 'same-origin', keepalive: true, cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-Agora': '1' }, body: JSON.stringify(b),
    }).catch(() => {});
  } catch { /* Monitoring darf nichts kaputt machen */ }
}

/** API-Aufruf mit Status ≥ 500 (Pfad ohne Query; IDs ersetzt der Server). */
export function apiFehlerMelden(methode, pfad, status) {
  if (status < 500 || String(pfad).startsWith('/api/fehlerbericht')) return;
  fehlerMelden('api', `${methode} ${String(pfad).split(/[?#]/)[0]} → ${status}`);
}

let gestartet = false;
/** window.onerror + unhandledrejection (einmal je Seite, aus common.js). */
export function fehlerMonitoringStarten() {
  if (gestartet || !globalThis.addEventListener) return;
  gestartet = true;
  addEventListener('error', (e) => {
    if (e.target && e.target !== window) return;                 // fehlende Bilder/Skripte: kein JS-Fehler
    const f = e.error;
    fehlerMelden('fehler', f?.message ? `${f.name || 'Error'}: ${f.message}` : e.message, f?.stack || (e.filename ? `at ${e.filename}:${e.lineno}:${e.colno}` : ''));
  });
  addEventListener('unhandledrejection', (e) => {
    const g = e.reason;
    if (g?.name === 'AbortError' || g?.name === 'ApiFehler' || (g && typeof g.status === 'number')) return;   // API-Fehler: siehe apiFehlerMelden
    fehlerMelden('promise', g?.message ? `${g.name || 'Error'}: ${g.message}` : String(g), g?.stack || '');
  });
}
