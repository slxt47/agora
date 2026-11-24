# ADR 0004: Gemeinsame Notizen mit Versionsprüfung statt OT/CRDT

**Status:** angenommen (24.11.2026)

## Kontext
Im Meeting sollen alle an einem gemeinsamen Text mitschreiben können. Echtes gleichzeitiges Editieren (Operational
Transformation oder CRDT wie Yjs) braucht eine eigene Synchronisation je Tastendruck und viel Code bzw. eine größere
Bibliothek. Zur Wahl standen außerdem Sperren je Absatz (wer tippt, sperrt den Absatz) und „letzter Schreiber gewinnt“.

## Entscheidung
Ein Text je Meeting mit Versionsnummer (optimistische Sperre). Der Client speichert 0,7 s nach der letzten Eingabe und
schickt die Version mit, auf der er beruht. Ist sie veraltet, antwortet der Server mit 409 und dem aktuellen Stand. Der
Client führt die eigene und die neue Fassung zeilenweise gegen die gemeinsame Basis zusammen (3-Wege, wie diff3, in
`web/src/raum/notizen-rechnen.js`). Überschneiden sich die Änderungen, zeigt er einen Konflikt-Hinweis: „Meine Fassung
behalten“ oder „Neue Fassung übernehmen“. Änderungen anderer kommen als Ereignis (mit Text, wenn klein) und werden
genauso eingemischt.

## Folgen
+ wenig Code, Server bleibt einfach (eine Zeile je Meeting), nichts geht unbemerkt verloren
+ typischer Fall (eine Person schreibt Protokoll, andere ergänzen eigene Zeilen) läuft ohne Rückfrage
− tippen zwei Personen gleichzeitig in derselben Zeile, muss eine wählen
− Grenze 100 000 Zeichen; große Texte gehen nicht über den Datenkanal (Clients holen per GET)
