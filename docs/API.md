# Agora – API-Vertrag (Backend ↔ Frontend)

Alle Antworten JSON. Fehler: HTTP-Status + `{"fehler": "deutscher Text"}`.
Zustandsändernde Aufrufe (POST/PUT/PATCH/DELETE) brauchen Header `X-Agora: 1` (CSRF-Schutz), sonst 403.
Anmeldung per Cookie `__Host-agora` (httponly, secure, samesite=lax). Test: `AGORA_COOKIE=agora`, `AGORA_COOKIE_SECURE=0`.
Wer in einem Meeting ist, schickt bei Raum-Aktionen Header `X-Teilnahme: <teilnahme>` (Geheimnis aus dem Beitritt).
Code eines Raums: `abc-defg-hij` (Kleinbuchstaben ohne l/o/0/1, Muster `^[a-z]{3}-[a-z]{4}-[a-z]{3}$`).

## Seiten (vom Backend ausgeliefert, Vite-Mehrseiten-Build in `web/dist`)
| Pfad | Datei | Zweck |
|---|---|---|
| `/` | index.html | Start: Anmelden/Registrieren, Sofort-Meeting, Beitreten per Code/Link |
| `/konto` | konto.html | Profil, Meetings planen/verwalten, persönlicher Raum, Aufzeichnungen, Konto löschen/Export |
| `/r/{code}` | raum.html | Pre-Join-Lobby → (Passcode) → Warteraum → Meeting |
| `/passwort?t=` | passwort.html | Neues Passwort setzen |
| `/a/{token}` | a.html | Freigegebene Aufzeichnung ansehen |
| `/w/{code}` | webinar.html | Anmeldung zu einem Webinar (`?abmelden=<token>`: Anmeldung löschen) |
| `/admin` | admin.html | Admin-Bereich – nur für Admins, sonst 404 (`/admin.html` immer 404) |
| `/api/konto/bestaetigen?t=` | – | E-Mail bestätigen → Weiterleitung `/?bestaetigt=1` bzw. `/?bestaetigt=0` |

## Konto
| Methode + Pfad | Body | Antwort |
|---|---|---|
| POST `/api/konto/registrieren` | `{name, email, passwort}` (Passwort ≥ 10) | `{ok:true}` – Mail mit Bestätigungslink |
| POST `/api/konto/anmelden` | `{email, passwort}` | `{ok:true, ich}` + Cookie. 403 `nicht bestätigt`, 403 `Dieses Konto ist gesperrt. …`. Mit Zwei-Faktor: `{zwei_faktor:true, schritt}` (noch kein Cookie) |
| POST `/api/konto/anmelden/code` | `{schritt, code}` (TOTP oder Wiederherstellungscode) | `{ok:true, ich, codes_uebrig}` + Cookie. 400 `Der Code stimmt nicht.` bzw. abgelaufen/zu viele (dann neu mit Passwort) |
| POST `/api/konto/bestaetigung-erneut` | `{email}` | `{ok:true}` (immer) |
| POST `/api/konto/abmelden` | – | `{ok:true}` |
| GET `/api/konto/ich` | – | `{angemeldet:false}` oder `{angemeldet:true, ich}` |
| PATCH `/api/konto/ich` | `{name?, zeitzone?, sprache?: "de"\|"en", farbe?: "#rrggbb"}` | `{ich}` |
| POST `/api/konto/passwort-vergessen` | `{email}` | `{ok:true}` (immer) |
| POST `/api/konto/passwort-setzen` | `{token, passwort}` | `{ok:true}` (alle Sitzungen ungültig) |
| POST `/api/konto/passwort-aendern` | `{alt, neu}` | `{ok:true}` |
| GET `/api/konto/export` | – | JSON-Download aller eigenen Daten |
| DELETE `/api/konto` | `{passwort}` | `{ok:true}` – löscht Konto, Räume, Aufnahmen |

`ich` = `{id, name, email, zeitzone, sprache, farbe, persoenlicher_raum, zwei_faktor, admin}` (farbe = Avatar-Farbe `#rrggbb`, persoenlicher_raum = Code, zwei_faktor = bool).
Grenzen: Anmelden 10/10 min je IP+Mail, Registrieren 5/h je IP, Mails 3/h je Adresse.

### Sicherheitsereignisse (angemeldet)
GET `/api/konto/sicherheit` → `{ereignisse:[{zeit, art, ereignis}], tage: 30}` – nur eigene, neueste zuerst, höchstens 100.
`art`: `anmeldung` · `anmeldung_2fa` · `anmeldung_code` · `fehlversuch_passwort` · `fehlversuch_code` · `fehlversuch_gesperrt` ·
`passwort_geaendert` · `passwort_zurueckgesetzt` · `email_bestaetigt` · `zwei_faktor_an` · `zwei_faktor_aus` · `zwei_faktor_codes` ·
`zwei_faktor_code_benutzt` · `zwei_faktor_admin` · `gesperrt` · `entsperrt` (Quelle: Protokoll, 30 Tage; `ereignis` = Rohtext).

### Zwei-Faktor-Anmeldung (TOTP, angemeldet)
| Methode + Pfad | Body | Antwort |
|---|---|---|
| GET `/api/konto/2fa` | – | `{an, codes_uebrig}` |
| POST `/api/konto/2fa/einrichten` | `{passwort}` | `{geheim (Base32), uri (otpauth://totp/…)}` – noch nicht aktiv; 409 wenn schon an |
| POST `/api/konto/2fa/bestaetigen` | `{code}` | `{ok:true, wiederherstellungscodes:[10]}` – jetzt aktiv; Codes nur in dieser Antwort |
| POST `/api/konto/2fa/codes` | `{passwort, code}` | `{ok:true, wiederherstellungscodes:[10]}` – alte Codes ungültig |
| POST `/api/konto/2fa/ausschalten` | `{passwort, code}` | `{ok:true}` |

TOTP nach RFC 6238 (SHA-1, 6 Stellen, 30 s), Toleranz ±1 Schritt, jeder Schritt wird nur einmal angenommen (auch nicht der
vom Einschalten). `code` darf überall auch ein Wiederherstellungscode sein (`abcde-fghjk`, Groß/Klein, Leer- und
Bindestriche egal; jeder einmal). Der Anmeldeschritt gilt 5 min und 5 Versuche; dazu 10 Codes/10 min je Konto und 30/10 min je IP.
Das Geheimnis liegt verschlüsselt (Schlüssel aus `AGORA_GEHEIM`), Wiederherstellungscodes nur als HMAC.

## Räume
`raum` = `{code, titel, besitzer_name, ist_host, passcode_noetig, warteraum, gesperrt, aktiv, persoenlich,
geplant: null | {start (ISO UTC), dauer_min, zeitzone, beschreibung, wiederholung: "keine"|"taeglich"|"woechentlich", bis (ISO|null)},
naechster_termin (ISO|null), link}`

| Methode + Pfad | Body | Antwort |
|---|---|---|
| POST `/api/raeume` (angemeldet) | `{titel?, passcode?, warteraum?, chat_speichern?, geplant?}` – ohne `geplant` = Sofort-Meeting | `{raum}` |
| GET `/api/raeume` (angemeldet) | – | `{geplant:[raum], persoenlich: raum, vergangen:[raum]}` |
| GET `/api/raeume/{code}` | – | `{raum}` (öffentlich, ohne Passcode) |
| PATCH `/api/raeume/{code}` (Besitzer) | Felder wie POST | `{raum}` |
| DELETE `/api/raeume/{code}` (Besitzer) | – | `{ok:true}` |
| POST `/api/raeume/{code}/einladen` (Besitzer) | `{emails:[…≤20], passcode?}` (passcode wird nur in die Mail geschrieben, wenn er stimmt, sonst 400) | `{ok:true, gesendet:n}` – Mail mit `.ics` |
| GET `/api/raeume/{code}/ics` | – | `text/calendar` |

