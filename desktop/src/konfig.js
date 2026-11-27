// Einstellungen und reine Prüf-Funktionen der Desktop-App (ohne Electron, getestet mit node:test in test/konfig.test.js).
// Quelle der Einstellungen (spätere gewinnt): Vorgaben → <userData>/einstellungen.json → Umgebung (AGORA_ADRESSE).
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const VORGABE_ADRESSE = 'https://agora.slxt.dev';
const AKTIONEN = Object.freeze(['mikro', 'kamera', 'hand']);
// Systemweit: mit Strg/Cmd+Umschalt, damit sie anderen Programmen kaum in die Quere kommen
const VORGABE_KUERZEL = Object.freeze({ mikro: 'CommandOrControl+Shift+M', kamera: 'CommandOrControl+Shift+V', hand: 'CommandOrControl+Shift+H' });
// Fernsteuerung beenden: nur registriert, solange jemand steuert (Strg/Cmd+Alt+X)
const VORGABE_KUERZEL_ENDE = 'CommandOrControl+Alt+X';
const CODE_RE = /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/;
// Electron-Accelerator: Modifikatoren + genau eine Taste (Buchstabe, Ziffer, F1–F24 oder benannte Taste)
const MODS = new Set(['Command', 'Cmd', 'Control', 'Ctrl', 'CommandOrControl', 'CmdOrCtrl', 'Alt', 'Option', 'AltGr', 'Shift', 'Super', 'Meta']);
const TASTE_RE = /^([A-Z0-9]|F([1-9]|1[0-9]|2[0-4])|Space|Tab|Backspace|Delete|Insert|Return|Enter|Up|Down|Left|Right|Home|End|PageUp|PageDown|Escape|Esc|Plus|num[0-9]|[`\-=[\]\\;',./])$/;

/** Adresse prüfen: https (http nur für 127.0.0.1/localhost – Testinstanzen), ohne Zugangsdaten. → Ursprung oder null. */
function adressePruefen(text) {
  try {
    const u = new URL(String(text));
    const lokal = u.hostname === '127.0.0.1' || u.hostname === 'localhost' || u.hostname === '[::1]';
    if (u.username || u.password) return null;
    if (u.protocol !== 'https:' && !(u.protocol === 'http:' && lokal)) return null;
    return u.origin;
  } catch { return null; }
}

/** Accelerator gültig? Mindestens ein Modifikator (systemweit nie eine nackte Taste) und genau eine Taste. */
function kuerzelPruefen(text) {
  if (typeof text !== 'string' || text.length > 60) return false;
  const teile = text.split('+');
  const taste = teile.pop();
  return teile.length >= 1 && teile.every((t) => MODS.has(t)) && new Set(teile).size === teile.length && TASTE_RE.test(taste);
}

function konfigLaden(ordner, umgebung = {}) {
  const datei = path.join(ordner, 'einstellungen.json');
  let roh = {};
  try { roh = JSON.parse(fs.readFileSync(datei, 'utf8')) || {}; } catch { /* fehlt oder kaputt → Vorgaben */ }
  const kandidat = umgebung.AGORA_ADRESSE || roh.adresse || VORGABE_ADRESSE;
  let herkunft = adressePruefen(kandidat);
  let adresse = herkunft ? new URL(kandidat).href : null;
  if (!herkunft) { console.warn(`Agora: Adresse ${kandidat} ungültig – nehme ${VORGABE_ADRESSE}`); herkunft = VORGABE_ADRESSE; adresse = VORGABE_ADRESSE + '/'; }
  const kuerzel = { ...VORGABE_KUERZEL };
  for (const a of AKTIONEN) {
    if (!roh.kuerzel || !(a in roh.kuerzel)) continue;
    const w = roh.kuerzel[a];
    if (w === '' || w === null || w === false) kuerzel[a] = '';                   // abgeschaltet
    else if (kuerzelPruefen(w)) kuerzel[a] = w;
    else console.warn(`Agora: Tastenkürzel ${JSON.stringify(w)} für ${a} ungültig – nehme ${kuerzel[a]}`);
  }
  let kuerzelEnde = VORGABE_KUERZEL_ENDE;
  if (roh.kuerzel && 'fernsteuerungEnde' in roh.kuerzel) {
    const w = roh.kuerzel.fernsteuerungEnde;
    if (w === '' || w === null || w === false) kuerzelEnde = '';
    else if (kuerzelPruefen(w)) kuerzelEnde = w;
    else console.warn(`Agora: Tastenkürzel ${JSON.stringify(w)} für fernsteuerungEnde ungültig – nehme ${kuerzelEnde}`);
  }
  return { datei, adresse, herkunft, kuerzel, kuerzelEnde };
}

/** agora://r/<code>[#k=<schlüssel>] (auch agora://<code>) → { code, schluessel } oder null.
 *  Der E2EE-Schlüssel bleibt wie im Web nur im Fragment; alles andere am Link wird verworfen. */
function codeAusDeepLink(url) {
  if (typeof url !== 'string' || url.length > 200) return null;
  const m = /^agora:(?:\/\/)?(?:r\/)?([a-z]{3}-[a-z]{4}-[a-z]{3})\/?(\?[^#]*)?(#.*)?$/i.exec(url.trim());
  if (!m) return null;
  const code = m[1].toLowerCase();
  const k = /^#k=([A-Za-z0-9_-]{43})$/.exec(m[3] || '');
  return CODE_RE.test(code) ? { code, schluessel: k ? k[1] : '' } : null;
}

function raumAdresse(adresse, code, schluessel = '') {
  return new URL(`/r/${code}`, adresse).href + (schluessel ? `#k=${schluessel}` : '');
}

/** Darf das Hauptfenster dorthin navigieren? Nur gleiche Herkunft (Agora selbst). */
function erlaubteNavigation(url, herkunft) {
  try { return new URL(url).origin === herkunft; } catch { return false; }
}

module.exports = { VORGABE_ADRESSE, VORGABE_KUERZEL, VORGABE_KUERZEL_ENDE, AKTIONEN, adressePruefen, kuerzelPruefen, konfigLaden, codeAusDeepLink, raumAdresse, erlaubteNavigation };
