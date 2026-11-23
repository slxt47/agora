// Video-Effekte für die eigene Kamera (Lobby und Meeting): Hintergrund unscharf (leicht/stark), eingebaute Hintergründe,
// eigenes Bild (bleibt im Browser: nur localStorage, nie hochgeladen) und „Aussehen verbessern“ (milder Helligkeits-/
// Kontrastfilter). Die Wahl bleibt im Browser (localStorage „agora-effekt“). Ein gemeinsamer Zustand für Lobby und
// Meeting (Modul-Singleton „effekte“): Der Lobby-Track wird beim Beitritt mitsamt Prozessor veröffentlicht.
// Schwere Teile (track-processors, MediaPipe-WASM ~9 MB, Modell) lädt erst effekte-verarbeitung.js bei der ersten Wahl.
// Abschalten: unterstützt der Browser es nicht (Feature-Erkennung), im Datensparmodus (pausieren) und automatisch,
// wenn das Gerät pro Bild im Mittel länger als 100 ms rechnet.
import { TrackEvent } from 'livekit-client';
import { h, toast } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import { menue } from './menue.js';
import { BILDER, wahlPruefen, zielAus, verkleinert } from './effekte-wahl.js';
import './effekte.css';

const SPEICHER = 'agora-effekt', SPEICHER_BILD = 'agora-effekt-bild';
const BILD_URL = Object.fromEntries(Object.entries(import.meta.glob('./hintergruende/*.svg', { eager: true, query: '?url', import: 'default' }))
  .map(([pfad, url]) => [pfad.replace(/^.*\/(\w+)\.svg$/, '$1'), url]));
const ZU_LANGSAM_MS = 100, MESS_FENSTER = 150;

// ---------------------------------------------------------------- Feature-Erkennung (einmal)
// WASM-SIMD-Test (wasm-feature-detect): MediaPipe liefern wir nur in der SIMD-Variante aus.
const SIMD = new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11]);
let faehig = null;
export function faehigkeiten() {
  if (faehig) return faehig;
  faehig = { hintergrund: false, verbessern: false };
  try {
    const basis = typeof VideoFrame !== 'undefined' && typeof OffscreenCanvas !== 'undefined'
      && ((typeof MediaStreamTrackProcessor !== 'undefined' && typeof MediaStreamTrackGenerator !== 'undefined') || 'captureStream' in HTMLCanvasElement.prototype);
    if (!basis) return faehig;
    faehig.verbessern = typeof OffscreenCanvasRenderingContext2D !== 'undefined' && 'filter' in OffscreenCanvasRenderingContext2D.prototype;
    const gl = document.createElement('canvas').getContext('webgl2');
    gl?.getExtension('WEBGL_lose_context')?.loseContext();
    faehig.hintergrund = !!gl && typeof createImageBitmap !== 'undefined' && typeof WebAssembly === 'object' && WebAssembly.validate(SIMD);
  } catch { /* nichts */ }
  return faehig;
}

// ---------------------------------------------------------------- Bilder
function bildLaden(url) {
  return new Promise((ok, nein) => {
    const i = new Image();
    i.onload = () => ok(i);
    i.onerror = () => nein(new Error(t('effekte.bildFehler')));
    i.src = url;
  });
}
/** Bild (URL oder Datei) auf eine Canvas rastern → JPEG-Blob. SVGs werden so überall zu einem gewöhnlichen Bild. */
async function rastern(quelle, max) {
  const bild = quelle instanceof Blob ? await createImageBitmap(quelle) : await bildLaden(quelle);
  const { b, h: hoehe } = verkleinert(bild.naturalWidth || bild.width, bild.naturalHeight || bild.height, max);
  const c = document.createElement('canvas');
  c.width = b; c.height = hoehe;
  c.getContext('2d').drawImage(bild, 0, 0, b, hoehe);
  bild.close?.();
  return new Promise((ok, nein) => c.toBlob((blob) => (blob ? ok(blob) : nein(new Error(t('effekte.bildFehler')))), 'image/jpeg', 0.88));
}
function blobAusDataUrl(d) {
  const [kopf, daten] = d.split(',');
  const bin = atob(daten);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: /data:([^;]+)/.exec(kopf)?.[1] || 'image/jpeg' });
}
const dataUrl = (blob) => new Promise((ok, nein) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = nein; r.readAsDataURL(blob); });

// ---------------------------------------------------------------- Zustand
class Effekte extends EventTarget {
  constructor() {
    super();
    let roh = null;
    try { roh = JSON.parse(localStorage.getItem(SPEICHER) || 'null'); } catch { /* privat */ }
    this.wahl = wahlPruefen(roh);
    this.track = null;
    this.pausiert = false;
    this.zustand = 'aus';           // aus | laedt | aktiv | wartet | fehler
    this.meldung = '';
    this.kette = Promise.resolve();
    this.raster = new Map();        // Bild-ID → blob:-URL (gerastert)
    this.eigenes = null;            // blob:-URL des eigenen Bildes
    try { const d = localStorage.getItem(SPEICHER_BILD); if (d) this.eigenes = URL.createObjectURL(blobAusDataUrl(d)); } catch { /* kaputt/privat */ }
    this.messungen = [];
    this.messen = (ms) => this.messung(ms);
  }

