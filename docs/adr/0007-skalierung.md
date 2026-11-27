# ADR 0007: Mehrere LiveKit-Knoten, Regionen, kein Cascading

**Status:** angenommen (27.11.2026) · Beweis auf einem Rechner: `sh tests/skalierung/lauf.sh` · Betrieb: [`BETRIEB.md`](../BETRIEB.md#mehrere-livekit-knoten--optional-standardmäßig-aus)

## Kontext
Agora läuft auf einem Server mit einem LiveKit-Knoten (ADR 0001). Offen waren „Cascading SFUs“, „Horizontale
Skalierung von SFU-Nodes + Load-Balancing / Regionen“ und „Multi-Region-Routing“. Der Lasttest (LASTTEST.md) zeigt:
Ein Knoten trägt ~2–3 Meetings mit je 25 Personen oder eines mit 50; der erste Engpass ist die Bandbreite.

## Was open-source LiveKit (v1.13.11) kann – geprüft, nicht aus der Erinnerung
Alle Schlüssel stammen aus `livekit-server help-verbose` bzw. den yaml-Tags im Binary und wurden im Wegwerf-Stapel ausprobiert.

| Fähigkeit | Wie | Geprüft |
|---|---|---|
| Mehrere Knoten | gemeinsames Redis (`redis.address` bzw. `REDIS_HOST`/`REDIS_PASSWORD`); jeder Knoten meldet sich mit ID, IP, Region und Last an (`livekit-server list-nodes`) | ja |
| Raum → Knoten | Beim Anlegen (CreateRoom oder erster Beitritt) wählt der `node_selector` einen Knoten; Redis-Hash `room_node_map` merkt ihn. **Ein Raum liegt immer ganz auf einem Knoten.** | ja |
| Lastverteilung | `node_selector.sort_by`: `random`, `sysload`, `cpuload`, `rooms`, `clients`, `tracks`, `bytespersec` (anderes → „unknown sort by option“); `algorithm: lowest` | ja (`rooms`: 4 Räume → 2 + 2) |
| Regionen | `region` bzw. `LIVEKIT_REGION` je Knoten, `node_selector.kind: regionaware` + `regions: [{name, lat, lon}]`. Gewählt wird die Region, die **dem Knoten am nächsten liegt, der die Anfrage bekommt** – nicht dem Teilnehmenden. | ja |
| Signalisierung an jedem Knoten | Ein Knoten ohne den Raum leitet die Signalisierung über Redis (psrpc) zum Knoten des Raums; die Medien gehen direkt an den Raum-Knoten (dessen `NODE_IP`, dessen UDP-Port) | ja (Signal über A, Medien an B) |
| Server-API an jedem Knoten | ListRooms/ListParticipants/… beantwortet jeder Knoten für alle Räume (Zustand in Redis) | ja |
| Ausfall eines Knotens | Räume auf dem Knoten sind weg (Teilnehmende fliegen raus); ein Wiederbeitritt legt den Raum auf einem anderen Knoten neu an. Nach einem harten Ausfall gilt der tote Knoten noch ≈ 5–8 s als verfügbar (Statistik in Redis) – Anfragen, die in der Zeit dort landen, scheitern mit „no response from servers“ | ja |
| Egress | meldet sich ebenfalls über Redis an und nimmt Aufträge für Räume auf jedem Knoten an | nein (nur Doku) |

**Nicht** in open-source LiveKit:
- **Cascading / ein Raum über mehrere Knoten.** Alle Teilnehmenden eines Meetings hängen am selben Knoten; ein Meeting
  wird nie größer als ein Knoten trägt. Das verteilte „Mesh“ (Teilnehmende verbinden sich mit dem nächsten Knoten, die
  Knoten tauschen Spuren aus) gibt es nur in LiveKit Cloud. Eigenbau hieße: eine zweite SFU als Relay (z. B. mediasoup
  `PipeTransport`, Jitsi Octo) oder Spuren mit einem Server-Bot von Raum zu Raum spiegeln – viel Aufwand, neue
  Fehlerquellen, für Meetings bis 50 Personen nicht nötig. Für sehr viele Zuschauer gibt es HLS (ADR 0003).
- **Umzug laufender Räume.** Fällt ein Knoten aus, gibt es keine Übergabe; die Clients müssen neu beitreten.
- **Region nach Standort der Teilnehmenden.** LiveKit kennt die Region der Clients nicht; Cloud macht das per Geo-DNS.

## Entscheidung
- **Skalieren über mehr Meetings, nicht über größere Meetings**: weitere LiveKit-Knoten mit gemeinsamem Redis,
  `node_selector: regionaware`, Konfiguration `livekit-mehrknoten.yaml` (auf allen Knoten gleich; je Knoten in
  `livekit.env` nur `NODE_IP`, `LIVEKIT_REGION`). Produktion bleibt bei einem Knoten und `livekit.yaml`, bis ein
  zweiter Server da ist.
- **Die App wählt die Region** (`app/livekit_dienst.py`): `AGORA_LIVEKIT_KNOTEN` = je Knoten
  `<region> <signal-url> <api-url>`. Wunsch-Region eines Beitritts: Feld `region` (API), sonst das Land aus
  `CF-IPCountry` über `AGORA_LIVEKIT_LAENDER`, sonst der erste Knoten. Für einen neuen Raum geht CreateRoom an einen
  Knoten dieser Region → `regionaware` legt den Raum dort an. Die Signal-URL im Zugang ist die eines Knotens der
  Wunsch-Region (bei Breakout-Räumen die Region des Hauptraums). Ein bestehender Raum bleibt, wo er ist; wer aus einer
  anderen Region dazukommt, signalisiert über „seinen“ Knoten, die Medien gehen zum Raum-Knoten.
- **Ausfall in der App**: Scheitert der Verbindungsaufbau zu einem Knoten (Connect-Fehler/-Zeitlimit), wird er 15 s
  gemieden und der nächste gefragt – auch für die Signal-URL neuer Teilnehmender. Antwortet ein Knoten mit einem
  Fehler, gibt es keinen zweiten Versuch woanders (der Aufruf kam an; sonst liefe z. B. ein Datenpaket doppelt).
- **Sichtbarkeit**: `/api/gesund?tief=1` listet die Knoten (`pruefungen.livekit.knoten`), `/api/metriken` hat
  `agora_livekit_knoten_erreichbar{knoten,region}` und zählt Verbindungsfehler je Knoten; `scripts/waechter.sh` warnt,
  wenn ein Knoten fehlt. Je Knoten liefert LiveKit Prometheus-Metriken (`prometheus.port`, Label `node_id`).
- **Signalisierung über Cloudflare**: je Medien-Knoten ein eigener Tunnel mit eigenem Hostnamen (z. B.
  `rtc-hel.agora.slxt.dev` → `127.0.0.1:7880` auf diesem Server). Ein gemeinsamer Tunnel mit mehreren Connectoren
  ginge für `/rtc` zwar (jeder Knoten nimmt jeden Raum an), aber Cloudflare wählt den Connector selbst (keine Region)
  und dieselben Regeln schicken auch `/` an Server ohne App.

## Folgen
+ Mehr gleichzeitige Meetings durch weitere Server; Meetings starten nahe der Mehrheit (Land des ersten Beitritts)
+ Ein Knoten fällt aus → neue Meetings laufen auf den anderen, die App bleibt gesund (getestet)
+ Ohne `AGORA_LIVEKIT_KNOTEN` ändert sich nichts (ein Knoten wie bisher)
− Ein Meeting bleibt auf einen Knoten begrenzt (~50 Personen mit Kamera, LASTTEST.md)
− Fällt ein Knoten aus, ist jedes Meeting darauf unterbrochen; wer über den toten Knoten signalisiert hat, aber in
  einem Raum auf einem anderen Knoten ist, verliert ebenfalls die Verbindung (der Browser versucht dieselbe URL weiter –
  „Verbindung unterbrochen – wird wiederhergestellt …“). Neu laden/neu beitreten hilft sofort. Ein automatischer
  Neubeitritt über die App (neue Signal-URL) ist noch offen (TODO).
− Redis wird zur gemeinsamen Abhängigkeit aller Knoten (ohne Redis keine neuen Räume); es läuft auf dem Haupt-Server
  neben der App und ist nur über das private Netz erreichbar
− Die „Region“ eines Meetings bestimmt der erste Beitritt; ein Meeting mit Teilnehmenden aus mehreren Regionen hat für
  einige längere Wege (Medien gehen immer zum Raum-Knoten)
