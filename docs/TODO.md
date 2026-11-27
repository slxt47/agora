# Agora – TODO

Videokonferenz-Plattform im Stil von Zoom. Diese Liste deckt alle Kernfunktionen ab, gegliedert in Phasen: **MVP → Kernfunktionen → Erweiterungen → Produktionsreife**.

Legende: `[ ]` offen · `[x]` erledigt · 🔴 kritisch · 🟡 wichtig · 🟢 nice-to-have


> Stand 27.11.2026: 158 Punkte erledigt und live auf https://agora.slxt.dev, 9 offen – jeweils mit Grund.

---

## 0. Projekt-Setup

- [x] 🔴 Anforderungen & Scope festlegen (Zielgruppe, max. Teilnehmer pro Raum, Plattformen)
- [x] 🔴 Tech-Stack festlegen (z. B. Frontend: React/Svelte · Backend: Node.js/Go · Medien: mediasoup/LiveKit/Janus)
- [x] 🔴 Repository, Branching-Strategie, Code-Style, Linter, Formatter
- [x] 🔴 Monorepo- oder Multi-Repo-Struktur (`web`, `server`, `media`, `shared`)
- [x] 🟡 CI/CD-Pipeline (Build, Test, Lint, Deploy)
- [x] 🟡 Dev-Umgebung via Docker Compose (DB, Redis, TURN, Medienserver)
- [x] 🟡 Umgebungsvariablen / Secrets-Management
- [x] 🟢 Architektur-Dokumentation (ADRs), Diagramme
- [x] 🟢 Branding: Logo, Farbschema, Design-System

---

## 1. Authentifizierung & Benutzerkonten

- [x] 🔴 Registrierung (E-Mail + Passwort)
- [x] 🔴 Login / Logout, Session- bzw. JWT-Handling, Refresh-Tokens
- [x] 🔴 Passwort-Reset per E-Mail
- [x] 🟡 E-Mail-Verifizierung
- [x] 🟡 Gast-Zugang ohne Konto (nur Name eingeben)
- [x] 🟡 Benutzerprofil (Name, Avatar, Zeitzone, Sprache)
- [x] 🟡 Rate-Limiting & Brute-Force-Schutz
- [ ] 🟢 OAuth / SSO (Google, Microsoft, GitHub) — *braucht eigene Apps bei Google/Microsoft/GitHub (Florian)*
- [x] 🟢 Zwei-Faktor-Authentifizierung (TOTP)
- [ ] 🟢 SAML / OIDC für Unternehmen — *braucht einen Identity-Provider (Florian)*
- [x] 🟢 Konto löschen & Datenexport (DSGVO)

---

## 2. Meetings & Räume

- [x] 🔴 Sofort-Meeting starten
- [x] 🔴 Meeting-Link / Meeting-ID generieren und teilen
- [x] 🔴 Meeting beitreten per Link oder ID
- [x] 🔴 Raum-Lebenszyklus (erstellen, aktiv, beenden, aufräumen)
- [x] 🟡 Meeting-Passcode / Passwort
- [x] 🟡 Meeting planen (Datum, Uhrzeit, Dauer, Zeitzone, Beschreibung)
- [x] 🟡 Wiederkehrende Meetings
- [x] 🟡 Persönlicher Dauer-Raum pro Benutzer
- [x] 🟡 Einladung per E-Mail (inkl. `.ics`-Anhang)
- [x] 🟡 Warteraum (Waiting Room) mit Zulassen / Ablehnen durch Host
- [x] 🟢 Kalender-Integration (Google Calendar, Outlook)
- [x] 🟢 Meeting-Vorlagen
- [x] 🟢 Meeting-Verlauf / Übersicht vergangener Meetings

---

## 3. Audio & Video (Kern / WebRTC)

