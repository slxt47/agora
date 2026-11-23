// Video-Prozessoren (eigener Chunk, wird erst bei der ersten Effekt-Wahl geladen): @livekit/track-processors mit
// MediaPipe (Selfie-Segmentierung, WebGL) für Unschärfe/Hintergrundbild, plus „Aussehen verbessern“ – ein milder
// Helligkeits-/Kontrast-Filter (Canvas-2D ctx.filter auf einer OffscreenCanvas), der VOR der Segmentierung läuft.
// Ohne Hintergrund-Effekt läuft nur der Filter (kein MediaPipe-Download). Alle MediaPipe-Dateien kommen von uns
// selbst (/vendor/mediapipe-<version>/, siehe scripts/mediapipe.mjs) – nie von einem CDN.
/* global __MP_BASIS__, __MP_MODELL__ */
import { BackgroundTransformer, BackgroundProcessorWrapper, ProcessorWrapper, VideoTransformer } from '@livekit/track-processors';

export const NAME_HINTERGRUND = 'agora-hintergrund';
export const NAME_FILTER = 'agora-filter';
const ASSETS = { tasksVisionFileSet: `${__MP_BASIS__}/wasm`, modelAssetPath: __MP_MODELL__ };
const FILTER = 'brightness(1.07) contrast(1.06) saturate(1.05)';

/** Zeichnet ein VideoFrame gefiltert auf eine OffscreenCanvas und gibt ein neues Frame zurück (das alte wird geschlossen). */
function filterZeichner() {
  let leinwand = null, ctx = null;
  return (frame) => {
    const b = frame.displayWidth, h = frame.displayHeight;
    if (!leinwand) { leinwand = new OffscreenCanvas(b, h); ctx = leinwand.getContext('2d', { alpha: false }); }
    if (leinwand.width !== b || leinwand.height !== h) { leinwand.width = b; leinwand.height = h; }
    ctx.filter = FILTER;
    ctx.drawImage(frame, 0, 0, b, h);
    const neu = new VideoFrame(leinwand, { timestamp: frame.timestamp });
    frame.close();
    return neu;
  };
}

/** Hintergrund-Effekt; mit verbessern=true wird jedes Frame vorher mild aufgehellt. */
class Hintergrund extends BackgroundTransformer {
  constructor(optionen, verbessern) {
    super(optionen);
    this.verbessern = verbessern;
    this.zeichnen = filterZeichner();
  }

  transform(frame, controller) {
    if (this.verbessern && !this.isDisabled && frame instanceof VideoFrame && frame.codedWidth) {
      try { frame = this.zeichnen(frame); } catch { /* ungefiltert weiter */ }
    }
    return super.transform(frame, controller);
  }
}

/** Nur „Aussehen verbessern“: kein WebGL, kein MediaPipe. */
class NurFilter extends VideoTransformer {
  constructor(messen) { super(); this.messen = messen; }

  async init({ inputElement }) {
    this.transformer = new TransformStream({ transform: (f, c) => this.transform(f, c) });
    this.inputVideo = inputElement;
    this.zeichnen = filterZeichner();
    this.isDisabled = false;
  }

  async restart({ inputElement }) { this.inputVideo = inputElement; this.isDisabled = false; }

  transform(frame, controller) {
    if (this.isDisabled) { frame.close(); return; }
    const t0 = performance.now();
    let neu;
    try { neu = this.zeichnen(frame); } catch { controller.enqueue(frame); return; }
    controller.enqueue(neu);
    this.messen?.(performance.now() - t0);
  }

  update() { /* nichts einstellbar */ }
}

const optionen = (ziel) => (ziel.art === 'unschaerfe' ? { blurRadius: ziel.radius, imagePath: undefined } : { imagePath: ziel.bild, blurRadius: undefined });

export function hintergrundProzessor(ziel, messen) {
  const tr = new Hintergrund({
    ...optionen(ziel), assetPaths: ASSETS, segmenterOptions: { delegate: 'GPU' },
    onFrameProcessed: (s) => messen?.(s.processingTimeMs),
  }, ziel.verbessern);
  return new BackgroundProcessorWrapper(tr, NAME_HINTERGRUND, { maxFps: 30 });
}

/** Laufenden Hintergrund-Prozessor ohne Neustart umstellen (kein Flackern). */
export async function umschalten(prozessor, ziel) {
  prozessor.transformer.verbessern = ziel.verbessern;
  await prozessor.switchTo(ziel.art === 'unschaerfe' ? { mode: 'background-blur', blurRadius: ziel.radius } : { mode: 'virtual-background', imagePath: ziel.bild });
}

export function filterProzessor(messen) {
  return new ProcessorWrapper(new NurFilter(messen), NAME_FILTER, { maxFps: 30 });
}
