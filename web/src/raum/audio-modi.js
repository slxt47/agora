// Audio-Modi im Meeting und in der Lobby: „Originalton/Musik“ und Rauschunterdrückung (Browser / Stark mit RNNoise / Aus).
// RNNoise läuft als LiveKit-TrackProcessor: eigener AudioContext mit 48 kHz (RNNoise rechnet mit 48 kHz),
// Mikrofon → AudioWorklet (WASM) → MediaStreamDestination; LiveKit sendet die bearbeitete Spur.
// Dateien unter /vendor/rnnoise-<version>/ (scripts/rnnoise.mjs), Bibliothek erst bei Bedarf geladen.
import { AudioPresets, RoomEvent, Track, createLocalAudioTrack } from 'livekit-client';
import { h, toast } from '../common.js';
import { t } from '../i18n.js';
import { audioLesen, audioMerken, audioPruefen, erfassung, rauschenWirksam, veroeffentlichung } from './audio-wahl.js';

/* global __RNNOISE_BASIS__ */
const BASIS = __RNNOISE_BASIS__;
let wasmLaden = null;

/** RNNoise als LiveKit-TrackProcessor (Audio). */
export class Rauschfilter {
  constructor() { this.name = 'agora-rnnoise'; this.processedTrack = undefined; }

  async init({ track }) { await this.aufbauen(track); }

  async restart({ track }) { await this.abbauen(); await this.aufbauen(track); }

  async destroy() { await this.abbauen(); }

  async aufbauen(track) {
    const { RnnoiseWorkletNode, loadRnnoise } = await import('@sapphi-red/web-noise-suppressor');
    wasmLaden ||= loadRnnoise({ url: `${BASIS}/rnnoise.wasm`, simdUrl: `${BASIS}/rnnoise_simd.wasm` }).catch((e) => { wasmLaden = null; throw e; });
    const wasm = await wasmLaden;
    let ctx = new AudioContext({ sampleRate: 48000, latencyHint: 'interactive' });
    let quelle;
    try { quelle = ctx.createMediaStreamSource(new MediaStream([track])); } catch {
      // Firefox: Quelle und Kontext müssen dieselbe Abtastrate haben → Kontext in der Rate des Mikrofons
      await ctx.close();
      ctx = new AudioContext({ latencyHint: 'interactive' });
      quelle = ctx.createMediaStreamSource(new MediaStream([track]));
    }
    this.ctx = ctx;
    await ctx.audioWorklet.addModule(`${BASIS}/rnnoise-worklet.js`);
    this.knoten = new RnnoiseWorkletNode(ctx, { maxChannels: 1, wasmBinary: wasm });
    const ziel = ctx.createMediaStreamDestination();
    quelle.connect(this.knoten).connect(ziel);
    this.quelle = quelle;
    if (ctx.state === 'suspended') await ctx.resume().catch(() => {});
    this.processedTrack = ziel.stream.getAudioTracks()[0];
  }

  async abbauen() {
    try { this.quelle?.disconnect(); this.knoten?.disconnect(); this.knoten?.destroy(); } catch { /* schon weg */ }
    this.processedTrack?.stop();
    await this.ctx?.close().catch(() => {});
    this.ctx = this.knoten = this.quelle = null;
    this.processedTrack = undefined;
  }
}

/** Formularfelder (Schalter + Auswahl) – für Lobby-Menü und Einstellungen im Meeting. */
export function audioFelder(wahl, aendern) {
  const sel = h('select', { name: 'rauschen', disabled: wahl.musik, onchange: (e) => aendern({ rauschen: e.target.value }) },
    [['browser', t('audio.rauschenBrowser')], ['stark', t('audio.rauschenStark')], ['aus', t('audio.rauschenAus')]]
      .map(([w, text]) => h('option', { value: w, selected: rauschenWirksam(wahl) === w }, text)));
  const hinweis = h('small', {}, wahl.musik ? t('audio.rauschenMusik') : t('audio.rauschenHinweis'));
  const musik = (an) => {
    sel.disabled = an;
    sel.value = an ? 'aus' : audioLesen().rauschen;
    hinweis.textContent = an ? t('audio.rauschenMusik') : t('audio.rauschenHinweis');
  };
  return [
    h('label', { class: 'feld' }, h('span', {}, t('audio.rauschen')), sel, hinweis),
    h('label', { class: 'schalter' },
      h('span', { class: 'txt' }, h('span', {}, t('audio.musik')), h('small', {}, t('audio.musikHinweis'))),
      h('input', { type: 'checkbox', role: 'switch', name: 'musik', checked: wahl.musik,
        onchange: (e) => { aendern({ musik: e.target.checked }); musik(e.target.checked); } })),
  ];
}

