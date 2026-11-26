// Preload-Brücke (Sandbox, contextIsolation): die Seite bekommt genau window.agoraDesktop mit
//   desktop: true, beiKuerzel(fn) → abmelden()
// fn erhält nur 'mikro' | 'kamera' | 'hand' – alles andere vom Hauptprozess wird verworfen. Keine Node-/IPC-Objekte nach außen.
'use strict';
const { contextBridge, ipcRenderer } = require('electron');

const AKTIONEN = new Set(['mikro', 'kamera', 'hand']);
const hoerer = new Set();

ipcRenderer.on('agora:kuerzel', (_e, aktion) => {
  if (typeof aktion !== 'string' || !AKTIONEN.has(aktion)) return;
  for (const fn of hoerer) { try { fn(aktion); } catch { /* Fehler der Seite bleiben dort */ } }
});

contextBridge.exposeInMainWorld('agoraDesktop', Object.freeze({
  desktop: true,
  beiKuerzel(fn) {
    if (typeof fn !== 'function' || hoerer.size >= 4) return () => {};
    hoerer.add(fn);
    return () => { hoerer.delete(fn); };
  },
}));