### Anmerkungen auf geteilten Bildschirmen
POST `/api/raeume/{code}/anmerkungen` (X-Teilnahme) Body `{erlaubt: bool}` → `{ok:true, erlaubt}`. Dürfen: Host, Co-Host
und wer laut LiveKit gerade den Bildschirm teilt (sonst 403; 503, wenn LiveKit nicht antwortet); 30/min je Teilnahme.
Das Recht steht in den Raum-Metadaten (`anmerkungen: bool`, Standard `true`, beim Meeting-Ende wieder `true`), dazu
Ereignis `{typ:"anmerkungen", erlaubt, von_name}`. Ohne Recht zeichnen nur Host, Co-Hosts und die teilende Person.
Die Striche gehen direkt zwischen den Browsern (Datenkanal, Topic `anmerkung`, `reliable`, Koordinaten 0…1 relativ zum
Videobild, in E2EE-Meetings verschlüsselt) und werden nicht gespeichert:
`{t:"strich", s:<identity der teilenden Person>, id, w:"stift"|"marker"|"pfeil", f:<Farbindex 0–5>, i:<Startindex>, p:[[x,y]…≤200]}`,
`{t:"text", s, id, f, x, y, text≤200}`, `{t:"weg", s, id, von}`, `{t:"leeren", s, nur?}`. Empfänger nehmen sie nur von
Teilnehmenden im Raum an, prüfen Format, Recht und Rate (30/s je Absender, Stoß 60) und verwerfen sie, wenn `s` nicht
(mehr) teilt. Wer später beitritt, sieht nur neue Anmerkungen.

### Ende-zu-Ende-Verschlüsselung (E2EE)
`raum` enthält zusätzlich `e2ee` (bool) und `e2ee_pruef` (32 Hex-Zeichen oder `null`). POST/PATCH nehmen
`{e2ee: true, e2ee_pruef}` bzw. `{e2ee: false}` (400 ohne gültigen Prüfwert). Der Schlüssel selbst geht nie an den
Server: Der Host-Browser erzeugt ein Geheimnis (32 Byte, base64url, im Link-Fragment `#k=…`) und schickt nur
`e2ee_pruef = HKDF-SHA-256(geheimnis, salt "agora-e2ee-v1", info "pruefwert")[0:16]` (hex). Den Medienschlüssel leitet
jeder Browser mit `info "medien"` selbst ab. Ändern (an/aus/neuer Schlüssel) nur, solange niemand verbunden ist, sonst
409 `Die Verschlüsselung lässt sich nur ändern, solange niemand im Meeting ist.` Einladungs-Mails, Kalender und `.ics`
enthalten den Schlüssel nicht. Siehe `docs/adr/0005-e2ee.md`.

### Beitreten
POST `/api/raeume/{code}/beitreten` Body `{name?, passcode?}` (name Pflicht für Gäste; angemeldet → Kontoname, `name` überschreibt).
- Besitzer, oder kein Warteraum: `{status:"zugelassen", zugang}`
- Warteraum an: `{status:"wartet", anfrage, anfrage_geheim}`
- 403 `Falscher Passcode` / `Meeting ist gesperrt` / `Meeting ist beendet`; 429 bei vielen Passcode-Versuchen.

GET `/api/raeume/{code}/warten/{anfrage}?g=<anfrage_geheim>` → `{status:"wartet"}` | `{status:"zugelassen", zugang}` | `{status:"abgelehnt"}` (Frontend fragt alle 2 s). Wurde das Meeting inzwischen beendet: `{status:"abgelehnt", grund:"beendet"}`. Der Zugang wird genau einmal ausgegeben (danach 404).

`zugang` = `{token (LiveKit-JWT), url (LiveKit-WebSocket-URL), identity, name, rolle: "host"|"cohost"|"teilnehmer"|"gast", teilnahme (Geheimnis für X-Teilnahme)}`.
LiveKit-URL: `AGORA_LIVEKIT_URL` (Prod `wss://agora.slxt.dev`, Pfad `/rtc` geht per Tunnel an LiveKit; Test `ws://127.0.0.1:7980`).
LiveKit-Teilnehmer: `identity` = `u<id>-<zufall>` oder `g-<zufall>`; `attributes.rolle` = Rolle, `attributes.farbe`; Client darf eigene Attribute setzen (`hand`="1"/"").
Raum-Metadaten (LiveKit `room.metadata`, JSON, nur Server schreibt): `{rechte:{chat, bildschirm, selbst_unmute, umbenennen, mehrfach_freigabe}, gesperrt, aufnahme:false|{von_name}}`.

### Host-Steuerung (X-Teilnahme, Rolle host/cohost; manche nur host)
| Methode + Pfad | Body | Antwort |
|---|---|---|
| GET `/api/raeume/{code}/warteraum` | – | `{wartend:[{anfrage, name, seit}]}` |
| POST `/api/raeume/{code}/warteraum/{anfrage}` | `{aktion:"zulassen"\|"ablehnen"}` | `{ok:true}` |
| POST `/api/raeume/{code}/steuerung` | `{aktion, ziel?, …}` | `{ok:true}` |

`aktion`: `stumm` (ziel) · `alle_stumm` · `kamera_aus` (ziel) · `entfernen` (ziel) · `sperren` `{an}` · `rolle` `{ziel, rolle:"cohost"|"teilnehmer"}` (nur host) ·
`host_uebertragen` `{ziel}` (nur host) · `beenden` (nur host, beendet für alle) · `rechte` `{chat?, bildschirm?, selbst_unmute?, umbenennen?, mehrfach_freigabe?}` · `umbenennen` `{ziel, name}` (host oder ziel selbst, wenn Recht `umbenennen`) · `kamera_bitten` (ziel) · `kamera_aus` (ziel, schaltet die Kamera-Spur serverseitig stumm).
Ein Gast ohne Konto kann co-host werden (über `teilnahme`). Bei `entfernen` darf die identity nicht wieder beitreten (Sperrliste je Raum).

GET `/api/raeume/{code}/zustand` (X-Teilnahme) → `{rolle, rechte, gesperrt, aufnahme, rollen:{identity: rolle}, wartend:n}`.

### Ereignisse (Server → Clients über LiveKit-Datenkanal, Topic `agora`, JSON, zuverlässig)
`{typ:"warteraum", wartend:n}` (nur an host/cohost) · `{typ:"rechte", rechte}` · `{typ:"rolle", identity, rolle}` · `{typ:"gesperrt", an}` ·
`{typ:"beendet"}` · `{typ:"aufnahme", aktiv, von_name}` · `{typ:"stumm", von_name}` (an Ziel) · `{typ:"kamera_bitten", von_name}` · `{typ:"entfernt"}` (an Ziel, kurz vor Entfernen).
Client → Client (Topic `reaktion`, lossy): `{emoji}`.
Zusätzliches Ereignis: `{typ:"kamera_aus", von_name}` (an Ziel). Ereignisse und Chat sendet nur der Server
(im Client: `participant` ist leer) – Pakete auf `agora`/`chat` von Teilnehmern ignorieren. Das Attribut `rolle` kann ein
Client selbst überschreiben (can_update_own_metadata für `hand`) → maßgeblich sind `zustand.rollen` und `rolle`-Ereignisse.