  zustandSetzen(z, meldung = '') { this.zustand = z; this.meldung = meldung; this.dispatchEvent(new Event('zustand')); }

  ziel() {
    const f = faehigkeiten();
    return zielAus(this.wahl, { eigenesBild: !!this.eigenes, hintergrundOk: f.hintergrund, verbessernOk: f.verbessern });
  }

  setzen(teil) {
    this.wahl = wahlPruefen({ ...this.wahl, ...teil });
    try { localStorage.setItem(SPEICHER, JSON.stringify(this.wahl)); } catch { /* privat */ }
    this.dispatchEvent(new Event('zustand'));
    return this.anwenden();
  }

  /** Datensparmodus: Effekte aussetzen, ohne die Wahl zu vergessen. */
  pausieren(an) {
    if (this.pausiert === an) return Promise.resolve();
    this.pausiert = an;
    if (an && this.ziel()) toast(t('effekte.pausiert'), '', { dauer: 3500 });
    return this.anwenden();
  }

  /** Effekt auf einen (neuen) Kamera-Track anwenden. Aufrufe laufen nacheinander (keine Wettläufe). */
  anwenden(track = this.track) {
    this.track = track;
    const lauf = this.kette.then(() => this.umsetzen(track));
    this.kette = lauf.catch(() => {});
    return lauf;
  }

  async umsetzen(track) {
    if (!track || track !== this.track) return;
    const ziel = this.pausiert ? null : this.ziel();
    const proc = track.getProcessor?.();
    if (!ziel) {
      if (proc) await track.stopProcessor().catch(() => {});
      this.zustandSetzen('aus', this.pausiert && this.ziel() ? t('effekte.pausiert') : '');
      return;
    }
    if (track.isMuted || track.mediaStreamTrack?.readyState !== 'live') {
      // Kamera aus: beim Einschalten anwenden (LiveKit startet einen vorhandenen Prozessor selbst neu)
      if (!proc) { track.once(TrackEvent.Unmuted, () => this.anwenden(track)); this.zustandSetzen('wartet', t('effekte.kameraAus')); return; }
    }
    this.zustandSetzen('laedt', t('effekte.laedt'));
    try {
      const v = await import('./effekte-verarbeitung.js');
      if (ziel.art === 'bild') ziel.bild = await this.bildUrl(ziel.id);
      if (track !== this.track) return;
      if (ziel.art === 'filter') {
        if (proc?.name !== v.NAME_FILTER) await track.setProcessor(v.filterProzessor(this.messen));
      } else if (proc?.name === v.NAME_HINTERGRUND) {
        await v.umschalten(proc, ziel);
      } else {
        await track.setProcessor(v.hintergrundProzessor(ziel, this.messen));
      }
      this.messungen = [];
      this.zustandSetzen('aktiv');
    } catch (e) {
      console.warn('Agora: Effekt nicht möglich', e);
      if (track.getProcessor?.()) await track.stopProcessor().catch(() => {});
      this.zustandSetzen('fehler', t('effekte.fehler'));
      toast(t('effekte.fehler'), 'err', { dauer: 6000 });
    }
  }

  /** Rechenzeit je Bild beobachten: im Mittel über 100 ms (unter ~10 Bilder/s) → Effekt aus. */
  messung(ms) {
    if (this.zustand !== 'aktiv') return;
    this.messungen.push(ms);
    if (this.messungen.length < MESS_FENSTER) return;
    const mittel = this.messungen.reduce((a, b) => a + b, 0) / this.messungen.length;
    this.messungen = [];
    if (mittel > ZU_LANGSAM_MS) {
      toast(t('effekte.zuLangsam'), 'warn', { dauer: 7000 });
      this.setzen({ hintergrund: 'aus', verbessern: false });
    }
  }

  async bildUrl(id) {
    if (id === 'eigen') return this.eigenes;
    if (!this.raster.has(id)) this.raster.set(id, URL.createObjectURL(await rastern(BILD_URL[id], 1280)));
    return this.raster.get(id);
  }

  /** Eigenes Bild: verkleinern (max. 1600 px), lokal merken (localStorage), nie hochladen. */
  async eigenesSetzen(datei) {
    const blob = await rastern(datei, 1600);
    if (this.eigenes) URL.revokeObjectURL(this.eigenes);
    this.eigenes = URL.createObjectURL(blob);
    try { localStorage.setItem(SPEICHER_BILD, await dataUrl(blob)); } catch { toast(t('effekte.bildNurSitzung'), 'warn', { dauer: 6000 }); }
    return this.setzen({ hintergrund: 'eigen' });
  }

  eigenesEntfernen() {
    if (this.eigenes) URL.revokeObjectURL(this.eigenes);
    this.eigenes = null;
    try { localStorage.removeItem(SPEICHER_BILD); } catch { /* egal */ }
    return this.setzen({ hintergrund: this.wahl.hintergrund === 'eigen' ? 'aus' : this.wahl.hintergrund });
  }
}

