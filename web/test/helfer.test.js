// Tests der reinen Hilfsfunktionen: npm test  (node:test, keine Abhängigkeiten)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  codeAusText, zeitText, uhrzeitText, minutenText, laufzeitText, wiederholungText, lokalZuUtc, utcZuLokal, zonenVersatz,
  linkTeile, linkify, escapeHtml, sichererLink, initialen, farbeAusText, groesseText, galerieRaster, seite, kachelnProSeite,
  jsonLesen, emailsAusText, seitText, passcodeVorschlag,
} from '../src/helfer.js';
import { t, woerterbuch } from '../src/i18n.js';

test('Code aus nacktem Code, Großschrift, Leerzeichen und ohne Striche', () => {
  assert.equal(codeAusText('abc-defg-hij'), 'abc-defg-hij');
  assert.equal(codeAusText('  ABC-DEFG-HIJ  '), 'abc-defg-hij');
  assert.equal(codeAusText('abcdefghij'), 'abc-defg-hij');
  assert.equal(codeAusText('abc defg hij'), 'abc-defg-hij');
});

test('Code aus eingefügten Links', () => {
  assert.equal(codeAusText('https://agora.slxt.dev/r/kmn-pqrs-tuv'), 'kmn-pqrs-tuv');
  assert.equal(codeAusText('https://agora.slxt.dev/r/kmn-pqrs-tuv?pwd=1#x'), 'kmn-pqrs-tuv');
  assert.equal(codeAusText('agora.slxt.dev/r/kmn-pqrs-tuv/'), 'kmn-pqrs-tuv');
  assert.equal(codeAusText('Komm rein: https://agora.slxt.dev/r/kmn-pqrs-tuv bis gleich'), 'kmn-pqrs-tuv');
  assert.equal(codeAusText('Einladung http://x.y/z?code=abc-defg-hij.'), 'abc-defg-hij');
});

test('Ungültige Codes ergeben null', () => {
  for (const x of ['', '   ', 'abc', 'abc-defg-hi', 'abc-defg-hijk', '123-4567-890', 'https://agora.slxt.dev/', null, undefined, 42]) {
    assert.equal(codeAusText(x), null, String(x));
  }
});

test('Zeitformat in Zeitzone', () => {
  const iso = '2026-11-24T13:00:00Z';
  assert.match(zeitText(iso, 'Europe/Vienna'), /24\. Nov\. 2026.*14:00/);
  assert.match(zeitText(iso, 'America/New_York'), /08:00/);
  assert.equal(zeitText('kaputt'), '');
  assert.equal(zeitText(iso, 'Keine/Zone').includes('13:00'), true);   // unbekannte Zone → UTC
  assert.equal(uhrzeitText('2026-11-24T13:05:00Z', 'Europe/Vienna'), '14:05');
});

test('Dauer- und Laufzeit-Texte', () => {
  assert.equal(minutenText(45), '45 Min.');
  assert.equal(minutenText(60), '1 Std.');
  assert.equal(minutenText(90), '1 Std. 30 Min.');
  assert.equal(laufzeitText(5), '0:05');
  assert.equal(laufzeitText(247), '4:07');
  assert.equal(laufzeitText(3847), '1:04:07');
  assert.equal(seitText(new Date(Date.now() - 10_000).toISOString()), 'gerade eben');
  assert.equal(seitText(new Date(Date.now() - 180_000).toISOString()), 'vor 3 Min.');
});

test('Wiederholung lesbar', () => {
  assert.equal(wiederholungText(null), 'Einmalig');
  assert.equal(wiederholungText({ wiederholung: 'keine' }), 'Einmalig');
  assert.equal(wiederholungText({ wiederholung: 'taeglich', zeitzone: 'Europe/Vienna' }), 'Täglich');
  // 24.11.2026 ist ein Dienstag
  assert.equal(wiederholungText({ wiederholung: 'woechentlich', start: '2026-11-24T13:00:00Z', zeitzone: 'Europe/Vienna' }), 'Wöchentlich (dienstags)');
  assert.equal(wiederholungText({ wiederholung: 'taeglich', zeitzone: 'Europe/Vienna', bis: '2026-12-31T22:00:00Z' }), 'Täglich bis 31.12.2026');
  // Wochentag hängt an der Zeitzone: 23:30 UTC Montag = Dienstag in Wien
  assert.equal(wiederholungText({ wiederholung: 'woechentlich', start: '2026-11-23T23:30:00Z', zeitzone: 'Europe/Vienna' }), 'Wöchentlich (dienstags)');
});

