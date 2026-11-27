// Fernsteuerung (ohne Electron): Freigabe-Prüfung, Ereignis-Format, Koordinaten, Kürzel-Sperre, Grenzen, xdotool-Adapter.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const F = require('../src/fernsteuerung');
const { xdotoolAdapter } = require('../src/eingabe');

const KENNUNG = 'Kk_' + 'a'.repeat(29);
const zustand = (aenderung = {}) => ({
  freigabe: { aktiv: true, art: 'bildschirm', bildschirm: { bounds: { x: 0, y: 0, width: 1920, height: 1080 }, scaleFactor: 1 } },
  zustimmung: { identity: 'g-steuer', kennung: KENNUNG }, freigabeServer: { von: 'u1-ich', an: 'g-steuer', id: KENNUNG },
  ich: 'u1-ich', e2ee: false, ...aenderung,
});
const bytes = (o) => new TextEncoder().encode(JSON.stringify(o));

test('darfSteuern: alle Regeln einzeln', () => {
  assert.deepEqual(F.darfSteuern(zustand(), 'g-steuer', false), { ok: true });
  const faelle = [
    [{ freigabe: null }, 'keine-freigabe'],
    [{ freigabe: { aktiv: true, art: 'fenster' } }, 'kein-bildschirm'],
    [{ zustimmung: null }, 'keine-zustimmung'],
    [{ freigabeServer: null }, 'keine-server-freigabe'],
    [{ freigabeServer: { von: 'u2-andere', an: 'g-steuer', id: KENNUNG } }, 'fremde-freigabe'],
    [{ freigabeServer: { von: 'u1-ich', an: 'g-steuer', id: 'b'.repeat(30) } }, 'freigabe-passt-nicht'],
    [{ freigabeServer: { von: 'u1-ich', an: 'g-boese', id: KENNUNG } }, 'freigabe-passt-nicht'],
    [{ e2ee: true }, 'unverschluesselt'],
  ];
  for (const [a, grund] of faelle) assert.equal(F.darfSteuern(zustand(a), 'g-steuer', false).grund, grund, grund);
  assert.equal(F.darfSteuern(zustand(), 'g-boese', false).grund, 'falscher-absender');
  assert.equal(F.darfSteuern(zustand(), undefined, false).grund, 'falscher-absender');
  assert.deepEqual(F.darfSteuern(zustand({ e2ee: true }), 'g-steuer', true), { ok: true });
});

test('Freigabe nur aus Server-Metadaten für mich', () => {
  const md = JSON.stringify({ fernsteuerung: [{ von: 'u2-x', an: 'g-a', id: KENNUNG }, { von: 'u1-ich', an: 'g-steuer', id: KENNUNG }] });
  assert.deepEqual(F.freigabeAusMetadaten(md, 'u1-ich'), { von: 'u1-ich', an: 'g-steuer', id: KENNUNG });
  assert.equal(F.freigabeAusMetadaten(md, 'u3-nein'), null);
  assert.equal(F.freigabeAusMetadaten('{kaputt', 'u1-ich'), null);
  assert.equal(F.freigabeAusMetadaten({ fernsteuerung: [{ von: 'u1-ich', an: 'g a', id: KENNUNG }] }, 'u1-ich'), null);
  assert.equal(F.freigabeAusMetadaten({ fernsteuerung: [{ von: 'u1-ich', an: 'g-a', id: 'kurz' }] }, 'u1-ich'), null);
});

test('Ereignisse: Format, Bereiche, Tasten, Größe', () => {
  assert.deepEqual(F.ereignisLesen(bytes({ t: 'bewegen', x: 0.5, y: 1 })), { t: 'bewegen', x: 0.5, y: 1 });
  assert.deepEqual(F.ereignisLesen(bytes({ t: 'klick', x: 0, y: 0, knopf: 'right', doppelt: true })), { t: 'klick', x: 0, y: 0, knopf: 'right', doppelt: true, mod: [] });
  assert.deepEqual(F.ereignisLesen(bytes({ t: 'taste', taste: 'Enter', mod: ['shift'] })), { t: 'taste', taste: 'enter', mod: ['shift'] });
  assert.deepEqual(F.ereignisLesen(bytes({ t: 'taste', taste: 'ä' })), { t: 'text', zeichen: 'ä' });
  assert.deepEqual(F.ereignisLesen(bytes({ t: 'taste', taste: 'C', mod: ['control'] })), { t: 'taste', taste: 'c', mod: ['control'] });
  for (const kaputt of [{ t: 'bewegen', x: 1.01, y: 0 }, { t: 'bewegen', x: -0.1, y: 0 }, { t: 'bewegen', x: NaN, y: 0 }, { t: 'bewegen', x: '0.5', y: 0 },
    { t: 'klick', x: 0, y: 0, knopf: 'vierter' }, { t: 'rollen', dx: 0, dy: 21 }, { t: 'rollen', dx: 0.5, dy: 0 },
    { t: 'taste', taste: 'a', mod: ['meta'] }, { t: 'taste', taste: 'a', mod: ['shift', 'shift'] }, { t: 'taste', taste: 'Meta' },
    { t: 'taste', taste: '\u0007' }, { t: 'taste', taste: 'ab' }, { t: 'taste', taste: 'ä', mod: ['control'] }, { t: 'exec', cmd: 'rm -rf /' }, [], null]) {
    assert.equal(F.ereignisLesen(bytes(kaputt)), null, JSON.stringify(kaputt));
  }
  assert.equal(F.ereignisLesen(new Uint8Array(600)), null);
  assert.equal(F.ereignisLesen(new TextEncoder().encode('kein json')), null);
});

