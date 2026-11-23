// Lokale Aufzeichnung (Host/Co-Host): Canvas-Komposition der sichtbaren Bühne (Kacheln an ihrer Bildschirmposition,
// Avatar + Name ohne Video) plus gemischter Ton aller Teilnehmenden (Web Audio). Ersatz, wenn das nicht geht:
// getDisplayMedia auf den aktuellen Tab (+ eigenes Mikro dazugemischt). Danach Upload in die Bibliothek mit Fortschritt
// (POST /api/aufnahmen) oder – ohne Konto / bei Fehler – Download.
import { Track } from 'livekit-client';
import { h, hochladen } from '../common.js';
import { t } from '../i18n.js';
import { initialen, groesseText, laufzeitText } from '../helfer.js';

const BREITE = 1280, HOEHE = 720, FPS = 25;
export const MAX_BYTES = 500 * 1024 * 1024;

export function aufnahmeTyp() {
  if (typeof MediaRecorder === 'undefined') return null;
  for (const typ of ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm', 'video/mp4;codecs=avc1.42E01E,mp4a.40.2', 'video/mp4']) {
    if (MediaRecorder.isTypeSupported(typ)) return typ;
  }
  return null;
}
export const kannAufnehmen = () => !!aufnahmeTyp();

export class Rekorder {
  constructor(m) {
    this.m = m;
    this.laeuft = false;
    this.teile = [];
    this.bytes = 0;
    this.quellen = new Map();     // MediaStreamTrack.id → AudioNode
  }

