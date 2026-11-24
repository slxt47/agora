// Tests der Datei-Vorprüfung im Browser: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dateiPruefen } from '../src/raum/dateien-rechnen.js';
import { t } from '../src/i18n.js';

test('Dateien: Vorprüfung im Browser', () => {
  const info = { grenze_datei: 100, grenze_meeting: 250, belegt: 200, endungen: ['pdf', 'png'] };
  assert.equal(dateiPruefen({ name: 'a.pdf', size: 10 }, info), null);
  assert.equal(dateiPruefen({ name: 'a.PDF', size: 10 }, info), null);
  assert.equal(dateiPruefen({ name: 'a.exe', size: 10 }, info).text, 'dateien.fehlerTyp');
  assert.equal(dateiPruefen({ name: 'ohne', size: 10 }, info).werte.endung, '–');
  assert.equal(dateiPruefen({ name: 'a.pdf', size: 0 }, info).text, 'dateien.fehlerLeer');
  assert.equal(dateiPruefen({ name: 'a.pdf', size: 101 }, info).text, 'dateien.fehlerGross');
  assert.equal(dateiPruefen({ name: 'a.pdf', size: 60 }, info).text, 'dateien.fehlerVoll');
});

test('Datei-Texte vorhanden', () => {
  for (const k of ['dateien.fehlerTyp', 'dateien.fehlerLeer', 'dateien.fehlerGross', 'dateien.fehlerVoll', 'dateien.ungeprueft', 'dateien.geprueft']) assert.notEqual(t(k), k, k);
});