test('Wanduhr ↔ UTC inklusive Sommerzeit', () => {
  assert.equal(lokalZuUtc('2026-11-24', '14:00', 'Europe/Vienna'), '2026-11-24T13:00:00.000Z');
  assert.equal(lokalZuUtc('2026-07-01', '14:00', 'Europe/Vienna'), '2026-07-01T12:00:00.000Z');
  assert.equal(lokalZuUtc('2026-11-24', '09:30', 'America/New_York'), '2026-11-24T14:30:00.000Z');
  assert.equal(lokalZuUtc('2026-03-29', '04:00', 'Europe/Vienna'), '2026-03-29T02:00:00.000Z');
  assert.equal(lokalZuUtc('kaputt', '14:00', 'UTC'), null);
  assert.deepEqual(utcZuLokal('2026-11-24T13:00:00Z', 'Europe/Vienna'), { datum: '2026-11-24', zeit: '14:00' });
  assert.deepEqual(utcZuLokal('2026-11-24T02:00:00Z', 'America/Los_Angeles'), { datum: '2026-11-23', zeit: '18:00' });
  assert.equal(zonenVersatz(Date.UTC(2026, 0, 1), 'Asia/Kolkata'), 330);
});

test('Links erkennen und sicher ausgeben', () => {
  assert.deepEqual(linkTeile('Siehe https://slxt.org/x, danke.'), [
    { text: 'Siehe ' }, { link: 'https://slxt.org/x', text: 'https://slxt.org/x' }, { text: ', danke.' },
  ]);
  assert.deepEqual(linkTeile('www.example.com!'), [{ link: 'https://www.example.com/', text: 'www.example.com' }, { text: '!' }]);
  assert.deepEqual(linkTeile('(https://de.wikipedia.org/wiki/A_(B))'), [
    { text: '(' }, { link: 'https://de.wikipedia.org/wiki/A_(B)', text: 'https://de.wikipedia.org/wiki/A_(B)' }, { text: ')' },
  ]);
  assert.deepEqual(linkTeile('javascript:alert(1)'), [{ text: 'javascript:alert(1)' }]);
  assert.equal(linkify('<b>hi</b> https://a.de/?q="x"'), '&lt;b&gt;hi&lt;/b&gt; <a href="https://a.de/?q=" target="_blank" rel="noopener noreferrer">https://a.de/?q=</a>&quot;x&quot;');
  assert.equal(escapeHtml(`<a href='x'>&`), '&lt;a href=&#39;x&#39;&gt;&amp;');
  assert.equal(sichererLink('javascript:alert(1)'), null);
  assert.equal(sichererLink('data:text/html,x'), null);
  assert.equal(sichererLink('https://x.de'), 'https://x.de/');
});

test('Initialen, Farben, Größen', () => {
  assert.equal(initialen('Anna Maria Berger'), 'AB');
  assert.equal(initialen('florian'), 'F');
  assert.equal(initialen('  '), '?');
  assert.equal(initialen('Ümit Özdemir'), 'ÜÖ');
  assert.equal(farbeAusText('x'), farbeAusText('x'));
  assert.match(farbeAusText('Gast'), /^#[0-9a-f]{6}$/);
  assert.equal(groesseText(512), '512 B');
  assert.equal(groesseText(1536), '1,5 KB');
  assert.equal(groesseText(5 * 1024 ** 3), '5,00 GB');
});

test('Galerie-Raster und Seiten', () => {
  assert.deepEqual(galerieRaster(1, 1600, 900, 16 / 9, 0), { spalten: 1, zeilen: 1, breite: 1600, hoehe: 900 });
  assert.equal(galerieRaster(4, 1600, 900).spalten, 2);
  assert.equal(galerieRaster(3, 400, 900).spalten, 1);      // hochkant: untereinander
  assert.equal(galerieRaster(9, 1600, 900).spalten, 3);
  const s = seite([1, 2, 3, 4, 5, 6, 7], 3, 5);
  assert.deepEqual(s, { eintraege: [7], seite: 2, seiten: 3 });
  assert.equal(seite([], 6, 0).seiten, 1);
  assert.equal(kachelnProSeite(390), 6);
  assert.equal(kachelnProSeite(1440), 16);
});

test('JSON, E-Mails, Passcode', () => {
  assert.deepEqual(jsonLesen('{"a":1}'), { a: 1 });
  assert.equal(jsonLesen('kaputt', 'x'), 'x');
  assert.deepEqual(jsonLesen(new TextEncoder().encode('{"emoji":"👍"}')), { emoji: '👍' });
  const e = emailsAusText('a@b.de, A@B.de; foo\nc@d.at');
  assert.deepEqual(e.gueltig, ['a@b.de', 'c@d.at']);
  assert.deepEqual(e.ungueltig, ['foo']);
  assert.match(passcodeVorschlag(), /^\d{6}$/);
});

test('Übersetzung mit Platzhaltern und Rückfall', () => {
  assert.equal(t('aufnahme.von', { name: 'Florian' }), woerterbuch.de['aufnahme.von'].replace('{name}', 'Florian'));
  assert.equal(t('gibt.es.nicht'), 'gibt.es.nicht');
});
