// Reine Helfer für die Video-Effekte (ohne DOM, mit node:test geprüft): gespeicherte Wahl prüfen, daraus das Ziel für den
// Prozessor ableiten, Bildausschnitt berechnen.

/** Eingebaute Hintergründe (scripts/hintergruende.mjs erzeugt je ein SVG). */
export const BILDER = ['nachthimmel', 'polarlicht', 'nebel', 'daemmerung', 'studio', 'morgen'];
/** Unschärfe-Radius des LiveKit-Hintergrundprozessors (Standard dort: 10; wirkt auf ¼-Auflösung als Sigma r/4) */
export const UNSCHAERFE = { leicht: 12, stark: 28 };
export const STANDARD = Object.freeze({ hintergrund: 'aus', verbessern: false });

/** Gespeicherte Wahl → immer gültige Wahl {hintergrund: 'aus'|'leicht'|'stark'|<bild>|'eigen', verbessern: bool}. */
export function wahlPruefen(roh) {
  const w = roh && typeof roh === 'object' ? roh : {};
  const h = typeof w.hintergrund === 'string' ? w.hintergrund : 'aus';
  const gueltig = h === 'aus' || h === 'eigen' || Object.hasOwn(UNSCHAERFE, h) || BILDER.includes(h);
  return { hintergrund: gueltig ? h : 'aus', verbessern: w.verbessern === true };
}

/**
 * Was soll der Prozessor tun? null = gar nichts (Prozessor ab), {art:'filter'} = nur „Aussehen verbessern“,
 * {art:'unschaerfe', radius} oder {art:'bild', id}. Ohne eigenes Bild fällt „eigen“ auf „aus“ zurück.
 */
export function zielAus(wahl, { eigenesBild = false, hintergrundOk = true, verbessernOk = true } = {}) {
  const w = wahlPruefen(wahl);
  const verbessern = w.verbessern && verbessernOk;
  let h = w.hintergrund;
  if (h === 'eigen' && !eigenesBild) h = 'aus';
  if (!hintergrundOk) h = 'aus';
  if (Object.hasOwn(UNSCHAERFE, h)) return { art: 'unschaerfe', radius: UNSCHAERFE[h], verbessern };
  if (h !== 'aus') return { art: 'bild', id: h, verbessern };
  return verbessern ? { art: 'filter', verbessern } : null;
}

/** Bild auf höchstens max Pixel (längste Seite) verkleinern, Seitenverhältnis bleibt. → {b, h} */
export function verkleinert(b, h, max) {
  const f = Math.min(1, max / Math.max(b, h, 1));
  return { b: Math.max(1, Math.round(b * f)), h: Math.max(1, Math.round(h * f)) };
}
