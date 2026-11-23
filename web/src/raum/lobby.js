// Pre-Join-Lobby: Kameravorschau, Geräteauswahl (Kamera, Mikrofon, Lautsprecher per setSinkId), Pegelanzeige,
// Lautsprechertest, Mikro/Kamera vor dem Beitritt an/aus, verständliche Fehler (blockiert / kein Gerät / belegt).
// Die hier erzeugten LiveKit-Tracks werden beim Beitritt direkt veröffentlicht (kein zweiter Berechtigungsdialog).
import {
  createLocalVideoTrack, createLocalAudioTrack, createAudioAnalyser, Room, MediaDeviceFailure, supportsAudioOutputSelection, VideoPresets,
} from 'livekit-client';
import { $, h } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import { effekte, effektePanel } from './effekte.js';

const SPEICHER = 'agora-geraete';
export function geraeteMerken(werte) {
  try { localStorage.setItem(SPEICHER, JSON.stringify({ ...geraeteLesen(), ...werte })); } catch { /* privat */ }
}
export function geraeteLesen() {
  try { return JSON.parse(localStorage.getItem(SPEICHER) || '{}') || {}; } catch { return {}; }
}

/** Verständlicher Text zu einem Kamera-/Mikrofonfehler. art = 'kamera' | 'mikrofon' */
export function geraeteFehler(e, art) {
  if (!navigator.mediaDevices?.getUserMedia) return t('geraet.unsicher');
  const f = MediaDeviceFailure.getFailure(e);
  const wer = art === 'kamera' ? t('geraet.kamera') : t('geraet.mikrofon');
  if (f === MediaDeviceFailure.PermissionDenied) return t('geraet.blockiert', { wer });
  if (f === MediaDeviceFailure.NotFound) return t('geraet.keins', { wer });
  if (f === MediaDeviceFailure.DeviceInUse) return t('geraet.belegt', { wer });
  return t('geraet.anderer', { wer, text: e?.message || '' });
}

export const VIDEO_AUFLOESUNG = VideoPresets.h720.resolution;

export class Lobby {
  constructor() {
    const g = geraeteLesen();
    this.micAn = g.micAn !== false;
    this.camAn = g.camAn !== false;
    this.wahl = { videoinput: g.videoinput || '', audioinput: g.audioinput || '', audiooutput: g.audiooutput || '' };
    this.videoTrack = null;
    this.audioTrack = null;
    this.analyse = null;
    this.fehler = { kamera: '', mikrofon: '' };
    this.schleife = 0;
    this.videoEl = $('#vorschau-video');
    this.aktiv = true;
    this.geraeteNeu = () => this.geraeteFuellen();
  }

  async start(name) {
    this.avatar(name);
    $('#lobby-mic').addEventListener('click', () => this.micUmschalten());
    $('#lobby-cam').addEventListener('click', () => this.kameraUmschalten());
    $('#wahl-kamera').addEventListener('change', (e) => this.wechseln('videoinput', e.target.value));
    $('#wahl-mikrofon').addEventListener('change', (e) => this.wechseln('audioinput', e.target.value));
    $('#wahl-lautsprecher').addEventListener('change', (e) => this.wechseln('audiooutput', e.target.value));
    $('#ton-testen').addEventListener('click', () => this.tonTesten());
    $('#lobby-effekte').addEventListener('click', (e) => effektePanel(e.currentTarget));
    effekte.addEventListener('zustand', () => this.aktiv && this.knoepfe());
    if (!supportsAudioOutputSelection()) $('#feld-lautsprecher').hidden = true;
    navigator.mediaDevices?.addEventListener?.('devicechange', this.geraeteNeu);
    await Promise.all([this.camAn ? this.kameraHolen() : null, this.mikrofonHolen()]);
    await this.geraeteFuellen();
    this.knoepfe();
  }

  avatar(name) {
    const s = $('#vorschau-avatar span');
    s.textContent = (name || '?').trim().slice(0, 1).toUpperCase() || '?';
  }

