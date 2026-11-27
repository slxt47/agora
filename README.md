# Agora

Videokonferenzen direkt im Browser, ohne Installation. Selbst gehostet, im slxt-Design, ohne Werbung und ohne Tracking.

**Live:** https://agora.slxt.dev

## Funktionen

- **Konten:** Registrierung mit E-Mail-Bestätigung, Passwort-Reset per Link, Profil (Name, Zeitzone, Sprache, Avatar-Farbe), Datenexport und Konto löschen. Gäste können ohne Konto beitreten.
- **Zwei-Faktor-Anmeldung (TOTP):** freiwillig im Profil; QR-Code wird im Browser gezeichnet, 10 Wiederherstellungscodes
  (nur einmal sichtbar, gespeichert nur als Hash), ±30 s Toleranz, jeder Code nur einmal, Geheimnis verschlüsselt gespeichert.
- **Sicherheit im Konto:** eigene Sicherheitsereignisse der letzten 30 Tage (Anmeldungen, Fehlversuche, Passwort- und
  Zwei-Faktor-Änderungen, Maßnahmen des Agora-Teams).
- **Meetings:**
  - Sofort-Meeting, Code `abc-defg-hij` oder Link, Passcode
  - Warteraum mit Zulassen und Ablehnen, Meeting sperren
  - Planen mit Zeitzone, auch täglich oder wöchentlich wiederkehrend
  - persönlicher Dauer-Raum, optional mit Dauer-Chat (öffentliche Nachrichten bleiben über Meetings hinweg, Besitzer kann leeren)
  - Meeting-Verlauf im Konto: vergangene Meetings mit Datum, Dauer, Spitzenzahl gleichzeitig Verbundener und (bei gespeichertem Chat) Chat und Umfragen
  - Vorlagen: Titel, Dauer, Passcode an/aus, Warteraum, Chat speichern, Beschreibung, Standard-Rechte und „alle stumm beim Beitritt“
    speichern und daraus sofort starten oder planen
  - Einladung per E-Mail mit `.ics`
  - Erinnerungs-Mails (opt-in): für eigene Termine 10/15/30/60 min vorher, für Eingeladene nur mit Haken „Erinnerung schicken“
  - Push-Benachrichtigungen (Web Push, opt-in je Gerät im Profil): Erinnerung vor eigenen Terminen, „Jemand wartet im
    Warteraum“ (nur wenn weder Host noch Co-Host im Meeting ist), Einladungen, Server-Aufnahme/Transkript fertig. Eigene
    kleine RFC-8291/8292-Umsetzung auf `cryptography` (Inhalt für das Gerät verschlüsselt, VAPID-Schlüssel selbst erzeugt);
    der Service Worker zeigt die Benachrichtigung und öffnet die passende Seite. iPhone/iPad: nur als installierte App (ab iOS 16.4).
  - Kalender ohne OAuth: „Zu Google Kalender“, „Zu Outlook/Office 365“, „Zu Outlook.com“ (mit Join-Link, Google auch mit Wiederholung) und `.ics`;
    privates Kalender-Abo (iCal-Feed mit geheimem Link, neu erzeugbar) für Apple, Google und Outlook
- **Audio und Video:**
  - LiveKit-SFU mit Simulcast, Adaptive Stream und Dynacast
  - Vorschau vor dem Beitritt mit Geräteauswahl, Pegelanzeige und Lautsprechertest
  - Gerätewechsel im laufenden Meeting
  - Verbindungsqualität, Datensparmodus
  - Verbindungsstatistik: Der Browser misst alle 10 s (WebRTC `getStats`) Laufzeit, Paketverlust, Jitter, Bitraten,
    Verbindungsweg (UDP/TCP/Relay) und Auflösung. Popover „Verbindung“ an der eigenen Kachel, Warnung mit Tipps bei
    dauerhaft schlechter Verbindung; Host und Co-Hosts sehen die Qualität aller in der Teilnehmerliste.
    Der Server speichert nur einen Schnitt je Meeting (ohne IPs, ohne Namen).
  - Rückmeldung am Ende: „Wie war die Qualität?“ mit 1–5 Sternen, Problemen zum Ankreuzen und Text; im Konto sieht
    der Besitzer je Raum und Meeting den Schnitt, die Verbindungswerte und die Hinweise (ohne Namen).
  - Video-Effekte in Lobby und Meeting: Hintergrund leicht oder stark unscharf, sechs eigene Hintergründe im slxt-Stil,
    eigenes Bild (bleibt im Browser, wird nie hochgeladen), „Aussehen verbessern“ (milder Helligkeits-/Kontrastfilter).
    Läuft mit `@livekit/track-processors` und MediaPipe komplett im Browser; WASM und Modell liegen unter `/vendor/`,
    nichts kommt von einem CDN. Im Datensparmodus pausiert, auf zu langsamen Geräten schaltet es sich selbst ab.
  - Audio-Modi in Lobby und Meeting (je Gerät gemerkt): **Originalton (Musik)** ohne Echo-/Rauschunterdrückung und
    automatische Lautstärke, stereo, Opus mit 128 kbit/s (`musicHighQualityStereo`), ohne DTX/RED – für Musik und
    Instrumente (mit Kopfhörern). **Rauschunterdrückung** „Browser“ (Standard), „Stark“ (KI-Modell RNNoise als WASM im
    AudioWorklet, als LiveKit-TrackProcessor, `@sapphi-red/web-noise-suppressor` 0.4.1 – MIT, darin RNNoise BSD-3 und
    `@shiguredo/rnnoise-wasm` Apache-2.0) oder „Aus“. Dateien selbst gehostet unter `/vendor/rnnoise-<version>/`,
    geladen erst bei Bedarf.
