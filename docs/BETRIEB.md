# Betrieb

## Dienste

| Dienst | Lauscht auf | Aufgabe |
|---|---|---|
| `app` | 127.0.0.1:8110 | Webseite und API, SQLite und Aufnahmen in `./daten` (Aufnahmen/Dateien optional in S3) |
| `livekit` | 127.0.0.1:7880 (Signalisierung), öffentlich UDP 7882 und TCP 7881 | SFU für Video und Ton |
| `redis`, `egress` | 127.0.0.1:6390, 7884 | nur Profil `egress`: Server-Aufnahme + Livestream (siehe unten); `redis` auch Profil `redis` (mehrere App-Worker) |
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

## Anmelden mit Google, Microsoft, GitHub – optional, standardmäßig aus

Je Anbieter `AGORA_GOOGLE_CLIENT_ID`/`_SECRET`, `AGORA_MICROSOFT_CLIENT_ID`/`_SECRET` (+ optional `AGORA_MICROSOFT_TENANT`,
Standard `common`), `AGORA_GITHUB_CLIENT_ID`/`_SECRET` in `app.env`, dann `docker compose up -d app`. Fehlt eins der
beiden, bleibt der Anbieter aus (keine Knöpfe, Routen 404). Registrierung bei den Anbietern (Redirect-URIs, Scopes,
`xms_edov` bei Microsoft): [`docs/ANMELDUNG.md`](ANMELDUNG.md). Die App braucht ausgehend HTTPS zu
`accounts.google.com`/`oauth2.googleapis.com`/`www.googleapis.com`, `login.microsoftonline.com`, `github.com` und
`api.github.com`. **Microsoft-Geheimnis läuft ab** (höchstens 24 Monate): Termin notieren und rechtzeitig erneuern.
Fehlversuche stehen im Protokoll (`Anmeldung Google gescheitert: token (…)`), ohne Codes oder Tokens.
Beweis ohne echte Zugangsdaten: `APP_PORT=8291 LK_PORT=7900 sh tests/e2e/sso.sh` startet einen Wegwerf-Dex
(`dexidp/dex:v2.44.0`, per Digest gepinnt, Port `DEX_PORT`=5556) und meldet sich im Browser über „Google“ (→ Dex) und den
Firmen-Login an; der Sicherheitstest (`tests/sicherheit/lauf.sh`) greift mit einem eigenen bösen Anbieter an
(`IDP_PORT`, Standard APP_PORT+9).

## Firmen-Login (OIDC je Organisation) – optional, standardmäßig aus