### 3.1 Geräte & Vorschau
- [x] 🔴 Kamera- und Mikrofon-Zugriff anfragen (`getUserMedia`)
- [x] 🔴 Geräteauswahl (Kamera, Mikrofon, Lautsprecher)
- [x] 🔴 Vorschau vor dem Beitritt (Pre-Join-Lobby)
- [x] 🟡 Mikrofon-Pegelanzeige & Audiotest
- [x] 🟡 Berechtigungsfehler verständlich behandeln (blockiert, kein Gerät)
- [x] 🟡 Geräte im laufenden Meeting wechseln / Hot-Plug

### 3.2 Medien-Übertragung
- [x] 🔴 WebRTC-Signalisierung (WebSocket)
- [x] 🔴 STUN/TURN-Server einrichten (coturn) – inkl. TLS auf Port 443
- [x] 🔴 SFU (Selective Forwarding Unit) für Gruppenanrufe
- [x] 🔴 Audio/Video senden und empfangen
- [x] 🔴 Mute / Unmute (Audio) und Kamera an/aus
- [x] 🟡 Simulcast / SVC (mehrere Qualitätsstufen pro Stream)
- [x] 🟡 Adaptive Bitrate & Bandbreitenschätzung
- [x] 🟡 Verbindungsabbrüche erkennen, automatisches Reconnect / ICE-Restart
- [x] 🟡 Aktiver-Sprecher-Erkennung
- [x] 🟡 Codec-Auswahl (VP8/VP9/AV1/H.264, Opus)
- [x] 🟢 Verbindungsqualitäts-Anzeige pro Teilnehmer
- [x] 🟢 Datensparmodus (nur Audio / niedrige Auflösung)

### 3.3 Audio-Qualität
- [x] 🟡 Echo-Unterdrückung, Rauschunterdrückung, Auto-Gain
- [x] 🟢 KI-Rauschunterdrückung (z. B. RNNoise)
- [x] 🟢 Stereo / Originalton-Modus (Musik)

---

## 4. Video-Layout & Oberfläche

- [x] 🔴 Galerie-Ansicht (Grid)
- [x] 🔴 Sprecher-Ansicht (aktiver Sprecher groß)
- [x] 🟡 Teilnehmer anheften (Pin) / spotlighten
- [x] 🟡 Paginierung bei vielen Teilnehmern
- [x] 🟡 Vollbildmodus
- [x] 🟡 Eigene Videovorschau verschiebbar / ausblendbar
- [x] 🟡 Responsive Layout (Desktop, Tablet, Smartphone)
- [x] 🟡 Dark Mode / Light Mode
- [x] 🟢 Picture-in-Picture
- [x] 🟢 Videos ohne Kamera als Avatar / Initialen anzeigen
- [x] 🟢 Tastenkürzel (Mute, Kamera, Hand heben, Chat)

---

## 5. Bildschirmfreigabe

- [x] 🔴 Gesamten Bildschirm freigeben
- [x] 🔴 Einzelnes Fenster / Browser-Tab freigeben
- [x] 🟡 Systemton bei Freigabe mitsenden (wo unterstützt)
- [x] 🟡 Optimierung für Text vs. Video (Framerate/Auflösung)
- [x] 🟡 Nur ein Präsentator gleichzeitig (Host kann Mehrfach-Freigabe erlauben)
- [x] 🟡 Hinweis-Banner „Du teilst deinen Bildschirm“ + Stop-Button
- [x] 🟢 Fernsteuerung (Remote Control) — *nur mit der Desktop-App auf der Seite der teilenden Person (Zustimmung im Dialog der App, roter Rahmen, Ende per Kürzel/Tray), Eingabe über @jitsi/robotjs bzw. xdotool; Linux unter xvfb geprüft, Windows/macOS nicht ausgeführt – [`DESKTOP.md`](DESKTOP.md)*
- [x] 🟢 Anmerkungen / Zeichnen auf geteiltem Bildschirm

---

## 6. Chat & Zusammenarbeit