- **Oberfläche:**
  - Galerie und Sprecheransicht, Anheften und Spotlight, Seiten bei vielen Teilnehmenden
  - Vollbild, Bild-in-Bild
  - Hell und Dunkel, Tastenkürzel, für Handy, Tablet und Desktop
  - Deutsch und Englisch: Schalter DE/EN in der Kopfzeile (im Browser gemerkt), für Angemeldete die Sprache aus dem Profil,
    sonst die Browsersprache. Nur das aktive Wörterbuch wird geladen; Datum/Uhrzeit per `Intl`. Fehlermeldungen der API und
    Mails ebenfalls zweisprachig; Impressum/Datenschutz bleiben deutsch (Hinweis „(German)“). Siehe [`docs/adr/0006-sprachen.md`](docs/adr/0006-sprachen.md).
- **Browser:** Chrome/Edge, Firefox und Safari; automatisch geprüft mit Chromium, Firefox und WebKit – Ergebnis je
  Funktion und was nur von Hand geht (iOS, echte Kamera, Safari-E2EE): [`docs/BROWSER.md`](docs/BROWSER.md).
- **Desktop-App** (`desktop/`, Electron): lädt Agora (Adresse einstellbar), systemweite Tastenkürzel für Mikro, Kamera und
  Hand (einstellbar), Tray, Deep Links `agora://r/<code>`, eigene Bildschirmauswahl, strikte Navigation und Rechte, kein
  Auto-Update. Pakete für Linux/Windows/macOS (unsigniert) über GitHub Actions: [`docs/DESKTOP.md`](docs/DESKTOP.md).
- **App (PWA) und Mobil:**
  - **Android-App** als Trusted Web Activity (`mobil/android/`, mit Bubblewrap erzeugt; Debug-APK per GitHub Actions bei
    Tag `android-v*`), Digital Asset Links über `AGORA_ANDROID_SHA256`; iOS über die PWA – siehe [`docs/MOBIL.md`](docs/MOBIL.md)
  - installierbar (Knopf auf der Startseite, auf iPhone/iPad Anleitung „Zum Home-Bildschirm“), Verknüpfungen „Neues Meeting“ und „Beitreten“
  - Service Worker cacht nur App-Hülle und statische Dateien (versioniert; nie `/api`, `/rtc`, Medien oder MediaPipe),
    ohne Netz erscheint „Keine Verbindung“; eine neue Version übernimmt erst nach „Neu laden“ (nie mitten im Meeting)
  - im Meeting: Bildschirm bleibt an (Wake Lock), Drehen ordnet neu, nach dem Zurückkommen aus dem Hintergrund (iOS)
    werden Kamera/Mikro und Ton wieder aufgenommen, Safe Areas, Bedienelemente mindestens 44 px
- **Webinar:** Raum als Webinar: nur Host, Co-Hosts und Panelisten (eigener Einladungslink) senden, Zuschauer sehen
  und hören zu (LiveKit-Token ohne Senderecht), Zuschauerzahl fürs Panel, „Aufs Podium holen“ und zurück, Chat der
  Zuschauer an alle / nur ans Panel / aus. Optional mit **Anmeldung** auf `/w/<code>`: Name, E-Mail, bis zu 5 Zusatzfragen,
  Kapazitätsgrenze, Bestätigungsmail mit persönlichem Beitrittslink (und .ics), Liste + CSV-Export für den Besitzer;
  Anmeldedaten 30 Tage nach dem Webinar gelöscht.
