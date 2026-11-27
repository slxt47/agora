# Lasttest und Netzsimulation

Stand: 23.11.2026 · Skript: `scripts/lasttest.sh` · immer gegen eine Wegwerf-Instanz, nie gegen Produktion.

## Aufbau

| Teil | Was |
|---|---|
| Server | aarch64, 8 Kerne, 15 GB RAM (davon beim Test ~4 GB frei), dieselbe Maschine wie Produktion |
| LiveKit | `livekit/livekit-server:v1.13.11` (wie Produktion), eigener Container `agora-last-livekit`, Ports 7710–7712 nur auf 127.0.0.1, Konfiguration `tests/last/livekit-last.yaml` |
| Last | offizielles CLI `livekit/livekit-cli:v2.20.1` (per Digest fest), `lk load-test`: 1 Raum, N Video-Publisher mit Simulcast (Auflösung „high“, VP8/H.264 gemischt) + N Abonnenten mit Layout 3×3 (jeder abonniert 9 Videos – wie die Galerie mit Adaptive Stream), Start 5 je Sekunde, 60 s |
| Messung | `docker stats` alle ~2 s für den LiveKit-Container und den Last-Erzeuger; Statistik von `lk` (Tracks, Bitrate, Paketverlust) |

Achtung beim Lesen: Auf dem Server liefen gleichzeitig andere Dienste und E2E-Läufe (load average 7–21 bei 8 Kernen).
Last-Erzeuger und LiveKit teilten sich die Maschine; der Erzeuger brauchte bei 50 bis zu 4,8 Kerne.

## Ergebnisse Lasttest

| N Publisher + N Abonnenten | weitergeleitete Videos | LiveKit CPU Ø / max (100 % = 1 Kern) | LiveKit RAM max | Bitrate an Abonnenten | Paketverlust | Fehler |
|---|---|---|---|---|---|---|
| 10 + 10 | 90 | 44 % / 83 % | 117 MiB | 53 Mbit/s (5,3 je Person) | 0 % | 0 |
| 25 + 25 (Lauf 1) | 225 | 76 % / 101 % | 276 MiB | 127 Mbit/s (5,1 je Person) | 0 % | 0 |
| 25 + 25 (Lauf 2) | 225 | 111 % / 161 % | 271 MiB | 126 Mbit/s (5,0 je Person) | 0 % | 0 |
| 50 + 50 (Lauf 1) | 414 von 450 | 113 % / 158 % | 685 MiB | 120 Mbit/s (2,4 je Person) | 8,1 % | 4 Abonnenten ohne Verbindung („invalid state“) |
| 50 + 50 (Lauf 2) | 450 | 121 % / 179 % | 838 MiB | 127 Mbit/s (2,5 je Person) | 6,3 % | 0 |

Bei 50 sank die Bitrate je Abonnent auf die Hälfte, der Verlust stieg auf 6–8 %. LiveKit selbst brauchte dabei
höchstens 1,8 Kerne; zur selben Zeit lag der Last-Erzeuger bei bis zu 4,8 Kernen und der Server bei load average 16–20.
Der Engpass bei 50 war damit eher die volle Maschine (Erzeuger + andere Dienste über Loopback) als LiveKit. Ein sauberer
Wert für 50 braucht einen Last-Erzeuger auf einem zweiten Rechner.

## Kapazität für diesen Server (Schätzung)

- **CPU:** kein Engpass. Ein Meeting mit 25 Personen (Galerie, Kamera an) kostet LiveKit etwa 1–1,5 Kerne, 50 Personen
  etwa 1,5–2 Kerne. Bei 8 Kernen und anderen Diensten auf der Maschine sind **2–3 gleichzeitige Meetings mit je 25
  Personen** oder **ein Meeting mit 50** realistisch.
- **RAM: Grenze in Produktion beachten.** LiveKit brauchte bei 50 + 50 bis zu 838 MiB; in `docker-compose.yml` hat
  `livekit` `mem_limit: 1g`. Für Meetings über ~35 Personen oder mehrere große Meetings gleichzeitig sollte das Limit
  auf **2g** steigen (sonst droht der OOM-Kill mitten im Meeting).
- **Netz:** In der Galerie bekommt jede Person etwa 5 Mbit/s (9 Videos, simulcast). 25 Personen ≈ 125 Mbit/s aus dem
  Server heraus, dazu ~1 Mbit/s je sendender Kamera hinein. Die Anbindung des Servers (und UDP-Port 7882) ist bei
  großen Meetings vermutlich der erste echte Engpass, nicht die CPU. Agora sendet wie der Lasttest 720p mit den
  Simulcast-Ebenen 180p/360p; Adaptive Stream holt in der Galerie meist nur die kleinen Ebenen, real also eher weniger.
- `MAX_TEILNEHMER` (Standard 50) passt zu diesen Werten.

## Netzsimulation (schlechtes Netz)

Aufbau (`sh scripts/lasttest.sh netz`): eigenes Docker-Netz `agora-lastnetz` (10.250.77.0/28) mit LiveKit
(10.250.77.10, `node_ip` = Container-IP, Konfiguration `tests/last/livekit-netz.yaml`), App im Testmodus (10.250.77.11:8132)
und einem Playwright-Container (10.250.77.12) mit `NET_ADMIN`. `tc qdisc add dev eth0 root netem loss 5% delay 150ms rate 1mbit`
wirkt nur auf eth0 **dieses** Containers (eigener Netz-Namespace, nie auf dem Host). Der Browser öffnet die Seite über
eine Weiterleitung auf `http://127.0.0.1:8132` (Kamera/Mikro gibt es nur in einem sicheren Kontext); Medien gehen direkt
per UDP an LiveKit im Docker-Netz. Zwei Browser (Host + Gast, Kamera-/Mikro-Attrappe), erst 15 s gutes Netz, dann netem
für ~60 s (nur ausgehend: 1 Mbit/s für beide Browser zusammen).

