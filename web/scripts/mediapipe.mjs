// MediaPipe (Hintergrund-Effekte) selbst hosten: WASM-Lader + WASM aus node_modules/@mediapipe/tasks-vision und das
// Segmentierungsmodell aus modelle/ nach public/vendor/mediapipe-<version>/ kopieren. Nichts kommt zur Laufzeit von einem
// CDN (CSP + Datenschutz). Der Pfad enthält die Version (und das Modell seine Prüfsumme) → darf „immutable“ gecacht werden.
// Nur die SIMD-Variante: Jeder Browser mit VideoFrame + OffscreenCanvas + WebGL2 kann WASM-SIMD; effekte.js prüft das vorher.
// Aufruf: node scripts/mediapipe.mjs (läuft in „npm run build“ und „npm run dev“). vite.config.js liest BASIS/MODELL von hier.
import { createHash } from 'node:crypto';
import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const WEB = new URL('../', import.meta.url);
const TV = new URL('node_modules/@mediapipe/tasks-vision/', WEB);
export const VERSION = JSON.parse(readFileSync(new URL('package.json', TV), 'utf8')).version;
const MODELL_SHA = '191ac9529ae506ee0beefa6b2c945a172dab9d07d1e802a290a4e4038226658b';
/** URL-Pfade, wie sie der Browser sieht */
export const BASIS = `/vendor/mediapipe-${VERSION}`;
export const MODELL = `${BASIS}/selfie_segmenter-${MODELL_SHA.slice(0, 8)}.tflite`;

export function kopieren() {
  const modell = readFileSync(new URL('modelle/selfie_segmenter.tflite', WEB));
  const sha = createHash('sha256').update(modell).digest('hex');
  if (sha !== MODELL_SHA) throw new Error(`modelle/selfie_segmenter.tflite: Prüfsumme ${sha} ≠ ${MODELL_SHA}`);
  const vendor = new URL('public/vendor/', WEB);
  rmSync(vendor, { recursive: true, force: true });          // alte Versionen weg
  const ziel = new URL(`public${BASIS}/`, WEB);
  mkdirSync(new URL('wasm/', ziel), { recursive: true });
  for (const f of ['vision_wasm_internal.js', 'vision_wasm_internal.wasm']) copyFileSync(new URL(`wasm/${f}`, TV), new URL(`wasm/${f}`, ziel));
  writeFileSync(new URL(`public${MODELL}`, WEB), modell);
  console.log(`MediaPipe ${VERSION} nach public${BASIS}/ kopiert`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) kopieren();
