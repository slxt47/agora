// Admin-Bereich: Zahlen und Texte (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { adminZahl } from '../src/admin-format.js';
import { woerterbuch } from '../src/i18n.js';

test('Zahlen deutsch, fehlende als Strich', () => {
  assert.equal(adminZahl(0), '0');
  assert.equal(adminZahl(12345), '12.345'.replace('.', new Intl.NumberFormat('de-AT').format(1000).charAt(1)));
  assert.equal(adminZahl(null), '–');
  assert.equal(adminZahl(undefined), '–');
});

test('Admin-Texte vorhanden', () => {
  for (const k of ['admin.laufend', 'admin.sperren', 'admin.meetingBeenden', 'admin.keineMeldungen', 'admin.link']) assert.ok(woerterbuch.de[k], k);
});