`AGORA_FIRMEN_LOGIN=1` in `app.env` schaltet frei, dass Owner von Organisationen einen eigenen OIDC-Identity-Provider
eintragen (ADR 0008, [`docs/ANMELDUNG.md`](ANMELDUNG.md#firmen-login-oidc-je-organisation)). Die App ruft dann
Discovery/JWKS/Token-Endpunkte dieser IdPs auf – nur `https` und öffentliche Adressen. Das Client-Geheimnis liegt
verschlüsselt mit einem Schlüssel aus `AGORA_GEHEIM` (ändert sich `AGORA_GEHEIM`, müssen Owner das Geheimnis neu
eintragen). Zurücknehmen: Variable entfernen → Knopf und Routen weg, Daten bleiben. Domain einer Organisation lösen
(Streitfall): `DELETE FROM org_sso WHERE domain = '…'` in der Datenbank.

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

## Fehler-Monitoring (selbst gehostet)

Nichts einzurichten: Browser melden Fehler an `POST /api/fehlerbericht`, Ausnahmen des Servers fängt eine Middleware ab;
beides steht gruppiert im Admin-Bereich unter `/admin#fehler`. Eine neue (oder wieder aufgetretene) Gruppe schickt eine Mail
an alle Admins aus `AGORA_ADMIN_MAILS` (höchstens 1 je Gruppe und Tag, 20/h je Admin, 60 Alarme/h insgesamt). Optional
zusätzlich ein ntfy-Thema: `AGORA_FEHLER_NTFY=https://ntfy.example/agora-fehler` in `app.env` (POST mit Titel, ohne
Anmeldung – ein geschütztes Thema über die URL selbst, z. B. ein langes zufälliges Thema; der Text ist die gesäuberte
Fehlermeldung). Im Log steht je Ausnahme nur `ausnahme <Methode> <Route>: <Typ>`.

Missbrauch: Der Endpunkt ist ohne Anmeldung offen (auch Gäste melden). Grenzen 30 Berichte/10 min je IP (IPv6 /64),
600/10 min insgesamt, 16 KB je Bericht, höchstens 2000 Gruppen aus Browsern. Eine Flut kann also echte Browser-Berichte
für 10 min verdrängen und Admins bis zu 20 Mails/h schicken – die Texte sind gesäubert und werden im Admin-Bereich nur
als Text gezeigt.

## Öffentliche Status-Seite

`/status` und `/api/status` sind öffentlich (über den Tunnel, 120 Abrufe/min je IP, Antwort 30 s zwischengespeichert).
Ein eigener Thread in der App prüft jede Minute: **App** (Schreibzugriff auf die Datenbank), **Medien** (LiveKit-Server-API
antwortet) und alle 5 Minuten **E-Mail** (Verbindung + STARTTLS + NOOP zum SMTP-Server, ohne Anmeldung, ohne Mail; ohne
`SMTP_HOST` entfällt der Dienst). Weil die App sich selbst prüft, zeigt die Seite einen Komplettausfall der App nicht
(dann ist sie selbst nicht erreichbar) – dafür bleibt cc-waechter/ntfy zuständig. Störungen und Wartungen trägt ein Admin
unter `/admin#status` ein; jede Änderung steht im Protokoll („Admin: Status-Meldung …“).

## Dateien im Meeting und Virenprüfung

Dateien liegen unter `daten/dateien/` (25 MB je Datei, 200 MB je Meeting). Erlaubte Endungen: `AGORA_DATEI_ENDUNGEN` in `app.env`
(Vorgabe ohne ausführbare, HTML-, Skript- und SVG-Dateien). Optional prüft clamd jede Datei vor der Annahme:
`AGORA_CLAMAV=host:port` (TCP, INSTREAM). clamd muss `StreamMaxLength` ≥ 25M haben (Vorgabe 25M passt knapp; besser 26M).
Ist clamd nicht erreichbar, lehnt die App Uploads mit 503 ab (fail closed). Ohne die Variable steht in der Oberfläche
„Nicht auf Viren geprüft“. Ein clamd-Container läuft wie die anderen gehärtet im Host-Netz, gebunden an 127.0.0.1.

## Öffentliche API (`/api/v1`)

Nutzer legen persönliche API-Schlüssel im Konto an (Reiter „Entwickler“), Beschreibung in [`API-V1.md`](API-V1.md).
Nichts einzurichten; die Grenzen (120/min je Schlüssel, 30 ungültige Schlüssel/10 min je IP) liegen wie alle anderen im
Arbeitsspeicher des einen Workers (mit `AGORA_REDIS_URL` in Redis, siehe „Zwei App-Worker mit Redis“). Download-Links sind mit `AGORA_GEHEIM` signiert – ändert er sich, werden offene Links
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

## Einbetten

`/e/<code>` und `/embed.js` laufen über den normalen Tunnel, nichts einzurichten. Nur diese beiden Pfade weichen von
`frame-ancestors 'none'` / `X-Frame-Options: DENY` bzw. `Cross-Origin-Resource-Policy: same-origin` ab (Haken
`einbetten.kopf_anpassen` am Ende der Middleware). Eine vorgeschaltete Stelle (Cloudflare-Regel, Proxy), die selbst
`X-Frame-Options` setzt, würde das Einbetten wieder verhindern.

## Android-App (Digital Asset Links)

`/.well-known/assetlinks.json` gibt es nur, wenn `AGORA_ANDROID_SHA256` in `app.env` steht (SHA-256 des
Signatur-Zertifikats, mehrere Komma-getrennt; optional `AGORA_ANDROID_PAKET`, Vorgabe `dev.slxt.agora.twa`), sonst 404.
Ungültige Werte → 404 und eine Zeile im Log. Nach dem Setzen `docker compose up -d app`. Eine Cloudflare-Regel darf
`/.well-known/` nicht umleiten oder sperren (Google lädt die Datei ohne Weiterleitungen). Details: [`MOBIL.md`](MOBIL.md).

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
Egress-Container). Egress-Aufträge (ohne Ziel) bleiben 7 Tage für die Statusanzeige.

## Öffentliche Übertragung (HLS) – optional, standardmäßig aus

Für Webinare mit sehr vielen Zuschauern (ADR 0003): Host/Co-Host startet „Mehr → Öffentliche Übertragung (HLS)“, je
Stufe läuft ein RoomComposite-Egress mit HLS-Ausgabe (MPEG-TS, 4-s-Segmente) nach `daten/hls/<code>/<kennung>/`, die App
liefert unter `/hls/…` aus, Zuschauer sehen auf `/h/<code>` zu (ohne WebRTC, ≈ 10–20 s Verzögerung). Braucht Egress
(Abschnitt oben) **und** `AGORA_HLS=1`.

**Getestet (27.11.2026, `sh tests/egress/hls-lauf.sh`, eigener Wegwerf-Stapel, Ports 7850–7854/8251):** Webinar mit
einer Person, 720p + 360p: Start bis „läuft“ ≈ 16 s, erste Segmente nach ≈ 16 s (720p ≈ 650 KB, 360p ≈ 330 KB je 4 s),
Zuschauerseite spielt nach ≈ 10 s (Chromium nativ) bzw. ≈ 7 s (hls.js), `currentTime` läuft weiter, Ende wird angezeigt.
Egress mit zwei Aufträgen: Spitze ≈ 1,6–2,1 Kerne, ≈ 1 GB RAM. Nur 720p ≈ wie eine Server-Aufnahme (≈ 1 Kern).

**Kapazität:** Egress schätzt je RoomComposite 3 Kerne und nimmt bei `cpus: 4` nur **einen** Auftrag an – 720p + 360p
(oder HLS + Aufnahme) brauchen zwei. Wer das will: `EGRESS_CPU_RAUM=1.5` in `livekit.env` (setzt
`cpu_cost.room_composite_cpu_cost`, `scripts/egress-start.sh`) – dann passen zwei Aufträge, die zusammen ≈ 2 Kerne
brauchen. Der Server ist geteilt: 360p nur bei Bedarf. Die Last für die Zuschauer trägt nicht LiveKit, sondern die
Auslieferung: Segmente sind statisch, eine Stufe 720p ≈ 3,3 Mbit/s je Zuschauer – ohne Zwischenspeicher bei Cloudflare
geht jede Anfrage durch den Tunnel zur App.

**Cloudflare-Zwischenspeicher (empfohlen, von Hand):** Die App schickt passende Kopfzeilen (Segmente `public,
max-age=86400, immutable`, Stufen-Playlist `max-age=2`, Master `max-age=5`, 404 `no-store`). Cloudflare speichert
`.m3u8`/`.ts` aber nur mit einer Cache-Regel: *Hostname = agora…, Pfad beginnt mit `/hls/` → Eligible for cache,
Edge TTL: Cache-Control des Ursprungs beachten*. Die Kennung im Pfad ist zufällig je Übertragung – auch „nur
Angemeldete“ dürfen so zwischengespeichert werden (wer die Adresse kennt, sieht zu; die Adresse gibt es nur gegen den
persönlichen Anmelde-Token).

**Einschalten (bewusst, nicht automatisch):**
1. Egress wie oben einrichten; optional `EGRESS_CPU_RAUM=1.5` in `livekit.env`.
2. `mkdir -p daten/hls && chmod 2770 daten/hls` (Besitzer 1000:1000 – sonst legt Docker den Ordner als root an).
3. `app.env`: `AGORA_HLS=1` (optional `AGORA_HLS_AUSGABE`, Standard `/daten/hls`; `AGORA_HLS_ZIEL`, Standard `/hls`).
4. `docker compose --profile egress up -d --force-recreate egress && docker compose up -d app`, dann im Webinar
   „Mehr → Öffentliche Übertragung (HLS) starten“, Link `https://HOST/h/<code>` weitergeben.

Aufräumen: Beim Stoppen oder Meeting-Ende bleiben die Dateien 1 h (Nachzügler), dann löscht der Hintergrund den Ordner;
Ordner ohne Eintrag nach 1 Tag. Die App legt jeden Ordner vorab mit 2770 an, damit sie Dateien des Egress (1001:1000)
löschen kann. Datenschutz: kein Konto, keine IPs; zum Zählen meldet sich jede offene Zuschauerseite alle 30 s mit einer
zufälligen Kennung (Präsenz wie in app/geteilt.py: im Arbeitsspeicher bzw. mit Redis über alle Worker, nur als Hash, 75 s gültig). Bei „nur Angemeldete“ wird wie beim Beitritt „beigetreten“
in der Anmeldung vermerkt.

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

## Push-Benachrichtigungen (Web Push)

Nichts einzurichten: Beim ersten Bedarf erzeugt die App ein VAPID-Schlüsselpaar (P-256) und legt es in
`daten/push-vapid.json` (0600) ab – es ist im Backup. Alternativ fest vorgeben: `AGORA_VAPID_PRIVAT` (32-Byte-Skalar,
Base64url). Geht der Schlüssel verloren oder ändert er sich, nehmen die Push-Dienste die alten Abos nicht mehr an
(Geräte bekommen 403, nach 10 Fehlschlägen werden sie gelöscht) – Nutzer schalten Push dann neu ein.
Optional: `AGORA_VAPID_SUBJEKT` (Kontakt für die Push-Dienste, `mailto:…` oder `https://…`, Standard `AGORA_SEITE`),
`AGORA_PUSH_DIENSTE` (Komma-Liste erlaubter Host-Endungen, Standard `fcm.googleapis.com, android.googleapis.com,
push.services.mozilla.com, push.apple.com, notify.windows.com`).

Ausgehend verbindet die App sich zu den Push-Diensten der Browser-Hersteller (Google, Mozilla, Apple, Microsoft) – wie
bei Webhooks nur zu öffentlichen IPs, ohne Weiterleitungen. Der Inhalt ist Ende-zu-Ende verschlüsselt (RFC 8291): Der
Dienst sieht Zeitpunkt, Größe und Gerät, nicht den Text. iPhone/iPad bekommen Push nur in der installierten App
(„Zum Home-Bildschirm“, iOS 16.4+), nicht im Safari-Tab. Zähler `agora_push_total{ergebnis=ok|weg|fehler}` in `/api/metriken`.

## KI-Zusammenfassung – optional

Mit `AGORA_KI_URL` (OpenAI-kompatibel; volle Adresse `…/v1/chat/completions`, oder nur bis `/v1` bzw. die Basis – der Rest
wird ergänzt) und `AGORA_KI_MODELL` bieten Transkript-Dialog und Bibliothek „Mit KI zusammenfassen“ an. Optional
`AGORA_KI_SCHLUESSEL` (Bearer), `AGORA_KI_TIMEOUT` (s, Standard 300), `AGORA_KI_ZEICHEN` (Teilgröße, Standard 12000).
Das ganze Transkript mit Namen geht an diese Adresse – deshalb nur einen eigenen Dienst eintragen (z. B. llama.cpp-Server
oder Ollama mit OpenAI-API auf 127.0.0.1). Die Oberfläche nennt Host:Port vor dem Senden, die API verlangt
`zustimmung: true`, im Protokoll steht „KI-Zusammenfassung angefordert (Host)“. Ein Auftrag nach dem anderen. Ohne die
Variablen gibt es nur „Stichwort-Kapitel“ (läuft auf dem Server, ohne KI).

## Objektspeicher (S3-kompatibel) – optional, standardmäßig aus

Ohne Einstellung liegen Aufnahmen (`daten/aufnahmen/<id>`), Whisper-Transkripte (`<id>.vtt`) und Dateien aus Meetings
(`daten/dateien/<id>`) wie bisher auf der Platte. Mit diesen Zeilen in `app.env` kommen sie in ein Bucket:
`AGORA_S3_ENDPOINT` (z. B. `https://fsn1.your-objectstorage.com`), `AGORA_S3_BUCKET`, `AGORA_S3_KEY`, `AGORA_S3_SECRET`,
`AGORA_S3_REGION` (Standard `us-east-1`); optional `AGORA_S3_SSE=AES256|aws:kms` (+ `AGORA_S3_KMS_KEY`),
`AGORA_S3_PFADSTIL=0` (virtuelle Hosts), `AGORA_S3_PRAEFIX=agora/`. Fehlt einer der vier Pflichtwerte, startet die App nicht.

- Client: eigener SigV4-Client (`app/s3.py`, nur Standardbibliothek) statt boto3 (~90 MB) oder `minio` (urllib3, certifi,
  pycryptodome). Gegen die AWS-Testvektoren und einen MinIO getestet.
- Hochladen: wie bisher erst nach `daten/tmp` (Größe, Kontingent, Virenprüfung), dann gestreamt per PUT
  (`UNSIGNED-PAYLOAD`, also nur über https:// sicher), erst danach der Eintrag in der Datenbank. `daten/tmp` braucht weiter
  Platz für die größte Datei (500 MB). Ausliefern gestreamt durch die App (kein öffentliches Bucket, keine vorsignierten
  Links), Range bleibt. Whisper bekommt eine temporäre Kopie in `daten/tmp`.
- Aufbewahrung unverändert (Aufnahmen 90 Tage, Dateien wie bisher) – die App löscht die Objekte selbst; eine
  Lifecycle-Regel im Bucket ist nicht nötig (höchstens als Netz: Objekte älter als 120 Tage löschen).
- Rechte des Schlüssels: nur dieses Bucket, `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket`.
- Backup: Objekte sind nicht mehr im nächtlichen Home-Backup – Versionierung/Replikation beim Anbieter einschalten.
- `/api/gesund?tief=1` prüft das Bucket (`objektspeicher`).

Umzug (erst Probe, dann kopieren, Dienst läuft weiter):

```sh
docker compose run --rm --no-deps app python -m app.speicher_migrieren --trocken   # zeigt, was kopiert würde
docker compose run --rm --no-deps app python -m app.speicher_migrieren             # kopiert (idempotent, prüft Größe)
# AGORA_S3_* in app.env eintragen, dann:
docker compose up -d app
docker compose run --rm --no-deps app python -m app.speicher_migrieren             # Nachzügler seit der ersten Kopie
docker compose run --rm --no-deps app python -m app.speicher_migrieren --loeschen  # lokale Kopien entfernen (erst nach Prüfung)
```

Der erste Lauf vor dem Umschalten braucht die `AGORA_S3_*`-Werte schon in der Umgebung
(`docker compose run -e AGORA_S3_ENDPOINT=… …` oder `app.env` vorher ergänzen und die App erst danach neu starten).
Zurück: `AGORA_S3_*` entfernen – Objekte müssen dann von Hand zurück nach `daten/` (gleiche Schlüssel = gleiche Pfade).
Test: `sh tests/speicher/lauf.sh` (Wegwerf-MinIO auf 127.0.0.1:9351/9352, Daten im tmpfs).

## Zwei App-Worker mit Redis – optional, standardmäßig aus

Standard ist ein uvicorn-Worker; Begrenzer und Präsenz liegen dann im Arbeitsspeicher (wie bisher). Mit
`AGORA_REDIS_URL` teilen sich alle Worker diese Zustände:

- **Begrenzer** (`kern.grenze`, alle Login-/Passcode-/API-Grenzen): gleitendes Fenster als Sorted Set, atomar in einem
  Lua-Skript mit der Uhr des Redis-Servers. Schlüssel nur als SHA-256 (`agora:g:…`, keine IPs/E-Mail-Adressen im Klartext),
  sie laufen mit dem Fenster ab.
- **Präsenz** (`agora:p:konten`, `agora:p:meeting`, Hashes der Konto-IDs bzw. Teilnahmen, 1 h): „Gerade aktiv“ in der
  Admin-Übersicht und `agora_online{bereich}` in `/api/metriken`.
- **Hintergrund-Aufgaben** (Aufräumen, Erinnerungen, Egress-Abgleich, Sicherung, Webhooks, Status-Prüfung) laufen nur
  im Worker, der `daten/hintergrund.lock` (flock) hält; stirbt er, übernimmt ein anderer. Dafür braucht es kein Redis.
  Das Schema legt beim Start immer nur ein Worker an (`daten/schema.lock`).
- **Redis weg** (Zeitlimit 2 s): Begrenzer und Präsenz fallen je Worker auf den Arbeitsspeicher zurück (Zähler
  `agora_redis_fehler_total`, eine Logzeile je Minute) – Anfragen hängen nicht, die Grenzen gelten dann je Worker.
- Weiter je Worker (bewusst): Zähler/Histogramme in `/api/metriken` (Prometheus sieht den Worker, den es gerade trifft –
  für Summen alle Worker abfragen oder bei einem bleiben), der 30-s-Zwischenspeicher der Status-Seite, der
  Whiteboard-Zwischenspeicher (wird über die Sequenz in der Datenbank geprüft) und der Passwort-Cache der Freigabe-Links.
- Mehrere Rechner gehen nicht: alle Worker brauchen dasselbe `daten/` (SQLite, Aufnahmen bzw. tmp).

Einschalten (der `redis`-Dienst ist derselbe wie für Egress – ohne Persistenz, `maxmemory 64mb`, nur 127.0.0.1:6390):

1. `livekit.env`: `REDIS_PASSWORD=<openssl rand -hex 24>` (falls noch nicht da – Redis liest es von dort).
2. `app.env`: `AGORA_REDIS_URL=redis://:<dasselbe Passwort>@127.0.0.1:6390/1` (Datenbank 1; LiveKit/Egress nutzen 0).
3. `docker-compose.override.yml` (nicht im Repo) für zwei Worker und etwas mehr Speicher:
   ```yaml
   services:
     app:
       command: ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8110", "--workers", "2",
                 "--no-access-log", "--no-server-header", "--timeout-keep-alive", "15"]
       mem_limit: 1g
       depends_on: [livekit, redis]
   ```
   (`depends_on: redis` nur, wenn der Dienst auch gestartet wird – sonst die Zeile weglassen.)
4. `docker compose --profile redis up -d redis && docker compose up -d app`
5. Prüfen: `docker logs agora-app-1 | grep -c "Application startup complete"` → 2; `curl -s http://127.0.0.1:8110/api/gesund?tief=1`
   (Hintergrund ok); `docker exec agora-redis-1 sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning -n 1 dbsize'` > 0
   nach ein paar Anmeldungen.

Zurück auf einen Worker: Override entfernen, `AGORA_REDIS_URL` aus `app.env`, `docker compose up -d app`
(Redis kann laufen bleiben, wenn Egress es braucht). Getestet: `sh tests/geteilt/lauf.sh` (Wegwerf-Redis auf
127.0.0.1:6391) – darin zwei echte uvicorn-Worker, die zusammen genau 30 Fehlerberichte je IP annehmen
(ohne Redis nahmen sie in der Gegenprobe 57 von 80 an).

## Mehrere LiveKit-Knoten – optional, standardmäßig aus

Hintergrund und Grenzen: [ADR 0007](adr/0007-skalierung.md). Kurz: weitere Server tragen **weitere Meetings**; ein
Meeting liegt immer ganz auf einem Knoten (open-source LiveKit hat kein Cascading). Die App legt neue Meetings in der
Region des ersten Beitritts an (`CF-IPCountry` → Region) und weicht auf andere Knoten aus, wenn einer nicht antwortet.

**Getestet (27.11.2026, `sh tests/skalierung/lauf.sh`, zwei Wegwerf-Knoten auf diesem Rechner):** 4 Meetings mit je zwei
Chromium-Gästen → 2 auf Knoten A, 2 auf B (Redis `room_node_map`, Prometheus `livekit_room_total` je Knoten und die
ICE-Kandidaten im Browser stimmen überein), Video läuft in allen; DE → Knoten A (eu-central), FR → Knoten B (eu-west),
ein Gast aus DE im FR-Meeting signalisiert über A und bekommt die Medien von B; nach `docker kill` von A läuft das
Meeting auf B weiter, neue Meetings und ein Wiederbeitritt in ein Meeting von A landen auf B, `/api/gesund?tief=1` bleibt
grün und meldet A als ausgefallen. Nicht getestet: echter zweiter Server, Egress mit mehreren Knoten, Ausfall von Redis.

**Aufbau mit einem zweiten Hetzner-Server** (Beispiel: Haupt-Server Falkenstein `eu-central`, neu Helsinki `eu-north`):

1. **Privates Netz:** In der Hetzner Cloud ein Netzwerk (z. B. `10.0.0.0/24`, Zone eu-central – umfasst fsn1, nbg1, hel1)
   anlegen und beide Server anhängen: Haupt-Server `10.0.0.2`, neuer Knoten `10.0.0.3`. Redis und LiveKit-API laufen nur
   darüber, nie öffentlich.
2. **Redis auf dem Haupt-Server** zusätzlich an die private IP binden (`docker-compose.override.yml`, Dienst `redis`:
   `--bind 127.0.0.1 10.0.0.2`) und nur für den Knoten öffnen:
   `sudo ufw allow from 10.0.0.3 to any port 6390 proto tcp comment 'Agora Redis für LiveKit-Knoten'`.
3. **LiveKit-Konfiguration:** auf beiden Servern `livekit-mehrknoten.yaml` statt `livekit.yaml` einbinden (Override:
   `volumes: ["./livekit-mehrknoten.yaml:/etc/livekit.yaml:ro"]`). Regionen und `node_selector` müssen überall gleich
   sein. Auf dem neuen Knoten `bind_addresses: ["127.0.0.1", "10.0.0.3"]`, damit die App die API erreicht.
4. **`livekit.env` je Knoten** (0600): `LIVEKIT_KEYS` überall **derselbe** Schlüssel (die App signiert die Token, jeder
   Knoten muss sie prüfen können), `REDIS_HOST=10.0.0.2:6390` (auf dem Haupt-Server `127.0.0.1:6390`) und dasselbe
   `REDIS_PASSWORD`, `NODE_IP=<öffentliche IPv4 dieses Servers>` (die Medien gehen direkt dorthin),
   `LIVEKIT_REGION=eu-central` bzw. `eu-north`.
5. **Firewall des neuen Knotens:** öffentlich `7882/udp` und `7881/tcp` (wie oben), privat
   `sudo ufw allow from 10.0.0.2 to any port 7880 proto tcp` (API für die App) und, falls Prometheus vom Haupt-Server
   abfragt, `6789/tcp` ebenso nur von `10.0.0.2`. Bei der Hetzner-Firewall dieselben Regeln.
6. **Signalisierung:** auf dem neuen Knoten nur `livekit` + `cloudflared` (gehärtet wie in `docker-compose.yml`) mit
   einem **eigenen Tunnel** und Hostnamen, z. B. `rtc-hel.agora.slxt.dev` → `http://127.0.0.1:7880`. Clients können
   jeden Knoten ansprechen; LiveKit leitet über Redis zum Knoten des Raums.
7. **App** (`app.env`, danach `docker compose up -d app`):
   `AGORA_LIVEKIT_KNOTEN=eu-central wss://agora.slxt.dev http://127.0.0.1:7880, eu-north wss://rtc-hel.agora.slxt.dev http://10.0.0.3:7880`
   und `AGORA_LIVEKIT_LAENDER=DE:eu-central,AT:eu-central,CH:eu-central,FI:eu-north,SE:eu-north,NO:eu-north,…`.
   Der erste Knoten ist der Standard (kein oder unbekanntes Land). Die CSP (`connect-src`) enthält automatisch alle
   Signal-URLs. `AGORA_LIVEKIT_URL`/`LIVEKIT_API_HOST` gelten dann nicht mehr.
8. **Egress** bleibt auf dem Haupt-Server; er hängt am selben Redis und nimmt Aufträge für Räume auf jedem Knoten an
   (verbindet sich mit seinem lokalen Knoten, LiveKit leitet weiter).
9. **Prüfen:** `docker exec agora-livekit-1 /livekit-server --config /etc/livekit.yaml list-nodes` zeigt beide Knoten
   mit Region; `curl -s http://127.0.0.1:8110/api/gesund?tief=1 | jq .pruefungen.livekit.knoten`; `sh scripts/waechter.sh`
   warnt („LiveKit-Knoten nicht erreichbar: …“), wenn einer fehlt; `agora_livekit_knoten_erreichbar` in `/api/metriken`.

**Verhalten bei Ausfall eines Knotens:** Meetings auf dem Knoten brechen ab; wer neu beitritt, landet auf einem anderen
Knoten (≈ 5–8 s nach einem harten Ausfall, so lange hält LiveKit den Knoten noch für verfügbar). Die App fragt den
ausgefallenen Knoten 15 s lang nicht mehr zuerst. Geplantes Abschalten: `docker compose stop livekit` – der Knoten meldet
sich ab und nimmt keine neuen Räume an. Zurück auf einen Knoten: `AGORA_LIVEKIT_KNOTEN` entfernen, App neu starten.

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
- Push-Abos (Adresse beim Push-Dienst, Schlüssel des Browsers, Gerätename, Zeitpunkt des Einschaltens und der letzten
  Zustellung) und die gewählten Ereignisse: bis man sie im Profil ausschaltet, der Dienst die Adresse für ungültig
  erklärt (404/410), nach 10 Fehlschlägen in Folge oder mit dem Konto. Inhalte von Benachrichtigungen werden nicht gespeichert.
- Dolmetscher-Kanäle: Sprachliste am Raum, bis Host/Co-Host sie ändern (oder der Raum gelöscht wird); Zuweisungen
  (identity, Name, Sprachpaar, Kanal, auf Sendung) nur für das laufende Meeting, beim Ende gelöscht. Ton wird nicht gespeichert.
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
- Übersetzung der Untertitel: nur im Browser der Person, die sie einschaltet (Translator-API, Modell vom Browser-Hersteller,
  Text bleibt auf dem Gerät); der Server speichert dafür nichts, er reicht nur die Sprache der Erkennung (`sprache`) mit.
- Zusammenfassungen/Kapitel/Aufgaben (Ergebnis, Art, Modell, wer es ausgelöst hat): neben dem Transkript – weg, wenn es
  gelöscht wird oder nach 90 Tagen abläuft, mit der Aufnahme bzw. dem Raum; ein neues Whisper-Transkript löscht die alte.
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
- Einbetten: je Raum nur „an/aus“ und die Liste der erlaubten Herkünfte, bis der Besitzer sie ändert oder den Raum löscht.
- Organisationen (Name, Kurzname, Farbe, Einstellungen, Mitglieder mit Rolle und Beitrittszeit): bis ein owner sie löscht.
  Einladungen (Adresse, Rolle, wer eingeladen hat, Link nur als Hash): bis zum Annehmen/Ablehnen/Zurückziehen, höchstens
  7 Tage. Räume, Vorlagen und Aufnahmen der Organisation folgen den üblichen Fristen (Aufnahmen 90 Tage) und verschwinden
  mit der Organisation. Austritt/Entfernen/Konto löschen: Mitgliedschaft und Schlüssel der Organisation weg, Räume/Vorlagen/
  Aufnahmen/Meeting-Verlauf der Organisation gehen an einen owner über; war es das letzte Mitglied, wird die Organisation
  gelöscht. Mitglieder sehen Namen und Rollen der anderen, E-Mail-Adressen nur owner/admin.
- Status-Seite: Prüfergebnisse je Dienst (app, medien, mail) nur als Zähler je 5-Minuten-Fenster (Proben, davon ok,
  Summe/Maximum der Antwortzeit – keine IPs, keine Fehlertexte): 90 Tage. Meldungen der Admins (Titel, Text, Zeiten, wer
  sie angelegt hat): öffentlich, solange sie laufen/geplant sind und 14 Tage danach; gelöscht 365 Tage nach dem Ende.
- Fehler-Monitoring (Nachricht, Stack, Seite – gesäubert: ohne Query/Fragment, also nie der E2EE-Schlüssel, ohne
  E-Mail-Adressen, Meeting-Codes, Token, IPs –, Anzahl, Zeiten, Versionen, Browser-Familie): 30 Tage nach dem letzten
  Auftreten; Admins können Gruppen vorher löschen. User-Agent, IP und Konto der meldenden Person werden nicht gespeichert.
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
