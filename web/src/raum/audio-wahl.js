// Audio-Modi (reine Funktionen, ohne LiveKit – getestet mit npm test):
// - „Originalton/Musik“: ohne Echo-/Rauschunterdrückung und Pegelautomatik, stereo, Opus 128 kbit/s, ohne DTX/RED
// - Rauschunterdrückung: „browser“ (eingebaute des Browsers), „stark“ (RNNoise als WASM, selbst gehostet), „aus“
// Die Wahl gilt je Gerät (localStorage), wie die Gerätewahl.
const SPEICHER = 'agora-audio';
export const RAUSCHEN = ['browser', 'stark', 'aus'];
export const MUSIK_BITRATE = 128_000;      // = AudioPresets.musicHighQualityStereo

export function audioPruefen(d) {
  return { musik: d?.musik === true, rauschen: RAUSCHEN.includes(d?.rauschen) ? d.rauschen : 'browser' };
}

export function audioLesen(speicher = globalThis.localStorage) {
  try { return audioPruefen(JSON.parse(speicher.getItem(SPEICHER) || '{}')); } catch { return audioPruefen({}); }
}

export function audioMerken(wahl, speicher = globalThis.localStorage) {
  try { speicher.setItem(SPEICHER, JSON.stringify(audioPruefen(wahl))); } catch { /* privat */ }
}

/** Wirksame Rauschunterdrückung: im Musikmodus immer aus. */
export const rauschenWirksam = (w) => (w.musik ? 'aus' : w.rauschen);

/** getUserMedia-Einstellungen fürs Mikrofon (alle Schlüssel gesetzt, damit ein Wechsel alte Werte überschreibt). */
export function erfassung(w) {
  if (w.musik) return { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 2, sampleRate: 48000 };
  // RNNoise ersetzt die Rauschunterdrückung des Browsers (beides zusammen klingt dumpf)
  return { echoCancellation: true, noiseSuppression: w.rauschen === 'browser', autoGainControl: true, channelCount: 1, sampleRate: undefined };
}

/** Optionen für publishTrack (LiveKit). presets = AudioPresets aus livekit-client. */
export function veroeffentlichung(w, presets) {
  return w.musik
    ? { audioPreset: presets.musicHighQualityStereo, dtx: false, red: false, forceStereo: true }
    : { audioPreset: presets.music, dtx: true, red: true, forceStereo: false };
}
