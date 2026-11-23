// Texte der Oberfläche. Heute nur Deutsch; Englisch kommt später als zweites Wörterbuch dazu (gleiche Schlüssel).
// t('schluessel', {platzhalter}) → Text. Fehlt ein Schlüssel in der Sprache, gilt Deutsch, fehlt er ganz, der Schlüssel.
// Statische Texte in den HTML-Seiten sind deutsch; Elemente mit data-t="schluessel" (und data-t-aria / data-t-title /
// data-t-placeholder) werden beim Laden übersetzt, sobald eine andere Sprache als Deutsch aktiv ist.
import { de } from './texte-de.js';

export const woerterbuch = { de, en: {} };

let sprache = 'de';
try {
  const s = (globalThis.document?.documentElement?.lang || 'de').slice(0, 2);
  if (woerterbuch[s]) sprache = s;
} catch { /* Node-Test */ }

export const aktuelleSprache = () => sprache;

export function spracheSetzen(neu) {
  if (!woerterbuch[neu]) return;
  sprache = neu;
  if (globalThis.document) { document.documentElement.lang = neu; seiteUebersetzen(); }
}

export function t(schluessel, werte) {
  let s = woerterbuch[sprache]?.[schluessel] ?? woerterbuch.de[schluessel] ?? schluessel;
  if (werte) s = s.replace(/\{(\w+)\}/g, (_, k) => (werte[k] ?? `{${k}}`));
  return s;
}

/** Statische Texte der Seite übersetzen (nur wenn nicht Deutsch – Deutsch steht schon im HTML). */
export function seiteUebersetzen(wurzel = globalThis.document) {
  if (!wurzel || sprache === 'de') return;
  const buch = woerterbuch[sprache];
  for (const el of wurzel.querySelectorAll('[data-t]')) if (buch[el.dataset.t]) el.textContent = buch[el.dataset.t];
  for (const [attr, name] of [['tAria', 'aria-label'], ['tTitle', 'title'], ['tPlaceholder', 'placeholder']]) {
    for (const el of wurzel.querySelectorAll(`[data-${name === 'aria-label' ? 't-aria' : name === 'title' ? 't-title' : 't-placeholder'}]`)) {
      const k = el.dataset[attr];
      if (buch[k]) el.setAttribute(name, buch[k]);
    }
  }
}