- **Ende-zu-Ende-Verschlüsselung (optional je Meeting):** Bild, Ton und Bildschirm (und Reaktionen/Anmerkungen zwischen
  den Browsern) verschlüsselt mit LiveKits E2EE (Insertable Streams, in Safari `RTCRtpScriptTransform`), Worker selbst
  gehostet. Der Schlüssel steckt nur im Link-Fragment `#k=…` – der Server kennt ihn nie, er speichert nur einen Prüfwert,
  mit dem die Lobby einen falschen Schlüssel sofort meldet. Schloss-Anzeige „Ende-zu-Ende verschlüsselt“, verständliche
  Meldung bei Entschlüsselungsfehlern. Grenzen (Chat/Umfragen über den Server, Aufnahme nur lokal, Breakouts mit
  demselben Schlüssel, keine Server-Aufzeichnung): [`docs/adr/0005-e2ee.md`](docs/adr/0005-e2ee.md).
- **Bildschirmfreigabe:** Bildschirm, Fenster oder Tab, mit Systemton, wo der Browser es kann. Standardmäßig nur eine Person gleichzeitig.
- **Live-Untertitel:** Wer will, lässt die eigene Sprache im Browser erkennen (Web Speech API – nur nach ausdrücklicher
  Zustimmung, weil Chrome/Edge den Ton an Google/Microsoft schicken; nie automatisch, nur bei offenem Mikrofon). Der Text
  geht über den Server an alle; jede Person blendet die Untertitel mit Namen selbst ein. Der Besitzer kann „Transkript
  speichern“ einschalten (alle sehen einen Hinweis) und lädt es als .txt/.vtt (Ende-Bildschirm oder Konto, 90 Tage).
  Optional „Transkript aus Aufnahme erstellen“ über einen Whisper-Dienst (`AGORA_WHISPER_URL`).
  **Zusammenfassung & Kapitel:** zu gespeicherten Transkripten (und Whisper-Transkripten von Aufnahmen) erstellt der Besitzer
  auf Knopfdruck Kapitelmarken, Zusammenfassung und Aufgaben – mit KI nur, wenn ein OpenAI-kompatibles Modell eingerichtet
  ist (`AGORA_KI_URL`, `AGORA_KI_MODELL`) und nach einem Hinweis, wohin das Transkript geht; ohne KI immer als
  „Stichwort-Kapitel“ (Pausen, Sprecherwechsel, häufige Wörter – so beschriftet). Kapitel als WebVTT im Spieler.
  **Untertitel übersetzen:** jede Person wählt für sich eine Zielsprache; übersetzt wird nur auf dem eigenen Gerät mit der
  eingebauten Translator-API des Browsers (derzeit Chrome am Computer, ab 138). Agora schickt Untertitel nie an einen
  Übersetzungsdienst – ohne die API gibt es einen Hinweis und den Originaltext.
- **Anmerkungen auf geteilten Bildschirmen:** Stift, Textmarker, Pfeil, Text, Radierer, sechs Farben, eigene oder (Host,
  Co-Host, teilende Person) alle löschen. Die Ebene skaliert mit dem Videobild, auch im Vollbild; Werkzeugleiste mit
  Pfeiltasten bedienbar, Esc beendet. Host, Co-Hosts und die teilende Person können Anmerkungen für andere sperren
  (Recht vom Server in den Raum-Metadaten). Die teilende Person sieht die Anmerkungen auf einer Vorschau ihres Bildschirms.
  Striche laufen direkt zwischen den Browsern (normiert, begrenzt) und werden nicht gespeichert.
- **Dolmetscher-Kanäle (Simultanübersetzung):** Host/Co-Host legen Sprachen fest und machen Teilnehmende zu Dolmetschern
  mit Sprachpaar (auch Webinar-Zuschauer – das Mikrofon-Recht gibt nur der Server per LiveKit-UpdateParticipant).
  Dolmetscher senden eine eigene Spur `dolmetsch:<sprache>` über ihr Pult (Kanal wechseln, starten/stoppen, Übergabe
  zwischen zwei Dolmetschern desselben Paares); Zuhörende wählen einen Sprachkanal, hören genau diese Spur und den Saal
  leise (20 %). Welche Spur zu welchem Kanal gehört, steht nur in den Raum-Metadaten des Servers; fremde
  `dolmetsch:`-Spuren schaltet der Server stumm. Funktioniert auch im Webinar.