  async kameraHolen() {
    try {
      this.videoTrack = await createLocalVideoTrack({ deviceId: this.wahl.videoinput || undefined, resolution: VIDEO_AUFLOESUNG, facingMode: 'user' });
      this.videoTrack.attach(this.videoEl);
      effekte.anwenden(this.videoTrack);     // gespeicherten Hintergrund-Effekt gleich in der Vorschau zeigen
      this.fehler.kamera = '';
      this.wahl.videoinput = (await this.videoTrack.getDeviceId()) || this.wahl.videoinput;
    } catch (e) {
      // Gespeicherte Kamera fehlt? Einmal mit Standardgerät versuchen
      if (this.wahl.videoinput && MediaDeviceFailure.getFailure(e) === MediaDeviceFailure.NotFound) { this.wahl.videoinput = ''; return this.kameraHolen(); }
      this.videoTrack = null;
      this.camAn = false;
      this.fehler.kamera = geraeteFehler(e, 'kamera');
    }
    this.meldung();
  }

  async mikrofonHolen() {
    try {
      this.audioTrack = await createLocalAudioTrack({ deviceId: this.wahl.audioinput || undefined, echoCancellation: true, noiseSuppression: true, autoGainControl: true });
      this.fehler.mikrofon = '';
      this.wahl.audioinput = (await this.audioTrack.getDeviceId()) || this.wahl.audioinput;
      if (!this.micAn) await this.audioTrack.mute();
      this.pegelStarten();
    } catch (e) {
      if (this.wahl.audioinput && MediaDeviceFailure.getFailure(e) === MediaDeviceFailure.NotFound) { this.wahl.audioinput = ''; return this.mikrofonHolen(); }
      this.audioTrack = null;
      this.micAn = false;
      this.fehler.mikrofon = geraeteFehler(e, 'mikrofon');
    }
    this.meldung();
  }

  pegelStarten() {
    this.analyse?.cleanup();
    this.analyse = null;
    if (!this.audioTrack) return;
    try { this.analyse = createAudioAnalyser(this.audioTrack, { cloneTrack: true }); } catch { return; }
    const balken = $('#pegel');
    cancelAnimationFrame(this.schleife);
    const tick = () => {
      if (!this.aktiv) return;
      const v = this.micAn && this.analyse ? this.analyse.calculateVolume() : 0;
      balken.style.width = `${Math.min(100, Math.round(v * 260))}%`;
      this.schleife = requestAnimationFrame(tick);
    };
    tick();
  }

  meldung() {
    const texte = [this.fehler.kamera, this.fehler.mikrofon].filter(Boolean);
    const s = $('#geraete-status');
    s.hidden = !texte.length;
    if (texte.length) { s.className = 'status warn oben'; s.textContent = texte.join(' '); }
    this.knoepfe();
  }

  knoepfe() {
    const mic = $('#lobby-mic'), cam = $('#lobby-cam');
    mic.innerHTML = icon(this.micAn ? 'mic' : 'micAus');
    mic.setAttribute('aria-pressed', String(this.micAn));
    mic.setAttribute('aria-label', this.micAn ? t('steuer.micAus') : t('steuer.micAn'));
    mic.title = mic.getAttribute('aria-label');
    cam.innerHTML = icon(this.camAn ? 'kamera' : 'kameraAus');
    cam.setAttribute('aria-pressed', String(this.camAn));
    cam.setAttribute('aria-label', this.camAn ? t('steuer.camAus') : t('steuer.camAn'));
    cam.title = cam.getAttribute('aria-label');
    $('#vorschau').classList.toggle('mit-video', !!(this.camAn && this.videoTrack));
    const fx = $('#lobby-effekte');
    fx.innerHTML = icon('hintergrund');
    fx.setAttribute('aria-label', t('effekte.knopf')); fx.title = t('effekte.knopf');
    fx.classList.toggle('an', !!effekte.ziel() && effekte.zustand === 'aktiv');
  }

