// Whiteboard: Zeichnen, Treffer und Geometrie – ohne DOM, damit es auch im Meeting-Verlauf (Konto) und in Node-Tests
// läuft. Objekte wie im Server (app/whiteboard.py): {id, art, farbe, breite, punkte | x,y,w,h, text}.

export const TAFEL = '#fbfaf7';          // Tafel bleibt in Hell und Dunkel hell – Farben sehen für alle gleich aus
export const FARBEN = ['#1d1d1f', '#e5484d', '#f08c00', '#2fb67c', '#2f80ed', '#8e6cff'];
export const NOTIZ_FARBEN = { '#1d1d1f': '#ffe58a', '#e5484d': '#ffc9c9', '#f08c00': '#ffd8a8', '#2fb67c': '#b2f2bb', '#2f80ed': '#a5d8ff', '#8e6cff': '#d0bfff' };
export const BREITEN = [2, 4, 8];
export const MAX_PUNKTE = 2000;
export const NOTIZ_TEXT = '#1d1d1f';

export const textGroesse = (o) => 10 + o.breite * 4;           // Text: Breite 2/4/8 → 18/26/42 px
const SCHRIFT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
export const schrift = (px) => `600 ${px}px ${SCHRIFT}`;

/** Eindeutige Objekt-id (Server erlaubt [A-Za-z0-9_-]{6,40}). */
export function neueId() {
  const z = new Uint8Array(12);
  globalThis.crypto.getRandomValues(z);
  return [...z].map((b) => 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'[b % 56]).join('');
}

const r1 = (v) => Math.round(v * 10) / 10;

/** Strich ausdünnen: Punkte näher als `abstand` fallen weg, höchstens MAX_PUNKTE, auf 0,1 gerundet. */
export function vereinfachen(punkte, abstand = 1.5) {
  const aus = [];
  for (let i = 0; i < punkte.length; i += 2) {
    const x = punkte[i], y = punkte[i + 1];
    const n = aus.length;
    if (n && i < punkte.length - 2 && Math.hypot(x - aus[n - 2], y - aus[n - 1]) < abstand) continue;
    aus.push(r1(x), r1(y));
  }
  if (aus.length === 2) aus.push(aus[0], aus[1]);              // Punkt = kurzer Strich
  if (aus.length / 2 > MAX_PUNKTE) {
    const schritt = Math.ceil(aus.length / 2 / MAX_PUNKTE), d = [];
    for (let i = 0; i < aus.length; i += 2 * schritt) d.push(aus[i], aus[i + 1]);
    if (d[d.length - 2] !== aus[aus.length - 2] || d[d.length - 1] !== aus[aus.length - 1]) { d.push(aus[aus.length - 2], aus[aus.length - 1]); }
    return d.slice(0, 2 * MAX_PUNKTE);
  }
  return aus;
}

/** Umgebendes Rechteck {x1, y1, x2, y2} (ohne Strichbreite). */
export function grenzen(o) {
  if (o.art === 'stift') {
    let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
    for (let i = 0; i < o.punkte.length; i += 2) {
      x1 = Math.min(x1, o.punkte[i]); x2 = Math.max(x2, o.punkte[i]);
      y1 = Math.min(y1, o.punkte[i + 1]); y2 = Math.max(y2, o.punkte[i + 1]);
    }
    return { x1, y1, x2, y2 };
  }
  return { x1: Math.min(o.x, o.x + o.w), y1: Math.min(o.y, o.y + o.h), x2: Math.max(o.x, o.x + o.w), y2: Math.max(o.y, o.y + o.h) };
}

/** Grenzen mehrerer Objekte oder null. */
export function alleGrenzen(objekte) {
  let g = null;
  for (const o of objekte) {
    const b = grenzen(o), rand = o.breite || 0;
    g = g ? { x1: Math.min(g.x1, b.x1 - rand), y1: Math.min(g.y1, b.y1 - rand), x2: Math.max(g.x2, b.x2 + rand), y2: Math.max(g.y2, b.y2 + rand) }
      : { x1: b.x1 - rand, y1: b.y1 - rand, x2: b.x2 + rand, y2: b.y2 + rand };
  }
  return g;
}

/** Kopie, um dx/dy verschoben. */
export function verschoben(o, dx, dy) {
  if (o.art === 'stift') return { ...o, punkte: o.punkte.map((v, i) => r1(v + (i % 2 ? dy : dx))) };
  return { ...o, x: r1(o.x + dx), y: r1(o.y + dy) };
}

function abstandStrecke(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay, l = dx * dx + dy * dy;
  const t = l ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / l)) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** Liegt der Punkt (Weltkoordinaten) auf dem Objekt? Rechteck/Ellipse nur am Rand, Text/Notiz in der ganzen Fläche. */
