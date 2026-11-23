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

- `app.env`: `SMTP_*`, `AGORA_GEHEIM`, `AGORA_SEITE`, `AGORA_LIVEKIT_URL` (`wss://HOST`), `LIVEKIT_API_HOST` (`http://127.0.0.1:7880`), `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `livekit.env`: `LIVEKIT_KEYS=<key>: <secret>` und `NODE_IP=<öffentliche IP>`
- `.env`: nur `TUNNEL_TOKEN` (von `scripts/cloudflare-einrichten.py`)

## Ausrollen

```sh
docker compose up -d --build app             # nach Codeänderungen
docker compose up -d --force-recreate livekit  # nach Änderungen an livekit.yaml
```

Prüfen: `curl -s http://127.0.0.1:8110/api/gesund`. `https://HOST/rtc/validate` muss 401 liefern; das zeigt, dass LiveKit über den Tunnel antwortet.

## Aufbewahrung

- Nicht bestätigte Konten: 48 h
- Sitzungen: 30 Tage
- Bestätigungslinks: 48 h
- Passwortlinks: 1 h
- Warteraum-Anfragen: 30 min
- Sicherheitsprotokoll ohne Inhalte: 30 Tage
- Chat und Umfragen: Werden beim Beenden gelöscht, außer der Raum hat „Chat speichern“.
- Breakout-Räume (Zuteilung, wer wo ist): werden beim Beenden des Meetings gelöscht.
- Aufnahmen: 90 Tage
- Erinnerungen: Einstellung im Profil, bis man sie ausschaltet (oder mit dem Konto). Adressen von Eingeladenen nur, wenn der Host „Erinnerung schicken“ ankreuzt – gelöscht nach dem Ende des Termins, zu dem eingeladen wurde (spätestens mit dem Meeting, Sicherheitsnetz 400 Tage). Versand-Merker (nur Hash aus Raum, Termin, Empfänger): 2 Tage.
- Meeting-Vorlagen: bis der Besitzer sie löscht (oder mit dem Konto); enthalten keine Passcodes, nur „Passcode an/aus“. Im Datenexport enthalten.
- Kalender-Abo: Nur wer es im Profil einschaltet. Gespeichert werden Salz, Hash des Links, Zeitpunkt des Einschaltens und des letzten Abrufs (keine IP). Weg beim Ausschalten, bei „Neuer Link“ (ersetzt) und mit dem Konto.
- Ein Meeting endet 10 min, nachdem die letzte Person gegangen ist.

## Updates

LiveKit-, Node- und Python-Images sind per Digest festgelegt. Für ein Update Tag und Digest bewusst ändern, `sh tests/e2e/lauf.sh` laufen lassen und danach ausrollen.
