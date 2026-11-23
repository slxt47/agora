// Reine Entscheidungshilfen für Breakout-Räume (ohne DOM) – laufen im Browser und unter `node --test`.

/**
 * Wohin soll der Client wechseln? z = Server-Zustand (GET …/breakout → breakout, kann null sein),
 * lokal = {ort, runde, zuteilung} = wo ich gerade bin und was ich zuletzt vom Server kannte.
 * Ergebnis: Raumnummer (0 = Hauptraum) oder null (bleiben).
 *
 * - Räume zu (kein Zustand, Entwurf, geschlossen) und ich bin in einem Breakout-Raum → zurück (0).
 * - Räume offen: nur bei neuer Runde oder geänderter eigener Zuteilung automatisch dorthin – wer selbst in den
 *   Hauptraum zurückgegangen ist oder als Host einen Raum besucht, wird nicht herumgeschoben.
 * - Während des Countdowns („schliesst“) bleibt jede:r, wo sie/er ist.
 */
export function wohin(z, lokal) {
  const ort = lokal?.ort || 0;
  if (!z || z.status === 'entwurf' || z.status === 'geschlossen') return ort ? 0 : null;
  if (z.status !== 'offen') return null;
  const mein = z.mein_raum || 0;
  const neu = z.runde !== lokal?.runde || mein !== (lokal?.zuteilung ?? null);
  if (neu && mein !== ort && (mein || ort)) return mein;
  return null;
}

/** Gleichmäßige Verteilung als Vorschau: wie viele Personen kommen in jeden der n Räume? */
export function groessen(personen, n) {
  const p = Math.max(0, Math.floor(personen) || 0), r = Math.max(1, Math.floor(n) || 1);
  return Array.from({ length: r }, (_, i) => Math.floor(p / r) + (i < p % r ? 1 : 0));
}

/** Sinnvolle Vorgabe für die Zahl der Räume: etwa 3–4 Personen je Raum, mindestens 2, höchstens 50. */
export function vorschlagAnzahl(personen) {
  return Math.min(50, Math.max(2, Math.round((Number(personen) || 0) / 4)));
}
