# Anmelden mit Google, Microsoft und GitHub

Stand: gebaut und mit einem lokalen Identity-Provider (Dex) bewiesen. **In Produktion aus**, bis Florian die
Zugangsdaten einträgt – ohne `AGORA_<ANBIETER>_CLIENT_ID` **und** `_CLIENT_SECRET` gibt es weder Knöpfe noch Routen.

Code: `app/anmeldung.py` (Ablauf, Konten), `app/oidc.py` (Protokoll: PKCE, Discovery, JWKS, id_token-Prüfung),
`web/src/anmeldung-start.js` (Startseite), `web/src/anmeldung-konto.js` (Profil). Tests: `tests/test_anmeldung.py`
(nachgebauter Anbieter, alle Angriffe), `tests/e2e/sso.sh` (echter Dex im Browser).

## Was Florian einrichten muss

Für jeden Anbieter eine eigene „App“ anlegen, Client-ID und -Geheimnis in `app.env` eintragen (0600, nicht im Repo),
dann `docker compose up -d app`. Jeder Anbieter ist einzeln an- und abschaltbar (Variablen leer = aus).
Die App braucht ausgehend HTTPS zu den Anbietern (wie für Mails ins Internet).

### Google (Google Cloud Console)

1. https://console.cloud.google.com → Projekt anlegen (z. B. „Agora“).
2. **Google Auth Platform → Branding**: App-Name „Agora“, Support-Mail, Logo optional, Startseite
   `https://agora.slxt.dev`, Datenschutz `https://slxt.org/datenschutz/#agora`, Nutzungsbedingungen
   `https://slxt.org/nutzungsbedingungen/#agora`, **autorisierte Domain** `slxt.dev` (und `slxt.org` für die Links).
3. **Zielgruppe**: „Extern“, danach **„App veröffentlichen“** (sonst können sich nur eingetragene Testnutzer anmelden).
4. **Datenzugriff (Scopes)**: nur `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile` – alles
   „nicht sensibel“, daher keine Google-Prüfung nötig.
5. **Clients → Client erstellen**: Typ „Webanwendung“, Name „Agora“,
   **Autorisierte Weiterleitungs-URI:** `https://agora.slxt.dev/api/anmeldung/google/zurueck`
   (keine JavaScript-Quellen nötig – der Tausch läuft auf dem Server).
6. Client-ID und Clientschlüssel kopieren:

```sh
AGORA_GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
AGORA_GOOGLE_CLIENT_SECRET=GOCSPX-…
```

### Microsoft (Microsoft Entra Admin Center)

1. https://entra.microsoft.com → **App-Registrierungen → Neue Registrierung**, Name „Agora“.
2. **Unterstützte Kontotypen:** „Konten in einem beliebigen Organisationsverzeichnis und persönliche Microsoft-Konten“
   (passt zu `AGORA_MICROSOFT_TENANT=common`, dem Standard).
3. **Umleitungs-URI:** Plattform „Web“, `https://agora.slxt.dev/api/anmeldung/microsoft/zurueck`.
4. **Zertifikate & Geheimnisse → Neuer geheimer Clientschlüssel** (Ablauf höchstens 24 Monate – **Datum notieren**,
   vor Ablauf erneuern, sonst geht die Microsoft-Anmeldung nicht mehr; die übrigen Wege bleiben).
5. **Tokenkonfiguration → Optionalen Anspruch hinzufügen → ID:** `email` **und** `xms_edov` (zustimmen, dass die
   Graph-Berechtigung `email` ergänzt wird). Ohne `xms_edov` nimmt Agora bei **Firmen-/Schulkonten** keine neuen Konten
   an (siehe Sicherheit: Microsoft bestätigt das Claim `email` dort nicht); private Microsoft-Konten gehen immer.
6. **API-Berechtigungen:** Microsoft Graph, delegiert: `openid`, `email`, `profile` (Standard `User.Read` darf bleiben).
7. Optional **Branding & Eigenschaften → Herausgeberüberprüfung** – ohne sie zeigt Microsoft Firmenkonten „nicht
   überprüft“, und manche Organisationen erlauben dann nur mit Admin-Zustimmung.

```sh
AGORA_MICROSOFT_CLIENT_ID=<Anwendungs-ID (Client)>
AGORA_MICROSOFT_CLIENT_SECRET=<Wert des geheimen Schlüssels, nicht die ID>
AGORA_MICROSOFT_TENANT=common          # optional: consumers (nur privat), organizations (nur Firmen) oder eine Mandanten-ID
```

### GitHub (Developer settings)

1. https://github.com/settings/developers → **OAuth Apps → New OAuth App** (eine OAuth App, keine GitHub App).
2. Application name „Agora“, Homepage URL `https://agora.slxt.dev`,
   **Authorization callback URL:** `https://agora.slxt.dev/api/anmeldung/github/zurueck`.
3. **Generate a new client secret** und kopieren. Agora fragt die Scopes `read:user user:email` an (nur lesen).

```sh
AGORA_GITHUB_CLIENT_ID=Ov23li…
AGORA_GITHUB_CLIENT_SECRET=…
```

### Prüfen nach dem Eintragen

`curl -s https://agora.slxt.dev/api/einstellungen` → `"anmeldung":[{"id":"google","name":"Google"}, …]`. Auf der
Startseite erscheinen unter „oder“ die Knöpfe. Einmal mit einem Testkonto je Anbieter anmelden; im Profil steht der
Anbieter unter „Anmelden mit anderen Konten“, im Protokoll „angemeldet über …“.

## Ablauf und Sicherheit

