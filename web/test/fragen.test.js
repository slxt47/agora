// Tests der Q&A-Sortierung: npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fragenSortieren } from '../src/raum/fragen-rechnen.js';
import { t } from '../src/i18n.js';

const f = (id, stimmen, status = 'offen', hervorgehoben = false) => ({ id, stimmen, status, hervorgehoben });

test('Fragen: hervorgehoben oben, dann offen vor beantwortet vor verworfen; nach Stimmen oder Zeit', () => {
  const liste = [f(1, 2), f(2, 5), f(3, 9, 'beantwortet'), f(4, 0, 'offen', true), f(5, 5), f(6, 50, 'verworfen')];
  assert.deepEqual(fragenSortieren(liste, 'stimmen').map((x) => x.id), [4, 2, 5, 1, 3, 6]);
  assert.deepEqual(fragenSortieren(liste, 'zeit').map((x) => x.id), [4, 5, 2, 1, 3, 6]);
  assert.equal(liste[0].id, 1);                     // Eingabe bleibt unverändert
});

test('Q&A-Texte vorhanden', () => {
  for (const k of ['fragen.tab', 'fragen.anonym', 'fragen.anonymWer', 'fragen.alsBeantwortet', 'fragen.sort.stimmen', 'fragen.sort.zeit']) assert.notEqual(t(k), k, k);
});
