// Tests der Breakout-Entscheidungen: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { wohin, groessen, vorschlagAnzahl } from '../src/raum/breakout-rechnen.js';
import { t } from '../src/i18n.js';

const offen = (mein, runde = 1) => ({ status: 'offen', mein_raum: mein, runde });

test('Neue Runde: Zugeteilte wechseln in ihren Raum, andere bleiben', () => {
  assert.equal(wohin(offen(2), { ort: 0, runde: 0, zuteilung: 0 }), 2);
  assert.equal(wohin(offen(0), { ort: 0, runde: 0, zuteilung: 0 }), null);
  assert.equal(wohin(offen(3), { ort: 0, runde: null, zuteilung: null }), 3);        // spät dazugekommen / neu geladen
});

test('Selbst zurück oder Host zu Besuch: kein erneutes Verschieben', () => {
  assert.equal(wohin(offen(2), { ort: 0, runde: 1, zuteilung: 2 }), null);           // selbst in den Hauptraum gegangen
  assert.equal(wohin(offen(0), { ort: 3, runde: 1, zuteilung: 0 }), null);           // Host besucht Raum 3
});

test('Zuteilung geändert, während offen', () => {
  assert.equal(wohin(offen(1), { ort: 2, runde: 1, zuteilung: 2 }), 1);
  assert.equal(wohin(offen(0), { ort: 2, runde: 1, zuteilung: 2 }), 0);              // aus dem Raum genommen
});

test('Countdown und geschlossen', () => {
  assert.equal(wohin({ status: 'schliesst', mein_raum: 1, runde: 1 }, { ort: 1, runde: 1, zuteilung: 1 }), null);
  assert.equal(wohin({ status: 'geschlossen', mein_raum: 1, runde: 1 }, { ort: 1, runde: 1, zuteilung: 1 }), 0);
  assert.equal(wohin(null, { ort: 2 }), 0);
  assert.equal(wohin(null, { ort: 0 }), null);
});

test('Gleichmäßige Größen und Vorschlag', () => {
  assert.deepEqual(groessen(7, 3), [3, 2, 2]);
  assert.deepEqual(groessen(0, 2), [0, 0]);
  assert.equal(vorschlagAnzahl(3), 2);
  assert.equal(vorschlagAnzahl(20), 5);
  assert.equal(vorschlagAnzahl(500), 50);
  assert.notEqual(t('breakout.titel'), 'breakout.titel');
});
