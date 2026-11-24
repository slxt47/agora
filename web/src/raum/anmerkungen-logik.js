// Anmerkungen – reine Logik ohne DOM/LiveKit (getestet mit npm test): Nachrichten prüfen, Striche verwalten,
// Begrenzer, Geometrie (sichtbares Videobild in einer Kachel mit object-fit: contain), Radierer-Treffer.
export const WERKZEUGE = ['stift', 'marker', 'pfeil', 'text', 'radierer'];
export const FARBEN = ['#ff4d6d', '#ffd23f', '#3ddc97', '#4cc9f0', '#ffffff', '#141414'];
export const GRENZEN = { punkte: 1000, striche: 400, text: 200, bytes: 16000, proSekunde: 30, stoss: 60 };
const ID = /^[0-9a-z]{4,16}$/;

const zahl = (v) => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1;
const punkt = (p) => Array.isArray(p) && p.length === 2 && zahl(p[0]) && zahl(p[1]);
export const runden = (v) => Math.round(Math.min(1, Math.max(0, v)) * 10000) / 10000;

/**
 * Nachricht aus dem Datenkanal prüfen. Gibt ein sauberes Objekt oder null zurück.
 *  strich: {t, s, id, w (stift|marker|pfeil), f, i (Startindex), p:[[x,y]…]}
 *  text:   {t, s, id, f, x, y, text}
 *  weg:    {t, s, id, von}          leeren: {t, s, nur?}
 */
export function nachrichtPruefen(d) {
  if (!d || typeof d !== 'object' || typeof d.s !== 'string' || !d.s || d.s.length > 128) return null;
  if (d.t === 'strich') {
    if (!ID.test(d.id) || !['stift', 'marker', 'pfeil'].includes(d.w) || !Number.isInteger(d.f) || d.f < 0 || d.f >= FARBEN.length) return null;
    if (!Number.isInteger(d.i) || d.i < 0 || d.i >= GRENZEN.punkte || !Array.isArray(d.p) || !d.p.length || d.p.length > 200 || !d.p.every(punkt)) return null;
    return { t: 'strich', s: d.s, id: d.id, w: d.w, f: d.f, i: d.i, p: d.p.map(([x, y]) => [x, y]) };
  }
  if (d.t === 'text') {
    if (!ID.test(d.id) || !Number.isInteger(d.f) || d.f < 0 || d.f >= FARBEN.length || !zahl(d.x) || !zahl(d.y)) return null;
    if (typeof d.text !== 'string') return null;
    const text = d.text.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, GRENZEN.text);
    return text ? { t: 'text', s: d.s, id: d.id, f: d.f, x: d.x, y: d.y, text } : null;
  }
  if (d.t === 'weg') {
    if (!ID.test(d.id) || typeof d.von !== 'string' || !d.von || d.von.length > 128) return null;
    return { t: 'weg', s: d.s, id: d.id, von: d.von };
  }
  if (d.t === 'leeren') {
    if (d.nur !== undefined && (typeof d.nur !== 'string' || !d.nur || d.nur.length > 128)) return null;
    return { t: 'leeren', s: d.s, ...(d.nur ? { nur: d.nur } : {}) };
  }
  return null;
}

/** Darf `von` diese (geprüfte) Nachricht schicken? rolle(identity) → host|cohost|…; erlaubt = Raum-Recht. */
export function darf(n, von, { erlaubt, rolle }) {
  const mod = ['host', 'cohost'].includes(rolle(von));
  const praesentiert = n.s === von;
  if (n.t === 'leeren') return n.nur ? n.nur === von || mod || praesentiert : mod || praesentiert;
  if (n.t === 'weg') return n.von === von || mod || praesentiert;
  return erlaubt || mod || praesentiert;
}

/** Striche je geteiltem Bildschirm: Map key `${von}:${id}` → {von, id, w, f, p} oder {von, id, text, x, y, f}. */
export class Striche {
  constructor() { this.m = new Map(); }

  get groesse() { return this.m.size; }

