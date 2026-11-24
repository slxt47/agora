// Admin-Bereich ohne DOM (auch in Node-Tests): Zahlen deutsch formatieren, fehlende Werte als „–“.
import { gebiet } from './i18n.js';
export function adminZahl(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '–';
  return new Intl.NumberFormat(gebiet()).format(Number(n));
}
