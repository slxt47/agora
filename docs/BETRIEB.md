# Betrieb

## Dienste

| Dienst | Lauscht auf | Aufgabe |
|---|---|---|
| `app` | 127.0.0.1:8110 | Webseite und API, SQLite und Aufnahmen in `./daten` |
| `livekit` | 127.0.0.1:7880 (Signalisierung), öffentlich UDP 7882 und TCP 7881 | SFU für Video und Ton |
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

## Kapazität

Lasttest und Netzsimulation: [`docs/LASTTEST.md`](LASTTEST.md) (`sh scripts/lasttest.sh`, nur gegen Wegwerf-Instanzen).
Kurz: CPU reicht für 2–3 Meetings mit je 25 Personen; LiveKit brauchte bei 50 + 50 bis zu 838 MiB – für Meetings über
~35 Personen `mem_limit` von `livekit` auf 2g erhöhen. Der erste echte Engpass ist die Bandbreite (~5 Mbit/s je Person in der Galerie).

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
- Breakout-Räume (Zuteilung, wer wo ist): werden beim Beenden des Meetings gelöscht.
- Verbindungsqualität: Werte je Teilnahme werden beim Beenden gelöscht; der Schnitt je Meeting (ohne IPs und Namen) bleibt, solange der Raum besteht.
- Rückmeldungen nach dem Meeting (Sterne, Probleme, Text, eigener Verbindungsschnitt): solange der Raum besteht; die gespeicherte identity dient nur gegen doppelte Bewertungen und wird nie angezeigt.
- Aufnahmen: 90 Tage
- Dauer-Chat (persönlicher Raum, nur wenn der Besitzer ihn einschaltet): öffentliche Nachrichten bleiben, bis der Besitzer ihn leert oder ausschaltet (oder mit dem Konto), höchstens die neuesten 1000 Nachrichten.
- Meeting-Verlauf (Titel, Code, Beginn, Ende, Spitzenzahl, Zahl der Beitritte – keine Namen, keine IPs): 90 Tage, einzeln löschbar, mit dem Konto weg. Wird der Raum gelöscht, bleibt der Eintrag ohne Chat-Verweis.
- Erinnerungen: Einstellung im Profil, bis man sie ausschaltet (oder mit dem Konto). Adressen von Eingeladenen nur, wenn der Host „Erinnerung schicken“ ankreuzt – gelöscht nach dem Ende des Termins, zu dem eingeladen wurde (spätestens mit dem Meeting, Sicherheitsnetz 400 Tage). Versand-Merker (nur Hash aus Raum, Termin, Empfänger): 2 Tage.
- Meeting-Vorlagen: bis der Besitzer sie löscht (oder mit dem Konto); enthalten keine Passcodes, nur „Passcode an/aus“. Im Datenexport enthalten.
- Kalender-Abo: Nur wer es im Profil einschaltet. Gespeichert werden Salz, Hash des Links, Zeitpunkt des Einschaltens und des letzten Abrufs (keine IP). Weg beim Ausschalten, bei „Neuer Link“ (ersetzt) und mit dem Konto.
- Meldungen: 365 Tage nach „erledigt“; blockierte Konten bis zum Aufheben oder Löschen eines der Konten
- Ein Meeting endet 10 min, nachdem die letzte Person gegangen ist.

## Updates

LiveKit-, Node- und Python-Images sind per Digest festgelegt. Für ein Update Tag und Digest bewusst ändern, `sh tests/e2e/lauf.sh` laufen lassen und danach ausrollen.
