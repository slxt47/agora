// Audio-Modi: Wahl prüfen, getUserMedia- und Veröffentlichungs-Optionen, RNNoise-Dateien selbst gehostet
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { audioPruefen, audioLesen, audioMerken, erfassung, rauschenWirksam, veroeffentlichung, MUSIK_BITRATE } from '../src/raum/audio-wahl.js';
import { BASIS, VERSION } from '../scripts/rnnoise.mjs';
import { woerterbuch } from '../src/i18n.js';

const PRESETS = { music: { maxBitrate: 32000 }, musicHighQualityStereo: { maxBitrate: MUSIK_BITRATE } };

test('Wahl prüfen: Unsinn wird Standard', () => {
  assert.deepEqual(audioPruefen(null), { musik: false, rauschen: 'browser' });
  assert.deepEqual(audioPruefen({ musik: 'ja', rauschen: 'laut' }), { musik: false, rauschen: 'browser' });
  assert.deepEqual(audioPruefen({ musik: true, rauschen: 'stark' }), { musik: true, rauschen: 'stark' });
});

test('Speicher: merken und lesen, kaputter Inhalt → Standard', () => {
  const m = new Map();
  const sp = { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v) };
  audioMerken({ musik: true, rauschen: 'aus' }, sp);
  assert.deepEqual(audioLesen(sp), { musik: true, rauschen: 'aus' });
  m.set('agora-audio', '{kaputt');
  assert.deepEqual(audioLesen(sp), { musik: false, rauschen: 'browser' });
});

test('Musik: keine Verarbeitung, stereo, 128 kbit/s, ohne DTX/RED; RNNoise im Musikmodus aus', () => {
  const w = { musik: true, rauschen: 'stark' };
  assert.deepEqual(erfassung(w), { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 2, sampleRate: 48000 });
  const v = veroeffentlichung(w, PRESETS);
  assert.equal(v.audioPreset.maxBitrate, 128000);
  assert.equal(v.dtx, false); assert.equal(v.red, false); assert.equal(v.forceStereo, true);
  assert.equal(rauschenWirksam(w), 'aus');
});

test('Sprache: Browser-Filter nur bei „browser“, RNNoise ersetzt ihn', () => {
  assert.equal(erfassung({ musik: false, rauschen: 'browser' }).noiseSuppression, true);
  assert.equal(erfassung({ musik: false, rauschen: 'stark' }).noiseSuppression, false);
  assert.equal(erfassung({ musik: false, rauschen: 'aus' }).noiseSuppression, false);
  assert.equal(erfassung({ musik: false, rauschen: 'aus' }).echoCancellation, true);
  const v = veroeffentlichung({ musik: false, rauschen: 'browser' }, PRESETS);
  assert.equal(v.dtx, true); assert.equal(v.red, true); assert.equal(v.forceStereo, false); assert.equal(v.audioPreset, PRESETS.music);
});

test('RNNoise selbst gehostet, Version fest', () => {
  assert.equal(BASIS, `/vendor/rnnoise-${VERSION}`);
  const paket = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  assert.equal(paket.dependencies['@sapphi-red/web-noise-suppressor'], VERSION);        // exakt gepinnt
  assert.match(paket.scripts.build, /mediapipe\.mjs && node scripts\/rnnoise\.mjs/);      // nach mediapipe (leert vendor/)
  const quelle = readFileSync(new URL('../src/raum/audio-modi.js', import.meta.url), 'utf8');
  assert.doesNotMatch(quelle, /https?:\/\//);
});

test('Audio-Texte vorhanden', () => {
  for (const k of ['audio.musik', 'audio.rauschenStark', 'audio.rnnoiseFehler', 'audio.knopf']) assert.ok(woerterbuch.de[k], k);
});
