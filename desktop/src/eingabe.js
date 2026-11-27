// Eingaben ins System geben (nur für die Fernsteuerung, nur aus main.js nach allen Prüfungen in fernsteuerung.js).
// Adapter in dieser Reihenfolge (oder fest über AGORA_EINGABE=robotjs|xdotool):
//   robotjs  – @jitsi/robotjs (MIT, von Jitsi gepflegt, N-API-Prebuilds für Linux x64/arm64, Windows x64/arm64/ia32,
//              macOS x64+arm64; kein Neubau für Electron nötig). Linux nur unter X11 bzw. XWayland (XTest).
//   xdotool  – nur Linux/X11, falls robotjs fehlt oder nicht lädt (Paket `xdotool` muss installiert sein).
// Ohne Adapter bietet die App keine Fernsteuerung an (die Seite erfährt das über die Brücke).
'use strict';
const { execFile } = require('node:child_process');

/** robotjs: Koordinaten in Systempixeln (macOS: Punkte); Rollen: positives y = nach oben.
 *  Linux: unicodeTap kommt bei Zeichen außerhalb ASCII (ä, €, …) als NoSymbol an (im Xvfb-Test gesehen) – dafür nimmt
 *  der Adapter xdotool `type`, wenn installiert (es belegt dafür kurz eine freie Taste im Tastaturlayout). */
function robotjsAdapter(xdoFuerText = null) {
  const r = require('@jitsi/robotjs');
  r.setMouseDelay(0);
  r.setKeyboardDelay(0);
  // Mit xdotool für Text laufen alle Aufrufe durch dieselbe Warteschlange – sonst überholte z. B. Enter ein „ä“
  const s = xdoFuerText ? (fn) => xdoFuerText.anhaengen(fn) : (fn) => fn();
  return {
    name: 'robotjs',
    bewegen: (x, y) => s(() => r.moveMouse(x, y)),
    knopf: (knopf, runter) => s(() => r.mouseToggle(runter ? 'down' : 'up', knopf)),
    klick: (knopf, doppelt) => s(() => r.mouseClick(knopf, !!doppelt)),
    rollen: (dx, dy) => s(() => r.scrollMouse(dx, -dy)),
    taste: (taste, mods) => s(() => (mods.length ? r.keyTap(taste, mods) : r.keyTap(taste))),
    text: (zeichen) => (xdoFuerText && zeichen.codePointAt(0) > 0x7e ? xdoFuerText.text(zeichen) : s(() => r.unicodeTap(zeichen.codePointAt(0)))),
    warten: () => xdoFuerText?.warten(),
  };
}

const XDO_TASTE = {
  enter: 'Return', tab: 'Tab', backspace: 'BackSpace', delete: 'Delete', space: 'space', home: 'Home', end: 'End',
  pageup: 'Prior', pagedown: 'Next', up: 'Up', down: 'Down', left: 'Left', right: 'Right', insert: 'Insert', escape: 'Escape',
};
const XDO_MOD = { shift: 'shift', control: 'ctrl', alt: 'alt' };
const XDO_KNOPF = { left: '1', middle: '2', right: '3' };

/** xdotool: jeder Aufruf ein eigener Prozess (ohne Shell, feste Argumente); Reihenfolge bleibt über eine Warteschlange. */
function xdotoolAdapter(pfad = 'xdotool') {
  let kette = Promise.resolve();
  // UTF-8-Locale erzwingen: ohne sie lehnt `xdotool type` Zeichen wie „ä“ ab („Invalid multi-byte sequence“)
  const umgebung = { ...process.env, LC_ALL: 'C.UTF-8' };
  const x = (...args) => { kette = kette.then(() => new Promise((fertig) => execFile(pfad, args, { timeout: 2000, env: umgebung }, () => fertig()))); return kette; };
  const taste = (t) => XDO_TASTE[t] || (/^f([1-9]|1[0-2])$/.test(t) ? t.toUpperCase() : t);
  return {
    name: 'xdotool',
    bewegen: (px, py) => x('mousemove', '--', String(px), String(py)),
    knopf: (knopf, runter) => x(runter ? 'mousedown' : 'mouseup', XDO_KNOPF[knopf]),
    klick: (knopf, doppelt) => x('click', '--repeat', doppelt ? '2' : '1', XDO_KNOPF[knopf]),
    rollen: (dx, dy) => {
      const n = (v, plus, minus) => Array.from({ length: Math.abs(v) }, () => (v > 0 ? plus : minus));
      const knoepfe = [...n(dy, '5', '4'), ...n(dx, '7', '6')];
      return knoepfe.length ? x('click', ...knoepfe.flatMap((k) => [k])) : kette;
    },
    taste: (t, mods) => x('key', '--clearmodifiers', [...mods.map((m) => XDO_MOD[m]), taste(t)].join('+')),
    text: (zeichen) => x('type', '--clearmodifiers', '--', zeichen),
    warten: () => kette,
    anhaengen: (fn) => { kette = kette.then(() => { try { fn(); } catch { /* weiter */ } }); return kette; },
  };
}

function xdotoolVorhanden() {
  return new Promise((fertig) => execFile('xdotool', ['version'], { timeout: 2000 }, (f) => fertig(!f)));
}

/** Passenden Adapter laden → Adapter oder null (mit Grund im Log). */
async function adapterLaden(wunsch = process.env.AGORA_EINGABE || '') {
  const versuche = wunsch ? [wunsch] : (process.platform === 'linux' ? ['robotjs', 'xdotool'] : ['robotjs']);
  for (const art of versuche) {
    try {
      if (art === 'robotjs') return robotjsAdapter(process.platform === 'linux' && await xdotoolVorhanden() ? xdotoolAdapter() : null);
      if (art === 'xdotool' && process.platform === 'linux' && await xdotoolVorhanden()) return xdotoolAdapter();
    } catch (e) {
      console.warn(`Agora: Eingabe-Adapter ${art} nicht verfügbar: ${e.message}`);
    }
  }
  return null;
}

module.exports = { adapterLaden, xdotoolAdapter, XDO_TASTE };
