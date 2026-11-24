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

## Nachtrag (24.11.2026): Server-Aufzeichnung als Option
LiveKit Egress v1.15.0 läuft auf arm64 (Index mit linux/arm64). Agora bietet „Aufnahme auf dem Server“ und Livestream
zusätzlich an – hinter `AGORA_EGRESS=1` und dem Compose-Profil `egress`, weil Chrome + Encoder je laufender Aufnahme
mehrere Kerne und rund 1 GB RAM belegen (Messwerte in docs/BETRIEB.md). Die Browser-Aufnahme bleibt der Standard.
HLS für große Zuschauerzahlen ist nicht umgesetzt: es bräuchte Segment-Auslieferung über den Tunnel und einen eigenen
Player; bis 100 Personen trägt LiveKit selbst, darüber ist ein Livestream zu YouTube/Twitch die günstigere Lösung.