- **Zusammenarbeit:** Chat an alle oder privat (Verlauf optional speicherbar), Reaktionen, Hand heben.
- **Whiteboard:** gemeinsame Tafel über der Bühne – Stift, Linie, Pfeil, Rechteck, Ellipse, Text, Notizzettel, Radierer,
  Auswählen/Verschieben, Rückgängig/Wiederholen (je Person), Farben, Strichstärke, Zoom/Verschieben, Export als PNG. Änderungen gehen als
  Operationen über den Server (nummeriert, Spätere laden den Stand); Host/Co-Hosts sperren (nur ansehen), leeren und öffnen es bei allen.
  Eigener Canvas-Code ohne Bibliothek.
- **Notizen:** gemeinsamer Text je Meeting (Markdown light mit Vorschau), speichert automatisch, Export als `.md`. Gleichzeitige
  Änderungen werden zeilenweise zusammengeführt, bei Überschneidung wählt man die Fassung (ADR 0004).
- **Fragen (Q&A):** eigener Reiter neben dem Chat – Fragen stellen (auf Wunsch anonym, auch gegenüber dem Host), hochstimmen,
  nach Stimmen oder Zeit sortieren; Host/Co-Hosts markieren beantwortet (optional mit schriftlicher Antwort), heben hervor oder verwerfen.
- **Dateien teilen:** Reiter „Dateien“ – hochladen (auch per Ziehen), bis 25 MB je Datei und 200 MB je Meeting, Bildvorschau.
  Herunterladen nur für Teilnehmende dieses Meetings; ausgeliefert mit `nosniff` und CSP-Sandbox, gefährliche Typen nur als Anhang.
  Optional Virenprüfung über clamd (`AGORA_CLAMAV`), sonst Hinweis „nicht auf Viren geprüft“. Beim Ende gelöscht, mit „Chat speichern“ 30 Tage im Meeting-Verlauf.
- **Umfragen:** Host und Co-Hosts stellen Fragen mit 2–10 Antworten (Einfach- oder Mehrfachwahl, anonym oder namentlich), sehen das Ergebnis live und teilen es mit allen. Eine Stimme je Person, vom Server geprüft.
- **Breakout-Räume:** 2–50 Räume, Zuteilung von Hand (ziehen oder auswählen) oder automatisch gleichmäßig, optionaler Timer. Alle wechseln automatisch, können „Hilfe rufen“ und (wenn erlaubt) selbst zurück. Der Host besucht jeden Raum, schickt Nachrichten an alle Räume und schließt sie mit 60-Sekunden-Countdown.
- **Melden und Blockieren:** Jede Person im Meeting kann eine andere melden (Grund + optionaler Text); die Meldung geht an die
  Admins (`AGORA_ADMIN_MAILS`), der Host sieht einen Hinweis (ohne den Namen der meldenden Person). Angemeldete blockieren
  Konten: Blockierte betreten ihre Meetings nicht mehr und können ihnen nicht privat schreiben (Verwalten im Profil).
- **Teams / Organisationen** (Konto → „Teams“): Ein Konto legt Organisationen an (Name, Kurzname, Logo-Farbe) und lädt
  per E-Mail ein (Link 7 Tage, annehmen/ablehnen, nur mit genau dieser Adresse). Rollen owner/admin/mitglied. Räume der
  Organisation verwaltet jeder owner/admin (und ist darin Host), alle Mitglieder sehen die Meeting-Liste. Gemeinsame
  Vorlagen (Mitglieder starten daraus eigene Meetings), Aufnahmen-Bibliothek der Organisation mit eigenem Kontingent
  (Standard 20 GB, `AGORA_ORG_KONTINGENT_GB`), Einstellungen: Standard-Rechte neuer Räume, Warteraum für Externe,
  E-Mail-Domain für Selbst-Beitritt (nur die eigene, keine Freemail-Anbieter). API-Schlüssel können nur für eine
  Organisation gelten. Austritt/Entfernen: Eigenes bleibt beim Konto, was für die Organisation angelegt wurde, bleibt in
  ihr. Der Admin-Bereich listet die Organisationen, der Datenexport enthält die Mitgliedschaften.
