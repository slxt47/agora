// Preload der Bildschirmauswahl: nur Quellen holen und eine gewählte id zurückgeben.
'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('auswahl', Object.freeze({
  quellen: () => ipcRenderer.invoke('agora-auswahl:quellen'),
  waehlen: (id) => ipcRenderer.send('agora-auswahl:gewaehlt', typeof id === 'string' ? id.slice(0, 200) : null),
}));