## Chat (X-Teilnahme)
POST `/api/raeume/{code}/chat` `{text (≤ 2000), an?: identity}` → `{nachricht}`; Server verteilt über Topic `chat` (an alle bzw. nur Empfänger + Absender).
GET `/api/raeume/{code}/chat` → `{nachrichten:[nachricht]}` (nur die für mich sichtbaren, laufendes Meeting).
`nachricht` = `{id, von, von_name, an, an_name, text, zeit (ISO)}`. 403 wenn Recht `chat` aus und Rolle nicht host/cohost.
Chat wird beim Beenden gelöscht, außer Besitzer hat `chat_speichern` (Raum-Einstellung) an → dann im Konto unter dem Raum abrufbar: GET `/api/raeume/{code}/verlauf` (Besitzer) → `{nachrichten:[nachricht + sitzung]}` (öffentliche + eigene private Nachrichten).

## Melden und Blockieren
| Methode + Pfad | Wer | Body | Antwort |
|---|---|---|---|
| POST `/api/raeume/{code}/melden` | X-Teilnahme (auch Gäste) | `{ziel (identity), grund, text? (≤ 1000)}` | `{ok:true}`; 409 schon gemeldet (je Person/Ziel/Meeting), 5/10 min |
| POST `/api/raeume/{code}/blockieren` | X-Teilnahme + Cookie desselben Kontos | `{ziel}` (nur Personen mit Konto) | `{ok:true, name}` |
| GET `/api/konto/blockiert` | angemeldet | – | `{blockiert:[{id, name, seit}]}` |
| DELETE `/api/konto/blockiert/{id}` | angemeldet | – | `{ok:true}` |

`grund`: `belaestigung` · `hass` · `unangemessen` · `stoerung` · `spam` · `sonstiges`. Eine Meldung geht per Mail an alle bestätigten
Konten aus `AGORA_ADMIN_MAILS`; Host/Co-Hosts (außer Meldender und Gemeldeter) bekommen das Ereignis
`{typ:"meldung", ziel, ziel_name, grund}` (Topic `agora`, ohne meldende Person).
Blockiert A das Konto B: B bekommt beim Beitreten zu Räumen von A 403 `Du kannst diesem Meeting nicht beitreten.`, private
Chat-Nachrichten von B an A 403 `Diese Person möchte keine privaten Nachrichten von dir.`

## Umfragen (X-Teilnahme)
`umfrage` = `{id, frage, optionen:[text], mehrfach, anonym, status: "entwurf"|"laeuft"|"beendet", geteilt, von_name, erstellt, gestartet, beendet,
ergebnis: null | {stimmen (Zahl der Abstimmenden), zaehler:[n je Antwort], namen?:[[name] je Antwort]}, meine_auswahl?: null | [index]}`.
`ergebnis` bekommen Host/Co-Host immer (live), alle anderen erst, wenn `geteilt`. `namen` nur für Host/Co-Host und nur bei `anonym:false`
– geteilte Ergebnisse zeigen nie Namen. `meine_auswahl` steht in Antworten an die Person selbst (nicht in Ereignissen an alle).

| Methode + Pfad | Wer | Body | Antwort |
|---|---|---|---|
| GET `/api/raeume/{code}/umfragen` | alle | – | `{umfragen:[umfrage]}` (Entwürfe nur für Host/Co-Host) |
| POST `/api/raeume/{code}/umfragen` | host/cohost | `{frage (≤ 300), optionen:[2–10 Texte ≤ 200, verschieden], mehrfach?: false, anonym?: true}` | `{umfrage}` (Entwurf) |
| POST `/api/raeume/{code}/umfragen/{id}/starten` | host/cohost | – | `{umfrage}`; 409 wenn nicht Entwurf |
| POST `/api/raeume/{code}/umfragen/{id}/abstimmen` | alle | `{auswahl:[index]}` (Einfachwahl genau 1) | `{umfrage}`; 409 `Du hast schon abgestimmt.` bzw. wenn nicht laufend |
| POST `/api/raeume/{code}/umfragen/{id}/beenden` | host/cohost | – | `{umfrage}`; 409 wenn nicht laufend |
| POST `/api/raeume/{code}/umfragen/{id}/teilen` | host/cohost | – | `{umfrage}` – Ergebnis für alle; beendet eine laufende Umfrage |
| DELETE `/api/raeume/{code}/umfragen/{id}` | host/cohost | – | `{ok:true}` |
| GET `/api/raeume/{code}/umfragen/verlauf` | Besitzer (Cookie) | – | `{umfragen:[umfrage + sitzung]}` – nur bei `chat_speichern` |

Eine Stimme je Teilnahme (identity), serverseitig über den Primärschlüssel erzwungen. Höchstens 50 Umfragen je Meeting.
Umfragen werden mit dem Meeting gelöscht, außer der Raum hat `chat_speichern`.
Ereignis (Topic `agora`): `{typ:"umfrage", aktion, umfrage}` – `aktion` `gestartet`/`beendet`/`geteilt` an alle (ohne Live-Ergebnis) und
zusätzlich mit Ergebnis an Host/Co-Hosts; `angelegt` und `stimme` nur an Host/Co-Hosts; `geloescht` an alle (`umfrage` = `{id}`).

## Breakout-Räume (X-Teilnahme)
Jeder Breakout-Raum ist ein eigener LiveKit-Raum `<code>__b<nr>` (nr 1–50). Das ist kein gültiger Meeting-Code (Info/Beitreten per Code → 404);
Zugang nur mit einem Token von POST `…/breakout/wechseln`. Je Meeting gibt es eine Runde; sie wird beim Meeting-Ende gelöscht.

`breakout` = `null | {status: "entwurf"|"offen"|"schliesst"|"geschlossen", anzahl, runde (zählt jedes Öffnen hoch), dauer_min|null, rueckkehr,
endet (ISO|null), rest_s (Sekunden bis Timer-Ende, nur offen), schliesst_um (ISO|null), schliesst_in_s (nur schliesst), mein_raum (0 = keiner),
ort (wo ich gerade bin, 0 = Hauptraum)}` – Host/Co-Host zusätzlich `raeume:[{nr, teilnehmer:[person], anwesend}]` und `ohne_raum:[person]`,
`person` = `{identity, name, rolle, nr, ort}` (nur gerade Verbundene).

| Methode + Pfad | Wer | Body | Antwort |
|---|---|---|---|
| GET `/api/raeume/{code}/breakout` | alle | – | `{breakout}` |
| POST `/api/raeume/{code}/breakout` | host/cohost | `{anzahl: 2–50, dauer_min?: 1–240\|null, rueckkehr?: true, automatisch?: false}` | `{breakout}` – neue Runde (Entwurf), ersetzt eine geschlossene; 409 während offen |
| PUT `/api/raeume/{code}/breakout/zuteilung` | host/cohost | `{zuteilung:{identity: nr (0 = keiner)}}` oder `{automatisch:true}` | `{breakout}` (Entwurf oder offen) |
| POST `/api/raeume/{code}/breakout/oeffnen` | host/cohost | – | `{breakout}`; legt die LiveKit-Räume an, startet den Timer |
| POST `/api/raeume/{code}/breakout/wechseln` | alle | `{nr}` (0 = Hauptraum) | `{zugang:{token, url, raum (LiveKit-Raumname), nr}}` |
| POST `/api/raeume/{code}/breakout/hilfe` | in einem Breakout-Raum | – | `{ok:true}` (3/min) |
| POST `/api/raeume/{code}/breakout/nachricht` | host/cohost | `{text ≤ 500}` | `{ok:true}` |
| POST `/api/raeume/{code}/breakout/schliessen` | host/cohost | – | `{breakout}` – Countdown 60 s (`AGORA_BREAKOUT_COUNTDOWN_S`) |