  async micUmschalten() {
    this.micAn = !this.micAn;
    if (!this.audioTrack && this.micAn) await this.mikrofonHolen();
    else if (this.audioTrack) await (this.micAn ? this.audioTrack.unmute() : this.audioTrack.mute());
    geraeteMerken({ micAn: this.micAn });
    this.knoepfe();
  }

  async kameraUmschalten() {
    this.camAn = !this.camAn;
    if (this.camAn) await this.kameraHolen();
    else { this.videoTrack?.stop(); this.videoTrack = null; }
    if (this.camAn || !this.fehler.kamera) geraeteMerken({ camAn: this.camAn });
    this.knoepfe();
    this.geraeteFuellen();
  }

  async wechseln(art, id) {
    this.wahl[art] = id;
    geraeteMerken({ [art]: id });
    if (art === 'videoinput' && this.videoTrack) await this.videoTrack.setDeviceId(id).catch(() => {});
    if (art === 'audioinput' && this.audioTrack) { await this.audioTrack.setDeviceId(id).catch(() => {}); this.pegelStarten(); }
  }

  async geraeteFuellen() {
    const fuellen = async (sel, art, leerText) => {
      let liste = [];
      try { liste = await Room.getLocalDevices(art, false); } catch { /* keine */ }
      liste = liste.filter((d) => d.deviceId);
      sel.replaceChildren(...(liste.length ? liste : [{ deviceId: '', label: leerText }]).map((d, i) =>
        h('option', { value: d.deviceId, selected: d.deviceId === this.wahl[art] }, d.label || `${leerText} ${i + 1}`)));
      sel.disabled = !liste.length;
    };
    await Promise.all([
      fuellen($('#wahl-kamera'), 'videoinput', t('geraet.kamera')),
      fuellen($('#wahl-mikrofon'), 'audioinput', t('geraet.mikrofon')),
      supportsAudioOutputSelection() ? fuellen($('#wahl-lautsprecher'), 'audiooutput', t('geraet.lautsprecher')) : null,
    ]);
  }

  /** Kurzer Zweiklang über den gewählten Lautsprecher. */
  async tonTesten() {
    try {
      const ctx = new AudioContext();
      const ziel = ctx.createMediaStreamDestination();
      const gain = ctx.createGain();
      gain.connect(ziel);
      const jetzt = ctx.currentTime;
      [[660, 0], [880, 0.22], [990, 0.44]].forEach(([f, ab]) => {
        const o = ctx.createOscillator();
        o.type = 'sine'; o.frequency.value = f;
        const g = ctx.createGain();
        g.gain.setValueAtTime(0, jetzt + ab);
        g.gain.linearRampToValueAtTime(0.25, jetzt + ab + 0.03);
        g.gain.exponentialRampToValueAtTime(0.001, jetzt + ab + 0.5);
        o.connect(g).connect(gain);
        o.start(jetzt + ab); o.stop(jetzt + ab + 0.55);
      });
      const a = new Audio();
      a.srcObject = ziel.stream;
      if (this.wahl.audiooutput && a.setSinkId) await a.setSinkId(this.wahl.audiooutput).catch(() => {});
      await a.play();
      setTimeout(() => { a.pause(); a.srcObject = null; ctx.close(); }, 1300);
    } catch { /* Ton nicht möglich – egal */ }
  }

  /** Lobby verlassen: Vorschau lösen, Tracks für den Beitritt zurückgeben (oder stoppen). */
  beenden(stoppen = false) {
    this.aktiv = false;
    cancelAnimationFrame(this.schleife);
    this.analyse?.cleanup();
    this.analyse = null;
    navigator.mediaDevices?.removeEventListener?.('devicechange', this.geraeteNeu);
    this.videoTrack?.detach(this.videoEl);
    if (stoppen) { this.videoTrack?.stop(); this.audioTrack?.stop(); this.videoTrack = this.audioTrack = null; }
    return { videoTrack: this.videoTrack, audioTrack: this.audioTrack, micAn: this.micAn, camAn: this.camAn && !!this.videoTrack, wahl: { ...this.wahl } };
  }
}
