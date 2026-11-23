// Melden und Blockieren: Regeln und Texte (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { GRUENDE, kontoVon } from '../src/raum/melden-regeln.js';
import { woerterbuch } from '../src/i18n.js';

test('Konto aus identity', () => {
  assert.equal(kontoVon('u12-ab34cd'), 12);
  assert.equal(kontoVon('g-1234567890'), null);
  assert.equal(kontoVon('ux-1'), null);
  assert.equal(kontoVon(undefined), null);
});

test('Jeder Grund hat einen Text', () => {
  assert.deepEqual(GRUENDE, ['belaestigung', 'hass', 'unangemessen', 'stoerung', 'spam', 'sonstiges']);
  for (const g of GRUENDE) assert.ok(woerterbuch.de[`melden.grund.${g}`], g);
  for (const k of ['melden.melden', 'melden.blockieren', 'melden.hostHinweis', 'blockiert.leer']) assert.ok(woerterbuch.de[k], k);
});
