// Reine Rechenhilfen für Umfragen (ohne DOM) – laufen im Browser und unter `node --test` (test/umfragen.test.js).

/**
 * Anteile in Prozent je Antwort. Grundlage ist die Zahl der Abstimmenden (bei Mehrfachwahl kann die Summe > 100 sein).
 * Bei Einfachwahl wird nach der Methode der größten Reste gerundet, damit die Summe genau 100 ergibt.
 */
export function anteile(zaehler, stimmen, mehrfach = false) {
  const n = Array.isArray(zaehler) ? zaehler.map((z) => Math.max(0, Number(z) || 0)) : [];
  const basis = Math.max(0, Number(stimmen) || 0) || (mehrfach ? 0 : n.reduce((a, b) => a + b, 0));
  if (!basis) return n.map(() => 0);
  const roh = n.map((z) => (z * 100) / basis);
  if (mehrfach) return roh.map((x) => Math.round(x));
  const unten = roh.map(Math.floor);
  let rest = 100 - unten.reduce((a, b) => a + b, 0);
  const reihenfolge = roh.map((x, i) => [x - Math.floor(x), i]).sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  for (const [, i] of reihenfolge) { if (rest <= 0) break; if (n[i] > 0) { unten[i]++; rest--; } }
  return unten;
}

/** Indizes der Antwort(en) mit den meisten Stimmen (leer, wenn niemand abgestimmt hat). */
export function spitzenreiter(zaehler) {
  const max = Math.max(0, ...(zaehler || []));
  return max ? zaehler.map((z, i) => (z === max ? i : -1)).filter((i) => i >= 0) : [];
}

/** Antworten aus dem Formular: getrimmt, leere raus. Gibt {optionen, fehler} zurück (fehler = Schlüssel oder null). */
export function optionenPruefen(texte) {
  const optionen = (texte || []).map((s) => String(s ?? '').replace(/\s+/g, ' ').trim()).filter(Boolean);
  if (optionen.length < 2) return { optionen, fehler: 'umfrage.zuWenig' };
  if (optionen.length > 10) return { optionen, fehler: 'umfrage.zuViele' };
  if (new Set(optionen.map((o) => o.toLocaleLowerCase('de'))).size !== optionen.length) return { optionen, fehler: 'umfrage.doppelt' };
  return { optionen, fehler: null };
}
