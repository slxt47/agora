// Push: Browser-Prüfung (iOS nur installiert), Schlüssel-Umwandlung, Gerätename, Service Worker öffnet nur eigene Pfade  (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pushMoeglich, schluesselBytes, geraetName } from '../src/push-hilfe.js';

const voll = (nav, extra = {}) => ({ navigator: { serviceWorker: {}, ...nav }, PushManager: {}, Notification: {}, isSecureContext: true, matchMedia: () => ({ matches: false }), ...extra });

test('pushMoeglich: Desktop ja, iOS nur als installierte App, ohne PushManager nein', () => {
  assert.deepEqual(pushMoeglich(voll({ userAgent: 'Mozilla/5.0 (X11; Linux x86_64) Chrome/140.0' })), { ok: true });
  const ios = { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1' };
  assert.deepEqual(pushMoeglich(voll(ios)), { grund: 'ios' });
  assert.deepEqual(pushMoeglich(voll({ ...ios, standalone: true })), { ok: true });
  assert.deepEqual(pushMoeglich(voll({ platform: 'MacIntel', maxTouchPoints: 5 })), { grund: 'ios' });    // iPadOS meldet sich als Mac
  assert.deepEqual(pushMoeglich({ navigator: { userAgent: 'x' }, matchMedia: () => ({ matches: false }) }), { grund: 'nein' });
  assert.deepEqual(pushMoeglich(voll({ userAgent: 'x' }, { isSecureContext: false })), { grund: 'nein' });
});

test('schluesselBytes: Base64url ohne Auffüllung → 65 Byte', () => {
  const k = 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8';  // gitleaks:allow (RFC-8291-Testvektor, öffentlicher Schlüssel)
  const b = schluesselBytes(k);
  assert.equal(b.length, 65);
  assert.equal(b[0], 4);
  assert.equal(Buffer.from(b).toString('base64url'), k);
});

test('geraetName', () => {
  assert.equal(geraetName('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36'), 'Chrome · Android');
  assert.equal(geraetName('Mozilla/5.0 (Windows NT 10.0) Chrome/140.0 Safari/537.36 Edg/140.0'), 'Edge · Windows');
  assert.equal(geraetName('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Gecko/20100101 Firefox/140.0'), 'Firefox · macOS');
  assert.equal(geraetName('?'), 'Browser');
});

test('Service Worker: Push zeigt Benachrichtigung, Klick öffnet nur Pfade der eigenen Herkunft', () => {
  const sw = readFileSync(new URL('../src/sw-vorlage.js', import.meta.url), 'utf8');
  assert.match(sw, /addEventListener\('push'/);
  assert.match(sw, /showNotification\(/);
  assert.match(sw, /addEventListener\('notificationclick'/);
  assert.match(sw, /addEventListener\('pushsubscriptionchange'/);
  const code = /function zielPfad\(url\) \{([\s\S]*?)\n\}/.exec(sw)[1];
  const zielPfad = new Function('url', code);     // nur im Test: dieselbe Funktion wie im Service Worker
  assert.equal(zielPfad('/r/abc-defg-hij'), '/r/abc-defg-hij');
  assert.equal(zielPfad('/konto#aufnahmen'), '/konto#aufnahmen');
  for (const boese of ['https://boese.example/', '//boese.example/x', '/\\boese.example', 'javascript:alert(1)', 42, null]) assert.equal(zielPfad(boese), '/', String(boese));
});
