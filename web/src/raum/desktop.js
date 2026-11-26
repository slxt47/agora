// Desktop-App (desktop/, Electron): systemweite Tastenkürzel kommen über die Preload-Brücke window.agoraDesktop an
// und laufen durch dieselben Handler wie die Tasten M, V und H im Browser. Ohne Desktop-App passiert hier nichts.
const TASTE = { mikro: 'm', kamera: 'v', hand: 'h' };

/** Meldet sich bei der Brücke an; gibt eine Abmelde-Funktion zurück. */
export function desktopKuerzel(tastenAktion, bruecke = globalThis.agoraDesktop) {
  if (!bruecke || typeof bruecke.beiKuerzel !== 'function') return () => {};
  return bruecke.beiKuerzel((aktion) => {
    const taste = Object.hasOwn(TASTE, aktion) ? TASTE[aktion] : null;
    if (taste) tastenAktion(taste);
  });
}
