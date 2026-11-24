// Vite-Build: sieben Seiten (Start, Konto, Raum, Passwort, Aufnahme-Freigabe, Offline, Admin) + Service Worker (/sw.js).
// Alles selbst gehostet, keine data:-URLs (strenge CSP). Das Backend liefert web/dist aus: /r/{code} → raum.html,
// /a/{token} → a.html, /konto → konto.html.
// Entwicklung: `npm run dev` leitet /api an das Backend weiter (AGORA_API, Standard http://127.0.0.1:8000).
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { defineConfig } from 'vite';
import { BASIS as MP_BASIS, MODELL as MP_MODELL } from './scripts/mediapipe.mjs';

// @livekit/track-processors lädt MediaPipe ohne assetPaths von jsDelivr bzw. storage.googleapis.com. Wir geben die
// Pfade zwar immer selbst an (effekte-verarbeitung.js), ersetzen die Vorgaben aber trotzdem durch die eigenen –
// so steht keine CDN-Adresse im Build, und ein Update, das die Stellen ändert, bricht den Build statt still zu laden.
const CDN_ERSATZ = [
  [/`https:\/\/cdn\.jsdelivr\.net\/npm\/@mediapipe\/tasks-vision@\$\{[^}]+\}\/wasm`/g, JSON.stringify(`${MP_BASIS}/wasm`)],
  [/"https:\/\/storage\.googleapis\.com\/mediapipe-models\/[^"]+\.tflite"/g, JSON.stringify(MP_MODELL)],
];
const ohneCdn = {
  name: 'agora-ohne-cdn',
  transform(code, id) {
    if (!/[\\/]@livekit[\\/]track-processors[\\/]dist[\\/]/.test(id)) return null;
    let neu = code;
    for (const [muster, ersatz] of CDN_ERSATZ) {
      if (neu.search(muster) < 0) this.error(`agora-ohne-cdn: Muster ${muster} nicht gefunden – @livekit/track-processors geändert?`);
      neu = neu.replace(muster, ersatz);
    }
    if (/https:\/\/(cdn\.jsdelivr|storage\.googleapis|unpkg)/.test(neu)) this.error('agora-ohne-cdn: CDN-Adresse übrig');
    return { code: neu, map: null };
  },
};

// Service Worker: Vorlage src/sw-vorlage.js → dist/sw.js mit Cache-Version (Hash über alle Build-Dateien) und der
// Vorab-Liste (Offline-Seite + alles, was sie lädt, Manifest, Icons). Ändert sich irgendeine Datei, ändert sich sw.js
// → der Browser installiert die neue Version, die Seite bietet „Neu laden“ an.
const serviceWorker = {
  name: 'agora-service-worker',
  apply: 'build',
  writeBundle(opt, bundle) {      // erst hier sind auch die HTML-Seiten im Bundle
    const offline = bundle['offline.html'];
    if (!offline) this.error('offline.html fehlt im Build');
    const verweise = [...String(offline.source).matchAll(/(?:src|href)="(\/[^/"][^"]*)"/g)].map((m) => m[1]);
    const vorab = [...new Set(['/offline.html', ...verweise, '/manifest.webmanifest', '/icons/icon-192.png', '/icons/apple-touch-icon.png'])];
    const hash = createHash('sha256');
    for (const n of Object.keys(bundle).sort()) hash.update(n).update(bundle[n].type === 'chunk' ? bundle[n].code : bundle[n].source);
    const vorlage = readFileSync(new URL('./src/sw-vorlage.js', import.meta.url), 'utf8');
    hash.update(vorlage);
    const code = vorlage.replace('__VERSION__', hash.digest('hex').slice(0, 12)).replace('__VORAB__', JSON.stringify(vorab));
    writeFileSync(`${opt.dir}/sw.js`, code);
  },
};

export default defineConfig({
  base: '/',
  plugins: [ohneCdn, serviceWorker],
  define: { __MP_BASIS__: JSON.stringify(MP_BASIS), __MP_MODELL__: JSON.stringify(MP_MODELL) },
  build: {
    outDir: 'dist', emptyOutDir: true, sourcemap: false, assetsInlineLimit: 0, modulePreload: { polyfill: false },
    target: 'es2022',
    chunkSizeWarningLimit: 700,   // livekit-client allein ist ~520 kB (nur auf der Raum-Seite)
    rollupOptions: { input: { index: 'index.html', konto: 'konto.html', raum: 'raum.html', passwort: 'passwort.html', a: 'a.html', webinar: 'webinar.html', offline: 'offline.html', admin: 'admin.html' } },
  },
  server: { proxy: { '/api': process.env.AGORA_API || 'http://127.0.0.1:8000' } },
});
