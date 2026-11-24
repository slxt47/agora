// Wörterbücher Deutsch/Englisch (ADR 0006): gleiche Schlüssel, gleiche Platzhalter, alle benutzten Schlüssel vorhanden,
// HTML-Seiten passen zum deutschen Wörterbuch, Sprachwahl.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { de } from '../src/texte-de.js';
import { en } from '../src/texte-en.js';
import { spracheErmitteln, woerterbuch, woerterbuchLaden } from '../src/i18n.js';

const platzhalter = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
const tags = (s) => [...String(s).matchAll(/<\/?([a-z]+)/g)].map((m) => m[1]).sort();

test('Englisch hat genau die Schlüssel von Deutsch', () => {
  const fehlt = Object.keys(de).filter((k) => !(k in en));
  const extra = Object.keys(en).filter((k) => !(k in de));
  assert.deepEqual(fehlt, [], `fehlt in texte-en.js: ${fehlt.join(', ')}`);
  assert.deepEqual(extra, [], `zu viel in texte-en.js: ${extra.join(', ')}`);
});

test('gleiche Platzhalter und gleiche HTML-Tags', () => {
  for (const k of Object.keys(de)) {
    assert.deepEqual(platzhalter(en[k]), platzhalter(de[k]), `Platzhalter in ${k}`);
    assert.deepEqual(tags(en[k]), tags(de[k]), `HTML in ${k}`);
  }
});

test('Englisch ohne deutsche Reste (Umlaute, typische Wörter)', () => {
  const verdaechtig = Object.entries(en).filter(([, v]) => /[äöüÄÖÜß„]|\b(und|oder|nicht|bitte|der|die|das|mit|für)\b/.test(v)
    // erlaubt: Werte der API (z. B. „angemeldet“ als Suchbeispiel im Admin-Protokoll)
    && !/e\.g\. angemeldet/.test(v));
  assert.deepEqual(verdaechtig, []);
});

test('alle im Code benutzten Schlüssel gibt es', () => {
  const dateien = (dir) => readdirSync(new URL(dir, import.meta.url), { recursive: true }).filter((f) => f.endsWith('.js')).map((f) => dir + f);
  const fehlt = [];
  for (const f of dateien('../src/')) {
    if (f.includes('texte-') || f.endsWith('i18n.js')) continue;
    const code = readFileSync(new URL(f, import.meta.url), 'utf8');
    for (const m of code.matchAll(/\bt\('([\w.]+)'/g)) if (!(m[1] in de)) fehlt.push(`${f}: ${m[1]}`);
  }
  assert.deepEqual(fehlt, []);
});

test('HTML-Seiten: data-t* verweisen auf deutsche Texte, die genau im HTML stehen', () => {
  const seiten = readdirSync(new URL('../', import.meta.url)).filter((f) => f.endsWith('.html'));
  assert.ok(seiten.length >= 8);
  const norm = (s) => s.replace(/\s+/g, ' ').trim().replace(/&amp;/g, '&');
  let anzahl = 0;
  for (const s of seiten) {
    const html = readFileSync(new URL(`../${s}`, import.meta.url), 'utf8');
    for (const m of html.matchAll(/<(\w+)[^>]*?\sdata-t(-html)?="([\w.]+)"[^>]*>([\s\S]*?)<\/\1>/g)) {
      anzahl++;
      assert.ok(m[3] in de, `${s}: ${m[3]} fehlt`);
      assert.equal(norm(m[4]), norm(de[m[3]]), `${s}: ${m[3]}`);
    }
    for (const m of html.matchAll(/(aria-label|placeholder|title)="([^"]*)"([^>]*?)\sdata-t-(aria|placeholder|title)="([\w.]+)"/g)) {
      assert.equal(m[2], de[m[5]], `${s}: ${m[5]}`);
    }
    // Rest-Prüfung: kein sichtbarer deutscher Text ohne data-t (grob: Text zwischen Tags mit Umlaut)
    const ohne = [...html.replace(/<(script|style|svg)[\s\S]*?<\/\1>/g, '').matchAll(/<([a-z0-9]+)(?![^>]*data-t)[^>]*>([^<]*[äöüÄÖÜß][^<]*)</g)]
      .map((x) => x[2].trim()).filter((x) => x && x !== 'Deutsch');
    assert.deepEqual(ohne, [], `${s}: ohne data-t`);
  }
  assert.ok(anzahl > 150);
});

test('Sprachwahl: URL vor gespeicherter Wahl vor Browser', () => {
  assert.equal(spracheErmitteln({ url: '?sprache=en', gespeichert: 'de', browser: ['de-DE'] }), 'en');
  assert.equal(spracheErmitteln({ gespeichert: 'en', browser: ['de-DE'] }), 'en');
  assert.equal(spracheErmitteln({ gespeichert: 'xx', browser: ['de-AT', 'en'] }), 'de');
  assert.equal(spracheErmitteln({ browser: ['fr-FR', 'en-US', 'de'] }), 'en');
  assert.equal(spracheErmitteln({ browser: ['fr-FR'] }), 'en');
  assert.equal(spracheErmitteln({ browser: [] }), 'de');
});

test('Wörterbücher werden einzeln geladen', async () => {
  assert.ok(woerterbuch.de);            // Node: Deutsch aktiv
  assert.equal(woerterbuch.en, null);   // Englisch erst bei Bedarf
  assert.equal((await woerterbuchLaden('en'))['allg.abbrechen'], 'Cancel');
});