/** Im Meeting: Raum-Optionen setzen, Mikrofon bei Wechsel neu veröffentlichen, RNNoise anhängen. */
export class AudioModi {
  constructor(m) {
    this.m = m;
    this.wahl = audioLesen();
    this.zustand('aus');
    this.verbinden(m.room);
    document.addEventListener('agora:raumwechsel', (e) => this.verbinden(e.detail.room));
  }

  verbinden(room) {
    this.optionenSetzen(room);
    room.on(RoomEvent.LocalTrackPublished, (pub) => {
      if (room !== this.m.room || pub.source !== Track.Source.Microphone || !pub.track) return;
      // Für Anzeige/Tests: wirkt die Verarbeitung des Browsers? („roh“ = Originalton)
      const s = pub.track.mediaStreamTrack?.getSettings?.() || {};
      document.body.dataset.mikrofon = s.echoCancellation === false && s.autoGainControl === false ? 'roh' : 'sprache';
      this.filterAnwenden(pub.track);
    });
  }

  /** Vorgaben des Raums: gelten für jede neue Mikrofonspur (auch nach Breakout-Wechsel). */
  optionenSetzen(room) {
    Object.assign(room.options.audioCaptureDefaults, erfassung(this.wahl));
    Object.assign(room.options.publishDefaults, veroeffentlichung(this.wahl, AudioPresets));
  }

  zustand(z) { document.body.dataset.rauschfilter = z; }   // aus | laedt | aktiv | fehler (auch für E2E-Tests)

  async filterAnwenden(track) {
    if (rauschenWirksam(this.wahl) !== 'stark') { this.zustand('aus'); return; }
    if (track.getProcessor?.()?.name === 'agora-rnnoise') return;
    this.zustand('laedt');
    try {
      if (!track.audioContext) await track.setAudioContext(new AudioContext());   // Processor-API verlangt einen Kontext
      await track.setProcessor(new Rauschfilter());
      this.zustand('aktiv');
    } catch (e) {
      this.zustand('fehler');
      console.warn('RNNoise nicht verfügbar:', e);
      toast(t('audio.rnnoiseFehler'), 'warn', { dauer: 6000 });
    }
  }

  /** Neue Wahl übernehmen: Optionen setzen und das Mikrofon mit den neuen Einstellungen neu veröffentlichen. */
  setzen(neu) {      // nacheinander: ein Wechsel veröffentlicht das Mikrofon neu, das darf sich nicht überholen
    // … aber höchstens 5 s auf den vorigen warten (hängt ein Veröffentlichen, bleibt die Wahl trotzdem bedienbar)
    const vorher = this.kette || Promise.resolve();
    const grenze = new Promise((ok) => setTimeout(() => ok('zeit'), 5000));
    this.kette = Promise.race([vorher, grenze]).then((r) => { if (r === 'zeit') console.warn('Audio-Modus: voriger Wechsel hängt'); return this.wechseln(neu); });
    return this.kette;
  }

  async wechseln(neu) {
    const vorher = this.wahl;
    this.wahl = audioPruefen({ ...this.wahl, ...neu });
    audioMerken(this.wahl);
    const room = this.m.room;
    this.optionenSetzen(room);
    if (neu.musik !== undefined && neu.musik !== vorher.musik) toast(this.wahl.musik ? t('audio.musikAn') : t('audio.musikAus'), '', { dauer: 4000 });
    const lp = room.localParticipant;
    const pub = lp.getTrackPublication(Track.Source.Microphone);
    if (!pub?.track) { this.zustand('aus'); return; }
    const stumm = pub.isMuted;
    const geraet = room.getActiveDevice('audioinput');
    try {
      await lp.unpublishTrack(pub.track);
      const track = await createLocalAudioTrack({ ...room.options.audioCaptureDefaults, deviceId: geraet || undefined });
      if (stumm) await track.mute();
      await lp.publishTrack(track, { source: Track.Source.Microphone });
    } catch (e) {
      toast(t('meeting.veroeffentlichenFehler', { text: e.message }), 'err');
    }
    this.m.steuerAktualisieren();
  }

  /** Einträge für das Mehr-Menü */
  menueEintrag() {
    return { text: t('audio.musik'), symbol: 'lautsprecher', an: this.wahl.musik, aktion: () => this.setzen({ musik: !this.wahl.musik }) };
  }

  felder() { return audioFelder(this.wahl, (neu) => this.setzen(neu)); }
}
