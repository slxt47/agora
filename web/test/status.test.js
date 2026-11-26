// Status-Seite: Prozent, Balkenstufen, Antwortzeit, Texte (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { prozentText, balkenStufe, msText } from '../src/status-format.js';
import { de } from '../src/texte-de.js';
import { en } from '../src/texte-en.js';

test('Prozent nie aufgerundet', () => {
  assert.equal(prozentText(100, 'en-GB'), '100 %');
  assert.equal(prozentText(99.999, 'en-GB'), '99.99 %');
  assert.equal(prozentText(99.5, 'de-AT'), '99,5 %');
  assert.equal(prozentText(null), '–');
});

test('Balkenstufen', () => {
  assert.equal(balkenStufe(null), 'leer');
  assert.equal(balkenStufe(100), 'ok');
  assert.equal(balkenStufe(99.9), 'ok');
  assert.equal(balkenStufe(97), 'gestoert');
  assert.equal(balkenStufe(10), 'ausfall');
});

test('Antwortzeit', () => {
  assert.equal(msText(12.4, 'en-GB'), '12 ms');
  assert.equal(msText(0.3, 'en-GB'), '1 ms');
  assert.equal(msText(1500, 'en-GB'), '1.5 s');
  assert.equal(msText(null), '–');
});

test('Texte für alle Zustände', () => {
  for (const k of ['gesamt.ok', 'gesamt.eingeschraenkt', 'gesamt.stoerung', 'gesamt.wartung', 'gesamt.unbekannt', 'dienst.app', 'dienst.medien',
    'dienst.mail', 'zustand.ok', 'zustand.gestoert', 'zustand.ausfall', 'zustand.unbekannt', 'mz.untersuchung', 'mz.erkannt', 'mz.beobachtung',
    'mz.behoben', 'mz.geplant', 'mz.laeuft', 'mz.abgeschlossen', 'mz.aktiv', 'art.stoerung', 'art.wartung', 'art.hinweis', 'achse.24h', 'achse.7d', 'achse.90d']) {
    assert.ok(de[`status.${k}`] && en[`status.${k}`], k);
  }
});
