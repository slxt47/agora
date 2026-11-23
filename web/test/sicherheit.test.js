// QR-Code für die Zwei-Faktor-Einrichtung und Texte (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import qrcode from 'qrcode-generator';
import { qrPfad } from '../src/qr.js';
import { woerterbuch } from '../src/i18n.js';

const URI = 'otpauth://totp/Agora:ada%40example.org?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Agora&algorithm=SHA1&digits=6&period=30';

test('QR: gültige Größe, Ruhezone, Suchmuster oben links', () => {
  const { groesse, module, d } = qrPfad(URI);
  assert.equal((module - 17) % 4, 0);            // Version v hat 17 + 4v Module
  assert.ok(module >= 41 && module <= 57, `Version 6–10 erwartet, ${module} Module`);
  assert.equal(groesse, module + 8);
  assert.match(d, /^(M\d+ \d+h\d+v1h-\d+z)+$/);
  assert.ok(d.startsWith('M4 4h7v1h-7z'));        // erste Zeile des Suchmusters: 7 dunkle Module ab Ruhezone
});

test('QR: Pfad entspricht genau den dunklen Modulen', () => {
  const qr = qrcode(0, 'M'); qr.addData(URI, 'Byte'); qr.make();
  const n = qr.getModuleCount();
  let dunkel = 0;
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (qr.isDark(y, x)) dunkel++;
  const summe = [...qrPfad(URI).d.matchAll(/h(\d+)v/g)].reduce((s, m) => s + Number(m[1]), 0);
  assert.equal(summe, dunkel);
});

test('Texte für Zwei-Faktor vorhanden', () => {
  for (const k of ['zweifaktor.einschalten', 'zweifaktor.codesText', 'zweifaktor.anmeldenText', 'zweifaktor.codesUebrig']) assert.ok(woerterbuch.de[k], k);
});