export function trifft(o, x, y, toleranz = 6) {
  const tol = toleranz + (o.breite || 0) / 2;
  if (o.art === 'stift') {
    const p = o.punkte;
    for (let i = 0; i + 3 < p.length; i += 2) if (abstandStrecke(x, y, p[i], p[i + 1], p[i + 2], p[i + 3]) <= tol) return true;
    return p.length === 2 && Math.hypot(x - p[0], y - p[1]) <= tol;
  }
  if (o.art === 'linie' || o.art === 'pfeil') return abstandStrecke(x, y, o.x, o.y, o.x + o.w, o.y + o.h) <= tol;
  const g = grenzen(o);
  if (o.art === 'text' || o.art === 'notiz') return x >= g.x1 - toleranz && x <= g.x2 + toleranz && y >= g.y1 - toleranz && y <= g.y2 + toleranz;
  if (o.art === 'rechteck') {
    const innen = x >= g.x1 + tol && x <= g.x2 - tol && y >= g.y1 + tol && y <= g.y2 - tol;
    const aussen = x >= g.x1 - tol && x <= g.x2 + tol && y >= g.y1 - tol && y <= g.y2 + tol;
    return aussen && !innen;
  }
  if (o.art === 'ellipse') {
    const rx = (g.x2 - g.x1) / 2, ry = (g.y2 - g.y1) / 2, cx = g.x1 + rx, cy = g.y1 + ry;
    if (rx < 1 || ry < 1) return Math.hypot(x - cx, y - cy) <= tol + Math.max(rx, ry);
    const d = Math.hypot((x - cx) / rx, (y - cy) / ry);
    return Math.abs(d - 1) * Math.min(rx, ry) <= tol;
  }
  return false;
}

/** Oberstes getroffenes Objekt (zuletzt gezeichnet = oben) oder null. */
export function oberstes(objekte, x, y, toleranz) {
  const liste = [...objekte];
  for (let i = liste.length - 1; i >= 0; i--) if (trifft(liste[i], x, y, toleranz)) return liste[i];
  return null;
}

/** Text in Zeilen umbrechen, die in `breite` passen (harte Umbrüche bleiben). */
export function umbrechen(ctx, text, breite) {
  const zeilen = [];
  for (const absatz of String(text || '').split('\n')) {
    let zeile = '';
    for (const wort of absatz.split(' ')) {
      const probe = zeile ? `${zeile} ${wort}` : wort;
      if (zeile && ctx.measureText(probe).width > breite) { zeilen.push(zeile); zeile = wort; } else zeile = probe;
    }
    zeilen.push(zeile);
  }
  return zeilen;
}

/** Größe eines Textobjekts aus seinem Text (für Treffer und Auswahl). */
export function textMass(ctx, text, breite) {
  const px = textGroesse({ breite });
  ctx.font = schrift(px);
  const zeilen = String(text || '').split('\n');
  return { w: Math.max(px, ...zeilen.map((z) => ctx.measureText(z).width)), h: zeilen.length * px * 1.25 };
}

