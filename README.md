# Agora

Videokonferenzen direkt im Browser, ohne Installation. Selbst gehostet, im slxt-Design, ohne Werbung und ohne Tracking.

**Live:** https://agora.slxt.dev

## Funktionen

- **Konten:** Registrierung mit E-Mail-Bestätigung, Passwort-Reset per Link, Profil (Name, Zeitzone, Sprache, Avatar-Farbe), Datenexport und Konto löschen. Gäste können ohne Konto beitreten.
- **Meetings:**
  - Sofort-Meeting, Code `abc-defg-hij` oder Link, Passcode
  - Warteraum mit Zulassen und Ablehnen, Meeting sperren
  - Planen mit Zeitzone, auch täglich oder wöchentlich wiederkehrend
  - persönlicher Dauer-Raum
  - Einladung per E-Mail mit `.ics`
- **Audio und Video:**
  - LiveKit-SFU mit Simulcast, Adaptive Stream und Dynacast
  - Vorschau vor dem Beitritt mit Geräteauswahl, Pegelanzeige und Lautsprechertest
  - Gerätewechsel im laufenden Meeting
  - Verbindungsqualität, Datensparmodus
  - Video-Effekte in Lobby und Meeting: Hintergrund leicht oder stark unscharf, sechs eigene Hintergründe im slxt-Stil,
    eigenes Bild (bleibt im Browser, wird nie hochgeladen), „Aussehen verbessern“ (milder Helligkeits-/Kontrastfilter).
    Läuft mit `@livekit/track-processors` und MediaPipe komplett im Browser; WASM und Modell liegen unter `/vendor/`,
    nichts kommt von einem CDN. Im Datensparmodus pausiert, auf zu langsamen Geräten schaltet es sich selbst ab.
- **Oberfläche:**
  - Galerie und Sprecheransicht, Anheften und Spotlight, Seiten bei vielen Teilnehmenden
  - Vollbild, Bild-in-Bild
  - Hell und Dunkel, Tastenkürzel, für Handy, Tablet und Desktop
- **Bildschirmfreigabe:** Bildschirm, Fenster oder Tab, mit Systemton, wo der Browser es kann. Standardmäßig nur eine Person gleichzeitig.
- **Zusammenarbeit:** Chat an alle oder privat (Verlauf optional speicherbar), Reaktionen, Hand heben.
- **Host-Kontrollen:**
  - Rollen Host, Co-Host, Teilnehmer und Gast
  - einzelne oder alle stummschalten, um Kamera bitten oder Kamera aus
  - entfernen (mit Sperre), Rechte für Chat, Bildschirm, Selbst-Unmute und Umbenennen
  - Host übertragen, für alle beenden
- **Aufzeichnung:**
  - Der Host zeichnet im Browser auf. Alle sehen einen Hinweis.
  - Die Aufnahme wird in die Bibliothek hochgeladen (500 MB je Datei, 5 GB je Konto, 90 Tage).
  - Freigabelinks mit Ablauf und optionalem Passwort

## Aufbau

| Teil | Technik |
|---|---|
| `app/` | FastAPI + SQLite, argon2, LiveKit-Server-API (`livekit-api`), Mails über SMTP |
| `web/` | Vite, Vanilla JS, `livekit-client`, `@livekit/track-processors` (MediaPipe-Dateien selbst gehostet, `scripts/mediapipe.mjs`), slxt-Design (`src/app.css`), Hell/Dunkel über `public/modus.js` |
| `livekit.yaml` | LiveKit-SFU: Signalisierung nur lokal, Medien über UDP 7882 und ICE-TCP 7881 |
| `docker-compose.yml` | `livekit`, `app` und `cloudflared`, alle im Host-Netz und gehärtet (read-only, cap_drop, no-new-privileges) |

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
docker build --target test -t agora:test . && docker run --rm agora:test        # 32 API-Tests
docker run --rm -v "$PWD/web:/web" -w /web node:22.23.5-alpine sh -c "npm ci --ignore-scripts && npm test"
sh tests/e2e/lauf.sh       # Browser-E2E: echte LiveKit, zwei Browser mit Kamera-Attrappe
```

## Lizenz

MIT, siehe [LICENSE](LICENSE).