- [x] 🔴 Gruppenchat im Meeting
- [x] 🟡 Private Nachrichten an einzelne Teilnehmer
- [x] 🟡 Chat-Verlauf speichern (optional) und nach Meeting abrufen
- [x] 🟡 Dateien teilen (Größenlimit, Virenscan)
- [x] 🟡 Links klickbar, Emojis
- [x] 🟡 Ungelesen-Badge / Benachrichtigung
- [x] 🟢 Reaktionen (👍 👏 ❤️) als Overlay
- [x] 🟢 „Hand heben“
- [x] 🟢 Umfragen (Polls) inkl. Ergebnisse
- [x] 🟢 Q&A-Modus für Webinare
- [x] 🟢 Gemeinsames Whiteboard
- [x] 🟢 Gemeinsame Notizen

---

## 7. Teilnehmerverwaltung & Host-Kontrollen

- [x] 🔴 Teilnehmerliste mit Status (Mic/Kamera, Sprecher, Hand)
- [x] 🔴 Rollen: Host, Co-Host, Teilnehmer, Gast
- [x] 🔴 Teilnehmer stummschalten / alle stummschalten
- [x] 🔴 Teilnehmer aus Meeting entfernen
- [x] 🟡 Kamera eines Teilnehmers deaktivieren lassen / Aufforderung zum Einschalten
- [x] 🟡 Meeting sperren (keine neuen Beitritte)
- [x] 🟡 Host übertragen / Co-Host ernennen
- [x] 🟡 Berechtigungen steuern (Chat, Bildschirmfreigabe, Umbenennen, Selbst-Unmute)
- [x] 🟡 Meeting für alle beenden vs. selbst verlassen
- [x] 🟡 Teilnehmer melden / blockieren
- [x] 🟢 Teilnehmer umbenennen
- [x] 🟢 Breakout-Rooms (Zuweisung manuell/automatisch, Timer, Rückruf)
- [x] 🟢 Dolmetscher-Kanäle (Simultanübersetzung)

---

## 8. Aufzeichnung & Wiedergabe

- [x] 🟡 Lokale Aufzeichnung im Browser
- [x] 🟡 Server-seitige Aufzeichnung (Composite oder Einzelspuren)
- [x] 🟡 Einwilligungs-Hinweis / Aufnahme-Indikator für alle Teilnehmer
- [x] 🟡 Speicherung (S3-kompatibel / Object Storage) + Retention-Regeln — *lokal (Standard) oder S3 (`AGORA_S3_*`), Aufbewahrungsregeln wie bisher; in Produktion nicht eingeschaltet*
- [x] 🟡 Aufzeichnungen-Bibliothek: Liste, Wiedergabe, Download, Löschen
- [x] 🟢 Freigabelinks mit Passwort / Ablaufdatum
- [x] 🟢 Automatisches Transkript
- [x] 🟢 Zusammenfassung & Kapitelmarken (KI) — *KI nur mit eingerichtetem Modell (`AGORA_KI_URL`), sonst Stichwort-Kapitel ohne KI*

---

## 9. Video-Effekte & Barrierefreiheit

- [x] 🟢 Hintergrund unscharf
- [x] 🟢 Virtuelle Hintergründe (Bilder/Video)
- [x] 🟢 Videofilter, „Aussehen verbessern“
- [x] 🟢 Live-Untertitel (Speech-to-Text)
- [x] 🟢 Live-Übersetzung der Untertitel (auf dem Gerät, Translator-API des Browsers)
- [x] 🟡 Tastaturnavigation, Screenreader-Support (ARIA)
- [x] 🟡 Ausreichender Farbkontrast (WCAG 2.1 AA)
- [x] 🟢 Mehrsprachige Oberfläche (i18n): Deutsch, Englisch

---

## 10. Webinare & große Events (Erweiterung)

