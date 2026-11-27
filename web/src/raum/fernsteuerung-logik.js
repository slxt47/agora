// Fernsteuerung – reine Hilfen der steuernden Seite (ohne DOM, getestet in test/fernsteuerung.test.js):
// Mauspunkt → normierte Koordinate im geteilten Bild, Tasten/Rad → Ereignis im Format, das die Desktop-App prüft
// (desktop/src/fernsteuerung.js), Drossel für Mausbewegungen.
import { inhaltRechteck } from './anmerkungen-logik.js';

export const TOPIC = 'fernsteuerung';
const KNOEPFE = ['left', 'middle', 'right'];
const SONDERTASTEN = new Set(['Enter', 'Tab', 'Backspace', 'Delete', ' ', 'Home', 'End', 'PageUp', 'PageDown', 'ArrowUp', 'ArrowDown',
  'ArrowLeft', 'ArrowRight', 'Insert', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12']);

/** Punkt (clientX/Y) in einer Kachel (rect = getBoundingClientRect) mit Video (videoB × videoH, object-fit: contain)
 *  → {x, y} in 0…1 oder null (Punkt im Rand neben dem Bild). */
export function normiert(clientX, clientY, rect, videoB, videoH) {
  const ir = inhaltRechteck(rect.width, rect.height, videoB, videoH);
  if (!ir.w || !ir.h) return null;
  const x = (clientX - rect.left - ir.x) / ir.w, y = (clientY - rect.top - ir.y) / ir.h;
  if (x < 0 || y < 0 || x > 1 || y > 1) return null;
  const r = (v) => Math.round(v * 10000) / 10000;
  return { x: r(x), y: r(y) };
}

export function mods(e) {
  return [e.shiftKey && 'shift', e.ctrlKey && 'control', e.altKey && 'alt'].filter(Boolean);
}

/** KeyboardEvent → Ereignis oder null (Esc beendet die Steuerung und wird nie gesendet; Meta/Win nie). */
export function tastenEreignis(e) {
  if (e.key === 'Escape' || e.metaKey || e.isComposing) return null;
  if (SONDERTASTEN.has(e.key)) return { t: 'taste', taste: e.key, mod: mods(e) };
  if ([...e.key].length === 1) return { t: 'taste', taste: e.key, mod: mods(e) };
  return null;
}

export function knopf(button) { return KNOEPFE[button] || null; }

/** WheelEvent → ganze Rast-Schritte (−5…5); Pixel- und Zeilen-Modus. */
export function rollen(deltaX, deltaY, deltaMode = 0) {
  const f = deltaMode === 1 ? 1 / 3 : deltaMode === 2 ? 3 : 1 / 100;
  const s = (v) => Math.max(-5, Math.min(5, Math.round(v * f) || (v ? Math.sign(v) : 0)));
  return { t: 'rollen', dx: s(deltaX), dy: s(deltaY) };
}

/** Höchstens alle `ms` Millisekunden eine Mausbewegung (die letzte gewinnt, über `nachlauf` nachgeschickt). */
export class Drossel {
  constructor(ms = 33, uhr = () => Date.now()) { Object.assign(this, { ms, uhr, zuletzt: -Infinity }); }

  jetztSenden() {
    const t = this.uhr();
    if (t - this.zuletzt < this.ms) return false;
    this.zuletzt = t;
    return true;
  }
}

/** E2EE: Ereignis mit dem Fernsteuerungs-Schlüssel verpacken; AAD = identity des Absenders (ein Paket lässt sich nicht
 *  als das einer anderen Person weiterreichen). → Uint8Array {"v":1,"iv":…,"c":…} */
export async function verpacken(schluessel, ereignis, absender) {
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
  const c = new Uint8Array(await globalThis.crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: new TextEncoder().encode(absender) },
    schluessel, new TextEncoder().encode(JSON.stringify(ereignis))));
  const b64 = (u) => btoa(String.fromCharCode(...u));
  return new TextEncoder().encode(JSON.stringify({ v: 1, iv: b64(iv), c: b64(c) }));
}

/** Gegenstück: → Klartext-Bytes oder null (kein Paket von uns, falscher Schlüssel, falscher Absender, zu groß). */
export async function entpacken(schluessel, daten, absender) {
  try {
    if (!(daten instanceof Uint8Array) || daten.length > 1024) return null;
    const d = JSON.parse(new TextDecoder().decode(daten));
    if (d?.v !== 1 || typeof d.iv !== 'string' || typeof d.c !== 'string') return null;
    const aus = (s) => Uint8Array.from(atob(s), (x) => x.charCodeAt(0));
    const klar = await globalThis.crypto.subtle.decrypt({ name: 'AES-GCM', iv: aus(d.iv), additionalData: new TextEncoder().encode(absender) },
      schluessel, aus(d.c));
    return new Uint8Array(klar);
  } catch { return null; }
}

/** Freigabe aus den Raum-Metadaten, in der ich steuere → {von, an_name} oder null. */
export function meineSteuerung(md, ich) {
  const liste = Array.isArray(md?.fernsteuerung) ? md.fernsteuerung : [];
  return liste.find((g) => g && g.an === ich && typeof g.von === 'string') || null;
}