Regeln für `wechseln`: Teilnehmende nur in den eigenen Raum (status offen), Host/Co-Host in jeden (auch zu Besuch); zurück in den Hauptraum
immer für Host/Co-Host, Nicht-Zugeteilte und während des Countdowns, sonst nur bei `rueckkehr` (403). Nach dem Schließen gibt es nur noch
den Hauptraum. Automatisch verteilt werden alle Verbundenen außer Host/Co-Hosts (gleichmäßig, zufällig).
Timer abgelaufen → Schließen beginnt von selbst; Countdown abgelaufen → Ereignis `geschlossen`, nach 15 s werden die LiveKit-Räume gelöscht.

Ereignisse (Topic `agora`): `{typ:"breakout", aktion, runde}` mit `aktion` `angelegt` (nur Moderation), `zuteilung` (im Entwurf nur Moderation),
`geoeffnet`, `ort` (nur Moderation), `schliesst` (+ `schliesst_in_s`), `geschlossen` → Clients holen GET `…/breakout` und wechseln selbst.
`{typ:"breakout_hilfe", nr, identity, von_name}` (an Host/Co-Hosts) · `{typ:"breakout_nachricht", text, von_name}` (an alle).
Solange Räume offen sind, gehen alle Server-Ereignisse, Chat und Raum-Metadaten an Haupt- und Breakout-Räume (der Chat ist meetingweit);
Stummschalten, Entfernen, Rollen/Umbenennen wirken in dem Raum, in dem die Person gerade ist. „Leer“ und „voll“ zählen alle Räume.
Client: Ein Raumwechsel löst im Dokument `agora:raumwechsel` (`detail.room`) aus – z. B. für Video-Effekte, die den neuen Raum brauchen.

## Verbindungsqualität (X-Teilnahme)
Der Browser misst alle 10 s (`getStats` der LiveKit-PeerConnections) und meldet etwa alle 30 s eine Zusammenfassung.
`werte` = `{rtt (ms 0–60000), verlust (% 0–100), jitter (ms 0–10000), ein?, aus? (kbit/s), kandidat?: "udp"|"tcp"|"relay", aufloesung?: "640x360", fps?}`
– `rtt`, `verlust`, `jitter` nur zusammen. Bewertung (Server und Browser gleich): `schlecht` ab 5 % Verlust, 400 ms RTT oder 50 ms Jitter,
`mittel` ab 2 % / 200 ms / 30 ms, sonst `gut`.

| Methode + Pfad | Wer | Body | Antwort |
|---|---|---|---|
| POST `/api/raeume/{code}/statistik` | alle | `werte` oder `{}` (nur anmelden) | `{ok:true, bewertung: null\|"gut"\|"mittel"\|"schlecht"}`; 12/min je Teilnahme |
| GET `/api/raeume/{code}/statistik` | host/cohost | – | `{teilnehmer:{identity: werte + {bewertung, alter_s}}, meeting: schnitt\|null}` (nur Werte der letzten 90 s) |

`schnitt` = `{proben, teilnehmer, rtt, verlust, jitter, ein, aus, rtt_max, verlust_max, schlecht_anteil, mittel_anteil, kandidaten:{udp, tcp, relay} (in %)}`.
Gespeichert wird je Meeting nur die Summe (Tabelle `qualitaet`, ohne IPs und Namen); die letzten Werte je Teilnahme
(`qualitaet_person`) werden beim Meeting-Ende gelöscht.

Die Antwort von POST `…/statistik` enthält zusätzlich `feedback` (Rückmelde-Token, HMAC über Raum, Sitzung, identity
und Zeit, 24 h gültig – erneuert mit jeder Meldung). Damit geht die Rückmeldung auch nach dem Meeting-Ende (ohne X-Teilnahme):

| Methode + Pfad | Wer | Body | Antwort |
|---|---|---|---|
| POST `/api/raeume/{code}/feedback` | Token | `{token, sterne: 1–5, probleme?: ["ton"\|"echo"\|"video"\|"verbindung"\|"bildschirm"\|"bedienung"], text? (≤ 1000), statistik?: {proben, rtt, verlust, jitter, ein, aus, schlecht_anteil}}` | `{ok:true}`; eine Rückmeldung je Teilnahme (erneut senden ändert sie); 403 bei falschem/abgelaufenem Token; 20/10 min je IP |
| GET `/api/qualitaet` | angemeldet | – | `{raeume:{code:{bewertungen, sterne\|null, meetings, rtt\|null, verlust\|null}}, meetings:{"code:sitzung":{bewertungen, sterne\|null, rtt\|null, verlust\|null}}}` – nur eigene Räume mit Werten; `meetings` passt über `code` + `sitzung` zu den Einträgen von GET `/api/verlauf` (Anzeige unter „Vergangene Meetings“) |
| GET `/api/raeume/{code}/qualitaet` | Besitzer | – | `{meetings:[{sitzung, beginn, verbindung: schnitt\|null, feedback: null\|{anzahl, sterne, probleme:{art:n}, kommentare:[text]}}]}` (neueste 30, ohne Namen) |

## Aufzeichnung (lokal im Browser des Hosts, dann Upload in die Bibliothek)
POST `/api/raeume/{code}/aufnahme` (X-Teilnahme, host/cohost) `{aktiv:bool}` → setzt Indikator für alle (Metadaten + Ereignis).
POST `/api/aufnahmen` (angemeldet) roher Body `video/webm` oder `video/mp4`, Header `X-Raum: <code>`, `X-Titel: <urlencoded>`, optional `X-Dauer: <sekunden>` → `{aufnahme}`. 413/415/507 bei zu groß/falschem Typ/Speicher voll. Max 500 MB/Datei, 5 GB/Konto.
GET `/api/aufnahmen` → `{aufnahmen:[aufnahme], belegt, kontingent}` · GET `/api/aufnahmen/{id}/datei` (Range-fähig) · DELETE `/api/aufnahmen/{id}`.
POST `/api/aufnahmen/{id}/freigabe` `{tage: 1|7|30, passwort?}` → `{link}` (`/a/<token>`) · DELETE `/api/aufnahmen/{id}/freigabe`.
GET `/api/freigaben/{token}` → `{titel, dauer, groesse, typ, passwort_noetig}`; GET `/api/freigaben/{token}/datei?p=<passwort>`.
`aufnahme` = `{id, titel, raum_code, groesse, typ, dauer, erstellt, ablauf, freigabe: null|{link, bis, passwort}}`. Aufnahmen werden nach 90 Tagen gelöscht.

## Admin-Bereich (Cookie eines Admins; alle anderen – auch Abgemeldete – bekommen 404 `Nicht gefunden`)
Admin = bestätigtes Konto, dessen E-Mail in `AGORA_ADMIN_MAILS` steht. Jede ändernde Aktion schreibt `Admin: …` ins Protokoll (365 Tage).

| Methode + Pfad | Body / Parameter | Antwort |
|---|---|---|
| GET `/api/admin/uebersicht` | – | `{konten:{gesamt, bestaetigt, gesperrt, zwei_faktor, neu_7}, laufend:{meetings, teilnehmer, livekit}, meetings:{tage_7, tage_30, raeume}, aufnahmen:{anzahl, belegt}, meldungen_offen}` |
| GET `/api/admin/benutzer` | `?q=&seite=` (50 je Seite) | `{benutzer:[konto], gesamt, seite_groesse}` |
| POST `/api/admin/benutzer/{id}/sperren` | `{an: bool}` | `{benutzer: konto}` – Sperren meldet überall ab und beendet eigene laufende Meetings |
| POST `/api/admin/benutzer/{id}/2fa-zuruecksetzen` | – | `{benutzer: konto}` |
| DELETE `/api/admin/benutzer/{id}` | – | `{ok:true}` (wie Konto löschen) |
| GET `/api/admin/meldungen` | `?status=offen\|erledigt\|alle` | `{meldungen:[meldung + von_konto, ziel_konto]}` (max. 200) |
| POST `/api/admin/meldungen/{id}` | `{status: "offen"\|"erledigt", notiz?}` | `{meldung}` |
| GET `/api/admin/protokoll` | `?q=&benutzer=<id\|email>&raum=&nur_admin=1&seite=` | `{eintraege:[{zeit, benutzer_id, email, name, raum_code, ereignis}], weitere}` |
| GET `/api/admin/meetings` | – | `{meetings:[{code, titel, besitzer_name, besitzer_email, gestartet, teilnehmer, aufnahme}]}` |
| POST `/api/admin/meetings/{code}/beenden` | – | `{ok:true}`; 409 wenn nicht laufend |