export const effekte = new Effekte();

// ---------------------------------------------------------------- Auswahl (Popover am Knopf)
export function effektePanel(anker) {
  const f = faehigkeiten();
  const datei = h('input', { type: 'file', accept: 'image/*', class: 'nur-sr', tabindex: '-1', 'aria-hidden': 'true' });
  const status = h('p', { class: 'effekt-status', role: 'status', 'aria-live': 'polite' });
  const kachel = (wert, text, vorschau, extra = {}) => h('button', {
    type: 'button', role: 'radio', class: 'effekt-kachel', dataset: { wert }, title: text, 'aria-label': text,
    disabled: wert !== 'aus' && !f.hintergrund, onclick: () => effekte.setzen({ hintergrund: wert }), ...extra,
  }, vorschau, h('span', { class: 'effekt-name', 'aria-hidden': 'true' }, text));
  const symbol = (name, klasse = '') => h('span', { class: `effekt-symbol ${klasse}`.trim(), html: icon(name) });
  const bild = (url) => h('img', { src: url, alt: '', loading: 'lazy', decoding: 'async', draggable: 'false' });

  const eigenesKachel = () => (effekte.eigenes
    ? kachel('eigen', t('effekte.eigenes'), bild(effekte.eigenes))
    : null);
  const raster = h('div', { class: 'effekt-raster', role: 'radiogroup', 'aria-label': t('effekte.hintergrund') },
    kachel('aus', t('effekte.aus'), symbol('person')),
    kachel('leicht', t('effekte.leicht'), symbol('unschaerfe', 'leicht')),
    kachel('stark', t('effekte.stark'), symbol('unschaerfe', 'stark')),
    BILDER.map((id) => kachel(id, t(`effekte.bild.${id}`), bild(BILD_URL[id]))),
    eigenesKachel(),
    h('button', { type: 'button', class: 'effekt-kachel hochladen', title: t('effekte.eigenesWaehlen'), 'aria-label': t('effekte.eigenesWaehlen'), disabled: !f.hintergrund,
      onclick: () => datei.click() }, symbol('bildPlus'), h('span', { class: 'effekt-name', 'aria-hidden': 'true' }, t('effekte.eigenesKurz'))));
  const entfernen = h('button', { type: 'button', class: 'btn leise klein', onclick: () => effekte.eigenesEntfernen() }, t('effekte.eigenesEntfernen'));
  const verbessern = h('input', { type: 'checkbox', role: 'switch', disabled: !f.verbessern,
    onchange: (e) => effekte.setzen({ verbessern: e.target.checked }) });

  datei.addEventListener('change', async () => {
    const d = datei.files?.[0];
    datei.value = '';
    if (!d) return;
    if (!/^image\//.test(d.type)) { toast(t('effekte.bildFehler'), 'err'); return; }
    try { await effekte.eigenesSetzen(d); } catch { toast(t('effekte.bildFehler'), 'err'); }
  });

  const inhalt = h('div', { class: 'effekte' },
    h('div', { class: 'effekte-kopf' }, h('b', {}, t('effekte.hintergrund'))),
    f.hintergrund ? null : h('p', { class: 'status warn' }, t('effekte.nichtUnterstuetzt')),
    raster, datei,
    h('p', { class: 'sec klein effekt-hinweis' }, t('effekte.eigenesHinweis')),
    entfernen,
    h('label', { class: 'schalter' }, h('span', { class: 'txt' }, h('span', {}, t('effekte.verbessern')),
      h('small', {}, f.verbessern ? t('effekte.verbessernHinweis') : t('effekte.verbessernFehlt'))), verbessern),
    status);

  const aktualisieren = () => {
    if (!inhalt.isConnected && inhalt.dataset.offen) { effekte.removeEventListener('zustand', aktualisieren); return; }
    const neu = eigenesKachel(), alt = raster.querySelector('[data-wert="eigen"]');
    if (alt && (!neu || alt.querySelector('img')?.src !== effekte.eigenes)) alt.remove();
    if (neu && !raster.querySelector('[data-wert="eigen"]')) raster.lastElementChild.before(neu);
    for (const k of raster.querySelectorAll('[role="radio"]')) k.setAttribute('aria-checked', String(k.dataset.wert === effekte.wahl.hintergrund));
    entfernen.hidden = !effekte.eigenes;
    verbessern.checked = effekte.wahl.verbessern;
    status.dataset.zustand = effekte.zustand;
    status.textContent = effekte.pausiert && effekte.ziel() ? t('effekte.pausiert') : effekte.meldung;
    status.hidden = !status.textContent;
  };
  aktualisieren();
  effekte.addEventListener('zustand', aktualisieren);
  menue(anker, null, { inhalt, label: t('effekte.titel') });
  inhalt.dataset.offen = '1';
  raster.querySelector('[aria-checked="true"]:not(:disabled)')?.focus({ preventScroll: true });
}
