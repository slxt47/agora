# ADR 0002: Chat und Host-Ereignisse über den Server statt Client-zu-Client

**Status:** angenommen (23.11.2026)

## Kontext
LiveKit-Datenkanäle erlauben Nachrichten direkt zwischen Clients. Clients könnten so aber Absender fälschen,
Rechte (Chat aus) umgehen und Rollen behaupten.

## Entscheidung
Chat-Nachrichten und alle Host-Ereignisse gehen per API an die App. Sie prüft Rechte, speichert bei Bedarf und verteilt
über `send_data` (Topics `agora` und `chat`) ohne sendenden Teilnehmer. Clients akzeptieren diese Topics nur vom Server.
Rollen kommen nur aus `/zustand` und Server-Ereignissen. Nur Reaktionen und Spotlight laufen direkt (Topic `reaktion`
bzw. `spotlight`, Spotlight nur von Host/Co-Host angenommen).

## Folgen
+ fälschungssicher, Rechte serverseitig durchgesetzt, Verlauf optional speicherbar
− ein Umweg über die App (unkritisch bei Chat-Lautstärke)