`konto` = `{id, name, email, bestaetigt, gesperrt, zwei_faktor, admin, erstellt, letzte_anmeldung, raeume, aufnahmen_belegt}`.
`meldung` = `{id, zeit, raum_code, raum_titel, von_name, von_benutzer, ziel_name, ziel_benutzer, grund, grund_text, text, status, notiz, bearbeitet}`.
Admin-Konten (und das eigene) lassen sich hier nicht sperren/löschen (403). Live-Zahlen kommen aus LiveKit `ListRooms`
(`laufend.livekit:false` = LiveKit nicht erreichbar, dann Zahl aus der Datenbank, `teilnehmer:null`). „Meetings 7/30 Tage“ =
beendete Meetings laut Protokoll + gerade laufende. Gesperrte Konten: Anmelden 403, bestehende Sitzungen ungültig.

## Sonstiges
GET `/api/gesund` → `{ok:true}` · GET `/api/einstellungen` → `{livekit_url, max_teilnehmer, version}`.

### Monitoring (nur von 127.0.0.1 ohne Cloudflare-Header oder mit `Authorization: Bearer <AGORA_METRIKEN_TOKEN>`, sonst 404)
- GET `/api/gesund?tief=1` → 200/503 `{ok, version, pruefungen:{datenbank:{ok, ms}, livekit:{ok, ms, verbunden}, platte:{ok, frei_bytes, frei_prozent}, hintergrund:{ok, alter_s}}}`
  (Datenbank: echter Schreibzugriff; Platte: mindestens `AGORA_PLATTE_MIN_GB` (2) und 5 % frei unter `daten/`; Hintergrund: letzter Lauf ≤ 180 s).
- GET `/api/metriken` → Prometheus-Textformat 0.0.4. Momentwerte: `agora_meetings_aktiv`, `agora_teilnahmen_aktiv`, `agora_teilnehmer_verbunden`,
  `agora_livekit_raeume`, `agora_warteraum`, `agora_livekit_erreichbar`, `agora_livekit_antwort_ms`, `agora_konten{status}`, `agora_aufnahmen`,
  `agora_aufnahmen_bytes`, `agora_db_bytes`, `agora_daten_frei_bytes`, `agora_daten_gesamt_bytes`, `agora_hintergrund_letzter_lauf_zeitstempel_sekunden`,
  `agora_start_zeitstempel_sekunden`, `agora_info{version}`. Zähler (seit Start): `agora_anmeldungen_total{ergebnis=ok|fehlgeschlagen|begrenzt}`,
  `agora_registrierungen_total`, `agora_mails_total{ergebnis=ok|fehler}`, `agora_livekit_fehler_total{aufruf}`, `agora_hintergrund_fehler_total`,
  `agora_http_anfragen_total{gruppe, status=2xx…}`. Histogramm `agora_http_dauer_sekunden{gruppe}` mit
  `gruppe` ∈ konto, raeume, meeting, aufnahmen, freigaben, betrieb, api, statisch, seiten (nie Codes oder IDs als Label).

## Ergänzungen Backend (Stand Umsetzung)
- `raum` hat zusätzlich `chat_speichern` (bool) und `beendet` (bool, Sofort-Meeting endgültig beendet). Alle Links (`raum.link`,
  Freigabe-`link`) sind absolut (`AGORA_SEITE` + Pfad). Zeiten immer ISO UTC `YYYY-MM-DDTHH:MM:SSZ`.
- `geplant.start` beim Anlegen: ISO mit Offset/`Z` oder ohne Offset (dann in `geplant.zeitzone`, Standard Konto-Zeitzone);
  `bis` als Datum (`YYYY-MM-DD` = Tagesende) oder Zeit. `dauer_min` 5–1440. PATCH: `passcode: null|""` entfernt den Passcode.
- GET `/api/raeume`: `geplant` = geplante mit kommendem Termin + offene Sofort-Meetings (nach `naechster_termin`, Sofort zuerst);
  `vergangen` = beendete Sofort-Meetings + geplante ohne weiteren Termin (max. 50).
- Lebenszyklus: Beitritt startet ein Meeting (`aktiv`). Ende durch Host (`beenden`) oder nach 10 min ohne Teilnehmer
  (Hintergrund fragt LiveKit jede Minute). Ein beendetes Sofort-Meeting kann nur der Besitzer wieder öffnen (sonst 403
  `Meeting ist beendet`); geplante/persönliche Räume starten beim nächsten Beitritt einfach neu. Sofort-Räume werden 30 Tage
  nach letzter Aktivität gelöscht, vergangene geplante nach 90 Tagen.
- Besitzer wird beim Beitritt immer `host` (ein anderer Host wird `cohost`). `host_uebertragen`: bisheriger Host wird
  `teilnehmer` (ohne Konto `gast`). Co-Host darf nur an `teilnehmer`/`gast` handeln (nicht am Host, nicht an Co-Hosts).
  `alle_stumm` betrifft alle mit niedrigerem Rang.
- `entfernen` sperrt für dieses Meeting identity + Konto (bzw. bei Gästen die IP) → 403 `Du wurdest aus diesem Meeting entfernt.`
- Weitere 403: `Meeting ist voll` (`max_teilnehmer`). Rechte `bildschirm` aus → Teilnehmer/Gäste bekommen kein
  `screen_share` mehr (Token und laufende Rechte werden angepasst).
- Fehlertexte u. a.: Anmelden falsch 400 `E-Mail oder Passwort falsch.`, nicht bestätigt 403 `E-Mail-Adresse noch nicht bestätigt – …`,
  401 `Bitte anmelden.`, 429 `Zu viele Versuche – bitte kurz warten.`, CSRF 403 `Anfrage abgelehnt`.
- `/api/konto/bestaetigen` meldet bei Erfolg gleich an (Cookie) und leitet auf `/?bestaetigt=1`.
- Registrieren mit schon bestätigter Adresse: Antwort trotzdem `{ok:true}`, Mail „Du hast schon ein Konto“ mit Passwort-Link.
- Download eigener Aufnahme als Datei: GET `/api/aufnahmen/{id}/datei?download=1`.
- Nur bei `AGORA_TEST=1`: GET `/api/test/posteingang` → `{mails:[{an, betreff, text}]}`, GET `/api/test/livekit` (Fake-Aufrufe).
  `AGORA_GRENZEN_AUS=1` schaltet alle Rate-Limits ab (nur für E2E).

## Kalender ohne OAuth
`raum.kalender` = `null` (kein Termin) oder `{google, outlook, office365, ics}` – fertige Links zum Eintragen:
Google (`calendar.google.com/calendar/render?action=TEMPLATE`, Zeiten in UTC, `ctz` = Zeitzone des Meetings, bei Wiederholung
`recur=RRULE:…` ab dem ersten Termin), Outlook.com (`outlook.live.com/calendar/0/action/compose`) und Outlook/Office 365
(`outlook.office.com/…`) mit `startdt`/`enddt` in UTC – diese Links kennen keine Wiederholung, daher der nächste Termin und im
Text ein Hinweis auf die `.ics`. `ics` = absolute URL von GET `/api/raeume/{code}/ics`. Einladungsmails enthalten die Links ebenfalls.

