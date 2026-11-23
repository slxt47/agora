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
| `/api/konto/bestaetigen?t=` | – | E-Mail bestätigen → Weiterleitung `/?bestaetigt=1` bzw. `/?bestaetigt=0` |

## Konto
| Methode + Pfad | Body | Antwort |
|---|---|---|
| POST `/api/konto/registrieren` | `{name, email, passwort}` (Passwort ≥ 10) | `{ok:true}` – Mail mit Bestätigungslink |
| POST `/api/konto/anmelden` | `{email, passwort}` | `{ok:true, ich}` + Cookie. 403 `nicht bestätigt` |
| POST `/api/konto/bestaetigung-erneut` | `{email}` | `{ok:true}` (immer) |
| POST `/api/konto/abmelden` | – | `{ok:true}` |
| GET `/api/konto/ich` | – | `{angemeldet:false}` oder `{angemeldet:true, ich}` |
| PATCH `/api/konto/ich` | `{name?, zeitzone?, sprache?: "de"\|"en", farbe?: "#rrggbb"}` | `{ich}` |
| POST `/api/konto/passwort-vergessen` | `{email}` | `{ok:true}` (immer) |
| POST `/api/konto/passwort-setzen` | `{token, passwort}` | `{ok:true}` (alle Sitzungen ungültig) |
| POST `/api/konto/passwort-aendern` | `{alt, neu}` | `{ok:true}` |
| GET `/api/konto/export` | – | JSON-Download aller eigenen Daten |
| DELETE `/api/konto` | `{passwort}` | `{ok:true}` – löscht Konto, Räume, Aufnahmen |

`ich` = `{id, name, email, zeitzone, sprache, farbe, persoenlicher_raum}` (farbe = Avatar-Farbe `#rrggbb`, persoenlicher_raum = Code).
Grenzen: Anmelden 10/10 min je IP+Mail, Registrieren 5/h je IP, Mails 3/h je Adresse.

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

## Aufzeichnung (lokal im Browser des Hosts, dann Upload in die Bibliothek)
POST `/api/raeume/{code}/aufnahme` (X-Teilnahme, host/cohost) `{aktiv:bool}` → setzt Indikator für alle (Metadaten + Ereignis).
POST `/api/aufnahmen` (angemeldet) roher Body `video/webm` oder `video/mp4`, Header `X-Raum: <code>`, `X-Titel: <urlencoded>`, optional `X-Dauer: <sekunden>` → `{aufnahme}`. 413/415/507 bei zu groß/falschem Typ/Speicher voll. Max 500 MB/Datei, 5 GB/Konto.
GET `/api/aufnahmen` → `{aufnahmen:[aufnahme], belegt, kontingent}` · GET `/api/aufnahmen/{id}/datei` (Range-fähig) · DELETE `/api/aufnahmen/{id}`.
POST `/api/aufnahmen/{id}/freigabe` `{tage: 1|7|30, passwort?}` → `{link}` (`/a/<token>`) · DELETE `/api/aufnahmen/{id}/freigabe`.
GET `/api/freigaben/{token}` → `{titel, dauer, groesse, typ, passwort_noetig}`; GET `/api/freigaben/{token}/datei?p=<passwort>`.
`aufnahme` = `{id, titel, raum_code, groesse, typ, dauer, erstellt, ablauf, freigabe: null|{link, bis, passwort}}`. Aufnahmen werden nach 90 Tagen gelöscht.

## Sonstiges
GET `/api/gesund` → `{ok:true}` · GET `/api/einstellungen` → `{livekit_url, max_teilnehmer, version}`.

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
