// Texte der Oberfläche: Deutsch (texte-de.js) und Englisch (texte-en.js), gleiche Schlüssel (test/i18n.test.js prüft das).
// t('schluessel', {platzhalter}) → Text. Fehlt ein Schlüssel in der Sprache, gilt Deutsch (falls geladen), sonst der Schlüssel.
//
// Sprache (ADR 0006): ?sprache=de|en → Wahl im Browser (localStorage „sprache“, Schalter in der Kopfzeile, für Angemeldete
// aus dem Konto übernommen, siehe common.js) → navigator.languages (erste de/en-Angabe; sonst Englisch). <html lang> wird
// sofort gesetzt. Geladen wird nur das Wörterbuch der aktiven Sprache – als eigener Chunk per Top-Level-await, damit alle
// Module, die t() beim Laden aufrufen, schon die richtige Sprache haben.
// Statische Texte in den HTML-Seiten sind deutsch; Elemente mit data-t="schluessel" (Text), data-t-html (vertrauenswürdiges
// HTML aus dem Wörterbuch, z. B. mit Links) sowie data-t-aria / data-t-title / data-t-placeholder werden übersetzt,
// sobald eine andere Sprache als Deutsch aktiv ist.
export const SPRACHEN = ['de', 'en'];
export const woerterbuch = { de: null, en: null };

/** Sprache aus URL, gespeicherter Wahl und Browser-Sprachen (ohne DOM testbar). */
export function spracheErmitteln({ url = '', gespeichert = null, browser = [] } = {}) {
  try {
    const q = new URLSearchParams(url).get('sprache');
    if (SPRACHEN.includes(q)) return q;
  } catch { /* egal */ }
  if (SPRACHEN.includes(gespeichert)) return gespeichert;
  for (const b of browser || []) {
    const s = String(b || '').slice(0, 2).toLowerCase();
    if (SPRACHEN.includes(s)) return s;
  }
  return (browser || []).length ? 'en' : 'de';
}

export function gespeicherteSprache() {
  try { return globalThis.localStorage?.getItem('sprache') || null; } catch { return null; }
}

let sprache = 'de';
if (globalThis.document) {
  sprache = spracheErmitteln({
    url: globalThis.location?.search || '',
    gespeichert: gespeicherteSprache(),
    browser: globalThis.navigator?.languages?.length ? navigator.languages : [globalThis.navigator?.language].filter(Boolean),
  });
  document.documentElement.lang = sprache;
}

/** Wörterbuch nachladen (eigener Chunk). */
export async function woerterbuchLaden(s) {
  if (!woerterbuch[s]) woerterbuch[s] = s === 'en' ? (await import('./texte-en.js')).en : (await import('./texte-de.js')).de;
  return woerterbuch[s];
}
await woerterbuchLaden(sprache);

export const aktuelleSprache = () => sprache;
/** Gebietsschema für Intl (Datum, Uhrzeit, Zahlen). */
export const gebiet = () => (sprache === 'en' ? 'en-GB' : 'de-AT');

/**
 * Sprache wechseln: merken (localStorage) und neu laden – nur so passen auch alle schon gezeichneten Texte.
 * Gibt false zurück, wenn der Browser nichts speichern kann (dann bleibt alles, wie es ist).
 */
export function spracheWechseln(neu, { neuLaden = true } = {}) {
  if (!SPRACHEN.includes(neu)) return false;
  try {
    localStorage.setItem('sprache', neu);
    if (localStorage.getItem('sprache') !== neu) return false;
  } catch { return false; }
  if (neu !== sprache && neuLaden) {
    const u = new URL(location.href);
    u.searchParams.delete('sprache');
    location.replace(u.href);
  }
  return true;
}

export function t(schluessel, werte) {
  let s = woerterbuch[sprache]?.[schluessel] ?? woerterbuch.de?.[schluessel] ?? schluessel;
  if (werte) s = s.replace(/\{(\w+)\}/g, (_, k) => (werte[k] ?? `{${k}}`));
  return s;
}

const ATTRIBUTE = [['tAria', 'aria-label', 'data-t-aria'], ['tTitle', 'title', 'data-t-title'], ['tPlaceholder', 'placeholder', 'data-t-placeholder']];

/** Statische Texte der Seite übersetzen (nur wenn nicht Deutsch – Deutsch steht schon im HTML). */
export function seiteUebersetzen(wurzel = globalThis.document) {
  if (!wurzel || sprache === 'de') return;
  const buch = woerterbuch[sprache];
  for (const el of wurzel.querySelectorAll('[data-t]')) if (buch[el.dataset.t]) el.textContent = buch[el.dataset.t];
  // Nur Texte aus dem eigenen Wörterbuch (keine Nutzereingaben) – sie enthalten Links oder Hervorhebungen.
  for (const el of wurzel.querySelectorAll('[data-t-html]')) if (buch[el.dataset.tHtml]) el.innerHTML = buch[el.dataset.tHtml];
  for (const [feld, attr, sel] of ATTRIBUTE) {
    for (const el of wurzel.querySelectorAll(`[${sel}]`)) {
      const k = el.dataset[feld];
      if (buch[k]) el.setAttribute(attr, buch[k]);
    }
  }
}
