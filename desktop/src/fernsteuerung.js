// Fernsteuerung – reine Prüf- und Rechenfunktionen (ohne Electron; getestet in test/fernsteuerung.test.js).
// Der Hauptprozess (main.js) nutzt sie, bevor er irgendeine Eingabe ins System gibt. Die Datei läuft auch als
// Skript im Browser (globalThis.AgoraFernsteuerung) – der Sicherheitstest prüft damit die Absender-Regel gegen echte
// LiveKit-Datenpakete.
//
// Regeln (alle müssen gelten, sonst wird ein Ereignis verworfen):
//  1. Die eigene Bildschirmfreigabe läuft, und zwar ein ganzer Bildschirm (bei Fenstern kennt Electron die Lage des
//     fremden Fensters nicht – dort gibt es keine Fernsteuerung).
//  2. Die Person hat im Dialog der Desktop-App (nicht nur in der Seite) zugestimmt → Zustimmung {identity, kennung}.
//  3. Der Server hat die Freigabe in die Raum-Metadaten geschrieben: {von: ich, an: identity, id: kennung} – nur der
//     Server kann Raum-Metadaten setzen, und die Kennung bindet sie an genau diese Zustimmung.
//  4. Der Absender des Datenpakets (von LiveKit gesetzt, nicht aus dem Inhalt) ist genau diese identity.
//  5. E2EE-Meeting → nur Pakete, die die Seite mit dem Fernsteuerungs-Schlüssel (HKDF aus dem Link-Geheimnis, AAD =
//     Absender) entschlüsseln konnte (sonst könnte jemand ohne Schlüssel Klartext einschleusen).
//  6. Ereignis hat ein bekanntes Format, Werte im Bereich; Rate-Grenzen pro Sekunde.
'use strict';

const TOPIC = 'fernsteuerung';
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const KENNUNG_RE = /^[A-Za-z0-9_-]{22,64}$/;
const KNOEPFE = new Set(['left', 'right', 'middle']);
const MODS = new Set(['shift', 'control', 'alt']);          // bewusst ohne Meta/Super/Command (Systemmenüs, Sperre)
// Tasten: KeyboardEvent.key → robotjs-Name. Einzelne druckbare Zeichen gehen als Text (unicode).
const TASTEN = {
  Enter: 'enter', Tab: 'tab', Backspace: 'backspace', Delete: 'delete', ' ': 'space', Home: 'home', End: 'end',
  PageUp: 'pageup', PageDown: 'pagedown', ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
  Insert: 'insert', Escape: 'escape',
  F1: 'f1', F2: 'f2', F3: 'f3', F4: 'f4', F5: 'f5', F6: 'f6', F7: 'f7', F8: 'f8', F9: 'f9', F10: 'f10', F11: 'f11', F12: 'f12',
};
const GRENZEN = Object.freeze({ proSekunde: 120, stoss: 240, tastenProSekunde: 40, tastenStoss: 80, missbrauch: 600 });

const zahl = (v, min, max) => typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max;

