// Dateien teilen – Vorprüfung im Browser (spart vergebliche Uploads; maßgeblich bleibt der Server).
/** Fehler {text, werte} oder null. info = {grenze_datei, grenze_meeting, belegt, endungen}. */
export function dateiPruefen(datei, info) {
  const name = String(datei?.name || '');
  const endung = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
  if (!datei?.size) return { text: 'dateien.fehlerLeer', werte: { name } };
  if (info.endungen?.length && !info.endungen.includes(endung)) return { text: 'dateien.fehlerTyp', werte: { name, endung: endung || '–' } };
  if (datei.size > info.grenze_datei) return { text: 'dateien.fehlerGross', werte: { name } };
  if ((info.belegt || 0) + datei.size > info.grenze_meeting) return { text: 'dateien.fehlerVoll', werte: { name } };
  return null;
}
