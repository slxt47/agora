# Desktop-App

`desktop/` ist eine schlanke Electron-Hülle (Electron 45.3.1, electron-builder 26.18.0, beide fest) um die Web-App.
Sie bringt, was im Browser nicht geht: **systemweite Tastenkürzel** (auch wenn ein anderes Fenster vorne ist), ein
Tray-Symbol, Deep Links `agora://r/<code>` und eine eigene Bildschirmauswahl. Alles andere – Meetings, E2EE, Whiteboard,
Aufnahme – ist dieselbe Web-App wie auf https://agora.slxt.dev; Updates der Seite kommen ohne neues Paket an.

## Aufbau

| Datei | Aufgabe |
|---|---|
| `src/main.js` | Fenster, Navigation, Rechte, Bildschirmauswahl, Kürzel, Tray, Deep Links |
| `src/konfig.js` | Einstellungen + reine Prüf-Funktionen (Adresse, Kürzel, Deep Link, Navigation), getestet in `test/konfig.test.js` |
| `src/preload.js` | Brücke zur Seite: genau `window.agoraDesktop = { desktop: true, beiKuerzel(fn) }` |
| `src/auswahl.*` | Bildschirmauswahl (Bildschirme/Fenster mit Vorschau), eigenes Preload |
| `src/selbsttest.js` | nur mit `AGORA_DESKTOP_TEST=1` + `AGORA_DESKTOP_SELBSTTEST=1`: Smoke-Test der gebauten App |
| `web/src/raum/desktop.js` | Seite: Kürzel der Brücke → dieselben Handler wie die Tasten M, V, H |

## Sicherheit

- `contextIsolation`, `sandbox`, kein `nodeIntegration`, kein `<webview>`; Fuses: kein `RunAsNode`, keine
  `NODE_OPTIONS`/`--inspect`, nur aus der ASAR-Datei laden (mit Integritätsprüfung), Cookies verschlüsselt.
- **Navigation:** nur innerhalb der eingestellten Agora-Herkunft (Schema + Host + Port). Andere `https`-Links
  (Impressum auf slxt.org, Kalender-Links, …) öffnet der Standardbrowser, alles andere wird verworfen. Neue Fenster
  (`target=_blank`) gibt es nicht: Agora-Links öffnen im selben Fenster, fremde im Standardbrowser.
- **Rechte** nur für die Agora-Herkunft: Kamera/Mikrofon (`media`, nur Ton/Bild), Bildschirm (`display-capture`). Dazu
  bewusst zwei harmlose: `fullscreen` (Taste F) und `clipboard-sanitized-write` („Link kopieren“, nur schreiben).
  Alles andere (Benachrichtigungen, Standort, MIDI, HID/USB/Seriell, …) wird abgelehnt.
- **Brücke:** Die Seite bekommt keine IPC- oder Node-Objekte, nur `beiKuerzel(fn)`; die Brücke reicht nur `mikro`,
  `kamera`, `hand` durch (alles andere verwirft sie, auch wenn der Hauptprozess es schicken würde), höchstens 4 Hörer.
- Bildschirmauswahl: eigenes Fenster mit eigener CSP und eigenem Preload; nur ids aus der angebotenen Liste werden
  angenommen. Systemton beim Teilen nur unter Windows (`loopback`); macOS 15+ nutzt die System-Auswahl.
- **Kein Auto-Update.** Die App lädt die Seite live, also kommen alle Agora-Änderungen sofort an. Die Hülle selbst
  (Electron mit Chromium) muss für Sicherheitsupdates neu gebaut und verteilt werden: Electron-Version in
  `desktop/package.json` heben, Tag `desktop-vX.Y.Z` setzen, Pakete aus dem Actions-Lauf verteilen. Ein Auto-Updater
  (electron-updater) bräuchte signierte Pakete und einen Update-Server – bewusst weggelassen.

## Einstellungen