Kalender-Abo (privater iCal-Feed, abonnierbar in Apple/Google/Outlook):
| Methode + Pfad | Antwort |
|---|---|
| GET `/api/konto/kalender` | `{abo}` |
| POST `/api/konto/kalender` | `{abo}` – einschalten bzw. neuen Link erzeugen (der alte ist sofort ungültig) |
| DELETE `/api/konto/kalender` | `{abo}` (aus) |
| GET `/kalender/{token}.ics` (ohne Anmeldung) | `text/calendar` – alle eigenen geplanten Meetings mit kommendem Termin, wiederkehrende als Serie (VTIMEZONE + RRULE), nie Passcodes; 404 bei unbekanntem Token. 120 Abrufe/h je IP. |

`abo` = `{aktiv, url|null, webcal|null, erstellt|null, abgerufen|null}`. Das Token wird per HMAC aus `AGORA_GEHEIM` und einem
zufälligen Salz abgeleitet und nur als Hash gespeichert (ändert sich `AGORA_GEHEIM`, ändern sich alle Abo-Links).

## Meeting-Vorlagen (angemeldet)
`vorlage` = `{id, name, einstellungen, erstellt, geaendert}`, `einstellungen` = `{titel, dauer_min (5–1440), passcode (bool: jedes
Meeting bekommt einen zufälligen Passcode), warteraum, chat_speichern, beschreibung (≤ 2000), rechte:{chat, bildschirm, selbst_unmute,
umbenennen, mehrfach_freigabe}, stumm_beitritt}` (fehlende Felder = Standard). Höchstens 20 Vorlagen je Konto (409).

| Methode + Pfad | Body | Antwort |
|---|---|---|
| GET `/api/vorlagen` | – | `{vorlagen:[vorlage]}` |
| POST `/api/vorlagen` | `{name (≤ 80), einstellungen}` oder `{name, aus_raum: code}` (eigener Raum; Passcode nur als an/aus) | `{vorlage}` |
| PUT `/api/vorlagen/{id}` | `{name?, einstellungen?}` | `{vorlage}` |
| DELETE `/api/vorlagen/{id}` | – | `{ok:true}` |
| POST `/api/vorlagen/{id}/raum` | wie POST `/api/raeume` (`titel?, passcode?, warteraum?, chat_speichern?, geplant?`) – überschreibt die Vorlage; `geplant.dauer_min`/`beschreibung` kommen sonst aus der Vorlage | `{raum, passcode}` – `passcode` = vom Server erzeugter (nur hier einmal sichtbar) bzw. mitgeschickter Passcode, sonst `null` |

Ein Raum aus einer Vorlage startet mit deren Rechten und setzt sie nach jedem Meeting-Ende darauf zurück (Änderungen im Meeting
gelten nur für dieses Meeting). `zugang.stumm_beitritt` (bool, nie für den Host): Der Client tritt mit stummem Mikrofon bei und
behandelt das wie „vom Host stummgeschaltet“ (bei Recht `selbst_unmute` aus also erst nach Freigabe entstummbar). Durchgesetzt
wird das im Client (Hinweis im Zugang); der Server schaltet nicht selbst stumm.

## Erinnerungen (opt-in)
| Methode + Pfad | Body | Antwort |
|---|---|---|
| GET `/api/konto/erinnerung` | – | `{minuten}` (0 = aus) |
| PUT `/api/konto/erinnerung` | `{minuten: 0\|10\|15\|30\|60}` | `{minuten}` |

POST `/api/raeume/{code}/einladen` nimmt zusätzlich `erinnerung: true` und `erinnerung_min: 10|15|30|60` (Standard 15) – nur bei
geplanten Meetings; Antwort dann `{ok, gesendet, erinnerung: n}` (n = vorgemerkte Adressen, nur tatsächlich angeschriebene).
Ohne Haken werden keine Adressen gespeichert. Der Hintergrund schickt jede Minute fällige Mails (Fenster `[Termin − minuten, Termin)`),
höchstens eine je Termin und Empfänger (Schlüssel in der Datenbank, übersteht Neustarts). Kein Versand nach Beginn und nicht, wenn
Meeting bzw. Einladung erst innerhalb des Fensters entstanden sind. Besitzer: vor jedem Vorkommen; Eingeladene: nur vor dem nächsten
Termin (bei Wiederholung das Vorkommen zur Zeit der Einladung). Erinnerungsmails enthalten nie einen Passcode.

## Meeting-Verlauf (angemeldet, Besitzer)
`eintrag` = `{id, code, titel, start, ende, dauer_s, spitze, teilnahmen, chat, sitzung}` – `spitze` = höchste Zahl gleichzeitig
Verbundener (Haupt- und Breakout-Räume, minütlich und bei jedem Beitritt gemessen), `teilnahmen` = ausgegebene Zugänge,
`chat` = Chat/Umfragen dieses Meetings sind gespeichert (`chat_speichern`) und der Raum existiert noch.

| Methode + Pfad | Antwort |
|---|---|
| GET `/api/verlauf` | `{meetings:[eintrag]}` – nur beendete, neueste zuerst, max. 100 |
| GET `/api/verlauf/{id}/inhalt` | `{meeting, nachrichten:[nachricht], umfragen:[umfrage], whiteboard:[objekt], notizen, fragen:[frage], dateien:[datei + ablauf]}` – nur diese Sitzung (öffentliche + eigene private Nachrichten); 404 ohne gespeicherten Chat. Weitere Felder aus „Zusammenarbeit“ siehe unten (Haken `verlauf.INHALT`) |
| DELETE `/api/verlauf/{id}` | `{ok:true}` |

Server-Haken in `raeume.py` für Module: `BEIM_START`, `BEIM_BEITRITT`, `BEI_ZAEHLUNG(raum, anzahl)` (neben `BEIM_ENDE`).

## Dauer-Chat im persönlichen Raum
`raum.dauer_chat` (bool). Nur der persönliche Raum: PATCH `/api/raeume/{code}` `{dauer_chat: true|false}` (Besitzer; bei anderen
Räumen und in POST `/api/raeume` → 400). Ist er an, bleiben öffentliche Chat-Nachrichten nach dem Meeting-Ende erhalten und
GET `…/chat` (laufendes Meeting, X-Teilnahme) liefert sie zusammen mit dem Chat des aktuellen Meetings (neueste 500). Private
Nachrichten gehören nie dazu. Ausschalten löscht den Dauer-Chat.
DELETE `/api/raeume/{code}/dauer-chat` (Besitzer) → `{ok:true, geloescht:n}`; läuft das Meeting, geht Ereignis `{typ:"chat_geleert"}`
an alle (Client leert die Liste und lädt neu). Höchstens 1000 Nachrichten je Raum (ältere löscht der Hintergrund).

## Zusammenarbeit (X-Teilnahme)
Alle Inhalte gehören zu einem Meeting (Raum + Sitzung) und werden mit seinem Ende gelöscht – außer der Raum hat `chat_speichern`;
dann liefert sie GET `/api/verlauf/{id}/inhalt` dem Besitzer. Server-Haken: `verlauf.INHALT` (f(raum_id, sitzung) → dict).

