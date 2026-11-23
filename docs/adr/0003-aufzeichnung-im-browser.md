# ADR 0003: Aufzeichnung im Browser des Hosts

**Status:** angenommen (23.11.2026), Server-Aufzeichnung als spätere Erweiterung

## Kontext
Server-Aufzeichnung (LiveKit Egress) braucht einen eigenen Chrome-Container mit Redis und viel RAM.

## Entscheidung
Der Host zeichnet die sichtbare Bühne als Canvas plus gemischtes Audio per MediaRecorder auf und lädt sie in die
Bibliothek hoch. Allen zeigt ein Hinweis (Raum-Metadaten), dass aufgezeichnet wird.

## Folgen
+ keine zusätzlichen Dienste, keine Serverlast
− der Tab muss sichtbar bleiben; Qualität hängt vom Gerät des Hosts ab
