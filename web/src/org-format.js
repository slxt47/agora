// Organisationen: kleine, reine Hilfen (ohne DOM, in Node getestet – test/orgs.test.js).
export const ROLLEN = ['mitglied', 'admin', 'owner'];
export const FARBEN_ORG = ['#6d4aff', '#3b6ff6', '#0e8a7a', '#b4237a', '#c2410c', '#7c3aed', '#0f766e', '#be123c', '#1d4ed8', '#9333ea'];

/** Rang einer Rolle (mitglied 1, admin 2, owner 3; unbekannt 0). */
export const rolleRang = (r) => ROLLEN.indexOf(r) + 1;

const ohneZeichen = (w) => [...w.replace(/[^\p{L}\p{N}]/gu, '')];

/** Kürzel fürs Logo: Anfangsbuchstaben der ersten zwei Wörter („Firma Eins“ → „FE“), sonst die ersten zwei Zeichen. */
export function orgKuerzel(name) {
  const woerter = String(name || '').trim().split(/[\s\-_&+.<>/]+/u).filter((w) => ohneZeichen(w).length);
  if (!woerter.length) return '?';
  if (woerter.length === 1) return ohneZeichen(woerter[0]).slice(0, 2).join('').toUpperCase();
  return (ohneZeichen(woerter[0])[0] + ohneZeichen(woerter[1])[0]).toUpperCase();
}

/** Vorschlag für den Kurznamen (wie der Server: Umlaute umschreiben, nur a–z, 0–9 und Bindestrich, 3–40 Zeichen). */
export function slugVorschlag(name) {
  const ersatz = { ä: 'ae', ö: 'oe', ü: 'ue', ß: 'ss' };
  const s = String(name || '').toLowerCase().replace(/[äöüß]/g, (c) => ersatz[c]).replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '').slice(0, 40).replace(/-+$/, '');
  if (s.length >= 3) return s;
  return s ? `${s}-team` : '';
}
