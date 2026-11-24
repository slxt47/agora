# Betrieb

## Dienste

| Dienst | Lauscht auf | Aufgabe |
|---|---|---|
| `app` | 127.0.0.1:8110 | Webseite und API, SQLite und Aufnahmen in `./daten` |
| `livekit` | 127.0.0.1:7880 (Signalisierung), öffentlich UDP 7882 und TCP 7881 | SFU für Video und Ton |
| `redis`, `egress` | 127.0.0.1:6390, 7884 | nur Profil `egress`: Server-Aufnahme + Livestream (siehe unten) |
| `cloudflared` | – | Tunnel `agora`: `HOST/rtc` geht an 127.0.0.1:7880, alles andere an 127.0.0.1:8110 |

Alle drei laufen im Host-Netz, damit LiveKit die öffentliche Schnittstelle sieht und der Tunnel beide lokalen Ports erreicht. LiveKit nutzt nur die Schnittstelle `eth0` (`rtc.interfaces.includes`), damit keine Docker-Brücken als Kandidaten auftauchen.

## Firewall

Öffentlich gebraucht werden UDP 7882 für die Medien (ein Port für alle Verbindungen) und TCP 7881 als ICE-TCP-Rückfall, wenn UDP blockiert ist:

```sh
sudo ufw allow 7882/udp comment 'Agora LiveKit Medien (UDP)'
sudo ufw allow 7881/tcp comment 'Agora LiveKit ICE-TCP'
```

Gibt es beim Hoster eine eigene Firewall, müssen die Ports dort auch frei sein. Wer an einem Meeting teilnimmt, sieht die öffentliche IP des Servers als ICE-Kandidaten.

## Geheimnisse (nicht im Repo, 0600)

- `app.env`: `SMTP_*`, `AGORA_GEHEIM` (fest lassen: daraus werden die Schlüssel für TOTP-Geheimnisse und Wiederherstellungscodes abgeleitet – ändert er sich, kommen Konten mit Zwei-Faktor-Anmeldung nicht mehr herein, bis 2FA für sie zurückgesetzt wird – im Admin-Bereich „Zwei-Faktor zurücksetzen“), `AGORA_SEITE`, `AGORA_LIVEKIT_URL` (`wss://HOST`), `LIVEKIT_API_HOST` (`http://127.0.0.1:7880`), `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `app.env` optional: `AGORA_ADMIN_MAILS` – Komma-Liste der E-Mail-Adressen der Admins (nur bestätigte Konten zählen). Sie bekommen Meldungen per Mail und sehen den Admin-Bereich unter `/admin` (alle anderen bekommen 404). Ohne die Variable gibt es keine Admins; Änderungen gelten nach Neustart der App (`docker compose up -d app`).
- `livekit.env`: `LIVEKIT_KEYS=<key>: <secret>` und `NODE_IP=<öffentliche IP>`
- `.env`: nur `TUNNEL_TOKEN` (von `scripts/cloudflare-einrichten.py`)

## Ausrollen

```sh
docker compose up -d --build app             # nach Codeänderungen
docker compose up -d --force-recreate livekit  # nach Änderungen an livekit.yaml
```

Prüfen: `curl -s http://127.0.0.1:8110/api/gesund`. `https://HOST/rtc/validate` muss 401 liefern; das zeigt, dass LiveKit über den Tunnel antwortet.

## Überwachung

`sh scripts/waechter.sh` prüft `/api/gesund?tief=1` und `/api/metriken` und schreibt eine Zeile, z. B.
`agora OK – 1 Meetings, 3 verbunden, LiveKit 4 ms, DB 2 ms, Platte 61 GB frei (54 %), Hintergrund vor 23 s, Mails 5 ok/0 Fehler, LiveKit-Fehler 0`.
Exit 0 = OK, 1 = Warnung (seit dem letzten Lauf: Mail-Fehler, > 5 LiveKit-Fehler, > 50 fehlgeschlagene Anmeldungen,
Hintergrund-Fehler), 2 = Fehler (App weg, Datenbank schreibt nicht, LiveKit antwortet nicht, Platte unter 2 GB oder 5 %,
Hintergrund länger als 3 min still). Der letzte Zählerstand liegt in `~/.cache/agora-waechter.stand`. Braucht curl, jq, awk.

