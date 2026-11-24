// RNNoise (KI-Rauschunterdrückung) selbst hosten: AudioWorklet + WASM (normal und SIMD) aus
// node_modules/@sapphi-red/web-noise-suppressor (MIT; enthält @shiguredo/rnnoise-wasm, Apache-2.0; RNNoise, BSD-3) nach
// public/vendor/rnnoise-<version>/ kopieren. Nichts kommt zur Laufzeit von einem CDN; Version im Pfad → „immutable“.
// Läuft in „npm run build“/„npm run dev“ NACH scripts/mediapipe.mjs (das leert public/vendor/).
// vite.config.js liest BASIS von hier; audio-modi.js lädt die Dateien erst, wenn jemand „Stark (RNNoise)“ wählt.
import { copyFileSync, mkdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const WEB = new URL('../', import.meta.url);
const PAKET = new URL('node_modules/@sapphi-red/web-noise-suppressor/', WEB);
export const VERSION = JSON.parse(readFileSync(new URL('package.json', PAKET), 'utf8')).version;
/** URL-Pfad, wie ihn der Browser sieht */
export const BASIS = `/vendor/rnnoise-${VERSION}`;
const DATEIEN = [['dist/rnnoise/workletProcessor.js', 'rnnoise-worklet.js'], ['dist/rnnoise.wasm', 'rnnoise.wasm'], ['dist/rnnoise_simd.wasm', 'rnnoise_simd.wasm']];

export function kopieren() {
  const ziel = new URL(`public${BASIS}/`, WEB);
  mkdirSync(ziel, { recursive: true });
  for (const [von, nach] of DATEIEN) copyFileSync(new URL(von, PAKET), new URL(nach, ziel));
  console.log(`RNNoise ${VERSION} nach public${BASIS}/ kopiert`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) kopieren();
