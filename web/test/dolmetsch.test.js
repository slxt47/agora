// Dolmetscher-Kanäle: welche Spur abonniert wird (nur die vom Server genannte), Partner für die Übergabe  (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { kanalSpur, kanalZiel, abonnieren, partner, sprachName, LEISE } from '../src/raum/dolmetsch-logik.js';

const Z = {
  sprachen: ['de', 'en', 'fr'],
  dolmetscher: { 'g-anna': { name: 'Anna', a: 'de', b: 'en', sprache: 'en', aktiv: true }, 'g-bert': { name: 'Bert', a: 'en', b: 'de', sprache: 'de', aktiv: false },
    'g-cleo': { name: 'Cleo', a: 'de', b: 'fr', sprache: 'fr', aktiv: true } },
  kanaele: { en: 'g-anna', fr: 'g-cleo' },
};

test('kanalSpur erkennt nur dolmetsch:<code>', () => {
  assert.equal(kanalSpur('dolmetsch:en'), 'en');
  for (const x of ['', 'dolmetsch:', 'dolmetsch:EN', 'mikrofon', 'xdolmetsch:en', 'dolmetsch:en ', null]) assert.equal(kanalSpur(x), null, String(x));
});

test('abonnieren: nur die Spur der Person auf Sendung, nur passender Name', () => {
  assert.deepEqual(kanalZiel(Z, 'en'), { identity: 'g-anna', spur: 'dolmetsch:en' });
  assert.equal(kanalZiel(Z, 'de'), null);                          // niemand übersetzt gerade ins Deutsche → Original
  assert.equal(kanalZiel(Z, ''), null);
  assert.equal(abonnieren(Z, 'en', 'g-anna', 'dolmetsch:en'), true);
  assert.equal(abonnieren(Z, 'en', 'g-boese', 'dolmetsch:en'), false);   // gleicher Spurname, aber nicht vom Server zugewiesen
  assert.equal(abonnieren(Z, 'en', 'g-anna', 'dolmetsch:fr'), false);
  assert.equal(abonnieren(Z, 'fr', 'g-anna', 'dolmetsch:en'), false);
  assert.equal(abonnieren(Z, '', 'g-anna', 'dolmetsch:en'), false);
  assert.equal(abonnieren({}, 'en', 'g-anna', 'dolmetsch:en'), false);
});

test('partner: gleiches Sprachpaar in beliebiger Richtung', () => {
  assert.deepEqual(partner(Z, 'g-anna').map((p) => p.identity), ['g-bert']);
  assert.deepEqual(partner(Z, 'g-cleo'), []);
  assert.deepEqual(partner(Z, 'g-niemand'), []);
});

test('Sprachnamen und Saal-Lautstärke', () => {
  assert.equal(sprachName('en', 'de'), 'Englisch');
  assert.equal(sprachName('de', 'en-GB'), 'German');
  assert.equal(LEISE, 0.2);
});
