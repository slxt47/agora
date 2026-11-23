// Admin-Bereich ohne DOM (auch in Node-Tests): Zahlen deutsch formatieren, fehlende Werte als „–“.
export function adminZahl(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '–';
  return new Intl.NumberFormat('de-AT').format(Number(n));
}
