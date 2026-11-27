// Fernsteuerung (steuernde Seite): Koordinaten im geteilten Bild, Tasten, Rad, Drossel, eigene Freigabe aus Metadaten.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Drossel, entpacken, knopf, meineSteuerung, normiert, rollen, tastenEreignis, verpacken } from '../src/raum/fernsteuerung-logik.js';
import { fernsteuerungSchluessel, schluesselNeu } from '../src/e2ee-schluessel.js';

test('normiert: object-fit contain mit Rändern, außerhalb des Bildes null', () => {
  const rect = { left: 100, top: 50, width: 800, height: 600 };          // 16:9-Bild in 4:3-Kachel → oben/unten Rand 75 px
  assert.deepEqual(normiert(100, 125, rect, 1920, 1080), { x: 0, y: 0 });
  assert.deepEqual(normiert(900, 575, rect, 1920, 1080), { x: 1, y: 1 });
  assert.deepEqual(normiert(500, 350, rect, 1920, 1080), { x: 0.5, y: 0.5 });
  assert.equal(normiert(500, 60, rect, 1920, 1080), null);                 // im schwarzen Rand
  assert.equal(normiert(50, 300, rect, 1920, 1080), null);
  assert.equal(normiert(500, 300, { left: 0, top: 0, width: 0, height: 0 }, 1920, 1080), null);
});

test('Tasten: Esc und Meta nie, Sondertasten und Zeichen mit Modifikatoren', () => {
  assert.equal(tastenEreignis({ key: 'Escape' }), null);
  assert.equal(tastenEreignis({ key: 'a', metaKey: true }), null);
  assert.equal(tastenEreignis({ key: 'Dead', isComposing: true }), null);
  assert.equal(tastenEreignis({ key: 'Shift', shiftKey: true }), null);
  assert.deepEqual(tastenEreignis({ key: 'Enter' }), { t: 'taste', taste: 'Enter', mod: [] });
  assert.deepEqual(tastenEreignis({ key: 'c', ctrlKey: true }), { t: 'taste', taste: 'c', mod: ['control'] });
  assert.deepEqual(tastenEreignis({ key: 'Ä', shiftKey: true }), { t: 'taste', taste: 'Ä', mod: ['shift'] });
});

test('Knöpfe, Rad in Rast-Schritten, Drossel', () => {
  assert.equal(knopf(0), 'left'); assert.equal(knopf(2), 'right'); assert.equal(knopf(1), 'middle'); assert.equal(knopf(3), null);
  assert.deepEqual(rollen(0, 100), { t: 'rollen', dx: 0, dy: 1 });
  assert.deepEqual(rollen(0, -3, 1), { t: 'rollen', dx: 0, dy: -1 });
  assert.deepEqual(rollen(0, 99999), { t: 'rollen', dx: 0, dy: 5 });
  assert.deepEqual(rollen(4, 0), { t: 'rollen', dx: 1, dy: 0 });
  let t = 0;
  const d = new Drossel(33, () => t);
  assert.ok(d.jetztSenden()); t = 10; assert.ok(!d.jetztSenden()); t = 40; assert.ok(d.jetztSenden());
});

test('meineSteuerung nur für mich', () => {
  const md = { fernsteuerung: [{ von: 'u1-a', an: 'g-b', id: 'x'.repeat(30) }] };
  assert.equal(meineSteuerung(md, 'g-b').von, 'u1-a');
  assert.equal(meineSteuerung(md, 'g-c'), null);
  assert.equal(meineSteuerung({}, 'g-b'), null);
  assert.equal(meineSteuerung(null, 'g-b'), null);
});

test('E2EE: verpacken/entpacken nur mit Schlüssel und passendem Absender', async () => {
  const geheim = schluesselNeu();
  const s = await fernsteuerungSchluessel(geheim);
  const ev = { t: 'taste', taste: 'a', mod: [] };
  const paket = await verpacken(s, ev, 'g-anna');
  assert.ok(!new TextDecoder().decode(paket).includes('taste'));
  assert.deepEqual(JSON.parse(new TextDecoder().decode(await entpacken(s, paket, 'g-anna'))), ev);
  assert.equal(await entpacken(s, paket, 'g-boris'), null);                                       // weitergereicht
  assert.equal(await entpacken(await fernsteuerungSchluessel(schluesselNeu()), paket, 'g-anna'), null);   // falscher Schlüssel
  assert.equal(await entpacken(s, new TextEncoder().encode(JSON.stringify(ev)), 'g-anna'), null);  // Klartext
  assert.equal(await entpacken(s, new Uint8Array(2000), 'g-anna'), null);
});
