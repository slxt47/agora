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

**Schlüssel für eine Organisation:** Owner und Admins wählen beim Anlegen „Gilt für: <Organisation>“. So ein Schlüssel
sieht und ändert nur die Meetings und Aufnahmen dieser Organisation (neue Meetings gehören ihr, mit ihren
Standard-Rechten), kann keine Webhooks verwalten und gilt nur, solange das Konto dort Owner oder Admin ist (sonst 403;
beim Austritt oder Herabstufen wird er gelöscht). Persönliche Schlüssel sehen die Meetings einer Organisation nicht.

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

## Webhooks

Agora ruft bei Ereignissen eine eigene HTTPS-Adresse auf. Einrichten im Konto unter **Entwickler → Webhooks** oder per
API (Recht `webhooks`). Höchstens 5 Endpunkte je Konto.

| Ereignis | wann | `daten` |
|---|---|---|
| `meeting.gestartet` | erste Person tritt einem eigenen Raum bei | `meeting {code, titel, link}`, `sitzung`, `gestartet` |
| `meeting.beendet` | Host beendet, Raum gelöscht oder 10 min leer | `meeting`, `sitzung`, `gestartet`, `beendet` |
| `teilnehmer.beigetreten` | Agora gibt einen Zugang aus (Lobby → Meeting, auch nach dem Warteraum) | `meeting`, `sitzung`, `teilnehmer {identity, name, rolle}` |
| `teilnehmer.gegangen` | nicht mehr verbunden (minütliche Prüfung, also bis ~1 min später) oder Meeting-Ende | wie oben + `grund`: `verlassen` \| `meeting_beendet` |
| `aufnahme.fertig` | Aufnahme liegt in der Bibliothek (Upload oder Server-Aufnahme) | `aufnahme {id, titel, raum_code, groesse, typ, dauer, erstellt, ablauf}` |
| `webinar.anmeldung` | jemand meldet sich zu einem eigenen Webinar an | `meeting`, `anmeldung {name, email, antworten, zeit}` |
| `test` | „Test senden“ | `nachricht`, `webhook` |

Aufruf: `POST <deine URL>`, Body JSON `{"id": "evt_…", "ereignis": "…", "erstellt": "…Z", "daten": {…}}`, Kopfzeilen
`X-Agora-Ereignis`, `X-Agora-Zustellung` (gleich bei Wiederholungen derselben Zustellung – zum Entdoppeln) und
`X-Agora-Signatur: t=<Unix-Zeit>,v1=<Hex>`. `v1` = HMAC-SHA-256 mit dem Geheimnis (`whsec_…`, nur beim Anlegen bzw. bei
„Neues Geheimnis“ sichtbar) über `"<t>.<roher Body>"`. Prüfe die Signatur mit dem **unveränderten** Body und verwirf
Aufrufe mit `t` älter als 5 Minuten.

```python
import hashlib, hmac, time

def echt(kopf: str, body: bytes, geheim: str, toleranz=300) -> bool:
    teile = dict(x.split('=', 1) for x in kopf.split(','))
    if abs(time.time() - int(teile['t'])) > toleranz:
        return False
    soll = hmac.new(geheim.encode(), teile['t'].encode() + b'.' + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(soll, teile['v1'])
```

```js
// Node (Express: express.raw({ type: 'application/json' }) für den rohen Body)
import { createHmac, timingSafeEqual } from 'node:crypto';
function echt(kopf, body, geheim) {
  const t = Object.fromEntries(kopf.split(',').map((x) => x.split('=')));
  if (Math.abs(Date.now() / 1000 - Number(t.t)) > 300) return false;
  const soll = createHmac('sha256', geheim).update(`${t.t}.`).update(body).digest('hex');
  return soll.length === t.v1.length && timingSafeEqual(Buffer.from(soll), Buffer.from(t.v1));
}
```

**Zustellung:** im Hintergrund, Zeitlimit 10 s je Versuch. Erfolg = HTTP 2xx. Sonst bis zu 5 Versuche: sofort, nach
1 min, 5 min, 30 min und 2 h. Weiterleitungen (3xx) werden **nicht** verfolgt und zählen als Fehlschlag. Nach
3 Zustellungen in Folge, die auch nach 5 Versuchen scheiterten, schaltet Agora den Endpunkt ab und schickt dem
Besitzer eine Mail; „Einschalten“ setzt den Zähler zurück. Reihenfolge ist nicht garantiert.

**Ziel-Regeln (Schutz vor SSRF):** nur `https://`, ohne Benutzer/Passwort/Fragment. Der Name wird aufgelöst, **alle**
Adressen müssen öffentlich sein (kein localhost, keine privaten, Link-Local-, CGNAT- oder IPv4-in-IPv6-Adressen);
verbunden wird genau mit der geprüften Adresse (TLS-Zertifikat und SNI für den Namen). Die Prüfung läuft beim Anlegen
und vor jedem Versuch.

**Protokoll:** je Endpunkt die letzten 50 Versuche (höchstens 14 Tage) mit Zeit, Ereignis, Versuch, HTTP-Status, Dauer
und Fehlertext – keine Antwort-Inhalte.

```sh
# anlegen (Geheimnis nur in dieser Antwort)
curl -s -X POST -H "Authorization: Bearer $SCHLUESSEL" -H 'Content-Type: application/json' \
  -d '{"url": "https://beispiel.de/agora-hook", "ereignisse": ["meeting.gestartet", "aufnahme.fertig"]}' "$AGORA/api/v1/webhooks"
curl -s -H "Authorization: Bearer $SCHLUESSEL" "$AGORA/api/v1/webhooks"
curl -s -X POST -H "Authorization: Bearer $SCHLUESSEL" "$AGORA/api/v1/webhooks/1/test"
curl -s -H "Authorization: Bearer $SCHLUESSEL" "$AGORA/api/v1/webhooks/1/zustellungen"
curl -s -X PATCH -H "Authorization: Bearer $SCHLUESSEL" -H 'Content-Type: application/json' -d '{"aktiv": true}' "$AGORA/api/v1/webhooks/1"
curl -s -X POST -H "Authorization: Bearer $SCHLUESSEL" "$AGORA/api/v1/webhooks/1/geheimnis"
curl -s -X DELETE -H "Authorization: Bearer $SCHLUESSEL" "$AGORA/api/v1/webhooks/1"
```
