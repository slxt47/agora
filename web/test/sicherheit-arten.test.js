// Sicherheitsereignisse: jede Art hat Symbol und Text (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SICHERHEIT_ARTEN, sicherheitArt } from '../src/sicherheit-arten.js';
import { woerterbuch } from '../src/i18n.js';

test('Arten wie im Backend, mit Text und Symbol', () => {
  // gleiche Liste wie app/sicherheit.py (ARTEN) – der API-Test prüft die Gegenrichtung
  assert.equal(SICHERHEIT_ARTEN.length, 16);
  for (const a of SICHERHEIT_ARTEN) {
    assert.ok(woerterbuch.de[`sicherheit.art.${a}`], a);
    assert.ok(['ok', 'warn'].includes(sicherheitArt(a).ton));
  }
  assert.deepEqual(sicherheitArt('unbekannt'), { symbol: 'info', ton: 'ok' });
});