Datei `einstellungen.json` im Profilordner (Linux `~/.config/Agora/`, Windows `%APPDATA%\Agora\`, macOS
`~/Library/Application Support/Agora/`), beim ersten Start angelegt, über das Tray-Menü zu öffnen; Änderungen gelten
nach einem Neustart:

```json
{
  "adresse": "https://agora.slxt.dev/",
  "kuerzel": { "mikro": "CommandOrControl+Shift+M", "kamera": "CommandOrControl+Shift+V", "hand": "CommandOrControl+Shift+H" }
}
```

- `adresse`: eigene Agora-Instanz; nur `https` (oder `http` auf 127.0.0.1/localhost für Tests). Umgebung
  `AGORA_ADRESSE` geht vor.
- `kuerzel`: [Electron-Accelerator](https://www.electronjs.org/docs/latest/api/accelerator) mit mindestens einem
  Modifikator (eine nackte Taste systemweit wäre gefährlich); `""` schaltet ein Kürzel ab. Ungültiges → Vorgabe.
  Ist ein Kürzel schon von einem anderen Programm belegt, bleibt es aus (Hinweis im Log). Unter Wayland erlauben viele
  Desktops globale Kürzel für X11-Programme nur, wenn das Fenster vorne ist – dann über XWayland starten oder die
  Kürzel des Desktops nutzen.
- Die Kürzel wirken nur im laufenden Meeting (wie die Tasten M/V/H) und auch über das Tray-Menü.

## Deep Links

`agora://r/abc-defg-hij` öffnet den Raum (auch `agora://abc-defg-hij`); `#k=…` (E2EE-Schlüssel) wird übernommen und
bleibt wie im Web nur im Fragment. Alles andere am Link wird verworfen. Registriert wird das Schema von den
Installationspaketen (deb/NSIS/dmg) und beim Start (`setAsDefaultProtocolClient`). Läuft die App schon, übernimmt die
laufende Instanz den Link.

## Bauen

```sh
sh desktop/scripts/docker-bauen.sh     # in Docker: npm ci, Tests, AppImage + deb für arm64 und x64 → desktop/dist/
rm -rf desktop/node_modules desktop/.cache desktop/dist   # danach aufräumen (~1 GB)
```

GitHub Actions (`.github/workflows/desktop.yml`) baut bei einem Tag `desktop-v<version>` (muss zur Version in
`desktop/package.json` passen) **unsignierte** Pakete für Linux (AppImage, deb), Windows (NSIS-Installer, zip; x64 +
arm64) und macOS (dmg, zip; x64 + arm64) und legt sie als Artefakte des Laufs ab (30 Tage). Kein Release.

Unsigniert heißt: Windows SmartScreen warnt („Unbekannter Herausgeber“), macOS Gatekeeper blockiert (Rechtsklick →
Öffnen, oder `xattr -dr com.apple.quarantine Agora.app`). Zum Signieren bräuchte es ein Code-Signing-Zertifikat
(Windows) bzw. Apple-Developer-Konto + Notarisierung (macOS) als Secrets (`CSC_LINK`, `CSC_KEY_PASSWORD`,
`APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`) – Entscheidung von Florian.

## Smoke-Test

```sh
# Testinstanz + Raum: siehe tests/e2e/docker-compose.test.yml; dann
ADRESSE=http://127.0.0.1:8221/r/<code> sh desktop/scripts/docker-smoke.sh
```

Startet die gebaute App (`dist/linux-arm64-unpacked/agora`) unter xvfb in Docker (`--no-sandbox` nur dort, weil im
Container der Sandbox-Helfer fehlt) und prüft mit dem Test-Haken: Brücke klein und ohne Node, Beitritt als Gast,
Kürzel Mikro/Kamera/Hand schalten die Knöpfe um und zurück, unbekannte Aktionen werden verworfen, Navigation nach
außen gesperrt (an den Standardbrowser übergeben), Benachrichtigungen/Standort abgelehnt, Bildschirmfreigabe über
`desktopCapturer`. Der Test-Haken löst die Kürzel so aus, wie es `globalShortcut` täte; den echten Tastendruck auf
Systemebene prüft er nicht (unter xvfb ohne Fenstermanager nicht verlässlich).

### Ergebnis 26.11.2026 (Linux arm64, Docker, xvfb)

| Prüfung | Ergebnis |
|---|---|
| Bau (`docker-bauen.sh`, ~3 min) | `Agora-1.0.0-linux-arm64.AppImage` 121 MB, `…-arm64.deb` 91 MB, `…-x86_64.AppImage` 120 MB, `…-amd64.deb` 96 MB |
| Kürzel registriert (globalShortcut) | Mikro, Kamera, Hand: ja |
| Brücke | nur `beiKuerzel`, `desktop`; `require`/`process`/`module` in der Seite undefiniert |
| Beitritt als Gast | ok |
| Kürzel Mikro / Kamera / Hand | Knopf schaltet um und wieder zurück (je ok) |
| unbekannte Aktion (`verlassen`) | vom Hauptprozess und von der Brücke verworfen |
| Bildschirmfreigabe | `desktopCapturer`-Quelle `screen:…` geteilt, eigene Kachel erscheint |
| Navigation nach außen | gesperrt, an den Standardbrowser übergeben (`location.href` und `window.open`) |
| Rechte | Benachrichtigungen `denied`, Standort `denied` |

Windows- und macOS-Pakete sind nur im Workflow gebaut (hier nicht ausgeführt: kein Windows/Mac, Workflow nicht
ausgelöst).

## Grenzen

- Versucht die Seite selbst, im Fenster nach außen zu navigieren (`location.href = 'https://…'`), verwirft die App
  das – aber `livekit-client` hat beim `beforeunload` das Meeting schon getrennt. In Agora führen solche Links nicht aus
  dem Meeting heraus (Links im Chat öffnen mit `target=_blank` → Standardbrowser, ohne Trennung).
- Fernsteuerung (Remote Control) ist nicht eingebaut; die Desktop-App wäre die Grundlage dafür.
- Linux: Das Tray-Symbol braucht einen Statusbereich (GNOME nur mit AppIndicator-Erweiterung).
