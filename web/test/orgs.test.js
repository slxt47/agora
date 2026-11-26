// Organisationen: Kürzel, Rollen, Kurzname, Texte (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { orgKuerzel, rolleRang, slugVorschlag, FARBEN_ORG } from '../src/org-format.js';
import { de } from '../src/texte-de.js';
import { en } from '../src/texte-en.js';

test('Kürzel fürs Logo', () => {
  assert.equal(orgKuerzel('Firma Eins'), 'FE');
  assert.equal(orgKuerzel('müller & söhne'), 'MS');
  assert.equal(orgKuerzel('agora'), 'AG');
  assert.equal(orgKuerzel('  '), '?');
  assert.equal(orgKuerzel('<b>x</b> y'), 'BX');
});

test('Rollen-Rang', () => {
  assert.ok(rolleRang('owner') > rolleRang('admin') && rolleRang('admin') > rolleRang('mitglied'));
  assert.equal(rolleRang('gast'), 0);
});

test('Kurzname wie der Server', () => {
  assert.equal(slugVorschlag('Müller & Söhne'), 'mueller-soehne');
  assert.equal(slugVorschlag('AB'), 'ab-team');
  assert.equal(slugVorschlag('!!'), '');
  assert.ok(slugVorschlag('x'.repeat(80)).length <= 40);
});

test('Farben und Texte', () => {
  assert.ok(FARBEN_ORG.every((f) => /^#[0-9a-f]{6}$/.test(f)));
  for (const r of ['owner', 'admin', 'mitglied']) assert.ok(de[`org.rolle.${r}`] && en[`org.rolle.${r}`], r);
});
