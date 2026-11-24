# Agora-Meetings einbetten

Ein Meeting lässt sich auf einer eigenen Webseite in einem `<iframe>` zeigen – mit Kamera, Mikrofon und
Bildschirmfreigabe.

## 1. Einbetten erlauben

Im Konto beim Raum auf **Einbetten** klicken, „Einbetten erlauben“ einschalten und die **Herkünfte** (Origins) der Seiten
eintragen, die das Meeting zeigen dürfen – je Zeile eine, z. B. `https://beispiel.de` oder `https://app.beispiel.de:8443`.

- nur die Herkunft: Schema + Host (+ Port), **ohne** Pfad, Query oder Platzhalter (`*`); höchstens 10
- `https://` Pflicht; `http://` nur für `localhost`, `127.0.0.1` und `[::1]` (lokale Entwicklung)
- Groß/Klein und ein abschließendes `/` werden vereinheitlicht

Agora liefert das Meeting dann unter `https://agora.slxt.dev/e/<code>` mit
`Content-Security-Policy: … frame-ancestors <genau diese Herkünfte>` und ohne `X-Frame-Options` aus. Jede andere
Agora-Seite (auch `/r/<code>`) bleibt bei `frame-ancestors 'none'` und `X-Frame-Options: DENY`. Ist Einbetten aus, gibt
`/e/<code>` 404. `frame-ancestors` prüft **alle** Vorfahren: Liegt deine Seite selbst in einem iframe, muss auch dessen
Herkunft in der Liste stehen.

## 2. Einbinden

```html
<div id="agora-meeting"></div>
<script src="https://agora.slxt.dev/embed.js"></script>
<script>
  const meeting = Agora.einbetten('#agora-meeting', {
    code: 'abc-defg-hij',      // Pflicht
    name: 'Gast',              // optional: Name vorbelegen
    hoehe: '640px',            // optional (Standard 640px), breite: '100%'
  });
  meeting.on('bereit', () => console.log('geladen'));
  meeting.on('beigetreten', (e) => console.log('im Meeting als', e.identity, e.rolle));
  meeting.on('verlassen', (e) => console.log('verlassen:', e.grund));   // verlassen | beendet | entfernt | abgelehnt | …
  // meeting.entfernen() nimmt das iframe wieder heraus
</script>
```

Ohne eigenes Skript: `<div data-agora-meeting="abc-defg-hij" data-agora-name="Gast" data-agora-hoehe="640px"></div>`
plus `<script src="https://agora.slxt.dev/embed.js"></script>`.

`embed.js` ist klein, ohne Abhängigkeiten, selbst gehostet (eine Stunde cachebar, `Cross-Origin-Resource-Policy:
cross-origin`). Wer lieber selbst ein iframe schreibt:

```html
<iframe src="https://agora.slxt.dev/e/abc-defg-hij?herkunft=https%3A%2F%2Fbeispiel.de"
        allow="camera; microphone; display-capture; fullscreen; autoplay; screen-wake-lock"
        allowfullscreen style="width:100%;height:640px;border:0"></iframe>
```

**Wichtig:** Ohne `allow="camera; microphone; display-capture"` blockiert der Browser Kamera, Mikrofon und
Bildschirmfreigabe im iframe (Permissions Policy). Setzt deine Seite selbst eine `Permissions-Policy`, muss sie diese
Funktionen für `https://agora.slxt.dev` erlauben, z. B. `camera=(self "https://agora.slxt.dev")`. Deine CSP braucht
`frame-src https://agora.slxt.dev` und `script-src https://agora.slxt.dev` (für `embed.js`).

## 3. Ereignisse (postMessage)

Die Seite im iframe schickt `{quelle: 'agora', typ, code, …}` per `postMessage` – **nur** an Herkünfte aus der Liste
(sie fragt `GET /api/einbetten/<code>` und nutzt `location.ancestorOrigins` bzw. den Parameter `herkunft`, immer mit
fester `targetOrigin`, nie `*`). `embed.js` nimmt nur Nachrichten an, deren `origin` die Agora-Herkunft ist **und**
deren `source` genau dieses iframe ist.

| `typ` | Felder |
|---|---|
| `bereit` | – (Seite geladen) |
| `beigetreten` | `identity`, `rolle` |
| `verlassen` | `grund`: `verlassen`, `beendet`, `beendetSelbst`, `entfernt`, `abgelehnt`, `doppelt`, `verbindung` |

Wer ohne `embed.js` arbeitet, prüft selbst: `if (e.origin !== 'https://agora.slxt.dev' || e.source !== iframe.contentWindow) return;`

## Grenzen

- **Keine Anmeldung im iframe:** Das Agora-Cookie ist `SameSite=Lax` und wird im fremden Kontext nicht mitgeschickt.
  Alle treten als **Gast** bei; Passcode (auch über `passcode` in `Agora.einbetten`, landet dann in der iframe-URL)
  und Warteraum gelten wie sonst. Der Besitzer startet und moderiert das Meeting über den normalen Link `/r/<code>`.
- Links auf andere Agora-Seiten (Startseite, Konto) öffnen sich in einem neuen Tab.
- Ende-zu-Ende-verschlüsselte Meetings brauchen den Schlüssel im Fragment (`/e/<code>#k=…`); `embed.js` setzt ihn nicht –
  dafür ein eigenes iframe mit Fragment verwenden.
- Browser mit strengem Drittanbieter-Schutz (Safari) speichern im iframe nichts dauerhaft (z. B. den Namen).
