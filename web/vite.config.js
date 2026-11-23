// Vite-Build: fünf Seiten (Start, Konto, Raum, Passwort, Aufnahme-Freigabe). Alles selbst gehostet, keine data:-URLs
// (strenge CSP). Das Backend liefert web/dist aus: /r/{code} → raum.html, /a/{token} → a.html, /konto → konto.html.
// Entwicklung: `npm run dev` leitet /api an das Backend weiter (AGORA_API, Standard http://127.0.0.1:8000).
import { defineConfig } from 'vite';

export default defineConfig({
  base: '/',
  build: {
    outDir: 'dist', emptyOutDir: true, sourcemap: false, assetsInlineLimit: 0, modulePreload: { polyfill: false },
    target: 'es2022',
    chunkSizeWarningLimit: 700,   // livekit-client allein ist ~520 kB (nur auf der Raum-Seite)
    rollupOptions: { input: { index: 'index.html', konto: 'konto.html', raum: 'raum.html', passwort: 'passwort.html', a: 'a.html' } },
  },
  server: { proxy: { '/api': process.env.AGORA_API || 'http://127.0.0.1:8000' } },
});
