// Agora Desktop: schlanke Electron-Hülle um die Web-App (Vorgabe https://agora.slxt.dev).
// Sicherheit: contextIsolation + Sandbox, kein nodeIntegration, Navigation nur innerhalb der Agora-Herkunft (alles andere
// → Standardbrowser, nur https), Rechte nur Kamera/Mikrofon/Bildschirm (+ Vollbild, Zwischenablage schreiben) und nur
// für die Agora-Herkunft. Systemweite Tastenkürzel (Mikro, Kamera, Hand) gehen über die Preload-Brücke an die Seite.
// Bildschirmfreigabe mit eigener Auswahl (desktopCapturer). Deep Links agora://r/<code>. Kein Auto-Update.
'use strict';
const path = require('node:path');
const fs = require('node:fs');
const {
  app, BrowserWindow, Menu, Tray, desktopCapturer, globalShortcut, ipcMain, nativeImage, session, shell,
} = require('electron');
const { konfigLaden, codeAusDeepLink, raumAdresse, erlaubteNavigation, AKTIONEN } = require('./konfig');

const TEST = process.env.AGORA_DESKTOP_TEST === '1';
const ICON = path.join(__dirname, 'icon.png');
let fenster = null, tray = null, konfig = null, wartenderLink = null;
const externGeoeffnet = [];          // nur für den Selbsttest: was an den Standardbrowser ging

if (TEST) {
  // Smoke-Test: Kamera/Mikro-Attrappe (Rechte und Bildschirmauswahl laufen über die echten Handler unten);
  // eigener Profilordner, damit nichts Echtes berührt wird
  app.commandLine.appendSwitch('use-fake-device-for-media-stream');
  if (process.env.AGORA_DESKTOP_PROFIL) app.setPath('userData', process.env.AGORA_DESKTOP_PROFIL);
}

// Nur eine Instanz: ein zweiter Start (z. B. über einen Deep Link) übergibt seine Argumente an die laufende
if (!app.requestSingleInstanceLock()) { app.quit(); return; }
app.on('second-instance', (_e, argv) => { deepLinkOeffnen(argv.find((a) => a.startsWith('agora://'))); zeigen(); });
app.on('open-url', (e, url) => { e.preventDefault(); deepLinkOeffnen(url); });          // macOS
if (!TEST) app.setAsDefaultProtocolClient('agora');

function deepLinkOeffnen(url) {
  if (!url) return;
  const ziel = codeAusDeepLink(url);
  if (!ziel) return;
  if (!fenster) { wartenderLink = ziel; return; }
  fenster.loadURL(raumAdresse(konfig.adresse, ziel.code, ziel.schluessel));
}

function zeigen() {
  if (!fenster) return;
  if (fenster.isMinimized()) fenster.restore();
  fenster.show(); fenster.focus();
}

/** Systemweites Kürzel → Aktion an die Seite (die Seite nutzt ihre normalen Tastenkürzel-Handler). */
function kuerzelSenden(aktion) {
  if (!AKTIONEN.includes(aktion) || !fenster || fenster.isDestroyed()) return false;
  fenster.webContents.send('agora:kuerzel', aktion);
  return true;
}

function kuerzelRegistrieren() {
  globalShortcut.unregisterAll();
  const ergebnis = {};
  for (const aktion of AKTIONEN) {
    const taste = konfig.kuerzel[aktion];
    if (!taste) continue;
    try { ergebnis[aktion] = globalShortcut.register(taste, () => kuerzelSenden(aktion)); } catch { ergebnis[aktion] = false; }
    if (!ergebnis[aktion]) console.warn(`Agora: Tastenkürzel ${taste} (${aktion}) ist schon belegt`);
  }
  return ergebnis;
}