Beide Endpunkte antworten nur auf 127.0.0.1 ohne `CF-Connecting-IP` (der Tunnel setzt ihn immer) – über
`https://HOST` gibt es 404. Für einen Abruf von woanders `AGORA_METRIKEN_TOKEN` in `app.env` setzen und
`Authorization: Bearer …` schicken. Prometheus kann `http://127.0.0.1:8110/api/metriken` direkt abfragen.

## Dateien im Meeting und Virenprüfung

Dateien liegen unter `daten/dateien/` (25 MB je Datei, 200 MB je Meeting). Erlaubte Endungen: `AGORA_DATEI_ENDUNGEN` in `app.env`
(Vorgabe ohne ausführbare, HTML-, Skript- und SVG-Dateien). Optional prüft clamd jede Datei vor der Annahme:
`AGORA_CLAMAV=host:port` (TCP, INSTREAM). clamd muss `StreamMaxLength` ≥ 25M haben (Vorgabe 25M passt knapp; besser 26M).
Ist clamd nicht erreichbar, lehnt die App Uploads mit 503 ab (fail closed). Ohne die Variable steht in der Oberfläche
„Nicht auf Viren geprüft“. Ein clamd-Container läuft wie die anderen gehärtet im Host-Netz, gebunden an 127.0.0.1.

## Öffentliche API (`/api/v1`)

Nutzer legen persönliche API-Schlüssel im Konto an (Reiter „Entwickler“), Beschreibung in [`API-V1.md`](API-V1.md).
Nichts einzurichten; die Grenzen (120/min je Schlüssel, 30 ungültige Schlüssel/10 min je IP) liegen wie alle anderen im
Arbeitsspeicher des einen Workers. Download-Links sind mit `AGORA_GEHEIM` signiert – ändert er sich, werden offene Links
ungültig (sie gelten ohnehin nur 15 min). Ein kompromittierter Schlüssel: Nutzer widerruft ihn selbst, oder ein Admin
sperrt das Konto (gesperrte Konten können keine Schlüssel nutzen). Im Protokoll stehen nur Erstellen/Widerrufen und
„Raum angelegt/gelöscht (API)“, keine einzelnen Abrufe.

## Webhooks