- [x] 🟢 Webinar-Modus (Panelisten vs. Zuschauer)
- [x] 🟢 Registrierungsseite für Teilnehmer
- [x] 🟢 Livestream per RTMP (YouTube, Twitch)
- [x] 🟢 HLS-Broadcast für sehr viele Zuschauer — *optional (`AGORA_EGRESS=1` + `AGORA_HLS=1`), Webinar → `/h/<code>`, 720p (+360p), ADR 0003; Cloudflare-Cache-Regel setzt Florian*
- [ ] 🟢 Cascading SFUs für horizontale Skalierung — *erst bei mehreren Servern sinnvoll*

---

## 11. Clients & Plattformen

- [x] 🔴 Web-App (Chrome, Firefox, Safari, Edge)
- [x] 🟡 PWA (installierbar)
- [x] 🟢 Desktop-App (Electron/Tauri) mit systemweiten Tastenkürzeln — *Electron in `desktop/`, Linux-Pakete lokal gebaut + Smoke-Test; Windows/macOS nur im Workflow, unsigniert (Zertifikate: Florian), siehe [`DESKTOP.md`](DESKTOP.md)*
- [ ] 🟢 Mobile Apps (iOS/Android oder React Native/Flutter) — *Android: TWA in `mobil/android/`, Debug-APK baut in GitHub Actions (geprüft 27.11.2026); offen: Play-Store-Signatur/-Eintrag und iOS-App (Mac + Apple-Entwicklerkonto, Florian)*
- [x] 🟢 Browser-Kompatibilitätsmatrix testen (insbesondere Safari/iOS) — *Chromium, Firefox, WebKit automatisch (`BROWSER=… sh tests/e2e/lauf.sh`, [`BROWSER.md`](BROWSER.md)); echter Safari/iOS-Praxistest (Mac/iPhone) bleibt Handarbeit*
- [x] 🟢 Einbettbares SDK / iFrame / Widget

---

## 12. Backend, Daten & Infrastruktur