/** Rechte: nur Kamera/Mikro/Bildschirm (+ Vollbild, Zwischenablage schreiben) und nur von der Agora-Herkunft. */
function rechteEinrichten(ses) {
  const ERLAUBT = new Set(['media', 'display-capture', 'fullscreen', 'clipboard-sanitized-write']);
  const vonAgora = (url) => { try { return new URL(url).origin === konfig.herkunft; } catch { return false; } };
  ses.setPermissionRequestHandler((wc, recht, antwort, details) => {
    let ok = ERLAUBT.has(recht) && vonAgora(details.requestingUrl || wc.getURL());
    if (ok && recht === 'media') ok = (details.mediaTypes || []).every((t) => t === 'audio' || t === 'video');
    antwort(ok);
  });
  ses.setPermissionCheckHandler((_wc, recht, herkunft) => ERLAUBT.has(recht) && vonAgora(herkunft));
  ses.setDevicePermissionHandler(() => false);              // kein WebHID/WebUSB/Serial
  // Bildschirmfreigabe: eigene Auswahl mit Vorschaubildern (desktopCapturer), System-Ton nur unter Windows
  ses.setDisplayMediaRequestHandler(async (anfrage, antwort) => {
    const herkunft = anfrage.securityOrigin || anfrage.frame?.url || '';
    if (!vonAgora(herkunft)) { console.warn(`Agora: Bildschirmfreigabe von ${herkunft} abgelehnt`); antwort({}); return; }
    try {
      const quellen = await desktopCapturer.getSources({ types: ['screen', 'window'], thumbnailSize: { width: 320, height: 180 } });
      if (TEST) console.log('AGORA_TEST bildschirmquellen', JSON.stringify(quellen.map((q) => q.id)));
      const id = TEST && process.env.AGORA_DESKTOP_TEST_QUELLE === 'erste' ? quellen[0]?.id : await quelleWaehlen(quellen);
      const quelle = quellen.find((q) => q.id === id);
      if (!quelle) { antwort({}); return; }
      antwort(process.platform === 'win32' && anfrage.audioRequested ? { video: quelle, audio: 'loopback' } : { video: quelle });
    } catch (e) {
      console.warn('Agora: Bildschirmauswahl', e);
      antwort({});
    }
  }, { useSystemPicker: process.platform === 'darwin' });  // macOS 15+: Systemauswahl, sonst unsere
}

/** Kleines modales Fenster mit den Quellen; liefert die gewählte id oder null. */
function quelleWaehlen(quellen) {
  return new Promise((fertig) => {
    const wahl = new BrowserWindow({
      parent: fenster, modal: true, width: 760, height: 560, resizable: true, minimizable: false, maximizable: false,
      title: 'Bildschirm teilen', backgroundColor: '#0b0e2a', autoHideMenuBar: true, show: false,
      webPreferences: { preload: path.join(__dirname, 'auswahl-preload.js'), contextIsolation: true, sandbox: true, nodeIntegration: false },
    });
    let erledigt = false;
    const ende = (id) => { if (erledigt) return; erledigt = true; ipcMain.removeHandler('agora-auswahl:quellen'); ipcMain.removeAllListeners('agora-auswahl:gewaehlt'); fertig(id); if (!wahl.isDestroyed()) wahl.close(); };
    ipcMain.handle('agora-auswahl:quellen', (e) => (e.sender === wahl.webContents
      ? quellen.map((q) => ({ id: q.id, name: q.name, bild: q.thumbnail.toDataURL(), bildschirm: q.id.startsWith('screen:') })) : []));
    ipcMain.on('agora-auswahl:gewaehlt', (e, id) => {
      if (e.sender !== wahl.webContents) return;
      ende(typeof id === 'string' && quellen.some((q) => q.id === id) ? id : null);
    });
    wahl.on('closed', () => ende(null));
    wahl.webContents.on('will-navigate', (e) => e.preventDefault());
    wahl.loadFile(path.join(__dirname, 'auswahl.html'));
    wahl.once('ready-to-show', () => wahl.show());
  });
}

