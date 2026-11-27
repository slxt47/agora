// PWA: Manifest, maskierbare Icons, Service-Worker-Regeln  (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const manifest = JSON.parse(readFileSync(new URL('../public/manifest.webmanifest', import.meta.url), 'utf8'));
const sw = readFileSync(new URL('../src/sw-vorlage.js', import.meta.url), 'utf8');

test('Manifest: Verknüpfungen, maskierbare Icons, Bereich', () => {
  assert.equal(manifest.scope, '/');
  assert.equal(manifest.start_url, '/');
  assert.deepEqual(manifest.shortcuts.map((s) => s.url), ['/?aktion=neu', '/?aktion=beitreten']);
  const maskable = manifest.icons.filter((i) => i.purpose === 'maskable').map((i) => i.sizes);
  assert.deepEqual(maskable, ['192x192', '512x512']);
});

test('Maskierbares Icon liegt in der Safe Zone (40 % Radius)', async () => {
  const { maskableSicher } = await import('../scripts/icons.mjs');
  assert.equal(maskableSicher(), true);
  assert.equal(maskableSicher(0.05), false, 'Prüfung muss zu knappe Ränder erkennen');
});

test('Service Worker: nie /api, /rtc, /vendor, Range, fremde Herkunft; Update nur auf Wunsch', () => {
  const nie = new RegExp(/const NIE = \/(.+)\/;/.exec(sw)[1].replace(/\\\//g, '/'));
  for (const p of ['/api/konto/ich', '/api/aufnahmen/1/datei', '/rtc', '/rtc/validate', '/vendor/mediapipe-0.10.14/wasm/x.wasm',
    '/hls/abc-defg-hij/AAAAAAAAAAAAAAAAAAAAAA/720p-live.m3u8']) assert.ok(nie.test(p), p);
  for (const p of ['/assets/a.js', '/apidoku', '/r/abc-defg-hij']) assert.ok(!nie.test(p), p);
  assert.match(sw, /headers\.has\('range'\)/);
  assert.match(sw, /u\.origin !== self\.location\.origin/);
  assert.match(sw, /typ === 'aktivieren'\) self\.skipWaiting\(\)/);
  assert.equal(sw.match(/skipWaiting\(\)/g).length, 1, 'skipWaiting nur nach „Neu laden“');
  assert.match(sw, /__VERSION__/);
});
