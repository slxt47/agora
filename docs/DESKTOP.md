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
| `src/preload.js` | Brücke zur Seite: genau `window.agoraDesktop = { desktop: true, beiKuerzel(fn), fernsteuerung: {…} }` |
| `src/fernsteuerung.js` | Fernsteuerung: reine Prüfregeln (Freigabe, Absender, E2EE, Format, Grenzen, Koordinaten, Kürzel-Sperre), `test/fernsteuerung.test.js` |
| `src/steuerung.js` | Fernsteuerung im Hauptprozess: Dialog, roter Rahmen (`rahmen.*`), Kürzel/Tray, Weitergabe an den Adapter |
| `src/eingabe.js` | Eingabe-Adapter: `@jitsi/robotjs` (Vorgabe) oder `xdotool` (Linux) |
| `src/selbsttest-fernsteuerung.js` | Smoke-Test der Fernsteuerung (`scripts/docker-smoke-fernsteuerung.sh`, Zielfenster `test/ziel.py`) |
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
- **Brücke:** Die Seite bekommt keine IPC- oder Node-Objekte, nur `beiKuerzel(fn)` und `fernsteuerung` (siehe unten);
  die Brücke reicht nur `mikro`, `kamera`, `hand` durch (alles andere verwirft sie, auch wenn der Hauptprozess es schicken
  würde), höchstens 4 Hörer. Der Hauptprozess nimmt Fernsteuerungs-Nachrichten nur vom eigenen Hauptfenster an.
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
  "kuerzel": { "mikro": "CommandOrControl+Shift+M", "kamera": "CommandOrControl+Shift+V", "hand": "CommandOrControl+Shift+H",
               "fernsteuerungEnde": "CommandOrControl+Alt+X" }
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

## Fernsteuerung

Wer in der Desktop-App einen **ganzen Bildschirm** teilt, kann anderen im Meeting Maus und Tastatur überlassen (im
Browser geht das nicht – der Browser darf keine Eingaben ins System geben).

**Ablauf:** Die Seite meldet dem Server „bereit“, solange die Freigabe läuft (der Server prüft die Bildschirmspur bei
LiveKit). Andere sehen im Menü der Bildschirm-Kachel „Steuerung anfragen“. Die Anfrage öffnet einen **nativen Dialog der
App** („… möchte deinen Bildschirm steuern“, Vorgabe „Ablehnen“) – die Seite allein kann nichts erlauben. Bei „Erlauben“
erzeugt die App eine Kennung, die Seite meldet sie dem Server, und der Server schreibt die Freigabe `{von, an, id}` in die
Raum-Metadaten. Die steuernde Person sieht „Du steuerst …“, eine Eingabe-Ebene über der Kachel (Fadenkreuz) und gibt mit
**Esc** zurück. Ihre Eingaben gehen als Datenpakete (zuverlässig, Topic `fernsteuerung`, nur an die teilende Person) über
LiveKit – in E2EE-Meetings Ende-zu-Ende verschlüsselt, nie über den Agora-Server.

**Was die App prüft, bevor sie irgendetwas eingibt** (`src/fernsteuerung.js`):
1. eigene Freigabe läuft, und zwar ein Bildschirm (bei Fenstern kennt Electron die Lage des fremden Fensters nicht – dort
   gibt es keine Fernsteuerung, der Dialog erscheint gar nicht);
2. Zustimmung im eigenen Dialog für genau diese identity;
3. Freigabe des Servers in den Raum-Metadaten mit derselben identity **und derselben Kennung**;
4. Absender des Pakets laut LiveKit (nicht aus dem Inhalt) = diese identity;
5. im E2EE-Meeting nur Pakete, die die Seite mit dem Fernsteuerungs-Schlüssel entschlüsseln konnte (AES-GCM, per HKDF
   aus dem Link-Geheimnis abgeleitet, AAD = identity des Absenders). Eigene Schicht, weil livekit-client 2.25 bei
   E2EE-verschlüsselten Datenpaketen dem Empfänger `encryptionType` als NONE meldet (im E2E-Test gesehen) – darauf kann
   sich die App nicht verlassen;