function fensterErstellen() {
  fenster = new BrowserWindow({
    width: 1280, height: 820, minWidth: 380, minHeight: 500, title: 'Agora', icon: ICON, backgroundColor: '#0b0e2a', show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true, nodeIntegration: false,
      webSecurity: true, allowRunningInsecureContent: false, spellcheck: false,
    },
  });
  const wc = fenster.webContents;
  if (TEST) wc.on('console-message', (e) => { if (e.level === 'warning' || e.level === 'error') console.log('AGORA_SEITE', e.level, String(e.message).slice(0, 300)); });
  // Navigation: innerhalb der Agora-Herkunft bleiben; andere https-Links im Standardbrowser, alles andere verwerfen
  const extern = (url) => {
    try {
      if (new URL(url).protocol !== 'https:') return;
      if (TEST) externGeoeffnet.push(url); else shell.openExternal(url);
    } catch { /* ungültig */ }
  };
  wc.on('will-navigate', (e, url) => { if (!erlaubteNavigation(url, konfig.herkunft)) { e.preventDefault(); extern(url); } });
  wc.on('will-redirect', (e, url) => { if (!erlaubteNavigation(url, konfig.herkunft)) { e.preventDefault(); extern(url); } });
  wc.setWindowOpenHandler(({ url }) => {
    // Agora-eigene Links mit target=_blank (z. B. „Zur Bibliothek“) im selben Fenster, Fremdes im Standardbrowser
    if (erlaubteNavigation(url, konfig.herkunft)) wc.loadURL(url); else extern(url);
    return { action: 'deny' };
  });
  wc.on('will-attach-webview', (e) => e.preventDefault());
  fenster.once('ready-to-show', () => fenster.show());
  fenster.on('close', (e) => {
    // Schließen legt ins Tray (Meeting läuft weiter); Beenden über das Tray-Menü
    if (tray && !app.istAmBeenden && !TEST) { e.preventDefault(); fenster.hide(); }
  });
  fenster.on('closed', () => { fenster = null; });
  fenster.loadURL(wartenderLink ? raumAdresse(konfig.adresse, wartenderLink.code, wartenderLink.schluessel) : konfig.adresse);
  wartenderLink = null;
}

function trayErstellen() {
  try {
    tray = new Tray(nativeImage.createFromPath(ICON).resize({ width: 22, height: 22 }));
  } catch { tray = null; return; }      // Desktops ohne Statusbereich
  tray.setToolTip('Agora');
  const k = konfig.kuerzel;
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Agora öffnen', click: zeigen },
    { type: 'separator' },
    { label: 'Mikrofon an/aus', accelerator: k.mikro || undefined, click: () => kuerzelSenden('mikro') },
    { label: 'Kamera an/aus', accelerator: k.kamera || undefined, click: () => kuerzelSenden('kamera') },
    { label: 'Hand heben/senken', accelerator: k.hand || undefined, click: () => kuerzelSenden('hand') },
    { type: 'separator' },
    { label: 'Einstellungen (Datei) öffnen', click: () => shell.openPath(konfig.datei) },
    { label: 'Beenden', click: () => { app.istAmBeenden = true; app.quit(); } },
  ]));
  tray.on('click', zeigen);
}

app.whenReady().then(() => {
  konfig = konfigLaden(app.getPath('userData'), process.env);
  if (!fs.existsSync(konfig.datei) && !TEST) {
    try { fs.writeFileSync(konfig.datei, JSON.stringify({ adresse: konfig.adresse, kuerzel: konfig.kuerzel }, null, 2) + '\n'); } catch { /* egal */ }
  }
  Menu.setApplicationMenu(null);
  rechteEinrichten(session.defaultSession);
  deepLinkOeffnen(process.argv.find((a) => a.startsWith('agora://')));
  fensterErstellen();
  trayErstellen();
  const registriert = kuerzelRegistrieren();
  if (TEST) {
    // Test-Haken (nur mit AGORA_DESKTOP_TEST=1): der Selbsttest löst Kürzel aus, wie das System es täte
    console.log('AGORA_DESKTOP_BEREIT', JSON.stringify({ registriert, adresse: konfig.adresse }));
    if (process.env.AGORA_DESKTOP_SELBSTTEST === '1') {
      require('./selbsttest').laufen({ fenster, kuerzelSenden, registriert, extern: externGeoeffnet })
        .then((r) => { console.log('AGORA_SELBSTTEST', JSON.stringify(r)); app.exit(r.ok ? 0 : 1); })
        .catch((e) => { console.log('AGORA_SELBSTTEST', JSON.stringify({ ok: false, fehler: String(e) })); app.exit(1); });
    }
  }
});

app.on('will-quit', () => globalShortcut.unregisterAll());
app.on('before-quit', () => { app.istAmBeenden = true; });
app.on('window-all-closed', () => { if (process.platform !== 'darwin' || TEST) app.quit(); });
app.on('activate', () => { if (!fenster) fensterErstellen(); else zeigen(); });
// Fremde Inhalte dürfen keine weiteren Fenster/Webviews erzeugen
app.on('web-contents-created', (_e, wc) => { wc.on('will-attach-webview', (ev) => ev.preventDefault()); });
