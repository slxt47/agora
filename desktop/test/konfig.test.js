// Prüft Einstellungen, Kürzel-Validierung, Deep Links und Navigations-Erlaubnis der Desktop-App (ohne Electron).
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const k = require('../src/konfig');

test('Adresse: https oder lokales http, sonst nichts', () => {
  assert.equal(k.adressePruefen('https://agora.slxt.dev/'), 'https://agora.slxt.dev');
  assert.equal(k.adressePruefen('http://127.0.0.1:8221/r/abc-defg-hij'), 'http://127.0.0.1:8221');
  assert.equal(k.adressePruefen('http://agora.slxt.dev'), null);
  assert.equal(k.adressePruefen('https://nutzer:pw@agora.slxt.dev'), null);
  assert.equal(k.adressePruefen('file:///etc/passwd'), null);
  assert.equal(k.adressePruefen('javascript:alert(1)'), null);
  assert.equal(k.adressePruefen('kein url'), null);
});

test('Kürzel: Modifikator Pflicht, genau eine Taste', () => {
  for (const ok of ['CommandOrControl+Shift+M', 'Alt+F9', 'Ctrl+Alt+Space', 'Super+Shift+1']) assert.ok(k.kuerzelPruefen(ok), ok);
  for (const nein of ['M', 'Shift', 'Ctrl+Ctrl+M', 'Ctrl+MM', 'Ctrl+M+N', 'Foo+M', '', 42, null, 'Ctrl+' + 'A'.repeat(70)]) assert.ok(!k.kuerzelPruefen(nein), String(nein));
});

test('Einstellungen: Vorgaben, Datei, Umgebung; ungültiges fällt auf Vorgabe zurück', () => {
  const ordner = fs.mkdtempSync(path.join(os.tmpdir(), 'agora-desktop-'));
  try {
    let c = k.konfigLaden(ordner, {});
    assert.equal(c.herkunft, 'https://agora.slxt.dev');
    assert.deepEqual(c.kuerzel, k.VORGABE_KUERZEL);
    fs.writeFileSync(path.join(ordner, 'einstellungen.json'), JSON.stringify({
      adresse: 'https://meet.example.org', kuerzel: { mikro: 'Alt+F9', kamera: 'M', hand: '' },
    }));
    c = k.konfigLaden(ordner, {});
    assert.equal(c.herkunft, 'https://meet.example.org');
    assert.equal(c.kuerzel.mikro, 'Alt+F9');
    assert.equal(c.kuerzel.kamera, k.VORGABE_KUERZEL.kamera);      // „M“ allein ist systemweit verboten
    assert.equal(c.kuerzel.hand, '');                              // abgeschaltet
    assert.equal(c.kuerzelEnde, k.VORGABE_KUERZEL_ENDE);           // Fernsteuerung beenden: Vorgabe
    fs.writeFileSync(path.join(ordner, 'einstellungen.json'), JSON.stringify({ kuerzel: { fernsteuerungEnde: 'X' } }));
    assert.equal(k.konfigLaden(ordner, {}).kuerzelEnde, k.VORGABE_KUERZEL_ENDE);   // ohne Modifikator verboten
    fs.writeFileSync(path.join(ordner, 'einstellungen.json'), JSON.stringify({ kuerzel: { fernsteuerungEnde: 'Alt+F12' } }));
    assert.equal(k.konfigLaden(ordner, {}).kuerzelEnde, 'Alt+F12');
    fs.writeFileSync(path.join(ordner, 'einstellungen.json'), JSON.stringify({
      adresse: 'https://meet.example.org', kuerzel: { mikro: 'Alt+F9', kamera: 'M', hand: '' },
    }));
    c = k.konfigLaden(ordner, { AGORA_ADRESSE: 'http://127.0.0.1:8221/' });
    assert.equal(c.herkunft, 'http://127.0.0.1:8221');
    c = k.konfigLaden(ordner, { AGORA_ADRESSE: 'http://boese.example' });
    assert.equal(c.herkunft, 'https://agora.slxt.dev');
    fs.writeFileSync(path.join(ordner, 'einstellungen.json'), '{kaputt');
    assert.equal(k.konfigLaden(ordner, {}).herkunft, 'https://agora.slxt.dev');
  } finally { fs.rmSync(ordner, { recursive: true, force: true }); }
});

test('Deep Links: agora://r/<code>, Schlüssel nur im Fragment', () => {
  const s = 'A'.repeat(43);
  assert.deepEqual(k.codeAusDeepLink('agora://r/abc-defg-hij'), { code: 'abc-defg-hij', schluessel: '' });
  assert.deepEqual(k.codeAusDeepLink('agora://ABC-DEFG-HIJ/'), { code: 'abc-defg-hij', schluessel: '' });
  assert.deepEqual(k.codeAusDeepLink(`agora://r/abc-defg-hij#k=${s}`), { code: 'abc-defg-hij', schluessel: s });
  assert.deepEqual(k.codeAusDeepLink('agora://r/abc-defg-hij#k=kurz'), { code: 'abc-defg-hij', schluessel: '' });
  for (const nein of ['agora://r/../../etc', 'agora://r/abc-defg-hij/../x', 'https://agora.slxt.dev/r/abc-defg-hij', 'agora://r/abcdefghij', 'agora://evil.example/abc-defg-hij', 42])
    assert.equal(k.codeAusDeepLink(nein), null, String(nein));
  assert.equal(k.raumAdresse('https://agora.slxt.dev/', 'abc-defg-hij'), 'https://agora.slxt.dev/r/abc-defg-hij');
  assert.equal(k.raumAdresse('http://127.0.0.1:8221/x', 'abc-defg-hij', s), `http://127.0.0.1:8221/r/abc-defg-hij#k=${s}`);
});

test('Navigation nur innerhalb der Agora-Herkunft', () => {
  const h = 'https://agora.slxt.dev';
  assert.ok(k.erlaubteNavigation('https://agora.slxt.dev/konto', h));
  assert.ok(!k.erlaubteNavigation('https://agora.slxt.dev.evil.example/', h));
  assert.ok(!k.erlaubteNavigation('http://agora.slxt.dev/', h));
  assert.ok(!k.erlaubteNavigation('https://slxt.org/impressum', h));
  assert.ok(!k.erlaubteNavigation('file:///etc/passwd', h));
  assert.ok(!k.erlaubteNavigation('nichts', h));
});
