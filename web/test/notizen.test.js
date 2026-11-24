// Tests der Notizen-Hilfen (Zusammenführen, Cursor, Markdown light): npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { abschnitte, zusammenfuehren, cursorNachher, bloecke, inline } from '../src/raum/notizen-rechnen.js';
import { t } from '../src/i18n.js';

const basis = 'Agenda\n- A\n- B\n- C';

test('Abschnitte: Ersetzen, Einfügen, Löschen', () => {
  assert.deepEqual(abschnitte(['a', 'b', 'c'], ['a', 'x', 'c']), [{ von: 1, bis: 2, neu: ['x'] }]);
  assert.deepEqual(abschnitte(['a', 'c'], ['a', 'b', 'c']), [{ von: 1, bis: 1, neu: ['b'] }]);
  assert.deepEqual(abschnitte(['a', 'b', 'c'], ['a']), [{ von: 1, bis: 3, neu: [] }]);
  assert.deepEqual(abschnitte(['a'], ['a']), []);
});

test('Zusammenführen ohne Überschneidung', () => {
  const meins = 'Agenda\n- A (erledigt)\n- B\n- C';
  const deren = 'Agenda\n- A\n- B\n- C\n- D von Anna';
  assert.deepEqual(zusammenfuehren(basis, meins, deren), { ok: true, text: 'Agenda\n- A (erledigt)\n- B\n- C\n- D von Anna' });
  // beide am Anfang und am Ende
  assert.deepEqual(zusammenfuehren(basis, `Titel\n${basis}`, `${basis}\nEnde`), { ok: true, text: `Titel\n${basis}\nEnde` });
  // gleiche Änderung bei beiden
  assert.deepEqual(zusammenfuehren(basis, 'Agenda\n- A\n- B!\n- C', 'Agenda\n- A\n- B!\n- C'), { ok: true, text: 'Agenda\n- A\n- B!\n- C' });
  // nur eine Seite geändert
  assert.equal(zusammenfuehren(basis, basis, 'neu').text, 'neu');
  assert.equal(zusammenfuehren(basis, 'meins', basis).text, 'meins');
  // eine löscht Zeile, andere ändert eine andere
  assert.deepEqual(zusammenfuehren(basis, 'Agenda\n- B\n- C', 'Agenda\n- A\n- B\n- C?'), { ok: true, text: 'Agenda\n- B\n- C?' });
});

test('Zusammenführen mit Überschneidung → Konflikt', () => {
  assert.deepEqual(zusammenfuehren(basis, 'Agenda\n- A1\n- B\n- C', 'Agenda\n- A2\n- B\n- C'), { ok: false });
  assert.deepEqual(zusammenfuehren(basis, `${basis}\nX`, `${basis}\nY`), { ok: false });          // beide hängen an dieselbe Stelle an
  assert.deepEqual(zusammenfuehren(basis, 'Agenda\n- C', 'Agenda\n- A\n- B neu\n- C'), { ok: false });
});

test('Cursor bleibt an seiner Stelle', () => {
  assert.equal(cursorNachher('hallo welt', 'hallo schöne welt', 2), 2);
  assert.equal(cursorNachher('hallo welt', 'neu: hallo welt', 8), 13);
  assert.equal(cursorNachher('abc', '', 3), 0);
});

test('Markdown light: Überschriften, Listen, Aufgaben, fett und Code – als Bausteine', () => {
  assert.deepEqual(inline('ein **fettes** `x`'), [{ text: 'ein ' }, { text: 'fettes', fett: true }, { text: ' ' }, { text: 'x', code: true }]);
  const b = bloecke('# Titel\n\nText\n- eins\n- [x] zwei\n* [ ] drei\n## Unter\n<script>');
  assert.deepEqual(b.map((x) => x.art), ['h1', 'absatz', 'liste', 'h2', 'absatz']);
  assert.deepEqual(b[2].punkte.map((p) => p.erledigt), [null, true, false]);
  assert.deepEqual(b[4].teile, [{ text: '<script>' }]);         // bleibt Text, wird nie HTML
});

test('Notizen-Texte vorhanden', () => {
  for (const k of ['notizen.tab', 'notizen.konflikt', 'notizen.status.speichert', 'notizen.status.konflikt', 'notizen.export']) assert.notEqual(t(k), k, k);
});
