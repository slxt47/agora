// Video-Effekte: gespeicherte Wahl, Ziel für den Prozessor, Verkleinern, Texte  (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { BILDER, UNSCHAERFE, STANDARD, wahlPruefen, zielAus, verkleinert } from '../src/raum/effekte-wahl.js';
import { woerterbuch } from '../src/i18n.js';

test('Wahl prüfen: Unsinn wird „aus“, gültige Werte bleiben', () => {
  assert.deepEqual(wahlPruefen(null), STANDARD);
  assert.deepEqual(wahlPruefen('x'), STANDARD);
  assert.deepEqual(wahlPruefen({ hintergrund: 'quatsch', verbessern: 'ja' }), STANDARD);
  assert.deepEqual(wahlPruefen({ hintergrund: 'stark', verbessern: true }), { hintergrund: 'stark', verbessern: true });
  for (const b of BILDER) assert.equal(wahlPruefen({ hintergrund: b }).hintergrund, b);
  assert.equal(wahlPruefen({ hintergrund: 'eigen' }).hintergrund, 'eigen');
  assert.equal(wahlPruefen({ hintergrund: '__proto__' }).hintergrund, 'aus');
});

test('Ziel: aus, nur Filter, Unschärfe, Bild, eigenes Bild, ohne Unterstützung', () => {
  assert.equal(zielAus({ hintergrund: 'aus' }), null);
  assert.deepEqual(zielAus({ hintergrund: 'aus', verbessern: true }), { art: 'filter', verbessern: true });
  assert.equal(zielAus({ hintergrund: 'aus', verbessern: true }, { verbessernOk: false }), null);
  assert.deepEqual(zielAus({ hintergrund: 'leicht' }), { art: 'unschaerfe', radius: UNSCHAERFE.leicht, verbessern: false });
  assert.ok(UNSCHAERFE.stark > UNSCHAERFE.leicht);
  assert.deepEqual(zielAus({ hintergrund: 'polarlicht', verbessern: true }), { art: 'bild', id: 'polarlicht', verbessern: true });
  assert.equal(zielAus({ hintergrund: 'eigen' }), null);
  assert.deepEqual(zielAus({ hintergrund: 'eigen' }, { eigenesBild: true }), { art: 'bild', id: 'eigen', verbessern: false });
  assert.equal(zielAus({ hintergrund: 'stark' }, { hintergrundOk: false }), null);
  assert.deepEqual(zielAus({ hintergrund: 'stark', verbessern: true }, { hintergrundOk: false }), { art: 'filter', verbessern: true });
});

test('Verkleinern behält das Seitenverhältnis und vergrößert nie', () => {
  assert.deepEqual(verkleinert(4000, 3000, 1600), { b: 1600, h: 1200 });
  assert.deepEqual(verkleinert(1080, 1920, 1600), { b: 900, h: 1600 });
  assert.deepEqual(verkleinert(800, 600, 1600), { b: 800, h: 600 });
});

test('Jeder Hintergrund hat einen Namen und ein Bild im Generator', () => {
  const quelle = readFileSync(new URL('../scripts/hintergruende.mjs', import.meta.url), 'utf8');
  for (const b of BILDER) {
    assert.ok(woerterbuch.de[`effekte.bild.${b}`], `Text effekte.bild.${b} fehlt`);
    assert.match(quelle, new RegExp(`\\n  ${b}\\(\\)`), `Generator für ${b} fehlt`);
  }
});

test('Kein CDN in den MediaPipe-Pfaden', async () => {
  const { BASIS, MODELL } = await import('../scripts/mediapipe.mjs');
  assert.match(BASIS, /^\/vendor\/mediapipe-\d+\.\d+\.\d+$/);
  assert.ok(MODELL.startsWith(`${BASIS}/`) && MODELL.endsWith('.tflite'));
});
