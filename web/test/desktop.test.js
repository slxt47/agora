// Desktop-Brücke: Kürzel der Electron-App → Tasten der Meeting-Handler (npm test)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { desktopKuerzel } from '../src/raum/desktop.js';

test('ohne Desktop-App: nichts, Abmelden geht trotzdem', () => {
  const ab = desktopKuerzel(() => assert.fail('darf nicht laufen'), undefined);
  assert.equal(typeof ab, 'function');
  ab();
  desktopKuerzel(() => {}, { beiKuerzel: 'kein' })();
});

test('mikro/kamera/hand → m/v/h, alles andere wird ignoriert', () => {
  let fn = null;
  const bruecke = { beiKuerzel: (f) => { fn = f; return () => { fn = null; }; } };
  const tasten = [];
  const ab = desktopKuerzel((t) => tasten.push(t), bruecke);
  for (const a of ['mikro', 'kamera', 'hand', 'verlassen', '__proto__', 'toString', 42, null]) fn(a);
  assert.deepEqual(tasten, ['m', 'v', 'h']);
  ab();
  assert.equal(fn, null);
});
