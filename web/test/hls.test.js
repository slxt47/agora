// Zuschauerseite der öffentlichen Übertragung: Token aus dem Link, nur eigene Quellen, Abspielweg, Zuschauer-Kennung.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { abspielweg, quelleGueltig, tokenAusLink, zuschauerId } from '../src/hls-logik.js';

test('Token aus dem eingefügten Link oder direkt', () => {
  const tok = 'AbC_defGHIjkl-mnoPQRstuVWX';
  assert.equal(tokenAusLink(`https://agora.slxt.dev/r/abc-defg-hij?anmeldung=${tok}`), tok);
  assert.equal(tokenAusLink(`  /h/abc-defg-hij?x=1&anmeldung=${tok}#k`), tok);
  assert.equal(tokenAusLink(tok), tok);
  for (const nein of ['', 'kurz', 'mit leerzeichen drin', '<script>alert(1)</script>', 'x'.repeat(500), null, undefined, '?anmeldung=ab']) {
    assert.equal(tokenAusLink(nein), '', String(nein));
  }
});

test('nur Quellen der eigenen Auslieferung', () => {
  const k = 'A'.repeat(22);
  assert.ok(quelleGueltig(`/hls/abc-defg-hij/${k}/index.m3u8`, 'abc-defg-hij'));
  for (const q of [`https://boese.example/hls/abc-defg-hij/${k}/index.m3u8`, `/hls/xyz-defg-hij/${k}/index.m3u8`,
    `/hls/abc-defg-hij/${k}/720p-live.m3u8`, `/hls/abc-defg-hij/../${k}/index.m3u8`, `//boese.example/hls/abc-defg-hij/${k}/index.m3u8`,
    `/hls/abc-defg-hij/${k.slice(1)}/index.m3u8`, 42, null]) {
    assert.ok(!quelleGueltig(q, 'abc-defg-hij'), String(q));
  }
  assert.ok(!quelleGueltig(`/hls/abc-defg-hij/${k}/index.m3u8`, '../x'));
});

test('Abspielweg: nativ (Safari), sonst hls.js, ohne MSE keiner', () => {
  assert.equal(abspielweg({ canPlayType: () => 'maybe' }, true), 'nativ');
  assert.equal(abspielweg({ canPlayType: () => '' }, true), 'hlsjs');
  assert.equal(abspielweg({ canPlayType: () => '' }, false), 'keiner');
});

test('Zuschauer-Kennung: zufällig, gemerkt, Speicher darf fehlen', () => {
  const m = new Map();
  const speicher = { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v) };
  const a = zuschauerId(speicher);
  assert.match(a, /^[A-Za-z0-9_-]{20}$/);
  assert.equal(zuschauerId(speicher), a);
  assert.match(zuschauerId(null), /^[A-Za-z0-9_-]{20}$/);
  assert.match(zuschauerId({ getItem: () => { throw new Error('privat'); }, setItem: () => { throw new Error('privat'); } }), /^[A-Za-z0-9_-]{20}$/);
});

test('hls.js fest gepinnt und nur als light-Build nachgeladen', () => {
  const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  assert.match(pkg.dependencies['hls.js'], /^\d+\.\d+\.\d+$/);
  const code = readFileSync(new URL('../src/hls.js', import.meta.url), 'utf8');
  assert.match(code, /import\('hls\.js\/light'\)/);
  assert.doesNotMatch(code, /https?:\/\/(cdn|unpkg)/);
});
