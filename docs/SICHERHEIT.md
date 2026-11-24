# Sicherheit: Review und Penetrationstest (24.11.2026)

## Umfang

- **Code-Review (White-Box)** von `app/*.py`, `web/src/**` (Fokus auf sicherheitsrelevante Stellen), `docker-compose.yml`,
  `livekit.yaml`, `Dockerfile`, README, `docs/*.md`, ADRs.
- **Black-Box-Test** gegen eine **Wegwerf-Instanz** (App + echtes LiveKit im Testmodus, nur 127.0.0.1, Ports 8195/7790–7792).
  Produktion (agora.slxt.dev) wurde weder angegriffen noch gescannt noch verändert.

Geprüft: Authentifizierung/Autorisierung aller Endpunkte (Cookie-Sitzung, `X-Teilnahme`, API-Schlüssel `/api/v1`, Admin,
Webhooks, Einbetten), IDOR zwischen Konten und Räumen (Aufnahmen, Dateien, Transkripte, Verlauf, Vorlagen, Webhooks,
API-Schlüssel, Webinar-Anmeldungen, Umfragen, Q&A), CSRF (`X-Agora`, Ausnahme `/api/v1`), XSS (alle `innerHTML`-Stellen,
Markdown-Notizen, Chat-Links, Dateinamen, Namen, Titel, Admin-Ansicht), CSP und Kopfzeilen, Cookies, CORS, SSRF (Webhooks,
Livestream, Whisper), Pfad-Traversal und Range, Body-Größen/DoS, Begrenzer und Brute Force (Login, 2FA, Passcode,
Freigabe-Passwort, API-Schlüssel, Reset-Token), Timing (`hmac.compare_digest` bzw. Vergleich nur über Hashes),
Passwort-Reset/Bestätigung, Sitzungs-Fixierung, LiveKit-Token-Grants und was ein manipulierter Client damit wirklich
senden kann, Datenkanal-Vertrauen, E2EE-Schlüssel, Geheimnisse in Logs, SQL-Injection, offene Umleitungen (`?weiter=`),
Service Worker, `postMessage` beim Einbetten.

## Methode

1. Alle Routen mit ihrer ersten Prüfung (Sitzung / Teilnahme / Besitzer / Rolle / Bearer) aufgelistet und einzeln gelesen.
2. `tests/sicherheit/angriffe.py` (Standardbibliothek + Playwright): ~310 Angriffe mit zwei und mehr Konten, Gästen,
   Webinar-Zuschauern, Admin. Was ein Client mit dem LiveKit-Token wirklich erreicht, wird mit dem echten `livekit-client`
   im Browser versucht und beim **Medienserver** nachgefragt (Twirp `ListParticipants`), nicht beim Client.
   XSS-Seiten werden mit abgeschalteter CSP gerendert – so zählt nur das Escaping (CSP ist die zweite Linie).
3. Jeder Fund: Fix + Unit-Test (`tests/test_sicherheit.py` u. a.) + Prüfung im Black-Box-Test.

## Funde

| # | Schwere | Fund | Status | Commit |
|---|---|---|---|---|
| 1 | Mittel | **Speicher-DoS ohne Anmeldung:** `json_body` las den ganzen Body in den Speicher und prüfte die Größe erst danach. Ein paar parallele 100-MB-Anfragen an `/api/konto/anmelden` (mit `X-Agora: 1`) hätten den Container (768 MB) gesprengt; ein `Content-Length: 100000000` mit wenig Body ließ den Server warten. | behoben: Content-Length vorab, Abbruch beim Lesen über der Grenze; tief verschachteltes JSON → 400 | `115dd10` |
| 2 | Mittel | **LiveKit-Token mit `canUpdateOwnMetadata`:** Jeder Client (auch Gast, Webinar-Zuschauer) konnte sich mit dem ausgegebenen Token selbst umbenennen (trotz Recht „umbenennen“ aus, z. B. „Host (offiziell)“), Attribute `rolle`/`farbe` und Metadaten fälschen. Rechte leitete das Frontend zwar nicht daraus ab, der Name ist aber Identitätsdiebstahl. | behoben: Recht aus (Token + UpdateParticipant), „Hand heben“ über `POST /api/raeume/{code}/hand` | `dc8549e` |
| 3 | Mittel | **Begrenzer per IPv6 umgehbar:** alle Grenzen „je IP“ zählten jede IPv6-Adresse einzeln – eine /64 hat 2^64 Adressen (Login, Passcode, Freigabe-Passwort, API-Schlüssel, Reset-Token). Dazu kein Limit je Konto gegen verteiltes Raten. | behoben: IPv6 als /64, max. 20 Login-Fehlversuche je Konto/15 min über alle IPs | `e32428e` |
| 4 | Niedrig | **SSRF-Prüfung mit Lücken:** `is_global` hält `64:ff9b::7f00:1` (NAT64 → 127.0.0.1), `64:ff9b::a9fe:a9fe` (Metadaten-Dienst) und `::127.0.0.1` für öffentlich – solche Webhooks wurden angenommen. Ausnutzbar nur mit NAT64 im Servernetz. | behoben: `app/netz.py` (eingebettete IPv4 muss öffentlich sein), Webhooks + Livestream | `69b172b` |
| 5 | Niedrig | **Keine HSTS von der App** (nur falls in Cloudflare an; im Repo nirgends festgelegt). | behoben: `max-age=31536000` bei https-`AGORA_SEITE` | `10cf19a` |