- [x] 🔴 REST- bzw. GraphQL-API (Benutzer, Meetings, Aufzeichnungen)
- [x] 🔴 WebSocket-Gateway für Signalisierung & Events
- [x] 🔴 Datenbank-Schema (Users, Meetings, Participants, Recordings, Chat)
- [x] 🔴 Migrationen
- [x] 🟡 Redis für Präsenz, Pub/Sub, Rate-Limits — *optional (`AGORA_REDIS_URL`): Begrenzer + Präsenz, mehrere Worker getestet; Pub/Sub nicht nötig, Ereignisse laufen über den LiveKit-Datenkanal*
- [x] 🟡 Object Storage für Aufzeichnungen und Dateien — *optional S3-kompatibel (Aufnahmen, Transkripte, Dateien), Migration per Skript; Standard weiter lokal unter daten/*
- [x] 🟡 E-Mail-Versand (Einladungen, Reset)
- [x] 🟡 Hintergrund-Jobs / Queue (Transkodierung, Mails, Aufräumen)
- [ ] 🟡 Horizontale Skalierung von SFU-Nodes + Load-Balancing / Regionen — *ein Server; Redis für LiveKit vorbereitet*
- [x] 🟡 Container-Deployment (Docker; optional Kubernetes)
- [x] 🟡 Reverse-Proxy, TLS-Zertifikate (Let's Encrypt), HTTP/2/3
- [x] 🟡 Firewall-/Port-Konzept für UDP-Medienports
- [ ] 🟢 Multi-Region-Routing (nächster Medienserver) — *ein Server*

---

## 13. Sicherheit & Datenschutz

- [x] 🔴 Verschlüsselung in Transit (DTLS-SRTP, TLS)
- [x] 🔴 Autorisierung pro Raum (Tokens für Join, Rollen serverseitig prüfen)
- [x] 🔴 Eingabevalidierung, Schutz vor XSS/CSRF/SQL-Injection
- [x] 🔴 Secrets & Passwörter sicher speichern (Argon2/bcrypt)
- [ ] 🟡 DSGVO: Datenschutzerklärung, Impressum, AV-Vertrag, Einwilligungen — *Texte fertig; AV-Verträge (Hetzner, Cloudflare, Apple) prüft Florian*
- [x] 🟡 Datenminimierung, Aufbewahrungsfristen, Löschkonzept
- [x] 🟡 Audit-Log (Host-Aktionen, Logins)
- [x] 🟡 Missbrauchsschutz (Spam, Zoom-Bombing, Rate-Limits)
- [x] 🟢 Ende-zu-Ende-Verschlüsselung (Insertable Streams / SFrame)
- [x] 🟢 Sicherheits-Review / Penetrationstest
- [x] 🟢 Abhängigkeits-Scans (Dependabot, `npm audit`)

---

## 14. Qualität, Tests & Betrieb

- [x] 🔴 Unit-Tests (Backend-Logik, Berechtigungen)
- [x] 🟡 Integrationstests (API, WebSocket, DB)
- [x] 🟡 End-to-End-Tests mit simulierten Browsern/Fake-Medien (Playwright)
- [x] 🟡 Lasttests (viele Teilnehmer, viele Räume) – SFU-Kapazität ermitteln
- [x] 🟡 Netzwerk-Simulation (Paketverlust, Latenz, niedrige Bandbreite)
- [x] 🟡 Logging, Metriken (Prometheus/Grafana), Tracing
- [x] 🟡 WebRTC-Statistiken sammeln (Bitrate, Jitter, Paketverlust, RTT)
- [x] 🟡 Fehler-Monitoring (z. B. Sentry) — *selbst gehostet: Browser + Server, gruppiert, Admin-Reiter „Fehler“, Mail/ntfy bei neuer Gruppe*
- [x] 🟡 Backups & Wiederherstellungstest
- [x] 🟢 Status-Seite, Alerting — *öffentliche Status-Seite /status (+ /api/status), Alerting über cc-waechter/ntfy*
- [x] 🟢 Nutzer-Feedback nach Meeting („Wie war die Qualität?“)

---

## 15. Zusatzfunktionen (Backlog)

- [x] 🟢 Admin-Dashboard (Nutzer, Nutzung, Statistiken)
- [x] 🟢 Teams / Organisationen / Workspaces — *Organisationen mit Rollen, Einladungen, Räumen, Vorlagen, Aufnahmen, Schlüsseln*
- [ ] 🟢 Abrechnung & Pläne (Free/Pro, Zeitlimits) — *Geschäftsentscheidung (Florian)*
- [ ] 🟢 Telefon-Einwahl (SIP / PSTN) — *braucht SIP-Anbieter (Florian)*
- [x] 🟢 Integrationen (Slack, Teams, Webhooks, öffentliche API)
- [x] 🟢 Benachrichtigungen (Push, E-Mail-Erinnerungen)
- [x] 🟢 Persistenter Raum-Chat außerhalb von Meetings

---

## Meilensteine

| Meilenstein | Inhalt | Status |
|---|---|---|
| **M1 – Prototyp** | Setup, Signalisierung, 1:1-Videoanruf, Mute/Kamera | [x] |
| **M2 – MVP** | SFU-Gruppenanrufe, Räume & Links, Chat, Galerie-/Sprecheransicht, Login | [x] |
| **M3 – Kernfunktionen** | Bildschirmfreigabe, Host-Kontrollen, Warteraum, Planung, Aufzeichnung | [x] |
| **M4 – Beta** | Breakout-Rooms, Reaktionen, Polls, virtuelle Hintergründe, Mobile/PWA | [x] |
| **M5 – Produktionsreife** | Sicherheit, DSGVO, Lasttests, Monitoring, Skalierung | [x] |
| **M6 – Erweiterungen** | Webinare, Transkription, E2EE, Integrationen | [x] |
