// Tests der Whiteboard-Geometrie und -Operationen: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  vereinfachen, grenzen, alleGrenzen, verschoben, trifft, oberstes, umbrechen, anwenden, gegenteil, passend, neueId, MAX_PUNKTE,
} from '../src/raum/whiteboard-zeichnen.js';

const strich = { id: 's1', art: 'stift', farbe: '#1d1d1f', breite: 4, punkte: [0, 0, 100, 0, 100, 100] };
const rechteck = { id: 'r1', art: 'rechteck', farbe: '#2f80ed', breite: 2, x: 200, y: 200, w: -100, h: 50 };

test('Strich vereinfachen: nahe Punkte fallen weg, Ende bleibt, höchstens MAX_PUNKTE', () => {
  assert.deepEqual(vereinfachen([0, 0, 0.5, 0.5, 1, 1, 10, 10.04]), [0, 0, 10, 10]);
  assert.deepEqual(vereinfachen([5, 5]), [5, 5, 5, 5]);
  const lang = [];
  for (let i = 0; i < 9000; i++) lang.push(i * 3, 0);
  const v = vereinfachen(lang);
  assert.ok(v.length / 2 <= MAX_PUNKTE && v.length % 2 === 0);
  assert.deepEqual(v.slice(-2), [8999 * 3, 0]);
});

test('Grenzen auch bei negativer Breite/Höhe', () => {
  assert.deepEqual(grenzen(rechteck), { x1: 100, y1: 200, x2: 200, y2: 250 });
  assert.deepEqual(grenzen(strich), { x1: 0, y1: 0, x2: 100, y2: 100 });
  assert.deepEqual(alleGrenzen([strich, rechteck]), { x1: -4, y1: -4, x2: 202, y2: 252 });
  assert.equal(alleGrenzen([]), null);
});

test('Treffer: Strich, Rechteck nur am Rand, Ellipse, Notiz in der Fläche', () => {
  assert.ok(trifft(strich, 50, 3));
  assert.ok(trifft(strich, 102, 50));
  assert.ok(!trifft(strich, 50, 50));
  assert.ok(trifft(rechteck, 100, 220));
  assert.ok(!trifft(rechteck, 150, 225));
  const e = { id: 'e1', art: 'ellipse', farbe: '#000000', breite: 2, x: 0, y: 0, w: 200, h: 100 };
  assert.ok(trifft(e, 200, 50) && trifft(e, 100, 0) && !trifft(e, 100, 50));
  const n = { id: 'n1', art: 'notiz', farbe: '#ffe58a', breite: 1, x: 0, y: 0, w: 200, h: 150, text: '' };
  assert.ok(trifft(n, 100, 75) && !trifft(n, 300, 75));
  const pf = { id: 'p1', art: 'pfeil', farbe: '#000000', breite: 2, x: 0, y: 0, w: 100, h: 100 };
  assert.ok(trifft(pf, 50, 51) && !trifft(pf, 50, 80));
  assert.equal(oberstes([n, rechteck], 100, 220)?.id, 'r1');      // zuletzt gezeichnet liegt oben
  assert.equal(oberstes([rechteck, n], 100, 75)?.id, 'n1');
  assert.equal(oberstes([rechteck], 0, 0), null);
});

test('Verschieben rundet auf 0,1', () => {
  assert.deepEqual(verschoben(strich, 1.04, 2).punkte, [1, 2, 101, 2, 101, 102]);
  assert.deepEqual([verschoben(rechteck, -0.06, 0).x, verschoben(rechteck, 0, 0).y], [199.9, 200]);
});

test('Umbrechen nach Breite, harte Zeilenumbrüche bleiben', () => {
  const ctx = { measureText: (s) => ({ width: s.length * 10 }) };
  assert.deepEqual(umbrechen(ctx, 'eins zwei drei\nvier', 95), ['eins zwei', 'drei', 'vier']);
  assert.deepEqual(umbrechen(ctx, '', 50), ['']);
});

test('Operationen: anwenden, Gegenteil (Rückgängig), an den Stand anpassen', () => {
  const s = new Map();
  const neu = { art: 'neu', objekt: strich };
  assert.deepEqual(gegenteil(s, neu), { art: 'loeschen', id: 's1' });
  anwenden(s, neu);
  const zug = { art: 'aendern', objekt: verschoben(strich, 5, 5) };
  assert.deepEqual(gegenteil(s, zug), { art: 'aendern', objekt: strich });
  anwenden(s, zug);
  assert.equal(s.get('s1').punkte[0], 5);
  assert.deepEqual(gegenteil(s, { art: 'loeschen', id: 's1' }), { art: 'neu', objekt: s.get('s1') });
  anwenden(s, { art: 'loeschen', id: 's1' });
  assert.equal(s.size, 0);
  anwenden(s, zug);                                   // ändern ohne Objekt: nichts
  assert.equal(s.size, 0);
  assert.equal(gegenteil(s, zug), null);
  // andere haben inzwischen gelöscht/wiederhergestellt
  assert.equal(passend(s, { art: 'loeschen', id: 's1' }), null);
  assert.deepEqual(passend(s, zug), { art: 'neu', objekt: zug.objekt });
  anwenden(s, neu);
  assert.deepEqual(passend(s, neu), { art: 'aendern', objekt: strich });
});

test('Objekt-ids passen zum Server-Muster', () => {
  const ids = new Set(Array.from({ length: 50 }, neueId));
  assert.equal(ids.size, 50);
  for (const i of ids) assert.match(i, /^[A-Za-z0-9_-]{6,40}$/);
});
