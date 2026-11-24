# Agora – öffentliche API (`/api/v1`)

Für eigene Skripte und Server: Meetings planen, Beitrittslinks holen, sehen, wer gerade im Meeting ist, Aufnahmen
herunterladen. Maschinenlesbar: [`openapi.yaml`](openapi.yaml) (OpenAPI 3.1, von Hand gepflegt; ein Test prüft, dass
jede Route darin steht).

## Schlüssel

Im Konto unter **Entwickler → API-Schlüssel → Neuer Schlüssel**: Name, Rechte und Gültigkeit (30/90/365 Tage oder ohne
Ablauf) wählen. Der Schlüssel (`agora_…`, 256 Bit Zufall) erscheint **genau einmal** – Agora speichert nur seinen
SHA-256 und die ersten 12 Zeichen zum Wiedererkennen. Die Liste zeigt, wann ein Schlüssel zuletzt benutzt wurde
(Zeit, keine IP). „Widerrufen“ löscht ihn sofort; danach antwortet die API mit 401. Höchstens 10 Schlüssel je Konto.
Erstellen und Widerrufen stehen in den Sicherheitsereignissen des Kontos. Ein gesperrtes Konto (Admin) kann keine
Schlüssel nutzen.

| Recht | erlaubt |
|---|---|
| `meetings:lesen` | Meetings auflisten/ansehen, Beitrittslink, Teilnehmende eines laufenden Meetings |
| `meetings:schreiben` | Meetings anlegen, ändern, löschen |
| `aufnahmen:lesen` | Aufnahmen auflisten, Download-Links erzeugen |
| `webhooks` | Webhooks verwalten (siehe unten) |

Die Rechte sind getrennt: Ein Schlüssel nur mit `meetings:schreiben` kann anlegen (die Antwort enthält das Meeting),
aber nicht auflisten.

## Grundregeln

- `Authorization: Bearer agora_…` bei jeder Anfrage. Cookies zählen hier nicht, ein `X-Agora`-Kopf ist nicht nötig.
- **Kein CORS:** Browser auf fremden Seiten können die API nicht lesen – der Schlüssel gehört auf einen Server, nie in
  eine Webseite.
- JSON rein und raus; Fehler: HTTP-Status + `{"fehler": "…"}` (deutsch). Zeiten: ISO 8601 in UTC (`…Z`).
- 401 = Schlüssel fehlt/ungültig/abgelaufen/widerrufen (`WWW-Authenticate: Bearer`), 403 = Recht fehlt,
  404 = gibt es nicht oder gehört einem anderen Konto, 429 = Grenze erreicht (`Retry-After` in Sekunden).
- **Grenzen:** 120 Anfragen pro Minute je Schlüssel; 30 Anfragen mit ungültigem Schlüssel je IP in 10 min
  (danach 429 für 10 min); Meetings anlegen zählt zur Konto-Grenze von 60 pro Stunde (wie im Konto).

```sh
export AGORA=https://agora.slxt.dev
export SCHLUESSEL=agora_…            # aus Konto → Entwickler
```

## Meetings

Ein Meeting im Antwortformat:

```json
{"code": "abc-defg-hij", "titel": "Jour fixe", "art": "geplant", "link": "https://agora.slxt.dev/r/abc-defg-hij",
 "aktiv": false, "beendet": false, "passcode_noetig": true, "warteraum": true, "chat_speichern": false, "e2ee": false,
 "geplant": {"start": "2030-01-07T08:00:00Z", "dauer_min": 30, "zeitzone": "Europe/Berlin", "beschreibung": "",
             "wiederholung": "woechentlich", "bis": "2030-03-01T22:59:59Z"},
 "naechster_termin": "2030-01-07T08:00:00Z", "erstellt": "2029-12-20T10:00:00Z"}
```

`art`: `sofort` (Sofort-Meeting), `geplant` (mit Termin, auch täglich/wöchentlich), `persoenlich` (persönlicher Raum).

