// Preload-Brücke (Sandbox, contextIsolation): die Seite bekommt genau window.agoraDesktop mit
//   desktop: true, beiKuerzel(fn) → abmelden(), fernsteuerung: {…} (siehe unten)
// fn erhält nur 'mikro' | 'kamera' | 'hand' – alles andere vom Hauptprozess wird verworfen. Keine Node-/IPC-Objekte nach außen.
// Fernsteuerung: die Seite reicht nur weiter (eigene Freigabe, Anfragen, Raum-Metadaten, Datenpakete mit der
// LiveKit-identity des Absenders); entscheiden und prüfen tut der Hauptprozess (steuerung.js, fernsteuerung.js).
'use strict';
const { contextBridge, ipcRenderer } = require('electron');

const AKTIONEN = new Set(['mikro', 'kamera', 'hand']);
const hoerer = new Set();
const endeHoerer = new Set();
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

ipcRenderer.on('agora:kuerzel', (_e, aktion) => {
  if (typeof aktion !== 'string' || !AKTIONEN.has(aktion)) return;
  for (const fn of hoerer) { try { fn(aktion); } catch { /* Fehler der Seite bleiben dort */ } }
});
ipcRenderer.on('agora-fs:ende', (_e, grund) => {
  for (const fn of endeHoerer) { try { fn(String(grund).slice(0, 40)); } catch { /* Seite */ } }
});

let verfuegbar = false;
ipcRenderer.invoke('agora-fs:verfuegbar').then((v) => { verfuegbar = v === true; }).catch(() => {});

contextBridge.exposeInMainWorld('agoraDesktop', Object.freeze({
  desktop: true,
  beiKuerzel(fn) {
    if (typeof fn !== 'function' || hoerer.size >= 4) return () => {};
    hoerer.add(fn);
    return () => { hoerer.delete(fn); };
  },
  fernsteuerung: Object.freeze({
    /** Hat die App einen Eingabe-Adapter (robotjs/xdotool)? */
    verfuegbar: () => verfuegbar,
    /** Eigene Bildschirmfreigabe läuft (true) oder endet (false). */
    freigabe(an, info) {
      ipcRenderer.send('agora-fs:freigabe', an === true, an === true && info ? { ich: String(info.ich || '').slice(0, 64), e2ee: info.e2ee === true } : null);
    },
    /** Anfrage → Dialog der App → {ok, kennung} | {ok: false}. */
    anfrage(identity, name) {
      if (typeof identity !== 'string' || !ID_RE.test(identity)) return Promise.resolve({ ok: false });
      return ipcRenderer.invoke('agora-fs:anfrage', identity, String(name || '').slice(0, 100));
    },
    metadaten(text) { if (typeof text === 'string' && text.length <= 20000) ipcRenderer.send('agora-fs:metadaten', text); },
    ereignis(absender, verschluesselt, daten) {
      if (typeof absender !== 'string' || !ID_RE.test(absender) || !(daten instanceof Uint8Array) || daten.length > 512) return;
      ipcRenderer.send('agora-fs:ereignis', absender, verschluesselt === true, daten);
    },
    beenden() { ipcRenderer.send('agora-fs:beenden'); },
    beiEnde(fn) {
      if (typeof fn !== 'function' || endeHoerer.size >= 2) return () => {};
      endeHoerer.add(fn);
      return () => { endeHoerer.delete(fn); };
    },
  }),
}));