test('Koordinaten: Bildschirm-Lage, Skalierung je Plattform, Ränder', () => {
  const zweiter = { bounds: { x: 1920, y: -200, width: 1280, height: 720 }, scaleFactor: 2 };
  assert.deepEqual(F.abbilden(0, 0, zweiter, 'linux'), { x: 3840, y: -400 });
  assert.deepEqual(F.abbilden(0.5, 0.5, zweiter, 'win32'), { x: 5120, y: 320 });
  assert.deepEqual(F.abbilden(0.5, 0.5, zweiter, 'darwin'), { x: 2560, y: 160 });
  assert.deepEqual(F.abbilden(1, 1, { bounds: { x: 0, y: 0, width: 1920, height: 1080 }, scaleFactor: 1 }, 'linux'), { x: 1919, y: 1079 });
  assert.ok(F.inRechteck({ x: 10, y: 10 }, [{ x: 0, y: 0, width: 100, height: 100 }]));
  assert.ok(!F.inRechteck({ x: 250, y: 10 }, [{ x: 0, y: 0, width: 100, height: 100 }], 2));
  assert.ok(F.inRechteck({ x: 150, y: 10 }, [{ x: 0, y: 0, width: 100, height: 100 }], 2));
});

test('Quelle → Bildschirm; Fenster ergeben keinen', () => {
  const anz = [{ id: 11, bounds: { x: 0, y: 0, width: 10, height: 10 } }, { id: 22, bounds: { x: 10, y: 0, width: 10, height: 10 } }];
  assert.equal(F.bildschirmZurQuelle({ id: 'screen:1:0', display_id: '22' }, anz).id, 22);
  assert.equal(F.bildschirmZurQuelle({ id: 'screen:0:0', display_id: '' }, anz).id, 11);
  assert.equal(F.bildschirmZurQuelle({ id: 'window:123:0', display_id: '' }, anz), null);
  assert.equal(F.bildschirmZurQuelle(null, anz), null);
});

test('eigene systemweite Kürzel werden erkannt', () => {
  const k = ['CommandOrControl+Shift+M', 'CommandOrControl+Alt+X', ''];
  assert.ok(F.kuerzelTreffer({ t: 'taste', taste: 'm', mod: ['shift', 'control'] }, k));
  assert.ok(F.kuerzelTreffer({ t: 'taste', taste: 'x', mod: ['control', 'alt'] }, k));
  assert.ok(!F.kuerzelTreffer({ t: 'taste', taste: 'm', mod: ['control'] }, k));
  assert.ok(!F.kuerzelTreffer({ t: 'text', zeichen: 'm' }, k));
});

test('Begrenzer: Stoß, Nachfüllen, Missbrauch', () => {
  let t = 0;
  const b = new F.Begrenzer(() => t);
  let ok = 0;
  for (let i = 0; i < 1000; i++) if (b.ok({ t: 'bewegen' })) ok++;
  assert.equal(ok, F.GRENZEN.stoss);
  assert.ok(b.missbrauch());
  t = 1000;
  assert.ok(b.ok({ t: 'bewegen' }));
  let tasten = 0;
  for (let i = 0; i < 200; i++) if (b.ok({ t: 'taste' })) tasten++;
  assert.equal(tasten, F.GRENZEN.tastenStoss);
});

test('xdotool-Adapter: feste Argumente ohne Shell', async () => {
  const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
  const ordner = fs.mkdtempSync(path.join(os.tmpdir(), 'agora-xdo-'));
  const log = path.join(ordner, 'log');
  const skript = path.join(ordner, 'xdotool');
  fs.writeFileSync(skript, `#!/bin/sh\nprintf '%s|' "$@" >> ${log}\necho >> ${log}\n`, { mode: 0o755 });
  const a = xdotoolAdapter(skript);
  a.bewegen(10, 20); a.knopf('left', true); a.knopf('left', false); a.rollen(0, -2); a.taste('enter', ['control']); a.text('$(reboot)');
  await a.warten();
  const zeilen = fs.readFileSync(log, 'utf8').trim().split('\n');
  assert.deepEqual(zeilen, ['mousemove|--|10|20|', 'mousedown|1|', 'mouseup|1|', 'click|4|4|', 'key|--clearmodifiers|ctrl+Return|', 'type|--clearmodifiers|--|$(reboot)|']);
  fs.rmSync(ordner, { recursive: true });
});