/** Rohes Paket (Uint8Array/Buffer/String) → geprüftes Ereignis oder null. Höchstens 512 Byte. */
function ereignisLesen(roh) {
  let text = roh;
  if (typeof roh !== 'string') {
    if (!roh || typeof roh.length !== 'number' || roh.length > 512) return null;
    try { text = new TextDecoder().decode(roh); } catch { return null; }
  }
  if (text.length > 512) return null;
  let d;
  try { d = JSON.parse(text); } catch { return null; }
  if (!d || typeof d !== 'object' || Array.isArray(d)) return null;
  const mods = Array.isArray(d.mod) ? d.mod : [];
  if (mods.length > 3 || !mods.every((m) => MODS.has(m)) || new Set(mods).size !== mods.length) return null;
  switch (d.t) {
    case 'bewegen':
      return zahl(d.x, 0, 1) && zahl(d.y, 0, 1) ? { t: 'bewegen', x: d.x, y: d.y } : null;
    case 'druecken': case 'loslassen': case 'klick':
      if (!zahl(d.x, 0, 1) || !zahl(d.y, 0, 1) || !KNOEPFE.has(d.knopf)) return null;
      return { t: d.t, x: d.x, y: d.y, knopf: d.knopf, doppelt: d.t === 'klick' && d.doppelt === true, mod: mods };
    case 'rollen':
      return zahl(d.dx, -20, 20) && zahl(d.dy, -20, 20) && Number.isInteger(d.dx) && Number.isInteger(d.dy) ? { t: 'rollen', dx: d.dx, dy: d.dy } : null;
    case 'taste': {
      if (typeof d.taste !== 'string' || d.taste.length > 12) return null;
      if (Object.hasOwn(TASTEN, d.taste)) return { t: 'taste', taste: TASTEN[d.taste], mod: mods };
      const zeichen = [...d.taste];
      if (zeichen.length !== 1) return null;
      const cp = zeichen[0].codePointAt(0);
      if (cp < 0x20 || (cp >= 0x7f && cp < 0xa0) || (cp >= 0xd800 && cp < 0xe000) || cp > 0x10ffff) return null;
      // Mit Strg/Alt (Kürzel wie Strg+C): als Taste mit Modifikator; sonst als Text (Umlaute, Akzente, Tastaturlayouts)
      if (mods.some((m) => m !== 'shift')) return /^[a-z0-9]$/i.test(d.taste) ? { t: 'taste', taste: d.taste.toLowerCase(), mod: mods } : null;
      return { t: 'text', zeichen: d.taste };
    }
    default:
      return null;
  }
}

/** Freigaben aus den Raum-Metadaten (Text oder Objekt) → die für mich gültige {von, an, id} oder null. */
function freigabeAusMetadaten(metadaten, ich) {
  let md = metadaten;
  if (typeof md === 'string') { try { md = JSON.parse(md); } catch { return null; } }
  const liste = md && Array.isArray(md.fernsteuerung) ? md.fernsteuerung : [];
  const g = liste.find((x) => x && x.von === ich);
  if (!g || !ID_RE.test(g.an || '') || !KENNUNG_RE.test(g.id || '')) return null;
  return { von: g.von, an: g.an, id: g.id };
}

/** Regeln 1–5: darf dieses Paket von `absender` überhaupt etwas auslösen? → {ok} oder {ok:false, grund}. */
function darfSteuern(zustand, absender, verschluesselt) {
  const z = zustand || {};
  if (!z.freigabe || !z.freigabe.aktiv) return { ok: false, grund: 'keine-freigabe' };
  if (z.freigabe.art !== 'bildschirm') return { ok: false, grund: 'kein-bildschirm' };
  if (!z.zustimmung || !KENNUNG_RE.test(z.zustimmung.kennung || '')) return { ok: false, grund: 'keine-zustimmung' };
  if (!z.freigabeServer) return { ok: false, grund: 'keine-server-freigabe' };
  const g = z.freigabeServer;
  if (!z.ich || g.von !== z.ich) return { ok: false, grund: 'fremde-freigabe' };
  if (g.an !== z.zustimmung.identity || g.id !== z.zustimmung.kennung) return { ok: false, grund: 'freigabe-passt-nicht' };
  if (typeof absender !== 'string' || absender !== g.an) return { ok: false, grund: 'falscher-absender' };
  if (z.e2ee && !verschluesselt) return { ok: false, grund: 'unverschluesselt' };
  return { ok: true };
}

/** Normierte Koordinate (0…1 im geteilten Bild) → Systemkoordinate. bounds in DIP (Electron screen.getAllDisplays()).
 *  robotjs rechnet unter macOS in Punkten (= DIP), unter Windows/Linux in physischen Pixeln (× scaleFactor). */
function abbilden(x, y, bildschirm, plattform = typeof process !== 'undefined' ? process.platform : 'linux') {
  const b = bildschirm.bounds;
  const f = plattform === 'darwin' ? 1 : (bildschirm.scaleFactor || 1);
  const px = Math.min(b.x + b.width - 1 / f, b.x + x * b.width);      // rechter/unterer Rand gehört noch dazu
  const py = Math.min(b.y + b.height - 1 / f, b.y + y * b.height);
  return { x: Math.round(px * f), y: Math.round(py * f) };
}