6. bekanntes Format, Werte im Bereich (x/y 0…1, Rad ±20, bekannte Tasten, Modifikatoren nur Umschalt/Strg/Alt – nie
   Meta/Win/Cmd), höchstens 512 Byte; Grenzen 120 Maus-/Rad-Ereignisse/s (Stoß 240) und 40 Tasten/s (Stoß 80); mehr als
   600 verworfene in 10 s beenden die Steuerung.

**Schutz der App selbst (Entscheidung):** Solange ein Agora-Fenster den Fokus hat oder ein Dialog offen ist, gehen
Klicks, Rad und Tasten **nicht** weiter; Klicks/Rad auf Punkte in einem Agora-Fenster nie (auch ohne Fokus). Nur
Mausbewegungen dürfen (harmlos, zeigen, wohin jemand zeigt). So kann niemand aus der Ferne den Erlauben-Dialog, die
Bildschirmauswahl, „Verlassen“ oder die Einstellungen bedienen. Tastenkombinationen, die eigene systemweite Kürzel
auslösen würden (Mikro, Kamera, Hand, Fernsteuerung beenden), gibt die App nie weiter.

**Sichtbar und jederzeit beendbar:** roter Rahmen + Banner („… steuert deinen Bildschirm – Strg+Alt+X beendet“) über dem
geteilten Bildschirm (durchklickbar, immer oben, nicht fokussierbar). Beenden per Kürzel `CommandOrControl+Alt+X`
(einstellbar: `kuerzel.fernsteuerungEnde`, `""` = aus; nur registriert, solange jemand steuert), Tray „Fernsteuerung
beenden“, Ende der Freigabe, Neuladen/Navigation/Absturz der Seite, Schließen des Fensters, Server nimmt die Freigabe
zurück (die steuernde Person beendet, Host/Co-Host greift ein, Person verlässt das Meeting, Meeting endet). Gedrückte
Maustasten werden beim Ende losgelassen.

**Eingabe-Adapter** (`src/eingabe.js`, fest über `AGORA_EINGABE=robotjs|xdotool`):
- `@jitsi/robotjs` 0.6.24 (MIT, von Jitsi gepflegt; N-API-Prebuilds für Linux x64/arm64, Windows x64/arm64/ia32,
  macOS x64+arm64 – kein Neubau für Electron; `asarUnpack`, `npmRebuild: false`). Unter Linux gehen Zeichen außerhalb
  ASCII (ä, €) über `xdotool type`, weil robotjs sie als „NoSymbol“ schickt (im Test gesehen); alle Eingaben laufen dann
  durch eine Warteschlange, damit die Reihenfolge stimmt.
- `xdotool` (nur Linux/X11) als Rückfall, wenn robotjs nicht lädt; das deb-Paket empfiehlt `xdotool`.
- Ohne Adapter bietet die App keine Fernsteuerung an.

**Grenzen je System:**

| System | Stand |
|---|---|
| Linux X11 (arm64) | **geprüft** (Docker, Xvfb, beide Adapter): Maus, Klick, Tasten inkl. Umlaut, Rad, Absender-/Freigabe-/E2EE-Regeln, Grenzen, Kürzel-Sperre, Agora-Fenster tabu, Beenden per echtem Tastendruck (globalShortcut), Server-Rücknahme, Ende der Freigabe. Ein Fenstermanager fehlte – Fokusfragen nur so weit geprüft |
| Linux Wayland | robotjs/xdotool erreichen nur XWayland-Programme; native Wayland-Fenster lassen sich so nicht steuern (bräuchte das RemoteDesktop-Portal, nicht eingebaut). Bildschirmteilen läuft über PipeWire – die Zuordnung zum Bildschirm kann fehlen, dann gibt es keine Fernsteuerung |
| Linux x64 | gleicher Code, Prebuild vorhanden; nicht ausgeführt |
| Windows | nicht ausgeführt. robotjs gibt Eingaben per `SendInput`; Fenster mit höheren Rechten (UAC, Programme als Administrator) nehmen keine Eingaben an. Koordinaten in physischen Pixeln (× Skalierung) |
| macOS | nicht ausgeführt. Braucht die Freigabe „Bedienungshilfen“ (Systemeinstellungen → Datenschutz → Bedienungshilfen) für Agora, sonst kommen keine Eingaben an; Koordinaten in Punkten. Mit der Systemauswahl (macOS 15+) kennt die App die gewählte Quelle nicht sicher – dann keine Fernsteuerung |