- **Öffentliche Status-Seite** `/status` (ohne Anmeldung, Deutsch/Englisch): Zustand von App, Video/Ton (Medienserver)
  und E-Mail aus einer Prüfung jede Minute (E-Mail alle 5 Minuten), Verfügbarkeit 24 h / 7 Tage / 90 Tage als Balken,
  Antwortzeit; Störungs-, Wartungs- und Hinweis-Meldungen schreiben Admins in `/admin` (Reiter „Status“). Dieselben Werte
  für Überwachungsdienste als JSON unter `/api/status` – ohne Hostnamen, IPs, Ports oder Versionen.
- **Admin-Bereich** `/admin` (nur Konten aus `AGORA_ADMIN_MAILS`, für alle anderen 404): Übersicht (Konten, laufende Meetings
  und Teilnehmende live aus LiveKit, Meetings der letzten 7/30 Tage, Speicher der Aufnahmen), Konten suchen, sperren,
  2FA zurücksetzen, löschen, Meldungen bearbeiten, Protokoll mit Filter, laufende Meetings beenden. Jede Aktion wird protokolliert.
- **Optionaler Objektspeicher (S3-kompatibel):** Aufnahmen, Whisper-Transkripte und Dateien aus Meetings liegen
  standardmäßig unter `daten/`; mit `AGORA_S3_ENDPOINT/BUCKET/KEY/SECRET/REGION` in einem S3-Bucket (AWS, Hetzner,
  MinIO …). Eigener kleiner SigV4-Client ohne neue Abhängigkeit, Upload und Download gestreamt, Range bleibt, optional
  serverseitige Verschlüsselung; Migration mit `python -m app.speicher_migrieren`.
- **Fehler-Monitoring (selbst gehostet, statt Sentry):** JS-Fehler, unbehandelte Promises, unerwartete LiveKit-Trennungen
  und API-Antworten ≥ 500 aus dem Browser (`POST /api/fehlerbericht`) sowie Ausnahmen des Servers, gruppiert nach
  Fingerabdruck mit Anzahl, erstem/letztem Auftreten, Version und Browser-Familie. Admin-Reiter „Fehler“ (Details, erledigt;
  erledigte Gruppen öffnen sich bei neuem Auftreten wieder), Mail an die Admins bei neuer Gruppe (höchstens 1 je Gruppe und
  Tag, optional ntfy). Ohne Namen, E-Mail-Adressen, IPs; Query und Fragment (also auch der E2EE-Schlüssel `#k=`) werden nie
  gespeichert; 30 Tage.
- **Öffentliche API** (`/api/v1`): persönliche API-Schlüssel im Konto (Reiter „Entwickler“) mit Rechten
  `meetings:lesen`, `meetings:schreiben`, `aufnahmen:lesen`, `webhooks`, optionalem Ablauf und „zuletzt benutzt“; nur
  einmal sichtbar, gespeichert als Hash, jederzeit widerrufbar. Meetings (auch geplant/wiederkehrend) auflisten, anlegen,
  ändern, löschen, Beitrittslink, Teilnehmende des laufenden Meetings, Aufnahmen mit kurzlebigen signierten
  Download-Links. Bearer statt Cookie, kein CORS, 120 Anfragen/min je Schlüssel. Doku: [`docs/API-V1.md`](docs/API-V1.md),
  [`docs/openapi.yaml`](docs/openapi.yaml).
- **Webhooks:** bis zu 5 HTTPS-Endpunkte je Konto für `meeting.gestartet`, `meeting.beendet`, `teilnehmer.beigetreten`,
  `teilnehmer.gegangen`, `aufnahme.fertig`, `webinar.anmeldung`; JSON mit HMAC-SHA-256-Signatur (`X-Agora-Signatur`),
  Geheimnis nur einmal sichtbar, Zustellung im Hintergrund mit bis zu 5 Versuchen (exponentiell), Protokoll der letzten
  50 Versuche (14 Tage), „Test senden“, Abschalten mit Mail nach wiederholten Fehlschlägen. Nur öffentliche Ziele
  (DNS geprüft, IP fest, keine Weiterleitungen).
- **Einbetten:** Der Besitzer erlaubt je Raum das Einbetten auf eigenen Seiten und nennt die Herkünfte (Origins);
  `/e/<code>` bekommt `frame-ancestors` genau mit diesen Herkünften (alle anderen Seiten bleiben `'none'`). Das
  selbst gehostete `/embed.js` (ohne Abhängigkeiten) baut das iframe mit `allow="camera; microphone; display-capture"`
  und meldet `bereit`, `beigetreten`, `verlassen` per postMessage mit Herkunftsprüfung. Doku: [`docs/EINBETTEN.md`](docs/EINBETTEN.md).
