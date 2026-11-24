// Einbetten: an welche Herkünfte die Seite im iframe postMessage schickt (ohne DOM, auch in Node-Tests nutzbar).
/** Zielherkünfte für postMessage: die tatsächliche Eltern-Herkunft, wenn bekannt und erlaubt, sonst alle erlaubten
 *  (postMessage mit fester targetOrigin liefert nur an eine passende Seite aus). */
export function ziele(erlaubt, vorfahren, wunsch) {
  const liste = (erlaubt || []).filter((x) => typeof x === 'string');
  const eltern = vorfahren?.[0];
  if (eltern) return liste.includes(eltern) ? [eltern] : [];
  if (wunsch && liste.includes(wunsch)) return [wunsch];
  return liste;
}
