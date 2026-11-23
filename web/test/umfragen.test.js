// Tests der Umfrage-Rechenhilfen: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { anteile, spitzenreiter, optionenPruefen } from '../src/raum/umfrage-rechnen.js';
import { t } from '../src/i18n.js';

test('Anteile bei Einfachwahl ergeben genau 100 %', () => {
  assert.deepEqual(anteile([1, 1, 1], 3), [34, 33, 33]);
  assert.deepEqual(anteile([1, 2, 0], 3), [33, 67, 0]);
  assert.deepEqual(anteile([2, 2], 4), [50, 50]);
  assert.deepEqual(anteile([0, 0, 0], 0), [0, 0, 0]);
  assert.deepEqual(anteile([1, 0, 0], 0), [100, 0, 0]);          // ohne Stimmenzahl: Summe als Basis
});

test('Anteile bei Mehrfachwahl beziehen sich auf die Abstimmenden', () => {
  assert.deepEqual(anteile([2, 1, 2], 2, true), [100, 50, 100]);
  assert.deepEqual(anteile([1, 2], 3, true), [33, 67]);
  assert.deepEqual(anteile([1, 2], 0, true), [0, 0]);
});

test('Spitzenreiter', () => {
  assert.deepEqual(spitzenreiter([1, 3, 3]), [1, 2]);
  assert.deepEqual(spitzenreiter([0, 0]), []);
});

test('Antworten prüfen', () => {
  assert.deepEqual(optionenPruefen([' Ja ', '', 'Nein']), { optionen: ['Ja', 'Nein'], fehler: null });
  assert.equal(optionenPruefen(['Ja', '  ']).fehler, 'umfrage.zuWenig');
  assert.equal(optionenPruefen(['Ja', 'ja']).fehler, 'umfrage.doppelt');
  assert.equal(optionenPruefen(Array.from({ length: 11 }, (_, i) => `A${i}`)).fehler, 'umfrage.zuViele');
  for (const k of ['umfrage.zuWenig', 'umfrage.zuViele', 'umfrage.doppelt']) assert.notEqual(t(k), k);
});
