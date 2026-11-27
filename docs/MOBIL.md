# Mobile Apps

Agora ist eine PWA (installierbar, Service Worker, Safe Areas, Wake Lock, ≥ 44 px Bedienelemente). Für die Stores gibt
es zusätzlich eine **Android-App als Trusted Web Activity (TWA)** in `mobil/android/`. iOS bleibt bei der PWA.

## Android: Trusted Web Activity statt Capacitor

| | TWA (Bubblewrap) | Capacitor |
|---|---|---|
| Was läuft | die Seite in Chrome (bzw. dem Standard-Browser mit TWA-Unterstützung), ohne Adressleiste | die Seite in einer Android-WebView mit nativer Hülle |
| WebRTC, getUserMedia, E2EE, Bildschirmfreigabe | wie in Chrome (getestet über die Browser-Matrix) | WebView: Kamera/Mikro nur mit eigenem Rechte-Code, `getDisplayMedia` fehlt, Insertable Streams je nach WebView |
| Updates | kommen mit der Seite (nichts neu einreichen) | Web-Teil kann gebündelt sein → neue App-Version |
| Code im Repo | erzeugte Gradle-Dateien (~1 MB, meist Symbole), keine eigene Logik | Capacitor-Projekt + Plugins + Brücke |
| APK | ~1–2 MB | ~5–10 MB |

**Gewählt: TWA.** Sie ist die leichtere Lösung: keine zweite Laufzeit, dieselbe Engine wie die getesteten Browser, keine
Plugin-Pflege, Kamera/Mikro/Bildschirm laufen über Chrome (Rechte-Dialog von Chrome). Nachteil: ohne Chrome (oder einen
anderen TWA-fähigen Browser) öffnet sie als Custom Tab; native Extras (z. B. Systemweite Kürzel, CallKit-ähnliche
Anrufanzeige) gibt es nicht.

### Aufbau

- `mobil/scripts/twa-erzeugen.js` erzeugt `mobil/android/` mit dem Kern von Bubblewrap (`@bubblewrap/core` 1.27.0, fest)
  aus `web/public/manifest.webmanifest`: Paket `dev.slxt.agora.twa`, Host `agora.slxt.dev`, Start `/`, Farben
  `#0b0e2a`, Verknüpfungen „Neues Meeting“ und „Beitreten“, minSdk 24, Rückfall Custom Tabs, keine
  Benachrichtigungs-Delegation (Agora schickt keine Push-Nachrichten). Neu erzeugen:
  `ICONS_VON=http://127.0.0.1:8221 sh mobil/scripts/twa-erzeugen.sh` (Symbole von einer Testinstanz).
- Links auf `https://agora.slxt.dev/…` (z. B. Einladungen `/r/<code>`) öffnen die App, sobald Android die Domain
  bestätigt hat (Intent-Filter mit `autoVerify`).
- Die Seite unterscheidet nicht zwischen TWA und Chrome.

### Digital Asset Links (`/.well-known/assetlinks.json`)

Ohne bestätigte Verknüpfung zeigt Chrome oben eine Adressleiste. Die App liefert die Datei aus, sobald in `app.env`
der Fingerabdruck steht (sonst 404):

```sh
AGORA_ANDROID_SHA256=AB:CD:…:EF          # SHA-256 des Signatur-Zertifikats, mehrere Komma-getrennt (z. B. Upload + Play)
AGORA_ANDROID_PAKET=dev.slxt.agora.twa   # optional, das ist die Vorgabe
```

Fingerabdruck: `keytool -list -v -keystore android.keystore -alias agora` (Zeile „SHA256“) bzw. in der Play Console
unter „App-Integrität“. Ungültige Werte → 404 und ein Hinweis im Log. Prüfen: `curl https://agora.slxt.dev/.well-known/assetlinks.json`
und Googles [Statement List Tester](https://developers.google.com/digital-asset-links/tools/generator).

### Bauen

GitHub Actions (`.github/workflows/android.yml`) baut bei einem Tag `android-v<versionName>` ein **Debug-APK**
(`./gradlew assembleDebug`, JDK 17, Android SDK 36) und legt es als Artefakt ab. Kein Release, kein Play Store.
Ein Debug-APK ist mit dem Debug-Schlüssel des Laufs signiert – dessen Fingerabdruck steht nicht in `assetlinks.json`,
also zeigt es die Adressleiste (zum Ausprobieren reicht das).

**Lokal nicht gebaut:** Der Server ist aarch64. Googles Build-Werkzeuge (`aapt2` aus dem Maven-Repo, `build-tools`)
gibt es für Linux nur als x86_64, und Docker hat hier keine amd64-Emulation (binfmt/QEMU müsste am Host eingerichtet
werden – das ändert die Kernel-Konfiguration des Servers und wurde deshalb nicht gemacht). Erwartet: ~1–2 GB
(JDK, SDK, Gradle-Cache), wenige Minuten auf x86_64.

### Für den Play Store (offen, Florian)

1. Upload-Schlüssel erzeugen (`keytool -genkeypair -alias agora -keyalg RSA -keysize 4096 -validity 10000 -keystore android.keystore`),
   sicher aufbewahren (nicht ins Repo – `mobil/.gitignore`).
2. Release-Build: `./gradlew bundleRelease` + signieren (oder Signatur-Secrets im Workflow), Play-Konto (25 USD einmalig),
   Datenschutz-Abschnitt und Store-Eintrag (Screenshots, Beschreibung), Datensicherheits-Formular.
3. `AGORA_ANDROID_SHA256` mit Upload- **und** Play-App-Signatur-Fingerabdruck setzen.

## iOS: PWA

Auf iPhone/iPad läuft Agora in Safari bzw. als PWA („Teilen → Zum Home-Bildschirm“; die Startseite zeigt die Anleitung).
Seit iOS 16.4 haben Home-Bildschirm-Apps Wake Lock und eigenen Speicher; Kamera/Mikro fragt iOS je App.
Grenzen (siehe [`BROWSER.md`](BROWSER.md)): keine Bildschirmfreigabe (`getDisplayMedia` fehlt), kein Lautsprecher-Wahlfeld,
Kamera/Mikro pausieren im Hintergrund (Agora nimmt sie beim Zurückkommen wieder auf), kein Systemton.
E2EE funktioniert mit `RTCRtpScriptTransform` (iOS ≥ 15.4) – von Hand auf einem echten Gerät prüfen.

### Was eine App-Store-App bräuchte (nicht gebaut)

- Einen **Mac** mit Xcode (iOS-Apps lassen sich nur auf macOS bauen und signieren) und ein **Apple-Developer-Konto**
  (99 USD/Jahr), App-ID, Zertifikate, Provisioning-Profile.
- Eine Hülle mit `WKWebView` (z. B. Capacitor oder eigenes Swift-Projekt): `getUserMedia` geht in WKWebView ab iOS 14.3,
  Rechte-Texte `NSCameraUsageDescription`/`NSMicrophoneUsageDescription`, Hintergrund-Modus „Audio“, damit ein Meeting
  beim Sperren weiterläuft (sonst wie Safari).
- App-Review: Apple lehnt reine Website-Hüllen oft ab (Richtlinie 4.2 „Minimum Functionality“) – es bräuchte echte
  native Teile, z. B. CallKit, Push-Einladungen (APNs), Bildschirmfreigabe per ReplayKit-Broadcast-Extension.
- Universal Links (`/.well-known/apple-app-site-association`) für Einladungslinks.

Bis dahin ist die PWA der iOS-Weg; sie braucht keinen Store und bekommt Updates sofort.
