// Anmerkungen: Nachrichten prüfen, Rechte, Striche, Radierer, Geometrie, Begrenzer
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Begrenzer, FARBEN, GRENZEN, Striche, darf, inhaltRechteck, nachrichtPruefen, neueId, runden } from '../src/raum/anmerkungen-logik.js';
import { woerterbuch } from '../src/i18n.js';

const strich = (x = {}) => ({ t: 'strich', s: 'praes', id: 'abcd12', w: 'stift', f: 0, i: 0, p: [[0.1, 0.2], [0.3, 0.4]], ...x });

test('Nachrichten: gültige durch, Unsinn raus', () => {
  assert.deepEqual(nachrichtPruefen(strich()), strich());
  for (const kaputt of [null, 'x', {}, strich({ w: 'kanone' }), strich({ f: FARBEN.length }), strich({ p: [[2, 0]] }), strich({ p: [] }),
    strich({ p: [[NaN, 0]] }), strich({ id: 'X!' }), strich({ i: -1 }), strich({ s: '' }), strich({ p: Array(201).fill([0, 0]) }),
    { t: 'text', s: 'praes', id: 'abcd12', f: 0, x: 0.5, y: 0.5, text: '   ' }, { t: 'boese', s: 'praes' }, { t: 'leeren', s: 'praes', nur: 5 }]) {
    assert.equal(nachrichtPruefen(kaputt), null, JSON.stringify(kaputt));
  }
  const text = nachrichtPruefen({ t: 'text', s: 'praes', id: 'abcd12', f: 1, x: 0.5, y: 0.5, text: `Hallo\u0007 ${'x'.repeat(300)}` });
  assert.equal(text.text.length, GRENZEN.text);
  assert.ok(!text.text.includes('\u0007'));
  assert.deepEqual(nachrichtPruefen({ t: 'leeren', s: 'praes', extra: 1 }), { t: 'leeren', s: 'praes' });
});

test('Rechte: ohne „erlaubt“ nur Host/Co-Host und die teilende Person; Löschen fremder nur für sie', () => {
  const rolle = (id) => ({ host: 'host', co: 'cohost' }[id] || 'teilnehmer');
  const aus = { erlaubt: false, rolle }, an = { erlaubt: true, rolle };
  const n = nachrichtPruefen(strich());
  assert.equal(darf(n, 'gast', aus), false);
  assert.equal(darf(n, 'gast', an), true);
  assert.equal(darf(n, 'host', aus), true);
  assert.equal(darf(n, 'co', aus), true);
  assert.equal(darf(n, 'praes', aus), true);
  const alle = { t: 'leeren', s: 'praes' };
  assert.equal(darf(alle, 'gast', an), false);
  assert.equal(darf(alle, 'praes', an), true);
  assert.equal(darf({ t: 'leeren', s: 'praes', nur: 'gast' }, 'gast', an), true);
  assert.equal(darf({ t: 'leeren', s: 'praes', nur: 'anderer' }, 'gast', an), false);
  assert.equal(darf({ t: 'weg', s: 'praes', id: 'abcd12', von: 'anderer' }, 'gast', an), false);
  assert.equal(darf({ t: 'weg', s: 'praes', id: 'abcd12', von: 'anderer' }, 'host', an), true);
});

test('Striche: fortsetzen, Pfeil ersetzen, löschen, Grenze', () => {
  const s = new Striche();
  s.anwenden(strich(), 'a');
  s.anwenden(strich({ i: 2, p: [[0.5, 0.5]] }), 'a');
  assert.equal(s.m.get('a:abcd12').p.length, 3);
  assert.equal(s.anwenden(strich({ i: 9, p: [[0.5, 0.5]] }), 'a'), false);          // Lücke
  s.anwenden(strich({ w: 'pfeil', id: 'pf0001', p: [[0, 0], [1, 1]] }), 'b');
  s.anwenden(strich({ w: 'pfeil', id: 'pf0001', p: [[0, 0], [0.5, 0.5]] }), 'b');
  assert.deepEqual(s.m.get('b:pf0001').p, [[0, 0], [0.5, 0.5]]);
  assert.equal(s.anwenden({ t: 'leeren', s: 'praes', nur: 'b' }, 'b'), true);
  assert.equal(s.groesse, 1);
  assert.equal(s.anwenden({ t: 'weg', s: 'praes', id: 'abcd12', von: 'a' }, 'host'), true);
  assert.equal(s.groesse, 0);
  for (let i = 0; i < GRENZEN.striche + 5; i++) s.anwenden(strich({ id: `id${String(i).padStart(4, '0')}` }), 'a');
  assert.equal(s.groesse, GRENZEN.striche);
});

test('Radierer trifft nahe Linien und Texte, auf Wunsch nur eigene', () => {
  const s = new Striche();
  s.anwenden(strich({ p: [[0.1, 0.1], [0.9, 0.1]] }), 'a');
  s.anwenden({ t: 'text', s: 'praes', id: 'text01', f: 0, x: 0.5, y: 0.8, text: 'Hi' }, 'b');
  assert.equal(s.treffer(0.5, 0.105, 0.015)?.id, 'abcd12');
  assert.equal(s.treffer(0.5, 0.5, 0.015), null);
  assert.equal(s.treffer(0.505, 0.8, 0.015)?.id, 'text01');
  assert.equal(s.treffer(0.505, 0.8, 0.015, 'a'), null);
});

test('Geometrie: sichtbares Bild bei object-fit contain', () => {
  assert.deepEqual(inhaltRechteck(1600, 900, 1920, 1080), { x: 0, y: 0, w: 1600, h: 900 });
  assert.deepEqual(inhaltRechteck(1000, 1000, 1920, 1080), { x: 0, y: 218.75, w: 1000, h: 562.5 });
  assert.deepEqual(inhaltRechteck(400, 100, 100, 100), { x: 150, y: 0, w: 100, h: 100 });
  assert.deepEqual(inhaltRechteck(400, 300, 0, 0), { x: 0, y: 0, w: 400, h: 300 });
  assert.equal(runden(1.2), 1); assert.equal(runden(-1), 0); assert.equal(runden(0.123456), 0.1235);
});

test('Begrenzer: Stoß, dann Rate', () => {
  let jetzt = 0;
  const b = new Begrenzer(10, 3, () => jetzt);
  assert.deepEqual([b.ok('x'), b.ok('x'), b.ok('x'), b.ok('x')], [true, true, true, false]);
  jetzt = 100;
  assert.equal(b.ok('x'), true);
  assert.equal(b.ok('x'), false);
  assert.equal(b.ok('y'), true);
  assert.match(neueId(), /^[0-9a-z]{4,16}$/);
});

test('Anmerkungs-Texte vorhanden', () => {
  for (const k of ['anm.starten', 'anm.leiste', 'anm.w.stift', 'anm.w.radierer', 'anm.farbe.0', 'anm.farbe.5', 'anm.alleLoeschen', 'anm.erlaubtAus']) {
    assert.ok(woerterbuch.de[k], k);
  }
});
