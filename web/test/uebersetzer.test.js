// Live-Übersetzung der Untertitel: Adapter für die Translator-API des Browsers, mit Attrappen (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Uebersetzer, translatorApi, ZIELSPRACHEN } from '../src/raum/uebersetzer.js';

function attrappe({ verfuegbar = 'available' } = {}) {
  const log = [];
  const Translator = {
    availability: async ({ sourceLanguage }) => (sourceLanguage === 'xx' ? 'unavailable' : verfuegbar),
    create: async (opts) => {
      log.push(['neu', opts.sourceLanguage, opts.targetLanguage]);
      if (opts.monitor) { const ziel = new EventTarget(); opts.monitor(ziel); const e = new Event('downloadprogress'); e.loaded = 0.5; ziel.dispatchEvent(e); }
      return { translate: async (t) => `[${opts.targetLanguage}] ${t}`, destroy: () => log.push(['weg']) };
    },
  };
  return { g: { Translator }, log };
}

test('ohne API: nicht verfügbar, status nein, nie ein fremder Dienst', async () => {
  const u = new Uebersetzer({ global: {} });
  assert.equal(u.verfuegbar, false);
  assert.equal(translatorApi({}), null);
  assert.equal(await u.status('de', 'en'), 'nein');
  await assert.rejects(u.uebersetzen('Hallo', 'de', 'en'));
  assert.equal(await u.uebersetzen('Hallo', 'de', 'de'), 'Hallo');       // gleiche Sprache: nichts zu tun
});

test('neue API (Translator): Status, Übersetzen, ein Übersetzer je Paar, Fortschritt, schliessen', async () => {
  const { g, log } = attrappe({ verfuegbar: 'downloadable' });
  const fortschritt = [];
  const u = new Uebersetzer({ global: g, fortschritt: (x) => fortschritt.push(x) });
  assert.equal(u.verfuegbar, true);
  assert.equal(await u.status('de', 'en'), 'laden');
  assert.equal(await u.status('xx', 'en'), 'nein');
  assert.equal(await u.status('en', 'en'), 'ja');
  assert.equal(await u.uebersetzen('Hallo zusammen', 'de', 'en'), '[en] Hallo zusammen');
  assert.equal(await u.uebersetzen('Noch was', 'de', 'en'), '[en] Noch was');
  assert.equal(await u.uebersetzen('Hi', 'en', 'de'), '[de] Hi');
  assert.equal(log.filter((x) => x[0] === 'neu').length, 2, 'je Sprachpaar nur einmal erzeugt');
  assert.deepEqual(fortschritt, [0.5, 0.5]);
  assert.equal(await u.uebersetzen('', 'de', 'en'), '');
  assert.equal(await u.uebersetzen('ohne Quelle', '', 'en'), 'ohne Quelle');
  u.schliessen();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(log.filter((x) => x[0] === 'weg').length, 2);
});

test('Fehler beim Erzeugen wird nicht gecacht', async () => {
  let n = 0;
  const g = { Translator: { availability: async () => 'available', create: async () => { n++; if (n === 1) throw new Error('kein Modell'); return { translate: async (t) => t.toUpperCase() }; } } };
  const u = new Uebersetzer({ global: g });
  await assert.rejects(u.uebersetzen('a', 'de', 'en'));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(await u.uebersetzen('a', 'de', 'en'), 'A');
});

test('ältere API (self.translation)', async () => {
  const g = { translation: {
    canTranslate: async ({ targetLanguage }) => (targetLanguage === 'fr' ? 'after-download' : targetLanguage === 'xx' ? 'no' : 'readily'),
    createTranslator: async ({ targetLanguage }) => ({ translate: async (t) => `${t} (${targetLanguage})` }),
  } };
  const u = new Uebersetzer({ global: g });
  assert.equal(translatorApi(g).art, 'alt');
  assert.equal(await u.status('de', 'en'), 'ja');
  assert.equal(await u.status('de', 'fr'), 'laden');
  assert.equal(await u.status('de', 'xx'), 'nein');
  assert.equal(await u.uebersetzen('Guten Tag', 'de', 'en'), 'Guten Tag (en)');
});

test('Zielsprachen: eindeutige ISO-Codes', () => {
  assert.equal(new Set(ZIELSPRACHEN).size, ZIELSPRACHEN.length);
  assert.ok(ZIELSPRACHEN.every((s) => /^[a-z]{2}$/.test(s)));
});