Die Zustellung läuft in einem eigenen Thread der App (4 parallel, alle 5 s oder sofort nach einem Ereignis), ausgehend
direkt vom Server (Host-Netz) per HTTPS zu öffentlichen Adressen – Ziele im eigenen Netz lehnt die App ab, auch wenn
DNS später dorthin zeigt (es wird mit genau der geprüften IP verbunden). Die Nutzlast verlässt dabei den Server: das ist
die Entscheidung des Konto-Besitzers (Datenschutz-Hinweis im Konto). Ändert sich `AGORA_GEHEIM`, lassen sich die
gespeicherten Geheimnisse nicht mehr entschlüsseln – Signaturen stimmen dann nicht mehr; Nutzer erzeugen mit „Neues
Geheimnis“ ein neues. Abgeschaltete Endpunkte (3 endgültige Fehlschläge in Folge) bekommen eine Mail; im Protokoll steht
„Webhook abgeschaltet (#id)“.

## Kapazität

Lasttest und Netzsimulation: [`docs/LASTTEST.md`](LASTTEST.md) (`sh scripts/lasttest.sh`, nur gegen Wegwerf-Instanzen).
Kurz: CPU reicht für 2–3 Meetings mit je 25 Personen; LiveKit brauchte bei 50 + 50 bis zu 838 MiB – für Meetings über
~35 Personen `mem_limit` von `livekit` auf 2g erhöhen. Der erste echte Engpass ist die Bandbreite (~5 Mbit/s je Person in der Galerie).

## Server-Aufnahme und Livestream (Egress) – optional, standardmäßig aus

`redis` und `egress` stehen im Compose-Profil `egress`; ein normales `docker compose up -d` startet sie nicht, und ohne
`AGORA_EGRESS=1` blendet die App die Funktion aus (API 404, Oberfläche ohne Einträge).

**Getestet (24.11.2026, `sh tests/egress/lauf.sh`, eigener Wegwerf-Stapel):** `livekit/egress:v1.15.0` hat ein arm64-Image
(Manifest-Index linux/amd64 + linux/arm64) und läuft gehärtet (read-only, cap_drop ALL, no-new-privileges, als 1001:1000,
Chrome ohne SYS_ADMIN). RoomComposite 720p30 mit einer Person: Start bis „läuft“ ≈ 6 s, fertige MP4 in der Bibliothek
≈ 6 s nach dem Stopp, 20 s ≈ 3,5 MB. Egress-Container dabei ≈ 1 Kern (docker stats ≈ 100 %) und 0,5–0,95 GB RAM (Spitze
beim Chrome-Start). Mit vielen Kacheln steigt die CPU – je gleichzeitiger Aufnahme/Stream 2–4 Kerne und ~1–1,5 GB
einplanen; `mem_limit: 2g`, `cpus: 4`. Egress nimmt nur so viele Aufträge an, wie seine CPU-Schätzung erlaubt.
Redis: ~10 MB (`mem_limit: 128m`, ohne Persistenz). Leerlauf des Egress-Containers ≈ 130 MB.

**Einschalten in Produktion (bewusst, nicht automatisch):**
1. `livekit.env` ergänzen: `REDIS_HOST=127.0.0.1:6390` und `REDIS_PASSWORD=<zufällig, z. B. openssl rand -hex 24>`
   (LiveKit liest beide als Umgebungsvariablen, `livekit.yaml` bleibt unverändert; Egress und Redis nutzen dieselbe Datei).
2. Ausgabeordner: `mkdir -p daten/egress && chmod 2770 daten/egress` (Besitzer 1000:1000; Egress schreibt als 1001:1000).
3. `app.env`: `AGORA_EGRESS=1` (optional `AGORA_EGRESS_AUSGABE`, Standard `/daten/egress`; `AGORA_EGRESS_ZIEL`, Standard `/out`).
4. `docker compose --profile egress up -d redis egress && docker compose up -d --force-recreate livekit && docker compose up -d app`
5. Prüfen: `docker logs agora-egress-1 | grep "service ready"`, dann im Meeting „Mehr → Auf dem Server aufzeichnen“.
Ports: Redis 127.0.0.1:6390, Egress-Vorlagen-Server 7884 (Host-Netz – nicht in der Firewall freigeben). Ausschalten:
`AGORA_EGRESS` entfernen, `docker compose --profile egress stop redis egress`, `REDIS_*` aus `livekit.env` nehmen, LiveKit neu erstellen.

Datenschutz: Server-Aufnahmen landen wie Browser-Aufnahmen in der Bibliothek des Raum-Besitzers (90 Tage, 5-GB-Kontingent;
die 500-MB-Grenze je Datei gilt nur für Uploads). Stream-URL und -Schlüssel werden nie protokolliert (Fehlertexte geschwärzt),
nur verschlüsselt und nur bis zum Ende des Streams gespeichert. Ziele im eigenen Netz sind gesperrt (Schutz vor SSRF über den
Egress-Container). Egress-Aufträge (ohne Ziel) bleiben 7 Tage für die Statusanzeige. HLS ist nicht umgesetzt (siehe ADR 0003).

## Transkript aus Aufnahme (Whisper) – optional

Mit `AGORA_WHISPER_URL` in `app.env` (OpenAI-kompatibel: multipart `file`, `model`, `response_format=vtt`; optional
`AGORA_WHISPER_MODELL`, Standard `whisper-1`, `AGORA_WHISPER_SCHLUESSEL` als Bearer, `AGORA_WHISPER_TIMEOUT` in s,
Standard 3600) bietet die Bibliothek „Transkript erstellen“ an. Ein Job nach dem anderen; das Ergebnis liegt als
`daten/aufnahmen/<id>.vtt` neben der Aufnahme und verschwindet mit ihr. Die Aufnahme verlässt dabei den Server nur,
wenn die URL auf einen fremden Dienst zeigt – deshalb nur einen eigenen Whisper eintragen.

Vorhandener Dienst auf diesem Server (nur gelesen, nicht verändert): `disslxt-repo-whisper-1`, whisper.cpp-Server
(`whisper-server --model /models/ggml-base.bin --port 8080`), nur im Docker-Netz `disslxt-repo_speech`, kein Host-Port.
So ist er für Agora (Host-Netz) nicht sauber erreichbar (die Container-IP 172.29.0.x ist nicht fest), und ohne
`--convert` nimmt whisper.cpp nur WAV an, Agora-Aufnahmen sind WebM/MP4. Anbinden, ohne den bestehenden Dienst zu ändern:
einen zweiten whisper.cpp-Container (gleiches Image, gleiches Modell-Volume nur lesend) mit
`--host 127.0.0.1 --port 8095 --convert --inference-path /v1/audio/transcriptions` im Host-Netz starten (braucht ffmpeg im
Image; RAM je nach Modell: base ≈ 400 MB, small ≈ 1 GB) und `AGORA_WHISPER_URL=http://127.0.0.1:8095/v1/audio/transcriptions`
setzen. Alternativ im disslxt-Projekt einen Port `127.0.0.1:8095:8080` freigeben und `--convert` ergänzen (Änderung dort).

## Aufbewahrung

- Nicht bestätigte Konten: 48 h
- Sitzungen: 30 Tage
- Bestätigungslinks: 48 h
- Passwortlinks: 1 h
- Zweiter Anmeldeschritt (Zwei-Faktor): 5 min
- Warteraum-Anfragen: 30 min
- Sicherheitsprotokoll ohne Inhalte: 30 Tage (Admin-Aktionen „Admin: …“: 365 Tage)
- Chat und Umfragen: Werden beim Beenden gelöscht, außer der Raum hat „Chat speichern“.
- Whiteboard (Operations-Log mit identity, Zeichnungen, Texte): wird beim Beenden gelöscht, außer der Raum hat „Chat speichern“ – dann bleibt es wie der Chat, solange der Raum besteht, und der Besitzer sieht es im Meeting-Verlauf. Sperre und Zähler werden immer gelöscht.
- Notizen (Text, Name der Person, die zuletzt gespeichert hat): wie Chat – beim Beenden gelöscht, mit „Chat speichern“ im Meeting-Verlauf, solange der Raum besteht.
- Fragen (Q&A) mit Stimmen: wie Chat. Auch anonyme Fragen speichern identity, Namen und Konto der fragenden Person (anonym nur gegenüber den anderen); im Meeting-Verlauf erscheinen anonyme Fragen ohne Namen.
- Dateien aus Meetings (Inhalt unter `daten/dateien/`, Name, Größe, wer geteilt hat): werden beim Beenden gelöscht; mit „Chat speichern“ bleiben sie noch 30 Tage für den Besitzer (Meeting-Verlauf), danach löscht der Hintergrund sie. Wird der Raum gelöscht, verschwinden auch verwaiste Dateien (spätestens nach einer Stunde).
- Breakout-Räume (Zuteilung, wer wo ist): werden beim Beenden des Meetings gelöscht.
- Verbindungsqualität: Werte je Teilnahme werden beim Beenden gelöscht; der Schnitt je Meeting (ohne IPs und Namen) bleibt, solange der Raum besteht.
- Rückmeldungen nach dem Meeting (Sterne, Probleme, Text, eigener Verbindungsschnitt): solange der Raum besteht; die gespeicherte identity dient nur gegen doppelte Bewertungen und wird nie angezeigt.
- Aufnahmen: 90 Tage (auch Server-Aufnahmen); Egress-Aufträge (Status, Fehler, ohne Stream-Ziel): 7 Tage; Stream-Ziel
  (verschlüsselt): nur solange der Stream läuft; verwaiste Dateien im Egress-Ausgabeordner: 1 Tag
- Dauer-Chat (persönlicher Raum, nur wenn der Besitzer ihn einschaltet): öffentliche Nachrichten bleiben, bis der Besitzer ihn leert oder ausschaltet (oder mit dem Konto), höchstens die neuesten 1000 Nachrichten.
- Meeting-Verlauf (Titel, Code, Beginn, Ende, Spitzenzahl, Zahl der Beitritte – keine Namen, keine IPs): 90 Tage, einzeln löschbar, mit dem Konto weg. Wird der Raum gelöscht, bleibt der Eintrag ohne Chat-Verweis.
- Erinnerungen: Einstellung im Profil, bis man sie ausschaltet (oder mit dem Konto). Adressen von Eingeladenen nur, wenn der Host „Erinnerung schicken“ ankreuzt – gelöscht nach dem Ende des Termins, zu dem eingeladen wurde (spätestens mit dem Meeting, Sicherheitsnetz 400 Tage). Versand-Merker (nur Hash aus Raum, Termin, Empfänger): 2 Tage.
- Meeting-Vorlagen: bis der Besitzer sie löscht (oder mit dem Konto); enthalten keine Passcodes, nur „Passcode an/aus“. Im Datenexport enthalten.
- Kalender-Abo: Nur wer es im Profil einschaltet. Gespeichert werden Salz, Hash des Links, Zeitpunkt des Einschaltens und des letzten Abrufs (keine IP). Weg beim Ausschalten, bei „Neuer Link“ (ersetzt) und mit dem Konto.
- Meldungen: 365 Tage nach „erledigt“; blockierte Konten bis zum Aufheben oder Löschen eines der Konten
- Webinar-Anmeldungen (Name, E-Mail, Antworten, Zeitpunkt des Beitritts; Beitrittslink nur als Hash): 30 Tage nach dem
  Webinar – bei geplanten Webinaren ab Termin + Dauer, außerdem beim Ende jedes Meetings im Raum auf höchstens +30 Tage
  gekürzt; ohne Termin spätestens 90 Tage nach der Anmeldung. Selbst löschbar über den Link in der Mail, mit dem Raum weg.
  Panel-Links: bis der Besitzer sie löscht (Token = HMAC aus `AGORA_GEHEIM`, nur Hash gespeichert).
- Live-Untertitel: nichts gespeichert, außer der Besitzer schaltet „Transkript speichern“ ein (alle sehen einen Hinweis);
  dann fertige Abschnitte mit Name und Zeit: 90 Tage, je Meeting oder ganz löschbar, mit dem Raum weg. Die Spracherkennung
  selbst läuft im Browser (bei Chrome/Edge beim Browser-Hersteller) und nur nach Zustimmung der jeweiligen Person.
- Transkripte aus Aufnahmen (Whisper): `.vtt` neben der Aufnahme, mit ihr gelöscht (90 Tage).
- API-Schlüssel (Name, SHA-256 des Schlüssels, erste 12 Zeichen, Rechte, Erstellzeit, Ablauf, letzte Nutzung ohne IP):
  bis der Besitzer sie widerruft oder das Konto gelöscht wird; abgelaufene Schlüssel sind sofort ungültig und werden
  30 Tage nach dem Ablauf gelöscht. Signierte Download-Links der API gelten 15 min und werden nicht gespeichert.
- Webhooks (Adresse, Ereignisse, Geheimnis verschlüsselt mit einem aus `AGORA_GEHEIM` abgeleiteten Schlüssel): bis der
  Besitzer sie löscht oder mit dem Konto. Zustell-Protokoll (Zeit, Ereignis, Versuch, HTTP-Status, Dauer, Fehlertext –
  keine Inhalte): die letzten 50 Versuche je Webhook, höchstens 14 Tage. Warteschlange (Nutzlast der Ereignisse, darin
  ggf. Name/E-Mail aus Webinar-Anmeldungen und Teilnehmernamen): bis zur Zustellung, höchstens 2 Tage.
  Anwesenheits-Merker für `teilnehmer.gegangen` (identity, Name, Rolle): nur bei Konten mit passendem Webhook und nur bis
  zum Ende des Meetings.
- Ein Meeting endet 10 min, nachdem die letzte Person gegangen ist.
- Anmerkungen auf geteilten Bildschirmen: nicht gespeichert (nur im Browser, weg mit dem Ende der Freigabe); der Server
  kennt nur das Recht „erlaubt“ für das laufende Meeting.
- Ende-zu-Ende-Verschlüsselung: Der Server speichert je Raum nur „an/aus“ und einen Prüfwert (bis zum Ausschalten oder
  Löschen des Raums). Der Schlüssel liegt nur in Links (Fragment `#k=…`) und im Browser-Speicher des Hosts; geht er
  verloren, hilft nur „Neuen Schlüssel erzeugen“ (alte Links gelten dann nicht mehr). Es gibt keinen Weg, ihn vom Server
  wiederzuholen – auch nicht für Admins.

## Updates

RNNoise (`@sapphi-red/web-noise-suppressor`) ist exakt gepinnt; `scripts/rnnoise.mjs` kopiert Worklet und WASM beim Build
nach `/vendor/rnnoise-<version>/` (wie MediaPipe: unveränderlich gecacht, vom Service Worker nicht gespeichert).

LiveKit-, Node- und Python-Images sind per Digest festgelegt. Für ein Update Tag und Digest bewusst ändern, `sh tests/e2e/lauf.sh` laufen lassen und danach ausrollen.