- **Host-Kontrollen:**
  - Rollen Host, Co-Host, Teilnehmer und Gast
  - einzelne oder alle stummschalten, um Kamera bitten oder Kamera aus
  - entfernen (mit Sperre), Rechte für Chat, Bildschirm, Selbst-Unmute und Umbenennen
  - Host übertragen, für alle beenden
- **Aufzeichnung:**
  - Der Host zeichnet im Browser auf. Alle sehen einen Hinweis.
  - Die Aufnahme wird in die Bibliothek hochgeladen (500 MB je Datei, 5 GB je Konto, 90 Tage).
  - Freigabelinks mit Ablauf und optionalem Passwort
  - Optional (`AGORA_EGRESS=1` + Dienste `redis`/`egress`, Profil `egress`): **Aufnahme auf dem Server** (LiveKit Egress,
    Galerie oder Sprecher, MP4 direkt in die Bibliothek des Besitzers) und **Livestream** zu YouTube/Twitch (RTMP/RTMPS,
    Stream-Schlüssel nie im Log, nur verschlüsselt und nur während des Streams gespeichert). Alle sehen REC bzw. LIVE.

## Aufbau

| Teil | Technik |
|---|---|
| `app/` | FastAPI + SQLite, argon2, LiveKit-Server-API (`livekit-api`), Mails über SMTP |
| `web/` | Vite, Vanilla JS, `livekit-client`, `@livekit/track-processors` (MediaPipe-Dateien selbst gehostet, `scripts/mediapipe.mjs`), RNNoise (`scripts/rnnoise.mjs`), slxt-Design (`src/app.css`), Hell/Dunkel über `public/modus.js` |
| `livekit.yaml` | LiveKit-SFU: Signalisierung nur lokal, Medien über UDP 7882 und ICE-TCP 7881 |
| `docker-compose.yml` | `livekit`, `app` und `cloudflared`, alle im Host-Netz und gehärtet (read-only, cap_drop, no-new-privileges); `redis` + `egress` nur mit Profil `egress` |

Für das Monitoring gibt es `/api/metriken` (Prometheus), `/api/gesund?tief=1` und `scripts/waechter.sh` (eine Zeile Status) – nur lokal, siehe [`docs/BETRIEB.md`](docs/BETRIEB.md).

Die Webseite und die Signalisierung (`/rtc`) laufen über einen Cloudflare-Tunnel. Video und Ton gehen direkt an den Server, weil WebRTC über UDP nicht durch den Tunnel passt. Die Schnittstellen beschreibt [`docs/API.md`](docs/API.md), den Betrieb [`docs/BETRIEB.md`](docs/BETRIEB.md).

## Starten

```sh
cp docs/app.env.beispiel app.env          # SMTP, AGORA_GEHEIM, LiveKit-Schlüssel eintragen (0600)
cp docs/livekit.env.beispiel livekit.env  # LIVEKIT_KEYS + NODE_IP (öffentliche IP)
python3 scripts/cloudflare-einrichten.py agora.example.org   # Tunnel, Route, DNS → .env
docker compose up -d --build
```

## Tests

```sh
docker build --target test -t agora:test . && docker run --rm agora:test        # 41+ API-Tests
docker run --rm -v "$PWD/web:/web" -w /web node:22.23.5-alpine sh -c "npm ci --ignore-scripts && npm test"
sh tests/e2e/lauf.sh       # Browser-E2E: echte LiveKit, zwei Browser mit Kamera-Attrappe
BROWSER=firefox sh tests/e2e/lauf.sh   # Browser-Matrix (firefox|webkit|chromium), Ergebnis in docs/BROWSER.md
sh desktop/scripts/docker-bauen.sh     # Desktop-App: Tests + AppImage/deb (arm64, x64); Smoke-Test: desktop/scripts/docker-smoke.sh
sh tests/egress/lauf.sh    # Server-Aufnahme + Livestream gegen eigenen Wegwerf-Stapel (Redis, LiveKit, Egress, App)
sh scripts/lasttest.sh     # Lasttest (lk load-test) + Netzsimulation (tc/netem im eigenen Docker-Netz), Ergebnisse in docs/LASTTEST.md
```

Objektspeicher gegen einen Wegwerf-MinIO: `sh tests/speicher/lauf.sh` (ohne MinIO überspringt pytest diese Tests).

## Lizenz

MIT, siehe [LICENSE](LICENSE).