| | vorher | mit netem |
|---|---|---|
| Ton beim Host (kbit/s) | 76 | 71 (Verlust 4 %) |
| Video beim Host (kbit/s / Bilder je s / Breite) | 349 / 20 / 640 px | 138 / 17 / 320 px |
| Video beim Gast (kbit/s / Bilder je s / Breite) | 320 / 20 / 640 px | 123 / 16,9 / 320 px |
| Eigenes Video des Hosts | 640 px, nicht begrenzt | 320 px, begrenzt durch „bandwidth“ |
| RTT | 1 ms | 158–178 ms |
| Popover „Verbindung“ (Host) | – | **Mittel**: UDP direkt, RTT 160 ms, Verlust 3,9 %, Jitter 14 ms, Senden 212 kbit/s, 320 × 180 · 19 fps |

Ergebnis: Das Gespräch bleibt bestehen, der Ton läuft praktisch unverändert weiter (Opus mit RED/DTX), das Video geht
über Simulcast/Dynacast auf die kleine Ebene (320 px) und ~40 % der Bitrate herunter, die Bildrate bleibt bei ~17 fps.
Agora zeigt die Verschlechterung im Popover („Mittel“, Tipps-Knopf). Die Warnung („seit einer Weile schlecht“) kam
nicht, weil die Werte unter der Schwelle „schlecht“ blieben (5 % Verlust bzw. 400 ms RTT) – so gewollt.

## Wiederholen

```sh
sh scripts/lasttest.sh last 10 25 50     # ~5 min, braucht freie Ports 7710–7712
sh scripts/lasttest.sh netz              # ~3 min, braucht freien Port 8132 und das Netz 10.250.77.0/28
NETEM="loss 10% delay 300ms rate 500kbit" sh scripts/lasttest.sh netz   # härteres Netz
```

Rohdaten und Bildschirmfotos landen in `${AUSGABE:-/tmp/agora-lasttest}/`. Das Skript räumt Container und Netz am Ende
selbst ab. Das Playwright-Abbild für die Netzsimulation läuft als root, weil `tc` root mit `NET_ADMIN` braucht – beides
gilt nur im eigenen Netz-Namespace des Containers.

## Mehrere Knoten (Skalierungsbeweis)

Stand: 27.11.2026 · Skript: `sh tests/skalierung/lauf.sh` (~5 min) · Hintergrund: [ADR 0007](adr/0007-skalierung.md).
Zwei LiveKit-Knoten v1.13.11 im Host-Netz (A: 7860–7863, B: 7870–7873, nur `lo`, `node_ip` 127.0.0.1), gemeinsames Redis
(6392), App im Testmodus (8261, `AGORA_LIVEKIT_KNOTEN`), Chromium-Gäste mit Kamera-Attrappe. Wo ein Raum liegt, prüft der
Test auf drei unabhängigen Wegen: Redis `room_node_map`, Prometheus `livekit_room_total` je Knoten und die
Host-Kandidaten, die der Server dem Browser nennt (UDP 7862 = A, 7872 = B). Medien laufen, wenn die dekodierten
Bilder fremder Videos wachsen.

| Abschnitt | Aufbau | Ergebnis |
|---|---|---|
| 1. Verteilung | beide Knoten `eu-central`, `sort_by: rooms`, App spricht nur A an | 4 Meetings × 2 Gäste → 2 auf A, 2 auf B; Video in allen 8 Browsern; ListRooms über A zählt alle 8 Verbundenen |
| 2. Regionen | A `eu-central`, B `eu-west`, `regionaware` | `CF-IPCountry: DE` → A, `FR` → B, ohne Land → A (erster Knoten); Gast aus DE im FR-Meeting: Signal über A, Medien von B, Video läuft |
| 3. Ausfall | `docker kill` von A | FR-Meeting auf B läuft weiter; App gesund, meldet A als ausgefallen (`agora_livekit_knoten_erreichbar` 0/1); neues Meeting → B; Wiederbeitritt ins DE-Meeting (lag auf A) → neu auf B, Video läuft |

Beobachtet: Nach einem harten Ausfall hält LiveKit den Knoten noch ≈ 5–8 s für verfügbar (Anfragen dorthin: „no response
from servers“) – der Test wartet deshalb 8 s. Der Gast, der über A signalisiert hatte, zeigt „Verbindung unterbrochen –
wird wiederhergestellt …“ und versucht weiter A (neu laden hilft; ADR 0007). Auf diesem Rechner wählt Chromium manchmal
ein „prflx“-Paar, weil es von einer Docker-Brücken-Adresse an 127.0.0.1 sendet und die Antwort über das NAT der Brücke
zurückkommt – das Ziel bleibt der Host-Kandidat des Knotens. LiveKit B am Ende: ≈ 96 MiB, 2 % CPU (Leerlauf).
Was das nicht zeigt: Latenz/Bandbreite zwischen echten Standorten, Egress mit mehreren Knoten, Ausfall von Redis.
