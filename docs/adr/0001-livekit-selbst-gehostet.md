# ADR 0001: Eigener LiveKit-Server als SFU

**Status:** angenommen (23.11.2026)

## Kontext
Video und Ton laufen über WebRTC (UDP) und passen nicht durch den Cloudflare-Tunnel, über den alle anderen Dienste
laufen. Zur Wahl standen: Cloudflare Realtime (verwaltete SFU + TURN, keine offenen Ports), ein eigener LiveKit-Server
(offene Medienports) und P2P-Mesh mit TURN (nur für kleine Runden).

## Entscheidung
Eigener LiveKit-Server (v1.13) im Host-Netz. Die Signalisierung läuft nur auf 127.0.0.1 und wird über den Tunnel als
`/rtc` veröffentlicht. Für die Medien sind UDP 7882 (ein Port für alle) und ICE-TCP 7881 offen, gebunden nur an die
öffentliche Schnittstelle.

## Folgen
+ volle Kontrolle, keine Kosten je Minute, Medien verlassen den eigenen Server nicht
+ Simulcast, Dynacast und Server-API (stummschalten, entfernen, Rechte) direkt verfügbar
− die öffentliche IP des Servers ist für Teilnehmende sichtbar; zwei Ports sind offen
− ohne TURN/TLS auf 443 scheitern sehr strenge Firmennetze (siehe offene Punkte)
