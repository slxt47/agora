// Einbetten: Zielherkünfte für postMessage (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ziele } from '../src/raum/einbetten-ziele.js';

test('nur erlaubte Herkünfte', () => {
  const erlaubt = ['https://a.example', 'https://b.example'];
  assert.deepEqual(ziele(erlaubt, ['https://a.example']), ['https://a.example']);      // ancestorOrigins bekannt
  assert.deepEqual(ziele(erlaubt, ['https://boese.example']), []);                     // fremde Eltern-Seite: nichts
  assert.deepEqual(ziele(erlaubt, undefined, 'https://b.example'), ['https://b.example']);
  assert.deepEqual(ziele(erlaubt, undefined, 'https://boese.example'), erlaubt);       // Wunsch ignoriert, feste targetOrigin je Eintrag
  assert.deepEqual(ziele(undefined, undefined, 'https://x.example'), []);
  assert.deepEqual(ziele(['https://a.example', 3], []), ['https://a.example']);
});