### Whiteboard
`objekt` = `{id ([A-Za-z0-9_-]{6,40}, vom Client), art: stift|linie|pfeil|rechteck|ellipse|text|notiz, farbe "#rrggbb", breite 1–40,`
`punkte:[x0,y0,…]` (stift, ≤ 2000 Punkte) bzw. `x, y, w, h` (sonst; Linie/Pfeil von x,y nach x+w,y+h), `text` (text/notiz, ≤ 2000)}`.
Zahlen werden auf 0,1 gerundet, unbekannte Felder verworfen. `op` = `{art:"neu", objekt}` · `{art:"aendern", objekt}` · `{art:"loeschen", id}`.

| Methode + Pfad | Wer | Body | Antwort |
|---|---|---|---|
| GET `/api/raeume/{code}/whiteboard` | alle | – | `{seq, objekte:[objekt] (Zeichenreihenfolge), gesperrt, darf_zeichnen}` |
| POST `/api/raeume/{code}/whiteboard/ops` | alle (gesperrt: host/cohost) | `{ops:[op] (1–20)}` (≤ 64 KB) | `{seq, angenommen}`; 403 gesperrt, 409 `neu` mit vorhandener id, 413 voll (3000 Objekte / 3 MB) |
| POST `/api/raeume/{code}/whiteboard/sperren` | host/cohost | `{an: bool}` | `{ok, gesperrt}` |
| POST `/api/raeume/{code}/whiteboard/leeren` | host/cohost | – | `{ok, seq}` |
| POST `/api/raeume/{code}/whiteboard/zeigen` | host/cohost | – | `{ok}` – öffnet das Whiteboard bei allen |

`aendern`/`loeschen` einer unbekannten id werden still übergangen (gleichzeitig gelöscht). Ab 4000 Log-Einträgen verdichtet der
Server das Log zu einem Schnappschuss. Grenze 80 Anfragen je 10 s und Person.
Pakete (Topic `whiteboard`, nur vom Server – Clients verwerfen Pakete mit Absender): `{typ:"ops", von_seq, seq, ops, von, von_name}` ·
`{typ:"neu_laden", seq, von, von_name}` (Paket wäre > 14 KB) · `{typ:"geleert", seq, von_name}` · `{typ:"gesperrt", an, von_name}` ·
`{typ:"zeigen", von, von_name}`. Passt `von_seq` nicht zu `seq + 1` des Clients, lädt er den Stand per GET. Rückgängig/Wiederholen
macht der Client (nur eigene Schritte, als Gegen-Operation).

### Notizen
Ein Text (Markdown light: `#`-Überschriften, `-`/`*`-Listen, `- [ ]`/`- [x]` Aufgaben, `**fett**`, `` `code` ``) je Meeting.
Verfahren: letzter Schreiber gewinnt mit Versionsprüfung ([ADR 0004](adr/0004-notizen-ohne-ot.md)) – kein OT.

| Methode + Pfad | Wer | Body | Antwort |
|---|---|---|---|
| GET `/api/raeume/{code}/notizen` | alle | – | `{text, version, geaendert, von_name}` (leer: `version` 0) |
| PUT `/api/raeume/{code}/notizen` | alle | `{text (≤ 100 000 Zeichen), version}` (Version, auf der die Änderung beruht) | neuer Stand; 409 `{fehler, konflikt:{text, version, geaendert, von_name}}` wenn inzwischen geändert; 413 zu lang |

60 Speichervorgänge je Minute und Person. Ereignis (Topic `agora`): `{typ:"notizen", version, von, von_name, text?}` – `text` fehlt, wenn
das Paket zu groß wäre (dann GET). Der Client führt bei 409 bzw. einem Ereignis während eigener ungespeicherter Änderungen beide
Fassungen zeilenweise zusammen (3-Wege); überschneiden sie sich, wählt die Person „Meine Fassung behalten“ oder „Neue übernehmen“.

### Fragen (Q&A)
`frage` = `{id, text, anonym, name (null bei anonym – auch für Host/Co-Hosts), status: "offen"|"beantwortet"|"verworfen", hervorgehoben,
stimmen, antwort, antwort_von, erstellt, beantwortet}`, in Antworten an die Person selbst zusätzlich `meins`, `ich_gestimmt`.
Der Server speichert identity, Name und Konto auch bei anonymen Fragen (Missbrauch bleibt nachvollziehbar; Protokoll ohne Inhalt).

| Methode + Pfad | Wer | Body | Antwort |
|---|---|---|---|
| GET `/api/raeume/{code}/fragen` | alle | – | `{fragen:[frage]}` (verworfene nur für Moderation und die fragende Person) |
| POST `/api/raeume/{code}/fragen` | alle | `{text (≤ 500), anonym?: false}` | `{frage}`; 10/min, höchstens 300 je Meeting |
| POST `/api/raeume/{code}/fragen/{id}/stimme` | alle außer der fragenden Person | `{an: bool}` | `{frage}`; 400 eigene, 409 verworfen |
| POST `/api/raeume/{code}/fragen/{id}/moderieren` | host/cohost | `{aktion: beantwortet\|offen\|verworfen\|hervorheben\|nicht_hervorheben, antwort? (≤ 1000, bei beantwortet)}` | `{frage}` |
| DELETE `/api/raeume/{code}/fragen/{id}` | fragende Person (solange offen) oder host/cohost | – | `{ok:true}` |

Ereignis (Topic `agora`): `{typ:"frage", aktion: neu|aktualisiert|geloescht, frage}` (ohne `meins`/`ich_gestimmt`). Verworfene gehen
mit Inhalt nur an Moderation und fragende Person, alle anderen bekommen `geloescht`; „Wieder öffnen“ schickt `neu` an alle.

### Dateien
`datei` = `{id, name, groesse, typ, von_name, erstellt, geprueft (clamd hat geprüft), bild (png/jpg/gif/webp), gefaehrlich}`, an die
Person selbst zusätzlich `meins`.

| Methode + Pfad | Wer | Body | Antwort |
|---|---|---|---|
| GET `/api/raeume/{code}/dateien` | alle | – | `{dateien:[datei], belegt, grenze_meeting, grenze_datei, virenpruefung, endungen:[…]}` |
| POST `/api/raeume/{code}/dateien` | alle (Recht `chat` aus: nur host/cohost) | roher Body, Header `X-Dateiname` (URL-kodiert) | `{datei}`; 400 leer/ohne Namen, 413 > 25 MB, 415 Endung nicht erlaubt, 422 Virus gefunden, 503 Virenprüfung nicht erreichbar, 507 Meeting voll (200 MB); 20 Uploads je 10 min |
| GET `/api/raeume/{code}/dateien/{id}` | alle (X-Teilnahme) | `?vorschau=1` | Datei; immer `nosniff`, `Content-Security-Policy: sandbox`, `no-store`; Anhang, nur Bilder mit `vorschau=1` inline; gefährliche Typen als `application/octet-stream`; 120/min |
| DELETE `/api/raeume/{code}/dateien/{id}` | wer sie geteilt hat oder host/cohost | – | `{ok:true}` |
| GET `/api/verlauf/{vid}/dateien/{id}` | Besitzer (Cookie) | – | gespeicherte Datei (nur `chat_speichern`, bis 30 Tage nach dem Meeting), immer als Anhang |

Der Typ kommt nur aus der Endung. Erlaubte Endungen: `AGORA_DATEI_ENDUNGEN` (Komma-Liste) oder die Vorgabe in `app/dateien.py`
(Dokumente, Bilder, Archive, Audio/Video – keine ausführbaren, HTML-, Skript- oder SVG-Dateien). Werden gefährliche Typen
erlaubt, gibt es sie trotzdem nur als Anhang mit `octet-stream`. Virenprüfung: `AGORA_CLAMAV=host:port` → clamd INSTREAM vor der
Annahme; Fund 422, Fehler 503 (nichts ungeprüft). Der Download geht per `fetch` mit X-Teilnahme (Blob), Bildvorschau ebenso.
Ereignis (Topic `agora`): `{typ:"datei", aktion: neu|geloescht, datei}`.
## Webinar + Registrierung (app/webinar.py)
Raum-Feld `webinar` (in `raum_json`): `false` oder `{registrierung, anmelde_link}`. Raum-Metadaten bekommen `webinar: true`.
Rollen im Webinar: `host`, `cohost`, `panelist` (senden) und `zuschauer` (LiveKit-Token mit `canPublish: false`, keine Quellen).
`RANG`: panelist 1, zuschauer 0. Wer nicht Besitzer ist und ohne Panel-Link kommt, wird `zuschauer`. Co-Host abgeben bzw.
Host übertragen im Webinar → `panelist`.