Ohne Befund (Auszug): IDOR (alle ~35 fremden Zugriffe 403/404, auch über `/api/v1`), signierte Download-Links, CSRF
(auch Pfad-Tricks `/api/v1/../`), XSS in Konto, Meeting (Chat, Teilnehmer, Q&A, Dateien, Notizen, Umfragen), Admin,
Webinar-Seite und Lobby, keine `javascript:`-Links, `?weiter=` (10 Varianten), SQL (alles parametrisiert, f-Strings nur
mit festen Spaltennamen), Pfad-Traversal (IDs per Regex, Dateinamen gesäubert, Auslieferung mit `sandbox`/`nosniff`),
Range (viele Bereiche, kein 500), Sitzungs-Fixierung, Abmelden/Passwortwechsel beenden Sitzungen, 2FA (5 Versuche je
Schritt, Grenzen je Konto/IP), Konto-Aufzählung, Datenexport ohne Hashes/Geheimnisse, CORS aus, Kopfzeilen (CSP ohne
`unsafe-inline`/`unsafe-eval`, `frame-ancestors`, XFO, COOP, nosniff, no-referrer), Einbetten (nur geprüfte Herkünfte,
`postMessage` nur an erlaubte Herkünfte, Empfänger prüft `origin` und `source`), Service Worker (nur gleiche Herkunft,
nie `/api`), Metriken/Tiefen-Gesundheit nicht über den Tunnel, LiveKit-Grants (Zuschauer senden nichts, Bildschirm nur
mit Recht – vom Medienserver erzwungen, keine Admin-Grants, 15 min), Datenkanal (`agora`/`chat`/`whiteboard` nur vom
Server angenommen), E2EE (Schlüssel nur im Fragment, Server kennt nur HKDF-Prüfwert), Stream-Schlüssel geschwärzt.

## Restrisiken (bewusst nicht behoben)

- **Rechte „selbst entstummen“ und „mehrfache Freigabe“ setzt nur der Client durch.** Das Token erlaubt allen
  Teilnehmenden Mikrofon; ein manipulierter Client kann sich nach „stumm durch Host“ wieder entstummen bzw. trotz
  laufender Freigabe teilen. Abhilfe wäre, Quellen per UpdateParticipant zu entziehen (eigene Logik für „vom Host stumm“).
  Gegenmittel heute: Host entfernt die Person.
- **Herabgestufte Co-Hosts:** ein vorher ausgegebenes Token (15 min) enthält noch die alten Quellen; beim
  Neuverbinden gelten sie bis zum Ablauf. Entfernen widerruft Token (`revoke_token_ts`), Herabstufen nicht.
- **Livestream (nur mit `AGORA_EGRESS=1`): DNS-Rebinding.** Die App prüft die Adressen des Stream-Ziels, der
  Egress-Container löst den Namen aber selbst noch einmal auf. Ein Co-Host mit eigener Domain könnte so interne
  RTMP-Ziele erreichen. Abhilfe: Egress-Container ohne Host-Netz bzw. mit Egress-Firewall.
- **Begrenzer im Arbeitsspeicher:** Neustart setzt sie zurück; bei > 50 000 Schlüsseln werden die ältesten verworfen
  (eine Flut vieler IPs könnte so ein Konto-Limit früher leeren – kostet die Angreifer viele argon2-Prüfungen).
- **Sperre eines Kontos durch Admins** beendet Sitzungen und laufende Meetings, lässt aber Freigabe-Links, das
  Kalender-Abo und Gast-Beitritte in die Räume des Kontos bestehen (Richtlinienfrage).
- **Login-CSRF über Bestätigungslinks:** wer jemandem den Bestätigungslink eines eigenen neuen Kontos schickt,
  meldet ihn in diesem Konto an (Komfort „nach Bestätigung gleich angemeldet“).
- **Konto-Sperre durch Dritte:** 20 falsche Passwörter je Konto sperren die Anmeldung für 15 min (gewollt, statt
  unbegrenztem Raten).
- HSTS ohne `includeSubDomains`/`preload`; Cloudflare-Zoneneinstellungen und `Secure`-Cookie in Produktion
  (`AGORA_COOKIE_SECURE=1` ist Standard) wurden nicht gegen Produktion geprüft.

## Erneut prüfen

```sh
(cd web && npm ci)                    # einmal: livekit-client für den Veröffentlichungs-Test
sh tests/sicherheit/lauf.sh           # baut Wegwerf-Instanz (App 8195, LiveKit 7790–7792), ~1 min, räumt auf
BEHALTEN=1 sh tests/sicherheit/lauf.sh                 # Instanz danach stehen lassen
APP_PORT=8196 LK_PORT=7800 sh tests/sicherheit/lauf.sh # andere Ports
```

Exit-Code ≠ 0, sobald ein Angriff klappt (`!!` / `ANGRIFF ERFOLGREICH`) oder ein Test abbricht. Einzelne Bereiche gegen
eine laufende Instanz: `NUR=ssrf,brute` als Umgebungsvariable an `angriffe.py`. Die Instanz läuft wie die E2E-Instanz
(`tests/e2e/docker-compose.test.yml`), aber mit Begrenzern und ohne lokale Webhook-Ziele
(`tests/sicherheit/docker-compose.sicherheit.yml`). `scripts/veroeffentlichen.sh` führt den Test nach den Browser-E2E aus.
Das Skript verweigert den Lauf ohne `AGORA_TEST=1` (kein `/api/test/posteingang`) – also nie gegen Produktion.