  async starten() {
    const typ = aufnahmeTyp();
    if (!typ) throw new Error(t('aufnahme.nichtUnterstuetzt'));
    this.typ = typ;
    this.ctx = new AudioContext();
    this.mix = this.ctx.createMediaStreamDestination();
    let videoStream;
    const canvas = h('canvas', { width: BREITE, height: HOEHE });
    if (canvas.captureStream) {
      this.canvas = canvas;
      this.c2d = canvas.getContext('2d', { alpha: false });
      videoStream = canvas.captureStream(FPS);
      this.zeichnen();
      this.timer = setInterval(() => this.zeichnen(), 1000 / FPS);
      this.audioAbgleichen();
    } else {
      // Ersatz: aktuellen Tab aufnehmen (Browser fragt nach)
      this.tab = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: FPS }, audio: true, preferCurrentTab: true, selfBrowserSurface: 'include' });
      videoStream = new MediaStream(this.tab.getVideoTracks());
      if (this.tab.getAudioTracks().length) this.quelleHinzu(this.tab.getAudioTracks()[0]);
      const mic = this.m.room.localParticipant.getTrackPublication(Track.Source.Microphone)?.track;
      if (mic) this.quelleHinzu(mic.mediaStreamTrack);
      this.tab.getVideoTracks()[0].addEventListener('ended', () => this.m.aufnahmeUmschalten());
    }
    const stream = new MediaStream([...videoStream.getVideoTracks(), ...this.mix.stream.getAudioTracks()]);
    this.rec = new MediaRecorder(stream, { mimeType: typ, videoBitsPerSecond: 2_500_000, audioBitsPerSecond: 128_000 });
    this.teile = []; this.bytes = 0;
    this.rec.ondataavailable = (e) => {
      if (!e.data?.size) return;
      this.teile.push(e.data); this.bytes += e.data.size;
      if (this.bytes > MAX_BYTES - 8 * 1024 * 1024 && this.laeuft) this.m.aufnahmeUmschalten(t('aufnahme.grenze'));
    };
    this.rec.start(1000);
    this.start = Date.now();
    this.laeuft = true;
  }

  /** Alle abonnierten Audiospuren (und das eigene Mikro) in den Mix hängen; verschwundene entfernen. */
  audioAbgleichen() {
    if (!this.laeuft && !this.mix) return;
    if (this.tab) return;
    const room = this.m.room, aktiv = new Set();
    const nimm = (track) => { if (track?.mediaStreamTrack && track.mediaStreamTrack.readyState === 'live') { aktiv.add(track.mediaStreamTrack.id); this.quelleHinzu(track.mediaStreamTrack); } };
    nimm(room.localParticipant.getTrackPublication(Track.Source.Microphone)?.track);
    for (const p of room.remoteParticipants.values()) {
      for (const pub of p.audioTrackPublications.values()) if (pub.isSubscribed) nimm(pub.track);
    }
    for (const [id, knoten] of this.quellen) if (!aktiv.has(id)) { knoten.disconnect(); this.quellen.delete(id); }
  }

  quelleHinzu(mst) {
    if (this.quellen.has(mst.id)) return;
    try {
      const q = this.ctx.createMediaStreamSource(new MediaStream([mst]));
      q.connect(this.mix);
      this.quellen.set(mst.id, q);
    } catch { /* Spur nicht nutzbar */ }
  }

  /** Bühne nachzeichnen: jede sichtbare Kachel an ihrer Position (skaliert auf 1280×720). */
  zeichnen() {
    const c = this.c2d, buehne = document.getElementById('buehne');
    c.fillStyle = '#0b0e2a';
    c.fillRect(0, 0, BREITE, HOEHE);
    const b = buehne.getBoundingClientRect();
    if (!b.width || !b.height) return;
    const s = Math.min(BREITE / b.width, HOEHE / b.height);
    const ox = (BREITE - b.width * s) / 2, oy = (HOEHE - b.height * s) / 2;
    for (const el of buehne.querySelectorAll('.kv')) {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const x = ox + (r.left - b.left) * s, y = oy + (r.top - b.top) * s, w = r.width * s, hh = r.height * s;
      c.save();
      c.beginPath(); c.roundRect ? c.roundRect(x, y, w, hh, 12 * s) : c.rect(x, y, w, hh); c.clip();
      c.fillStyle = '#171a33'; c.fillRect(x, y, w, hh);
      const v = el.querySelector('video');
      const mitVideo = el.classList.contains('mit-video') && v?.videoWidth;
      if (mitVideo) {
        const passt = el.classList.contains('bildschirm') || (el.parentElement?.id === 'gross' && !el.classList.contains('kamera'));
        const vr = v.videoWidth / v.videoHeight, kr = w / hh;
        let dw = w, dh = hh;
        if (passt ? vr > kr : vr < kr) dh = w / vr; else dw = hh * vr;
        if (el.classList.contains('spiegeln')) { c.translate(x + w, y); c.scale(-1, 1); c.drawImage(v, (w - dw) / 2, (hh - dh) / 2, dw, dh); c.setTransform(1, 0, 0, 1, 0, 0); }
        else c.drawImage(v, x + (w - dw) / 2, y + (hh - dh) / 2, dw, dh);
      } else {
        const name = el.querySelector('.kv-name span')?.textContent || '';
        const av = el.querySelector('.kv-avatar span');
        const rad = Math.min(w, hh) * 0.18;
        c.fillStyle = av?.style.background || '#6d4aff';
        c.beginPath(); c.arc(x + w / 2, y + hh / 2, rad, 0, Math.PI * 2); c.fill();
        c.fillStyle = '#fff'; c.textAlign = 'center'; c.textBaseline = 'middle';
        c.font = `700 ${Math.round(rad * 0.8)}px system-ui, sans-serif`;
        c.fillText(av?.textContent || initialen(name), x + w / 2, y + hh / 2);
      }
      // Namensschild
      const name = el.querySelector('.kv-name span')?.textContent || '';
      if (name) {
        const fs = Math.max(11, Math.round(13 * s));
        c.font = `600 ${fs}px system-ui, sans-serif`;
        const tw = Math.min(c.measureText(name).width, w - 24);
        c.fillStyle = 'rgba(8,9,20,.72)';
        c.beginPath(); c.roundRect ? c.roundRect(x + 8, y + hh - fs - 18, tw + 20, fs + 10, 99) : c.rect(x + 8, y + hh - fs - 18, tw + 20, fs + 10); c.fill();
        c.fillStyle = '#fff'; c.textAlign = 'left'; c.textBaseline = 'middle';
        c.fillText(name, x + 18, y + hh - fs / 2 - 13, w - 32);
      }
      if (el.classList.contains('spricht')) { c.strokeStyle = '#8ef0b0'; c.lineWidth = 3; c.strokeRect(x + 1.5, y + 1.5, w - 3, hh - 3); }
      c.restore();
    }
  }

  /** Stoppen → Blob. */
  stoppen() {
    return new Promise((fertig) => {
      if (!this.rec || this.rec.state === 'inactive') { fertig(null); return; }
      this.rec.onstop = () => {
        clearInterval(this.timer);
        this.tab?.getTracks().forEach((tr) => tr.stop());
        for (const q of this.quellen.values()) q.disconnect();
        this.quellen.clear();
        this.ctx?.close().catch(() => {});
        this.mix = null;
        const blob = new Blob(this.teile, { type: this.typ.split(';')[0] });
        this.teile = [];
        fertig(blob);
      };
      this.laeuft = false;
      this.dauer = Math.round((Date.now() - this.start) / 1000);
      this.rec.stop();
    });
  }

  laufzeit() { return this.laeuft ? laufzeitText((Date.now() - this.start) / 1000) : ''; }
  groesse() { return groesseText(this.bytes); }
}