/** Ein Objekt zeichnen (ctx bereits in Weltkoordinaten). */
export function zeichnen(ctx, o) {
  ctx.save();
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.strokeStyle = o.farbe;
  ctx.fillStyle = o.farbe;
  ctx.lineWidth = o.breite;
  if (o.art === 'stift') {
    const p = o.punkte;
    ctx.beginPath();
    ctx.moveTo(p[0], p[1]);
    if (p.length <= 4) ctx.lineTo(p[p.length - 2], p[p.length - 1]);
    for (let i = 2; i + 3 < p.length; i += 2) {           // geglättet: Kurve durch die Mittelpunkte
      const mx = (p[i] + p[i + 2]) / 2, my = (p[i + 1] + p[i + 3]) / 2;
      ctx.quadraticCurveTo(p[i], p[i + 1], mx, my);
    }
    if (p.length > 4) ctx.lineTo(p[p.length - 2], p[p.length - 1]);
    ctx.stroke();
  } else if (o.art === 'linie' || o.art === 'pfeil') {
    const x2 = o.x + o.w, y2 = o.y + o.h;
    ctx.beginPath(); ctx.moveTo(o.x, o.y); ctx.lineTo(x2, y2); ctx.stroke();
    if (o.art === 'pfeil' && (o.w || o.h)) {
      const w = Math.atan2(o.h, o.w), l = Math.max(12, o.breite * 3.5);
      ctx.beginPath();
      ctx.moveTo(x2 - l * Math.cos(w - 0.45), y2 - l * Math.sin(w - 0.45));
      ctx.lineTo(x2, y2);
      ctx.lineTo(x2 - l * Math.cos(w + 0.45), y2 - l * Math.sin(w + 0.45));
      ctx.stroke();
    }
  } else if (o.art === 'rechteck') {
    const g = grenzen(o);
    ctx.strokeRect(g.x1, g.y1, g.x2 - g.x1, g.y2 - g.y1);
  } else if (o.art === 'ellipse') {
    const g = grenzen(o);
    ctx.beginPath();
    ctx.ellipse((g.x1 + g.x2) / 2, (g.y1 + g.y2) / 2, (g.x2 - g.x1) / 2, (g.y2 - g.y1) / 2, 0, 0, Math.PI * 2);
    ctx.stroke();
  } else if (o.art === 'text') {
    const px = textGroesse(o);
    ctx.font = schrift(px);
    ctx.textBaseline = 'top';
    String(o.text || '').split('\n').forEach((z, i) => ctx.fillText(z, o.x, o.y + i * px * 1.25));
  } else if (o.art === 'notiz') {
    const g = grenzen(o);
    ctx.shadowColor = 'rgba(0, 0, 0, .18)'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 3;
    ctx.fillRect(g.x1, g.y1, g.x2 - g.x1, g.y2 - g.y1);
    ctx.shadowColor = 'transparent';
    ctx.fillStyle = NOTIZ_TEXT;
    ctx.font = schrift(18);
    ctx.textBaseline = 'top';
    const zeilen = umbrechen(ctx, o.text, g.x2 - g.x1 - 24);
    const passen = Math.max(1, Math.floor((g.y2 - g.y1 - 20) / 23));
    zeilen.slice(0, passen).forEach((z, i) => ctx.fillText(z, g.x1 + 12, g.y1 + 12 + i * 23, g.x2 - g.x1 - 24));
  }
  ctx.restore();
}

/** Ganze Tafel als PNG-Canvas (Export): Rand 40 px, höchstens 4096 px Kante. Gibt null zurück, wenn leer. */
export function exportCanvas(objekte, machCanvas) {
  const g = alleGrenzen(objekte);
  if (!g) return null;
  const rand = 40, w = g.x2 - g.x1 + 2 * rand, hh = g.y2 - g.y1 + 2 * rand;
  const s = Math.min(2, 4096 / Math.max(w, hh));
  const c = machCanvas(Math.ceil(w * s), Math.ceil(hh * s));
  const ctx = c.getContext('2d');
  ctx.fillStyle = TAFEL;
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.setTransform(s, 0, 0, s, (rand - g.x1) * s, (rand - g.y1) * s);
  for (const o of objekte) zeichnen(ctx, o);
  return c;
}

// ---------------------------------------------------------------- Operationen (wie der Server, Stand = Map id → Objekt)
/** Operation auf den Stand anwenden (neu/aendern setzen, loeschen entfernt). */
export function anwenden(objekte, op) {
  if (op.art === 'loeschen') objekte.delete(op.id);
  else if (op.art === 'neu' || objekte.has(op.objekt.id)) objekte.set(op.objekt.id, op.objekt);
}

/** Gegen-Operation zu `op` für den Stand VOR op (für Rückgängig) oder null. */
export function gegenteil(objekte, op) {
  if (op.art === 'neu') return { art: 'loeschen', id: op.objekt.id };
  const vorher = objekte.get(op.art === 'loeschen' ? op.id : op.objekt.id);
  if (!vorher) return null;
  return op.art === 'loeschen' ? { art: 'neu', objekt: vorher } : { art: 'aendern', objekt: vorher };
}

/** Operation an den aktuellen Stand anpassen (andere haben inzwischen gelöscht oder wiederhergestellt) oder null. */
export function passend(objekte, op) {
  if (op.art === 'loeschen') return objekte.has(op.id) ? op : null;
  const da = objekte.has(op.objekt.id);
  if (op.art === 'neu' && da) return { art: 'aendern', objekt: op.objekt };
  if (op.art === 'aendern' && !da) return { art: 'neu', objekt: op.objekt };
  return op;
}
