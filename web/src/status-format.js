// Status-Seite: reine Format-Hilfen (ohne DOM, in Node getestet – test/status.test.js).

/** Verfügbarkeit in Prozent → „99,95 %“ (bis zu 2 Nachkommastellen, 100 ohne); null → „–“. */
export function prozentText(p, gebiet = 'de-AT') {
  if (p === null || p === undefined || Number.isNaN(p)) return '–';
  const wert = p >= 100 ? 100 : Math.floor(p * 100) / 100;          // nie aufrunden: 99,999 % ist nicht 100 %
  return `${new Intl.NumberFormat(gebiet, { maximumFractionDigits: 2 }).format(wert)} %`;
}

/** Farbstufe eines Balkens: leer (keine Messung), ok (≥ 99,9 %), gestoert (≥ 95 %), ausfall. */
export function balkenStufe(p) {
  if (p === null || p === undefined) return 'leer';
  if (p >= 99.9) return 'ok';
  if (p >= 95) return 'gestoert';
  return 'ausfall';
}

/** Antwortzeit → „12 ms“ bzw. „1,2 s“. */
export function msText(ms, gebiet = 'de-AT') {
  if (ms === null || ms === undefined) return '–';
  const f = new Intl.NumberFormat(gebiet, { maximumFractionDigits: ms >= 1000 ? 1 : 0 });
  return ms >= 1000 ? `${f.format(ms / 1000)} s` : `${f.format(Math.max(ms, ms > 0 ? 1 : 0))} ms`;
}
