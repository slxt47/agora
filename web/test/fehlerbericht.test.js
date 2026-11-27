// Fehler-Monitoring im Browser: Säubern (nie das E2EE-Fragment #k=…), Bericht bauen, Texte (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bereinigen, seiteBereinigen, berichtBauen, apiFehlerMelden } from '../src/fehlerbericht.js';
import { woerterbuch } from '../src/i18n.js';
import { en } from '../src/texte-en.js';

test('Fragment, Query, Herkunft, E-Mail und Code verschwinden', () => {
  const s = bereinigen('Fehler bei https://agora.slxt.dev/r/abc-defg-hij?pc=1#k=GEHEIM123456 von ada@example.org');
  assert.equal(s, 'Fehler bei /r/‹code› von ‹e-mail›');
  assert.ok(!bereinigen('x /a.js#k=GEHEIM123456:1:2').includes('GEHEIM'));
  assert.ok(!bereinigen('k=GEHEIM123456 %23k%3DGEHEIM123456').includes('GEHEIM'));
  assert.equal(bereinigen('x'.repeat(900)).length, 500);
});

test('Seite: nur Pfad, Codes und Token ersetzt', () => {
  assert.equal(seiteBereinigen('/r/abc-defg-hij'), '/r/‹code›');
  assert.equal(seiteBereinigen('/a/AbCdEf0123456789xyz#k=1'), '/a/‹code›');
  assert.equal(seiteBereinigen('/konto'), '/konto');
});

test('Bericht: Felder, Stack gekürzt, ohne Fragment', () => {
  const stapel = Array.from({ length: 50 }, (_, i) => `at f${i} (https://h/assets/a.js?v=2#k=GEHEIM123456:${i}:1)`).join('\n');
  const b = berichtBauen('fehler', 'TypeError: kaputt', stapel, '/r/abc-defg-hij');
  assert.deepEqual(Object.keys(b).sort(), ['art', 'nachricht', 'seite', 'stapel', 'version']);
  assert.equal(b.stapel.split('\n').length, 20);
  assert.ok(!JSON.stringify(b).includes('GEHEIM') && !JSON.stringify(b).includes('abc-defg-hij'));
  assert.equal(b.stapel.split('\n')[0], 'at f0 (/assets/a.js)');
  assert.equal(berichtBauen('promise', '').nachricht, '(ohne Text)');
});

test('API-Fehler unter 500 und der Bericht selbst werden nicht gemeldet', () => {
  let aufrufe = 0;
  const alt = globalThis.fetch;
  let body = null;
  globalThis.fetch = (_u, o) => { aufrufe += 1; body = JSON.parse(o.body); return Promise.resolve(); };
  globalThis.location = { pathname: '/r/abc-defg-hij' };
  try {
    apiFehlerMelden('GET', '/api/x', 404);
    apiFehlerMelden('POST', '/api/fehlerbericht', 500);
    assert.equal(aufrufe, 0);
    apiFehlerMelden('GET', '/api/raeume/abc-defg-hij/zustand?x=1', 502);
    apiFehlerMelden('GET', '/api/raeume/abc-defg-hij/zustand?x=1', 502);      // gleicher Fehler nur einmal je Seite
    assert.equal(aufrufe, 1);
    assert.deepEqual([body.art, body.nachricht, body.seite], ['api', 'GET /api/raeume/‹code›/zustand → 502', '/r/‹code›']);
  } finally { globalThis.fetch = alt; delete globalThis.location; }
});

test('Texte für den Reiter „Fehler“ in beiden Sprachen', () => {
  for (const k of ['fb.reiter', 'fb.titel', 'fb.hinweis', 'fb.art.api', 'fb.art.ausnahme', 'fb.quelle.server', 'fb.erledigen', 'fb.auftreten']) {
    assert.ok(woerterbuch.de[k], k);
    assert.ok(en[k], k);
  }
});
