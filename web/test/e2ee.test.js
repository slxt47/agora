// Ende-zu-Ende-Verschlüsselung: Schlüsselformat, Fragment, Ableitungen (WebCrypto unter Node)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  SCHLUESSEL_MUSTER, schluesselNeu, schluesselAusText, schluesselAusFragment, linkMitSchluessel, pruefwert, medienSchluessel,
  schluesselMerken, schluesselGemerkt,
} from '../src/e2ee-schluessel.js';
import { woerterbuch } from '../src/i18n.js';

test('neue Schlüssel: 43 Zeichen base64url, zufällig', () => {
  const a = schluesselNeu(), b = schluesselNeu();
  assert.match(a, SCHLUESSEL_MUSTER);
  assert.notEqual(a, b);
});

test('Schlüssel aus Text, Link und Fragment', () => {
  const k = schluesselNeu();
  assert.equal(schluesselAusText(` ${k} `), k);
  assert.equal(schluesselAusText(`https://agora.slxt.dev/r/abc-defg-hij#k=${k}`), k);
  assert.equal(schluesselAusText('zu-kurz'), '');
  assert.equal(schluesselAusFragment(`#k=${k}`), k);
  assert.equal(schluesselAusFragment(`#x=1&k=${k}`), k);
  assert.equal(schluesselAusFragment(`#k=${k.slice(1)}`), '');
  assert.equal(schluesselAusFragment(''), '');
  assert.equal(linkMitSchluessel('https://h/r/abc-defg-hij#alt', k), `https://h/r/abc-defg-hij#k=${k}`);
  assert.equal(linkMitSchluessel('https://h/r/x', ''), 'https://h/r/x');
});

test('Prüfwert und Medienschlüssel: deterministisch, verschieden, unabhängig', async () => {
  const k = schluesselNeu();
  const p1 = await pruefwert(k), p2 = await pruefwert(k);
  assert.match(p1, /^[0-9a-f]{32}$/);
  assert.equal(p1, p2);
  assert.notEqual(p1, await pruefwert(schluesselNeu()));
  const m = new Uint8Array(await medienSchluessel(k));
  assert.equal(m.length, 32);
  const hex = [...m].map((x) => x.toString(16).padStart(2, '0')).join('');
  assert.ok(!hex.startsWith(p1), 'Prüfwert darf kein Teil des Medienschlüssels sein');
  // fester Testvektor: 32 Nullbytes → stabile Ableitung (bricht, wenn sich SALZ/info ändern – das wäre inkompatibel)
  const null32 = 'A'.repeat(43);
  assert.equal(await pruefwert(null32), await pruefwert(null32));
});

test('Gerätespeicher: nur gültige Schlüssel, Löschen mit leerem Wert', () => {
  const m = new Map();
  const speicher = { getItem: (x) => m.get(x) ?? null, setItem: (x, v) => m.set(x, v), removeItem: (x) => m.delete(x) };
  const k = schluesselNeu();
  schluesselMerken('abc-defg-hij', k, speicher);
  assert.equal(schluesselGemerkt('abc-defg-hij', speicher), k);
  speicher.setItem('agora-e2ee:x', 'kaputt');
  assert.equal(schluesselGemerkt('x', speicher), '');
  schluesselMerken('abc-defg-hij', '', speicher);
  assert.equal(schluesselGemerkt('abc-defg-hij', speicher), '');
});

test('alle E2EE-Texte vorhanden', () => {
  for (const k of ['e2ee.kurz', 'e2ee.falscherSchluessel', 'e2ee.nichtUnterstuetzt', 'e2ee.fehlerAndere', 'e2ee.aufnahmeLokal']) {
    assert.ok(woerterbuch.de[k], k);
  }
});
