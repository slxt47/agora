# Browser-Kompatibilität

Stand 26.11.2026 · automatisch geprüft mit Playwright 1.64.1 (Linux arm64, headless, Kamera/Mikro-Attrappen) gegen eine
Wegwerf-Testinstanz mit echter LiveKit.

```sh
BROWSER=firefox  sh tests/e2e/lauf.sh      # oder webkit / chromium  → tests/e2e/browser_matrix.py
sh tests/e2e/lauf.sh                       # ohne BROWSER: der volle Chromium-Ablauf (browser_e2e.py) wie bisher
APP_PORT=8221 LK_PORT=7820 BROWSER=webkit sh tests/e2e/lauf.sh   # eigene Ports, wenn mehrere Läufe parallel laufen
```

Jeder Lauf schreibt `tests/e2e/bilder/matrix-<browser>.json` und Bildschirmfotos `matrix-<browser>-*.png`. Exit-Code 1
nur bei **FEHLER**; „nicht unterstützt“ heißt: der Browser kann es nicht, und Agora zeigt das sauber an (geprüft).

## Ergebnis

| Funktion | Chromium 156 | Firefox 157 | WebKit 27.2 (Linux-Build) |
|---|---|---|---|
| Startseite, Service Worker (PWA) | ok | ok | ok |
| Registrierung, Mail-Link, Anmeldung | ok | ok | ok |
| Lobby: Vorschau, Kamera-/Mikrofonwahl | ok | ok | ok ¹ |
| Beitritt Host + Gast | ok | ok | ok |
| Video in beide Richtungen (dekodiert + gezeichnet) | ok | ok ² | ok |
| Ton (Audio-Spur des Gegenübers) | ok | ok | ok |
| Chat in beide Richtungen | ok | ok | ok |
| Tastenkürzel M / H | ok | ok | ok |
| Bildschirmfreigabe | ok | nicht automatisierbar ³ | nicht automatisierbar ³ |
| Whiteboard (Host ↔ Gast, Canvas) | ok | ok | ok |
| Lokale Aufnahme + Upload in die Bibliothek | ok (WebM) | ok (WebM) | nicht unterstützt ⁴ |
| Ende-zu-Ende-Verschlüsselung | ok (Insertable Streams) | ok (`RTCRtpScriptTransform`) | nicht unterstützt ⁵ |
| Teams / Organisationen (anlegen, Meeting planen) | ok | ok | ok |
| Status-Seite `/status` (Balken 90/24, 390 px ohne Querscrollen) | ok | ok | ok |
| Video-Effekte (MediaPipe, Unschärfe) | ok | nicht automatisierbar ⁶ | ok |
| keine JS-Fehler | ok | ok | ok ⁷ |

1. WebKit zeichnet die Kamera-Attrappe headless erst nach einer Weile; geprüft wird, dass die Kameraspur am `<video>` läuft.
2. Firefox zählt `getVideoPlaybackQuality().totalVideoFrames` bei MediaStreams nicht; der Test zählt zusätzlich die
   dekodierten Bilder aus `getStats()` (`framesDecoded`) und `mozPaintedFrames`.
3. Headless gibt es keine Bildschirmauswahl: Firefox antwortet `NotFoundError`, WebKit `NotAllowedError`. Geprüft wird,
   dass Agora sauber zurückfällt (Knopf nicht hängend, verständliche Meldung). Von Hand in Firefox und Safari prüfen.
4. Der WebKit-Build für Linux hat kein `MediaRecorder` – der Menüeintrag ist dann gesperrt. Safari auf macOS/iOS hat
   `MediaRecorder` (MP4); Agora wählt dort `video/mp4`, der Server nimmt WebM und MP4 an.
5. Der WebKit-Build für Linux hat weder `RTCRtpScriptTransform` noch Insertable Streams. Die Lobby sperrt dann den
   Schalter und den Beitritt zu E2EE-Räumen mit Hinweis. **Safari ab 15.4 (macOS/iOS) hat `RTCRtpScriptTransform`** –
   E2EE mit Safari ist damit möglich, aber hier nicht automatisch geprüft (siehe unten).
6. Firefox schaltet WebGL headless ohne GPU ab; ohne WebGL2 laufen die MediaPipe-Effekte nicht, Agora zeigt den Hinweis
   „Dieser Browser kann keine Hintergrund-Effekte“. Mit GPU (normaler Desktop) läuft der Canvas-Rückfall von
   `@livekit/track-processors` auch in Firefox.