| Methode + Pfad | Wer | Body / Antwort |
|---|---|---|
| GET `/api/raeume/{code}/webinar` | Besitzer | `{webinar: {an, chat, fragen_an, registrierung, kapazitaet, fragen[], anmelde_link, angemeldet, panel:[{id,name,link,erstellt}]}}` |
| PATCH `/api/raeume/{code}/webinar` | Besitzer | `{an?, chat?: "alle"\|"panel"\|"aus", fragen_an?, registrierung?, kapazitaet?: 1–100000\|null, fragen?: [≤5 × ≤200 Zeichen]}`. `an` umschalten nur ohne laufendes Meeting (409) |
| POST `/api/raeume/{code}/webinar/panel` | Besitzer | `{name}` → `{panel}`; Link `/r/{code}?panel=<token>` (höchstens 20) |
| DELETE `/api/raeume/{code}/webinar/panel/{id}` | Besitzer | Link ungültig |
| GET `/api/raeume/{code}/webinar/anmeldungen` | Besitzer | `{anmeldungen:[{id,name,email,antworten[],angemeldet,beigetreten}], fragen, kapazitaet}` |
| GET `/api/raeume/{code}/webinar/anmeldungen.csv` | Besitzer | CSV (`;`, UTF-8 mit BOM, Formeln entschärft) |
| DELETE `/api/raeume/{code}/webinar/anmeldungen/{id}` | Besitzer | einzelne Anmeldung löschen |
| GET `/api/webinare/{code}` | öffentlich | `{code,titel,besitzer_name,geplant,naechster_termin,kalender,fragen,frei,offen}`; 404 ohne Registrierung |
| POST `/api/webinare/{code}/anmelden` | öffentlich | `{name, email, antworten?:[]}` → `{ok}`; Mail mit `/r/{code}?anmeldung=<token>` (+ .ics bei Termin). Gleiche Adresse erneut → neuer Link, alter ungültig. 409 ausgebucht/beendet |
| POST `/api/webinare/{code}/abmelden` | öffentlich | `{token}` → Anmeldung gelöscht |
| POST `/api/raeume/{code}/beitreten` | – | zusätzlich `panel` oder `anmeldung` (Token). Panel-Link überspringt den Warteraum. Mit Registrierung ohne gültigen Link: 403 |
| POST `/api/raeume/{code}/webinar/rolle` | Host/Co-Host (X-Teilnahme) | `{ziel, rolle: "panelist"\|"zuschauer"}` – aufs Podium holen / zurück; Ereignis `rolle` an alle |
| GET `/api/raeume/{code}/webinar/zustand` | X-Teilnahme | `{webinar:false}` oder `{webinar:true, chat, fragen_an}`; fürs Panel zusätzlich `zuschauer`, `panel` (verbunden), `angemeldet` |

Chat der Zuschauer (`chat`): `alle`; `panel` = nur private Nachrichten an Host/Co-Host/Panel; `aus`. `fragen_an` ist der
Schalter für das Q&A-Modul (`webinar.einstellungen(raum)['fragen_an']`).
Hooks in `raeume.py` (für andere Module): `BEITRITT_ROLLE`, `ROLLE_STANDARD`, `META_EXTRA`, `RAUM_JSON_EXTRA`, `grundrolle()`.

## Server-Aufnahme + Livestream (LiveKit Egress, app/egress.py – nur mit `AGORA_EGRESS=1`)
Ohne Feature-Flag: GET liefert `{verfuegbar:false}`, POST 404 – die Oberfläche blendet die Einträge aus.

| Methode + Pfad | Wer (X-Teilnahme) | Body / Antwort |
|---|---|---|
| GET `/api/raeume/{code}/egress` | alle | `{verfuegbar}`; Host/Co-Host zusätzlich `aufnahme`, `stream`: `null` oder `{id, art, status: startet\|laeuft\|endet\|fertig\|fehler, fehler, von_name, layout, gestartet, aufnahme_id}` (gleicht dabei mit LiveKit ab) |
| POST `/api/raeume/{code}/egress/aufnahme` | Host/Co-Host | `{aktion:"start", layout?: "galerie"\|"sprecher"}` → `{ok, id}`; `{aktion:"stopp"}`. 409 läuft schon / Browser-Aufnahme läuft, 507 Kontingent des Besitzers voll, 502 Egress-Fehler |
| POST `/api/raeume/{code}/egress/stream` | Host/Co-Host | `{aktion:"start", url: "rtmp(s)://…", schluessel, layout?}` → `{ok, id}`; `{aktion:"stopp"}`. 400 bei anderem Schema, Zugangsdaten in der URL, Leerzeichen oder Zielen im eigenen Netz (Loopback/privat/nicht global) |

Raum-Metadaten: `aufnahme: {von_name, server: true}` während der Server-Aufnahme, `livestream: {von_name}` während des
Streams. Ereignis (Topic `agora`, nur an Host/Co-Hosts): `{typ:"egress", art, status, fehler}`; beim Start zusätzlich
`{typ:"aufnahme", aktiv:true, von_name, server:true}` an alle. Fertige Aufnahmen erscheinen in `GET /api/aufnahmen`
(`typ: video/mp4`). Fehlertexte sind von Stream-URL und Schlüssel bereinigt (`***`).

## Live-Untertitel + Transkript (app/untertitel.py)
| Methode + Pfad | Wer | Body / Antwort |
|---|---|---|
| POST `/api/raeume/{code}/untertitel` | X-Teilnahme (nicht `zuschauer`) | `{text ≤500, final: bool, dauer_ms?: 0–120000}` → `{ok}`; verteilt `{identity, name, text, final, zeit}` über Topic `untertitel` (nur vom Server angenommen). Gespeichert nur `final` und nur mit `transkript_speichern`. Grenze 60 je 30 s |
| PATCH `/api/raeume/{code}/transkript` | Besitzer (Cookie) | `{speichern: bool}`; Metadaten `transkript: true`, Ereignis `{typ:"transkript", an}` |
| GET `/api/raeume/{code}/transkripte` | Besitzer | `{speichern, aktuelle_sitzung, transkripte:[{sitzung, abschnitte, beginn, ende}]}` |
| GET `/api/raeume/{code}/transkript?sitzung=&format=txt\|vtt` | Besitzer | Datei (ohne `sitzung`: neuestes Meeting); 404 wenn leer |
| DELETE `/api/raeume/{code}/transkript?sitzung=` | Besitzer | ein Meeting oder alle |
| POST `/api/aufnahmen/{id}/transkript` | Besitzer der Aufnahme | nur mit `AGORA_WHISPER_URL` (sonst 404); startet Hintergrund-Job → `{transkript:{status:"laeuft"}}`; 409 läuft schon |
| GET `/api/aufnahmen/{id}/transkript[?format=vtt\|txt]` | Besitzer | ohne `format`: `{transkript: null\|{status, fehler}, verfuegbar}`; sonst die Datei |

`raum_json` hat zusätzlich `transkript_speichern`; `aufnahme_json` hat `transkript` (Status oder `null`) und `transkript_moeglich`.
Hooks in `aufnahmen.py`: `BEIM_ENTFERNEN`, `JSON_EXTRA`.

