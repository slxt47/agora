# ADR 0003: Aufzeichnung im Browser des Hosts

**Status:** angenommen (23.11.2026); Server-Aufzeichnung/Livestream (24.11.2026) und HLS-Broadcast (27.11.2026) als Optionen ergänzt

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

## Nachtrag (27.11.2026): HLS-Broadcast gebaut (optional)
Jetzt umgesetzt hinter `AGORA_EGRESS=1` **und** `AGORA_HLS=1` (beides standardmäßig aus), nur für Webinare:
- Egress RoomComposite mit `SegmentedFileOutput` (HLS, MPEG-TS, 4 s) je Stufe: 720p (Preset) und optional 360p (eigene
  Kodierung) – ein Egress kann nur eine Kodierung, also zwei Aufträge. Ausgabe in einen gemeinsamen Ordner
  (`daten/hls/<code>/<kennung>/`); die App erzeugt die Master-Playlist selbst und liefert nur feste Dateinamen aus.
- Zugang: öffentlich oder nur mit dem persönlichen Anmelde-Token des Webinars. Die Adresse enthält eine zufällige
  Kennung je Übertragung (Fähigkeits-URL), dadurch dürfen Segmente öffentlich zwischengespeichert werden
  (Cloudflare-Regel nötig, BETRIEB.md) – ein Token je Zuschauer in der URL würde jeden Zwischenspeicher aushebeln.
- Spieler: eigene Seite `/h/<code>` ohne WebRTC; Safari (und Chromium mit eingebautem HLS) spielt nativ, sonst hls.js
  (light, Apache-2.0, fest gepinnt, als eigener Chunk selbst ausgeliefert, nur bei Bedarf geladen).
- Zuschauerzahl ungefähr über Meldungen der Seite (alle 30 s, ohne IP), nicht über Segment-Abrufe (die kämen bei
  Zwischenspeicherung nie an). Nach dem Ende 1 h Nachlauf, dann gelöscht.
Weiter gilt: Für ein Publikum auf YouTube/Twitch bleibt der Livestream die günstigere Lösung; HLS lohnt sich, wenn die
Zuschauer bei Agora bleiben sollen (Anmeldung, keine Plattform-Konten, kein Tracking).