Smoke-Test (Linux): `sh desktop/scripts/docker-smoke-fernsteuerung.sh` (baut bei Bedarf nur `dist/linux-<arch>-unpacked`,
startet Xvfb mit deutschem Layout, ein fremdes Tk-Zielfenster und die App mit `src/selbsttest-fernsteuerung.js`;
`EINGABE=xdotool` prüft den Rückfall). Er geht den echten Weg Seite → Brücke → Hauptprozess → Adapter → X-Server und
beobachtet die Wirkung außerhalb der App (`xdotool getmouselocation`, Log des Zielfensters). Den Server-Teil und den
Datenkanal prüfen `tests/test_fernsteuerung.py`, die Browser-E2E (Fernsteuerung im E2EE-Meeting mit nachgebauter Brücke)
und der Sicherheitstest (nicht freigegebene identity wird mit den Regeln der App verworfen).

### Ergebnis 27.11.2026 (Linux arm64, Docker, Xvfb 1280×900, Layout de)

| Prüfung | robotjs | xdotool |
|---|---|---|
| ohne Freigabe / ohne Zustimmung / ohne Server-Freigabe / fremde Kennung | verworfen | verworfen |
| Dialog-Zustimmung (Testantwort), zweite Anfrage in < 3 s | Kennung 32 Zeichen, „zu-schnell“ | ebenso |
| roter Rahmen + Banner | „Stefan steuert deinen Bildschirm – Strg+Alt+X beendet“ | ebenso |
| Maus bewegen (normiert → 240,190) | `xdotool getmouselocation` x:240 y:190 | ebenso |
| falscher Absender (2 Pakete) | verworfen, Maus unbewegt, kein Klick | ebenso |
| Klick ins fremde Zielfenster | `klick 1 240 190` | ebenso |
| Tasten a, B (Umschalt), ä, Enter | in dieser Reihenfolge angekommen | ebenso |
| Rad | `rad unten` | ebenso |
| eigene Kürzel (Strg+Umschalt+M, Strg+Alt+X) | nicht weitergegeben | ebenso |
| Klick in Agora-Fenster / bei Fokus auf Agora | verworfen | verworfen |
| E2EE: unverschlüsselt | verworfen | verworfen |
| kaputte Formate (4) / 400 Pakete auf einmal | 4 verworfen / ≈ 150 über der Grenze | ebenso |
| Server nimmt Freigabe zurück | beendet, Rahmen weg | ebenso |
| echter Tastendruck Strg+Alt+X (über X) | globalShortcut beendet, Seite bekommt „kuerzel“ | ebenso |
| Freigabe endet | beendet | ebenso |

Der Meeting-Smoke-Test (`docker-smoke.sh`, siehe unten) lief mit dieser Fassung am 27.11.2026 erneut grün (Brücke jetzt
`beiKuerzel, desktop, fernsteuerung`, Kürzel, Bildschirmfreigabe, Navigation, Rechte).

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
- Fernsteuerung: Grenzen je System siehe oben (Wayland, macOS-Bedienungshilfen, Windows-UAC; nur ganze Bildschirme).
- Linux: Das Tray-Symbol braucht einen Statusbereich (GNOME nur mit AppIndicator-Erweiterung).
