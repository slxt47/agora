// Vite-Build: fünf Seiten (Start, Konto, Raum, Passwort, Aufnahme-Freigabe). Alles selbst gehostet, keine data:-URLs
// (strenge CSP). Das Backend liefert web/dist aus: /r/{code} → raum.html, /a/{token} → a.html, /konto → konto.html.
// Entwicklung: `npm run dev` leitet /api an das Backend weiter (AGORA_API, Standard http://127.0.0.1:8000).
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

export default defineConfig({
  base: '/',
  plugins: [ohneCdn],
  define: { __MP_BASIS__: JSON.stringify(MP_BASIS), __MP_MODELL__: JSON.stringify(MP_MODELL) },
  build: {
    outDir: 'dist', emptyOutDir: true, sourcemap: false, assetsInlineLimit: 0, modulePreload: { polyfill: false },
    target: 'es2022',
    chunkSizeWarningLimit: 700,   // livekit-client allein ist ~520 kB (nur auf der Raum-Seite)
    rollupOptions: { input: { index: 'index.html', konto: 'konto.html', raum: 'raum.html', passwort: 'passwort.html', a: 'a.html' } },
  },
  server: { proxy: { '/api': process.env.AGORA_API || 'http://127.0.0.1:8000' } },
});