```sh
# Auflisten (optional ?art=geplant)
curl -s -H "Authorization: Bearer $SCHLUESSEL" "$AGORA/api/v1/meetings"

# Sofort-Meeting mit Warteraum und Passcode
curl -s -X POST -H "Authorization: Bearer $SCHLUESSEL" -H 'Content-Type: application/json' \
  -d '{"titel": "Kurz besprechen", "warteraum": true, "passcode": "4711-abc"}' "$AGORA/api/v1/meetings"

# Wöchentlich wiederkehrend; Zeit ohne Zone = Zeitzone des Meetings
curl -s -X POST -H "Authorization: Bearer $SCHLUESSEL" -H 'Content-Type: application/json' \
  -d '{"titel": "Jour fixe", "geplant": {"start": "2030-01-07T09:00", "dauer_min": 30, "zeitzone": "Europe/Berlin",
       "wiederholung": "woechentlich", "bis": "2030-03-01"}}' "$AGORA/api/v1/meetings"

# Ändern (nur übergebene Felder; "geplant" ersetzt den Termin ganz; "passcode": "" entfernt ihn)
curl -s -X PATCH -H "Authorization: Bearer $SCHLUESSEL" -H 'Content-Type: application/json' \
  -d '{"titel": "Jour fixe (neu)"}' "$AGORA/api/v1/meetings/abc-defg-hij"

# Löschen (beendet ein laufendes Meeting; der persönliche Raum lässt sich nicht löschen)
curl -s -X DELETE -H "Authorization: Bearer $SCHLUESSEL" "$AGORA/api/v1/meetings/abc-defg-hij"

# Beitrittslink
curl -s -H "Authorization: Bearer $SCHLUESSEL" "$AGORA/api/v1/meetings/abc-defg-hij/link"
# → {"code": "abc-defg-hij", "link": "https://agora.slxt.dev/r/abc-defg-hij", "passcode_noetig": true, "e2ee": false, "naechster_termin": …}

# Wer ist gerade drin? (live vom Medienserver)
curl -s -H "Authorization: Bearer $SCHLUESSEL" "$AGORA/api/v1/meetings/abc-defg-hij/teilnehmer"
# → {"aktiv": true, "teilnehmer": [{"identity": "u3-…", "name": "Ada", "rolle": "host", "gast": false,
#                                  "beigetreten": "…Z", "breakout": false}]}
```

Felder beim Anlegen/Ändern: `titel`, `passcode` (4–32 Zeichen), `warteraum`, `chat_speichern`, `geplant`
(`start`, `dauer_min` 5–1440, `zeitzone`, `beschreibung`, `wiederholung` `keine|taeglich|woechentlich`, `bis`).
Andere Felder → 400. Ende-zu-Ende-Verschlüsselung lässt sich über die API nicht einschalten (der Schlüssel entsteht im
Browser des Hosts); bei `e2ee: true` fehlt dem Link deshalb der Schlüssel.

## Aufnahmen

```sh
curl -s -H "Authorization: Bearer $SCHLUESSEL" "$AGORA/api/v1/aufnahmen"
curl -s -H "Authorization: Bearer $SCHLUESSEL" "$AGORA/api/v1/aufnahmen/<id>"

# Kurzlebiger, signierter Download-Link (15 min) – ohne Schlüssel abrufbar
URL=$(curl -s -H "Authorization: Bearer $SCHLUESSEL" "$AGORA/api/v1/aufnahmen/<id>/download" | jq -r .url)
curl -s -o aufnahme.webm "$URL"
```

Der Link (`/api/v1/download/<id>?bis=…&sig=…`) ist mit HMAC-SHA-256 (Schlüssel aus `AGORA_GEHEIM`) über Aufnahme,
Konto und Ablauf signiert; geänderte Werte, ein abgelaufener Link oder eine gelöschte Aufnahme → 403. Wer den Link
hat, kann die Datei bis zum Ablauf laden – nur an Dienste geben, denen man die Aufnahme anvertraut.