7. WebKit meldet für jedes `<select>` „Refused to apply a stylesheet …“ – die eingebauten Stile des Auswahlfelds
   fallen unter die CSP `style-src 'self'`. Die Felder funktionieren; Agora selbst setzt keine Inline-Stile.
   Außerdem meldet WebKit eine beim Verlassen der Seite abgebrochene Anfrage als „… due to access control checks“
   (Agora fängt sie ab); auch das zählt der Test nicht als Fehler.

## Gefunden und behoben

- **Aufnahme-Hinweis beim Aufnehmenden selbst:** Der Server meldet „Aufnahme läuft“ an alle, oft bevor der eigene
  Rekorder fertig gestartet ist (in Firefox und Chromium unter Last reproduzierbar). Dann sah der Host selbst den Dialog
  „Dieses Meeting wird aufgezeichnet“, der die Steuerleiste blockierte. Jetzt merkt sich das Meeting den eigenen Start.
- **Bildschirmfreigabe in Firefox ohne Quelle** (`NotFoundError`, z. B. Linux/Wayland ohne Desktop-Portal): statt der
  englischen Browser-Meldung kommt ein verständlicher Hinweis (DE/EN).
- Für die Tests selbst: Firefox sammelt Loopback-ICE-Kandidaten erst mit `media.peerconnection.ice.loopback` – die
  Test-LiveKit lauscht nur auf 127.0.0.1 (im echten Betrieb ohne Bedeutung).

## Was sich nicht automatisch prüfen lässt

- **Safari auf macOS und iOS:** Der Playwright-WebKit ist ein Linux-Build (GTK) – gleiche Engine, aber andere
  Medien-Schicht (GStreamer statt AVFoundation/VideoToolbox), kein `MediaRecorder`, kein `RTCRtpScriptTransform`.
  Echte Safari-Tests brauchen einen Mac bzw. ein iPhone. Von Hand prüfen:
  - **E2EE** mit `RTCRtpScriptTransform` (Safari ≥ 15.4): Verschlüsseln, Entschlüsseln, falscher Schlüssel, Wechsel in
    einen Breakout-Raum; gemischt Safari ↔ Chrome ↔ Firefox.
  - **Aufnahme** als MP4 (H.264/AAC) und deren Wiedergabe in der Bibliothek.
  - **Bildschirmfreigabe** (Safari macOS: nur ganzer Bildschirm/Fenster, kein Systemton; iOS: nicht möglich – der
    Knopf ist ausgeblendet, weil `getDisplayMedia` fehlt).
- **iOS-Besonderheiten:** Autoplay mit Ton erst nach einer Berührung (Agora startet Ton nach dem Beitritts-Tipp),
  Kamera/Mikro pausieren beim Wechsel in den Hintergrund (Agora nimmt sie beim Zurückkommen wieder auf), kein
  Lautsprecher-Wahlfeld (`setSinkId` fehlt; das Feld wird ausgeblendet), Bild-im-Bild nur für `<video>` mit
  `playsinline`, Wake Lock erst ab iOS 16.4, Web Speech (Untertitel) nur mit Siri/Diktat eingeschaltet, PWA nur über
  „Zum Home-Bildschirm“.
- **Echte Kamera und echtes Mikrofon:** Alle Läufe nutzen Attrappen (Testbild/Sinuston). Gerätewechsel, Hot-Plug,
  Echo-Unterdrückung, Rauschunterdrückung „Stark“ (RNNoise) und Musik-Modus brauchen echte Hardware.
- **Bildschirmauswahl-Dialoge** der Browser (headless nicht vorhanden) und System-Ton beim Teilen.
- **GPU-Pfade:** Hardware-Codecs, WebGL-Effekte in Firefox (siehe 6.), Leistung der Effekte auf schwachen Geräten.
- **Untertitel (Web Speech API):** gibt es nur in Chromium-Browsern und Safari; Firefox hat sie nicht (Agora meldet das beim
  Einschalten; mitlesen geht trotzdem). Chromium headless schickt den Ton an keinen Dienst – nicht automatisierbar.
- **Edge:** nutzt Chromium; nicht eigens getestet.
