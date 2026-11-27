// Android-Projekt (Trusted Web Activity) mit Bubblewrap erzeugen: mobil/android/ aus web/public/manifest.webmanifest.
// Ohne Android-SDK/JDK – nur die Projektdateien (Gradle). Gebaut wird in GitHub Actions (.github/workflows/android.yml).
// Aufruf: sh mobil/scripts/twa-erzeugen.sh  (Docker; Symbole holt das Skript von ICONS_VON, z. B. einer Testinstanz)
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { TwaGenerator, TwaManifest, ConsoleLog } = require('@bubblewrap/core');

const HOST = process.env.AGORA_HOST || 'agora.slxt.dev';
const PAKET = process.env.AGORA_ANDROID_PAKET || 'dev.slxt.agora.twa';
const ICONS_VON = (process.env.ICONS_VON || `https://${HOST}`).replace(/\/$/, '');
const ZIEL = path.resolve(process.argv[2] || 'mobil/android');
const WEB = JSON.parse(fs.readFileSync(path.resolve(process.argv[3] || 'web/public/manifest.webmanifest'), 'utf8'));
const seite = (p) => `https://${HOST}${p}`;

function manifest(iconBasis) {
  const m = new TwaManifest({
    packageId: PAKET,
    host: HOST,
    name: WEB.name,
    launcherName: WEB.short_name,
    display: 'standalone',
    themeColor: WEB.theme_color, themeColorDark: WEB.theme_color,
    navigationColor: WEB.background_color, navigationColorDark: WEB.background_color,
    navigationDividerColor: WEB.background_color, navigationDividerColorDark: WEB.background_color,
    backgroundColor: WEB.background_color,
    enableNotifications: false,                 // Agora schickt keine Web-Push-Nachrichten
    startUrl: WEB.start_url,
    iconUrl: `${iconBasis}/icons/icon-512.png`,
    maskableIconUrl: `${iconBasis}/icons/icon-maskable-512.png`,
    splashScreenFadeOutDuration: 300,
    signingKey: { path: './android.keystore', alias: 'agora' },
    appVersionCode: 1,
    appVersion: '1.0.0',
    shortcuts: WEB.shortcuts.map((s) => ({ name: s.name, shortName: s.short_name, url: seite(s.url), chosenIconUrl: `${iconBasis}${s.icons[0].src}` })),
    generatorApp: 'agora/mobil/scripts/twa-erzeugen.js (@bubblewrap/core 1.27.0)',
    webManifestUrl: seite('/manifest.webmanifest'),
    fallbackType: 'customtabs',
    features: {},
    enableSiteSettingsShortcut: true,
    isChromeOSOnly: false,
    isMetaQuest: false,
    fullScopeUrl: seite(WEB.scope),
    minSdkVersion: 24,                          // Android 7: WebRTC in Chrome, ältere Geräte nutzen den Browser
    orientation: 'default',
    fingerprints: [],
    additionalTrustedOrigins: [],
    retainedBundles: [],
  });
  return m;
}

(async () => {
  fs.mkdirSync(ZIEL, { recursive: true });
  const log = new ConsoleLog('twa');
  await new TwaGenerator().createTwaProject(ZIEL, manifest(ICONS_VON), log);
  // twa-manifest.json mit den öffentlichen Adressen (für spätere `bubblewrap update`)
  await manifest(`https://${HOST}`).saveToFile(path.join(ZIEL, 'twa-manifest.json'));
  console.log('fertig:', ZIEL);
})().catch((e) => { console.error(e); process.exit(1); });