/** Liegt ein Systempunkt (gleiche Einheit wie abbilden) in einem der Rechtecke (DIP, mit Faktor)? */
function inRechteck(punkt, rechtecke, faktor = 1) {
  return rechtecke.some((r) => r && punkt.x >= r.x * faktor && punkt.x < (r.x + r.width) * faktor
    && punkt.y >= r.y * faktor && punkt.y < (r.y + r.height) * faktor);
}

/** desktopCapturer-Quelle → Anzeige: Bildschirm-Quellen tragen display_id; ohne (manche Linux-Setups) per Reihenfolge. */
function bildschirmZurQuelle(quelle, anzeigen) {
  if (!quelle || !String(quelle.id || '').startsWith('screen:')) return null;
  const nachId = anzeigen.find((a) => String(a.id) === String(quelle.display_id));
  if (nachId) return nachId;
  const nr = Number(String(quelle.id).split(':')[1]);
  return anzeigen.length === 1 ? anzeigen[0] : (Number.isInteger(nr) && anzeigen[nr]) || null;
}

/** Würde die Taste ein eigenes systemweites Kürzel der App auslösen (Mikro, Kamera, Hand, Fernsteuerung beenden)?
 *  Solche Kombinationen gibt die App nie weiter – sonst könnte die steuernde Person z. B. das Mikrofon einschalten. */
function kuerzelTreffer(ereignis, kuerzel) {
  if (!ereignis || ereignis.t !== 'taste') return false;
  const norm = (teile) => teile.map((x) => x.toLowerCase()).sort().join('+');
  const ALIAS = { commandorcontrol: 'control', cmdorctrl: 'control', ctrl: 'control', option: 'alt', return: 'enter', esc: 'escape' };
  const ist = norm([...ereignis.mod, ereignis.taste]);
  return kuerzel.filter(Boolean).some((k) => norm(String(k).split('+').map((x) => ALIAS[x.toLowerCase()] || x.toLowerCase())) === ist);
}

/** Token-Bucket: Maus/Rollen und Tasten getrennt; zählt Verworfenes (bei Missbrauch beendet main.js die Steuerung). */
class Begrenzer {
  constructor(uhr = () => Date.now()) {
    this.uhr = uhr;
    this.eimer = { maus: { v: GRENZEN.stoss, t: uhr() }, taste: { v: GRENZEN.tastenStoss, t: uhr() } };
    this.verworfen = [];
  }

  ok(ereignis) {
    const art = ereignis.t === 'taste' || ereignis.t === 'text' ? 'taste' : 'maus';
    const [rate, max] = art === 'taste' ? [GRENZEN.tastenProSekunde, GRENZEN.tastenStoss] : [GRENZEN.proSekunde, GRENZEN.stoss];
    const e = this.eimer[art], jetzt = this.uhr();
    e.v = Math.min(max, e.v + ((jetzt - e.t) / 1000) * rate);
    e.t = jetzt;
    if (e.v >= 1) { e.v -= 1; return true; }
    this.verworfen.push(jetzt);
    while (this.verworfen.length && jetzt - this.verworfen[0] > 10000) this.verworfen.shift();
    return false;
  }

  /** Mehr als GRENZEN.missbrauch verworfene Ereignisse in 10 s → Steuerung beenden. */
  missbrauch() { return this.verworfen.length > GRENZEN.missbrauch; }
}

const AgoraFernsteuerung = { TOPIC, GRENZEN, TASTEN, ereignisLesen, freigabeAusMetadaten, darfSteuern, abbilden, inRechteck, bildschirmZurQuelle, kuerzelTreffer, Begrenzer };
if (typeof module !== 'undefined' && module.exports) module.exports = AgoraFernsteuerung;
else globalThis.AgoraFernsteuerung = AgoraFernsteuerung;