1. **Start** (`POST /api/anmeldung/<anbieter>/start`, mit `X-Agora` gegen CSRF): Vorgang mit zufälligem `state`
   (gespeichert nur als SHA-256), `nonce`, PKCE-`code_verifier` (S256), Ziel `weiter` (nur eigene Pfade wie `/konto`)
   und 10 min Gültigkeit. Der Vorgang hängt an einem eigenen Cookie (`__Host-agora-anmeldung`, HttpOnly, Secure,
   SameSite=Lax) – wer einem anderen Browser einen fremden `code`+`state` unterschiebt (Login-CSRF), scheitert daran.
2. **Rückkehr** (`GET /api/anmeldung/<anbieter>/zurueck`): Vorgang einmalig einlösen, Code mit `code_verifier` tauschen
   (Geheimnis nur auf dem Server), dann:
   - **Google, Microsoft (OIDC):** Discovery und JWKS (1 h im Speicher, bei unbekannter `kid` einmal je Minute neu),
     id_token: nur RS*/PS*/ES* (nie `none`/HS*), Signatur mit dem Schlüssel der `kid`, `iss` exakt (Microsoft
     „common“: Vorlage `{tenantid}` mit der GUID aus `tid`), `aud` = Client-ID (mehrere Empfänger nur mit passendem
     `azp`), `exp`/`iat` (±60 s, höchstens 1 h alt), `nonce` aus dem Vorgang.
   - **GitHub (OAuth 2.0):** `/user` und `/user/emails`; zählt nur die **Haupt-Adresse, wenn sie verifiziert ist**.
3. **Konto zuordnen** (Schutz vor Kontoübernahme):
   - Verknüpfung bekannt (Aussteller + `sub`, nicht die Adresse) → dieses Konto.
   - Sonst nur mit **vom Anbieter bestätigter** Adresse: Google `email_verified`, GitHub „primary + verified“,
     Microsoft nur private Konten oder `xms_edov=true` (bei Firmenkonten kann ein Mandanten-Admin sonst jede Adresse
     eintragen – „nOAuth“).
   - Gibt es schon ein **bestätigtes** Agora-Konto mit der Adresse, wird **nicht** angemeldet: Die Person gibt einmal
     ihr Agora-Passwort (und mit 2FA den Code) ein, erst dann wird verknüpft (höchstens 5 Versuche, dieselbe
     Konto-Grenze wie beim Passwort-Login). Hat das Konto kein Passwort (selbst über einen anderen Anbieter angelegt),
     muss man sich wie beim ersten Mal anmelden und im Profil verknüpfen.
   - Gibt es nur ein **unbestätigtes** Konto (könnte jemand anderes mit eigenem Passwort vorab angelegt haben), wird es
     verworfen und neu angelegt – ein fremdes Passwort überlebt so nie.
   - Sonst entsteht ein **neues, bestätigtes Konto ohne Passwort** (Name vom Anbieter, persönlicher Raum).
4. **Zwei-Faktor** gilt weiter: Mit 2FA kommt die Person zum Code-Schritt (`/#zf=…` – das Fragment erreicht keinen
   Server und wird sofort aus der Adresse entfernt).
5. **Fehler** führen auf die Startseite mit festem Code (`?anmeldung=fehler&grund=token|anbieter|…`); Texte des
   Anbieters werden nie gespiegelt. Gesperrte Konten kommen auch so nicht herein.

**Profil → „Anmelden mit anderen Konten“:** verknüpfen (mit Passwort bestätigen, falls das Konto eins hat), trennen nur,
wenn das Konto ein Passwort hat (sonst käme man nicht mehr hinein). Konten ohne Passwort bekommen statt „Passwort
ändern“ einen Link per Mail zum Festlegen; „Konto löschen“ bestätigt man dort mit der eigenen E-Mail-Adresse.
Der Datenexport enthält die Verknüpfungen (Anbieter, Adresse, Daten).

## Vorschlag für die Datenschutzerklärung (slxt.org/datenschutz, Abschnitt Agora)

> **Anmeldung mit Google, Microsoft oder GitHub (freiwillig).** Statt mit E-Mail und Passwort kannst du dich über
> dein Konto bei Google (Google Ireland Limited), Microsoft (Microsoft Ireland Operations Limited) oder GitHub
> (GitHub, Inc., USA) anmelden. Erst wenn du auf „Mit … anmelden“ klickst, leitet dein Browser zum Anbieter weiter; dort
> meldest du dich an und stimmst der Weitergabe zu. Der Anbieter übermittelt uns dann: eine Kennung deines Kontos
> beim Anbieter, deinen Namen, deine E-Mail-Adresse und ob sie dort bestätigt ist (bei Microsoft zusätzlich die
> Kennung deiner Organisation, bei GitHub deinen Benutzernamen und die Liste deiner E-Mail-Adressen, von der wir nur
> die bestätigte Haupt-Adresse verwenden). Wir speichern Kennung, Anbieter und E-Mail-Adresse, solange die Verknüpfung
> besteht (Trennen jederzeit im Profil), sowie Name und E-Mail-Adresse in deinem Agora-Konto. Passwörter beim
> Anbieter erfahren wir nie; wir erhalten keine Kontakte, Dateien oder sonstigen Daten und rufen nach der Anmeldung
> nichts mehr beim Anbieter ab. Der Anbieter erfährt, dass du dich bei Agora anmeldest (Zeitpunkt, IP-Adresse,
> Browser) – dafür gelten seine Datenschutzbestimmungen; bei GitHub und ggf. Microsoft/Google kann das eine
> Übermittlung in die USA sein (EU-US Data Privacy Framework). Rechtsgrundlage: Art. 6 Abs. 1 lit. b DSGVO
> (Anmeldung auf deinen Wunsch). Ohne diese Anmeldung kannst du Agora genauso mit E-Mail und Passwort nutzen.