  anwenden(n, von) {
    if (n.t === 'strich') {
      const k = `${von}:${n.id}`;
      let s = this.m.get(k);
      if (!s) {
        if (this.m.size >= GRENZEN.striche) this.m.delete(this.m.keys().next().value);   // ältesten verwerfen
        s = { von, id: n.id, w: n.w, f: n.f, p: [] };
        this.m.set(k, s);
      }
      if (n.i > s.p.length) return false;              // Lücke (verlorene Nachricht gibt es bei „reliable“ nicht)
      s.p.splice(n.i, Infinity, ...n.p);
      if (s.p.length > GRENZEN.punkte) s.p.length = GRENZEN.punkte;
      return true;
    }
    if (n.t === 'text') {
      if (this.m.size >= GRENZEN.striche) this.m.delete(this.m.keys().next().value);
      this.m.set(`${von}:${n.id}`, { von, id: n.id, f: n.f, x: n.x, y: n.y, text: n.text });
      return true;
    }
    if (n.t === 'weg') return this.m.delete(`${n.von}:${n.id}`);
    if (n.t === 'leeren') {
      if (!n.nur) { const war = this.m.size; this.m.clear(); return war > 0; }
      let weg = false;
      for (const [k, s] of this.m) if (s.von === n.nur) { this.m.delete(k); weg = true; }
      return weg;
    }
    return false;
  }

  /** Strich, den der Radierer bei (x, y) trifft (normiert; toleranz relativ zur Breite). */
  treffer(x, y, toleranz, nurVon) {
    let best = null, bestD = toleranz;
    for (const s of [...this.m.values()].reverse()) {
      if (nurVon && s.von !== nurVon) continue;
      const d = s.text !== undefined ? Math.hypot(s.x - x, s.y - y) : abstandZuLinie(s.p, x, y);
      if (d <= bestD) { best = s; bestD = d; }
    }
    return best;
  }
}

function abstandZuLinie(p, x, y) {
  if (p.length === 1) return Math.hypot(p[0][0] - x, p[0][1] - y);
  let min = Infinity;
  for (let i = 1; i < p.length; i++) {
    const [ax, ay] = p[i - 1], [bx, by] = p[i];
    const dx = bx - ax, dy = by - ay, l = dx * dx + dy * dy;
    const t = l ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / l)) : 0;
    min = Math.min(min, Math.hypot(ax + t * dx - x, ay + t * dy - y));
  }
  return min;
}

/** Sichtbares Videobild in einem Kasten (object-fit: contain): {x, y, w, h} in Pixeln relativ zum Kasten. */
export function inhaltRechteck(kastenB, kastenH, videoB, videoH) {
  if (!kastenB || !kastenH) return { x: 0, y: 0, w: 0, h: 0 };
  if (!videoB || !videoH) return { x: 0, y: 0, w: kastenB, h: kastenH };
  const s = Math.min(kastenB / videoB, kastenH / videoH);
  const r2 = (v) => Math.round(v * 100) / 100 + 0;     // + 0: kein -0
  const w = videoB * s, h = videoH * s;
  return { x: r2((kastenB - w) / 2), y: r2((kastenH - h) / 2), w: r2(w), h: r2(h) };
}

/** Token-Bucket je Absender (Empfang) bzw. für sich selbst (Senden). */
export class Begrenzer {
  constructor(proSekunde = GRENZEN.proSekunde, stoss = GRENZEN.stoss, uhr = () => Date.now()) {
    Object.assign(this, { proSekunde, stoss, uhr });
    this.eimer = new Map();
  }

  ok(schluessel) {
    const jetzt = this.uhr();
    const e = this.eimer.get(schluessel) || { n: this.stoss, t: jetzt };
    e.n = Math.min(this.stoss, e.n + ((jetzt - e.t) / 1000) * this.proSekunde);
    e.t = jetzt;
    const ok = e.n >= 1;
    if (ok) e.n -= 1;
    this.eimer.set(schluessel, e);
    if (this.eimer.size > 500) this.eimer.delete(this.eimer.keys().next().value);
    return ok;
  }
}

/** Kurze zufällige ID für einen Strich. */
export const neueId = () => Math.random().toString(36).slice(2, 10).padEnd(6, '0');