/**
 * Upload-/Download-Box für eine fertige Aufnahme. ziel = Element, in das die Box kommt.
 * Mit Konto: Upload mit Fortschritt; ohne Konto, zu groß oder bei Fehler: Download-Knopf.
 */
export function aufnahmeAblegen(ziel, blob, { code, titel, dauer, angemeldet }) {
  const endung = blob.type.includes('mp4') ? 'mp4' : 'webm';
  const dateiname = `${titel.replace(/[^\p{L}\p{N}\- ]/gu, '_').slice(0, 80) || 'aufnahme'}.${endung}`;
  const text = h('p', { class: 'klein' });
  const balken = h('i');
  const fort = h('div', { class: 'fortschritt', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-label': t('aufnahme.upload') }, balken);
  const knoepfe = h('div', { class: 'knoepfe' });
  const box = h('div', { class: 'upload-box' }, h('b', {}, t('aufnahme.fertigTitel')), text, fort, knoepfe);
  ziel.append(box);
  const url = URL.createObjectURL(blob);
  const download = () => h('a', { class: 'btn klein', href: url, download: dateiname }, t('aufnahme.herunterladen'));
  const schliessen = (nachMs) => setTimeout(() => { box.remove(); if (!ziel.children.length && ziel.classList.contains('upload-schwebend')) ziel.remove(); }, nachMs);

  if (!angemeldet || blob.size > MAX_BYTES) {
    fort.hidden = true;
    text.textContent = !angemeldet ? t('aufnahme.ohneKonto') : t('aufnahme.zuGross', { groesse: groesseText(blob.size) });
    knoepfe.append(download(), h('button', { type: 'button', class: 'btn klein leise', onclick: () => schliessen(0) }, t('allg.schliessen')));
    return;
  }
  text.textContent = t('aufnahme.laedtHoch', { groesse: groesseText(blob.size) });
  const header = { 'Content-Type': blob.type, 'X-Raum': code, 'X-Titel': encodeURIComponent(titel) };
  if (dauer) header['X-Dauer'] = String(dauer);
  const { promise, abbrechen } = hochladen('/api/aufnahmen', blob, header, (anteil) => {
    const p = Math.round(anteil * 100);
    balken.style.width = `${p}%`;
    fort.setAttribute('aria-valuenow', String(p));
  });
  const abbr = h('button', { type: 'button', class: 'btn klein leise', onclick: abbrechen }, t('allg.abbrechen'));
  knoepfe.append(abbr);
  addEventListener('beforeunload', warnen);
  promise.then(() => {
    balken.style.width = '100%';
    text.textContent = t('aufnahme.hochgeladen');
    knoepfe.replaceChildren(h('a', { class: 'btn klein primary', href: '/konto#aufnahmen', target: '_blank', rel: 'noopener' }, t('aufnahme.zurBibliothek')),
      h('button', { type: 'button', class: 'btn klein leise', onclick: () => schliessen(0) }, t('allg.schliessen')));
  }).catch((e) => {
    text.textContent = t('aufnahme.uploadFehler', { text: e.message });
    fort.hidden = true;
    knoepfe.replaceChildren(download(), h('button', { type: 'button', class: 'btn klein leise', onclick: () => schliessen(0) }, t('allg.schliessen')));
  }).finally(() => removeEventListener('beforeunload', warnen));
}
function warnen(e) { e.preventDefault(); e.returnValue = ''; }
