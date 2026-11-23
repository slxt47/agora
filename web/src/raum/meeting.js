// Das laufende Meeting: LiveKit-Raum (simulcast, adaptiveStream, dynacast), Steuerleiste, Tastenkürzel, Ereignisse vom
// Server (Topic „agora“), Reaktionen (Topic „reaktion“), Hervorheben (Topic „spotlight“), Bildschirmfreigabe,
// Geräte im Gespräch, Wiederverbinden, Datensparmodus, Bild-im-Bild, Aufzeichnung, Verlassen / für alle beenden.
import {
  Room, RoomEvent, Track, VideoPresets, ScreenSharePresets, VideoQuality, DisconnectReason, ConnectionState,
  supportsAudioOutputSelection,
} from 'livekit-client';
import { $, h, api, toast, blatt, bestaetigen, kopieren, wachBleiben, ruhe, ApiFehler } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import { farbeAusText, jsonLesen, laufzeitText } from '../helfer.js';
import { Buehne } from './kacheln.js';
import { Chat, Leute, EMOJIS } from './panels.js';
import { menue, menueSchliessen } from './menue.js';
import { Rekorder, aufnahmeAblegen, kannAufnehmen } from './aufnahme.js';
import { geraeteMerken, geraeteFehler, VIDEO_AUFLOESUNG } from './lobby.js';
import { effekte, effektePanel } from './effekte.js';
import { mobilEinrichten } from './mobil.js';
import { Umfragen } from './umfragen.js';
import { Breakout } from './breakout.js';

const kodieren = (o) => new TextEncoder().encode(JSON.stringify(o));
const RECHTE_STANDARD = { chat: true, bildschirm: true, selbst_unmute: true, umbenennen: true, mehrfach_freigabe: false };

export class Meeting {
  constructor({ code, raum, ich, zugang, medien, onEnde }) {
    Object.assign(this, { code, raum, ich, zugang, medien, onEnde });
    this.rolle = zugang.rolle;
    this.rechte = { ...RECHTE_STANDARD };
    this.gesperrt = !!raum.gesperrt;
    this.aufnahmeInfo = null;        // {von_name} wenn jemand aufzeichnet
    this.rollen = {};
    this.wartend = 0;
    this.hand = false;
    this.sparmodus = 'aus';
    this.vomHostStumm = false;
    this.grund = null;               // warum das Meeting endet
    this.banner = new Map();
    this.reaktionZeit = new Map();
    this.rekorder = null;
    this.aufnahmeHinweisGezeigt = false;
  }

  // ---------------------------------------------------------------- Hilfen
  api(pfad, opts = {}) { return api(pfad, { ...opts, header: { 'X-Teilnahme': this.zugang.teilnahme, ...(opts.header || {}) } }); }
  istMod() { return this.rolle === 'host' || this.rolle === 'cohost'; }
  /** Rolle nur aus /zustand und Server-Ereignissen – das LiveKit-Attribut könnte der Client selbst setzen. */
  rolleVon(p) { return p.isLocal ? this.rolle : (this.rollen[p.identity] || 'teilnehmer'); }
  name(p) { return (p.name || '').trim() || t('allg.unbekannt'); }
  farbe(p) { const f = p.attributes?.farbe; return /^#[0-9a-f]{6}$/i.test(f || '') ? f : farbeAusText(p.identity); }
  darfUmbenennen(p) { return p.isLocal ? (this.istMod() || this.rechte.umbenennen !== false) : this.istMod(); }
  /** Darf ich an p moderieren? Host an allen anderen; Co-Host nur an Teilnehmenden/Gästen. */
  darfModerieren(p) {
    if (p.isLocal || !this.istMod()) return false;
    const r = this.rolleVon(p);
    return this.rolle === 'host' || r === 'teilnehmer' || r === 'gast';
  }

  // ---------------------------------------------------------------- Start
  async start() {
    const { wahl } = this.medien;
    document.body.classList.add('im-meeting');
    ruhe(true);
    $('#v-meeting').hidden = false;
    this.room = new Room({
      adaptiveStream: true,
      dynacast: true,
      videoCaptureDefaults: { resolution: VIDEO_AUFLOESUNG, deviceId: wahl.videoinput || undefined },
      audioCaptureDefaults: { deviceId: wahl.audioinput || undefined, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      publishDefaults: {
        simulcast: true, videoSimulcastLayers: [VideoPresets.h180, VideoPresets.h360], dtx: true, red: true,
        screenShareEncoding: ScreenSharePresets.h1080fps15.encoding,
      },
      audioOutput: wahl.audiooutput ? { deviceId: wahl.audiooutput } : undefined,
      stopLocalTrackOnUnpublish: true,
      disconnectOnPageLeave: true,
    });
    this.buehne = new Buehne(this);
    this.oberflaeche();
    this.ereignisse();
    mobilEinrichten(this);     // Drehen, Rückkehr aus dem Hintergrund (iOS)
    this.chat = new Chat(this);
    this.leute = new Leute(this);
    this.umfragen = new Umfragen(this);
    this.breakout = new Breakout(this);

    await this.room.connect(this.zugang.url, this.zugang.token);
    this.startZeit = Date.now();
    this.uhr = setInterval(() => this.zeitAnzeigen(), 1000);
    this.metadatenLesen(this.room.metadata);
    await this.zustandLaden();

    // Tracks aus der Lobby veröffentlichen (kein zweiter Berechtigungsdialog)
    const lp = this.room.localParticipant;
    try {
      if (this.medien.audioTrack) {
        await lp.publishTrack(this.medien.audioTrack, { source: Track.Source.Microphone });
        if (!this.medien.micAn || this.zugang.stumm_beitritt) await lp.setMicrophoneEnabled(false);
      }
      if (this.medien.videoTrack && this.medien.camAn) await lp.publishTrack(this.medien.videoTrack, { source: Track.Source.Camera });
      else this.medien.videoTrack?.stop();
    } catch (e) { toast(t('meeting.veroeffentlichenFehler', { text: e.message }), 'err'); }
    if (this.zugang.stumm_beitritt) {          // Vorlage „alle stumm beim Beitritt“: wie vom Host stummgeschaltet
      this.vomHostStumm = true;
      if (this.medien.micAn) toast(t('vorlage.stummBeitritt'), '', { dauer: 6000 });
    }

    this.chat.empfaengerAktualisieren();
    this.chat.rechteAktualisieren();
    this.chat.laden();
    this.umfragen.laden();
    this.breakout.laden();
    this.buehne.sync();
    this.steuerAktualisieren();
    if (!this.room.canPlaybackAudio) this.bannerSetzen('audio', 'warn', t('meeting.tonBlockiert'), { text: t('meeting.tonEinschalten'), fn: () => this.room.startAudio().then(() => this.bannerSetzen('audio')) });
    this.wake = await wachBleiben();
    document.addEventListener('visibilitychange', async () => { if (!document.hidden && this.room?.state === ConnectionState.Connected) this.wake = await wachBleiben(); });
    addEventListener('beforeunload', (e) => { if (this.rekorder?.laeuft) { e.preventDefault(); e.returnValue = ''; } });
    document.title = `${this.raum.titel || this.code} – Agora`;
    if (this.istMod()) this.leute.warteraumLaden();
    $('#s-mic').focus({ preventScroll: true });
  }

  async zustandLaden() {
    try {
      const z = await this.api(`/api/raeume/${this.code}/zustand`);
      if (z.rolle) this.rolle = z.rolle;
      if (z.rechte) this.rechte = { ...RECHTE_STANDARD, ...z.rechte };
      this.gesperrt = !!z.gesperrt;
      this.aufnahmeSetzen(z.aufnahme && z.aufnahme !== false ? z.aufnahme : null);
      this.rollen = z.rollen || {};
      this.wartendSetzen(z.wartend || 0);
    } catch { /* Metadaten reichen zur Not */ }
    this.kopfAktualisieren();
  }

  metadatenLesen(text) {
    const md = jsonLesen(text, null);
    if (!md) return;
    if (md.rechte) this.rechteSetzen(md.rechte, false);
    if ('gesperrt' in md) { this.gesperrt = !!md.gesperrt; this.kopfAktualisieren(); }
    if ('aufnahme' in md) this.aufnahmeSetzen(md.aufnahme || null);
  }

  // ---------------------------------------------------------------- LiveKit-Ereignisse
  ereignisse() {
    const r = this.room;
    const aktiv = () => r === this.room;     // nach einem Raumwechsel (Breakout) zählt nur der neue Raum
    const neuZeichnen = (p) => { this.buehne.aktualisieren(p); this.leute.planen(); };
    r.on(RoomEvent.ParticipantConnected, (p) => {
      this.buehne.sync(); this.chat.empfaengerAktualisieren(); this.leute.planen(); this.rekorder?.audioAbgleichen();
      if (this.istMod() && this.buehne.spotlightIdentity) r.localParticipant.publishData(kodieren({ identity: this.buehne.spotlightIdentity }), { reliable: true, topic: 'spotlight', destinationIdentities: [p.identity] }).catch(() => {});
    })
      .on(RoomEvent.ParticipantDisconnected, (p) => {
        this.buehne.sync(); this.chat.empfaengerAktualisieren(); this.leute.planen(); this.rekorder?.audioAbgleichen();
        if (this.buehne.spotlightIdentity === p.identity) this.buehne.spotlight(null);
      })
      .on(RoomEvent.TrackPublished, (pub, p) => { this.sparenAnwenden(pub); this.buehne.sync(); neuZeichnen(p); })
      .on(RoomEvent.TrackUnpublished, (_pub, p) => { this.buehne.sync(); neuZeichnen(p); this.steuerAktualisieren(); })
      .on(RoomEvent.TrackSubscribed, (track, pub, p) => {
        if (track.kind === Track.Kind.Audio) { const el = track.attach(); el.dataset.sid = pub.trackSid; $('#audio-ablage').append(el); this.rekorder?.audioAbgleichen(); }
        this.sparenAnwenden(pub);
        neuZeichnen(p);
      })
      .on(RoomEvent.TrackUnsubscribed, (track, _pub, p) => { if (track.kind === Track.Kind.Audio) { track.detach().forEach((el) => el.remove()); this.rekorder?.audioAbgleichen(); } neuZeichnen(p); })
      .on(RoomEvent.TrackMuted, (_pub, p) => { neuZeichnen(p); if (p.isLocal) this.steuerAktualisieren(); })
      .on(RoomEvent.TrackUnmuted, (_pub, p) => { neuZeichnen(p); if (p.isLocal) this.steuerAktualisieren(); })
      .on(RoomEvent.LocalTrackPublished, (pub) => { if (pub.source === Track.Source.Camera) effekte.anwenden(pub.track); this.buehne.sync(); neuZeichnen(r.localParticipant); this.steuerAktualisieren(); if (pub.source === Track.Source.Microphone) this.rekorder?.audioAbgleichen(); })
      .on(RoomEvent.LocalTrackUnpublished, () => { this.buehne.sync(); neuZeichnen(r.localParticipant); this.steuerAktualisieren(); })
      .on(RoomEvent.ActiveSpeakersChanged, (liste) => this.buehne.sprecher(liste))
      .on(RoomEvent.ConnectionQualityChanged, (_q, p) => this.buehne.aktualisieren(p))
      .on(RoomEvent.ParticipantNameChanged, (_n, p) => { neuZeichnen(p); this.chat.empfaengerAktualisieren(); })
      .on(RoomEvent.ParticipantAttributesChanged, (geaendert, p) => {
        neuZeichnen(p);
        if (!p.isLocal && 'hand' in geaendert && geaendert.hand === '1' && this.istMod()) toast(t('meeting.handGehoben', { name: this.name(p) }), '', { dauer: 3500 });
        if (p.isLocal && 'hand' in geaendert) { this.hand = geaendert.hand === '1'; this.steuerAktualisieren(); }
      })
      .on(RoomEvent.RoomMetadataChanged, (md) => this.metadatenLesen(md))
      .on(RoomEvent.DataReceived, (daten, p, _kind, topic) => aktiv() && this.datenEmpfangen(daten, p, topic))
      .on(RoomEvent.AudioPlaybackStatusChanged, () => {
        if (r.canPlaybackAudio) this.bannerSetzen('audio');
        else this.bannerSetzen('audio', 'warn', t('meeting.tonBlockiert'), { text: t('meeting.tonEinschalten'), fn: () => r.startAudio().then(() => this.bannerSetzen('audio')) });
      })
      .on(RoomEvent.MediaDevicesChanged, () => this.geraeteGeaendert())
      .on(RoomEvent.MediaDevicesError, (e, art) => toast(geraeteFehler(e, art === 'videoinput' ? 'kamera' : 'mikrofon'), 'err', { dauer: 7000 }))
      .on(RoomEvent.Reconnecting, () => this.verbindung(true))
      .on(RoomEvent.SignalReconnecting, () => this.verbindung(true))
      .on(RoomEvent.Reconnected, () => { this.verbindung(false); toast(t('meeting.wiederVerbunden'), 'ok', { dauer: 2500 }); this.zustandLaden(); })
      .on(RoomEvent.Disconnected, (grund) => aktiv() && this.getrennt(grund));
  }

  datenEmpfangen(daten, p, topic) {
    if (topic === 'agora' || topic === 'chat') {
      if (p) return;                          // nur der Server darf hier senden
      const d = jsonLesen(daten, null);
      if (!d) return;
      if (topic === 'chat') { this.chat.empfangen(d.nachricht || d); return; }
      this.serverEreignis(d);
      return;
    }
    if (!p) return;
    if (topic === 'reaktion') {
      const d = jsonLesen(daten, null);
      if (!d || !EMOJIS.includes(d.emoji)) return;
      const jetzt = Date.now();
      if (jetzt - (this.reaktionZeit.get(p.identity) || 0) < 400) return;   // Flut bremsen
      this.reaktionZeit.set(p.identity, jetzt);
      this.reaktionZeigen(p, d.emoji);
    } else if (topic === 'spotlight') {
      const r = this.rolleVon(p);
      if (r !== 'host' && r !== 'cohost') return;
      const d = jsonLesen(daten, null);
      const id = d?.identity || null;
      if (id && !this.room.getParticipantByIdentity(id)) return;
      this.buehne.spotlight(id);
      if (id) toast(t('meeting.spotlight', { name: id === this.room.localParticipant.identity ? t('allg.du') : this.name(this.room.getParticipantByIdentity(id)) }), '', { dauer: 3000 });
    }
  }

  serverEreignis(d) {
    const lp = this.room.localParticipant;
    switch (d.typ) {
      case 'warteraum': {
        const vorher = this.wartend;
        this.wartendSetzen(d.wartend || 0);
        if (this.istMod()) {
          this.leute.warteraumLaden();
          if ((d.wartend || 0) > vorher) toast(t('meeting.jemandWartet'), '', { aktion: { text: t('meeting.ansehen'), fn: () => this.leisteOeffnen('leute') }, dauer: 8000 });
        }
        break;
      }
      case 'rechte': this.rechteSetzen(d.rechte || {}, true); break;
      case 'rolle': {
        if (!d.identity) break;
        this.rollen[d.identity] = d.rolle;
        if (d.identity === lp.identity) {
          const war = this.rolle;
          this.rolle = d.rolle;
          if (war !== d.rolle) toast(t(`meeting.rolleNeu.${d.rolle}`), 'ok');
          if (this.istMod()) this.leute.warteraumLaden();
          if (war !== d.rolle) { this.umfragen.rolleGeaendert(); this.breakout.laden(); }
          this.chat.rechteAktualisieren();
          this.steuerAktualisieren();
        }
        this.buehne.aktualisieren();
        this.leute.planen();
        break;
      }
      case 'gesperrt':
        this.gesperrt = !!d.an;
        this.kopfAktualisieren();
        this.leute.planen();
        toast(d.an ? t('meeting.gesperrt') : t('meeting.entsperrt'), '', { dauer: 3000 });
        break;
      case 'beendet': this.grund = 'beendet'; this.room.disconnect(); break;
      case 'entfernt': this.grund = 'entfernt'; break;
      case 'aufnahme': this.aufnahmeSetzen(d.aktiv ? { von_name: d.von_name } : null); break;
      case 'umfrage': this.umfragen.ereignis(d); break;
      case 'breakout': case 'breakout_hilfe': case 'breakout_nachricht': this.breakout.ereignis(d); break;
      case 'stumm':
        this.vomHostStumm = true;
        lp.setMicrophoneEnabled(false).catch(() => {});
        toast(t('meeting.stummVon', { name: d.von_name || t('rolle.host') }), 'warn');
        break;
      case 'kamera_aus':
        lp.setCameraEnabled(false).catch(() => {});
        toast(t('meeting.kameraAusVon', { name: d.von_name || t('rolle.host') }), 'warn');
        break;
      case 'kamera_bitten':
        if (lp.isCameraEnabled) break;
        bestaetigen(t('meeting.kameraBittenTitel'), t('meeting.kameraBittenText', { name: d.von_name || t('rolle.host') }), { ja: t('steuer.camAn'), nein: t('meeting.spaeter') })
          .then((ja) => ja && this.kameraUmschalten());
        break;
      default: break;
    }
  }

  rechteSetzen(rechte, melden) {
    const vorher = this.rechte;
    this.rechte = { ...RECHTE_STANDARD, ...vorher, ...rechte };
    if (this.rechte.selbst_unmute !== false) this.vomHostStumm = false;
    if (melden && !this.istMod()) {
      if (vorher.chat !== this.rechte.chat) toast(this.rechte.chat ? t('meeting.chatAn') : t('meeting.chatAus'), '', { dauer: 3000 });
      if (vorher.bildschirm !== this.rechte.bildschirm && !this.rechte.bildschirm) toast(t('meeting.teilenAus'), '', { dauer: 3000 });
    }
    if (!this.istMod() && this.rechte.bildschirm === false && this.room?.localParticipant.isScreenShareEnabled) this.room.localParticipant.setScreenShareEnabled(false).catch(() => {});
    this.chat?.rechteAktualisieren();
    this.steuerAktualisieren();
    this.leute?.planen();
  }

  wartendSetzen(n) { this.wartend = this.istMod() ? n : 0; this.zaehlerSetzen(); }

  aufnahmeSetzen(info) {
    const vorher = this.aufnahmeInfo;
    this.aufnahmeInfo = info && typeof info === 'object' ? info : (info ? { von_name: '' } : null);
    this.kopfAktualisieren();
    if (this.aufnahmeInfo && !vorher && !this.rekorder?.laeuft && !this.aufnahmeHinweisGezeigt) {
      this.aufnahmeHinweisGezeigt = true;
      bestaetigen(t('aufnahme.hinweisTitel'), t('aufnahme.hinweisText', { name: this.aufnahmeInfo.von_name || t('rolle.host') }),
        { ja: t('aufnahme.verstanden'), nein: t('meeting.verlassen') }).then((ok) => { if (!ok) this.verlassen(); });
    }
    if (!this.aufnahmeInfo) this.aufnahmeHinweisGezeigt = false;
  }

  // ---------------------------------------------------------------- Oberfläche
  oberflaeche() {
    $('#m-titel').textContent = this.raum.titel || t('meeting.ohneTitel');
    $('#m-code').textContent = this.code;
    const k = (id, sym, label, fn) => { const b = $(id); b.innerHTML = icon(sym); b.setAttribute('aria-label', label); b.title = label; b.addEventListener('click', fn); };
    k('#k-vollbild', 'vollbild', t('meeting.vollbild'), () => this.vollbild());
    k('#k-info', 'info', t('meeting.info'), (e) => this.infoMenue(e.currentTarget));
    k('#k-hilfe', 'tastatur', t('meeting.tastenkuerzel'), () => this.hilfe());
    k('#k-ansicht', 'galerie', '', () => {
      this.buehne.ansichtSetzen(this.buehne.effektiveAnsicht() === 'galerie' ? 'sprecher' : 'galerie');
    });
    if (!document.documentElement.requestFullscreen && !document.documentElement.webkitRequestFullscreen) $('#k-vollbild').hidden = true;
    document.addEventListener('fullscreenchange', () => {
      const an = !!document.fullscreenElement;
      $('#k-vollbild').innerHTML = icon(an ? 'vollbildAus' : 'vollbild');
      $('#k-vollbild').setAttribute('aria-label', an ? t('meeting.vollbildAus') : t('meeting.vollbild'));
    });
    $('#leiste-zu').innerHTML = icon('schliessen');
    $('#leiste-zu').setAttribute('aria-label', t('meeting.leisteZu'));
    $('#leiste-zu').addEventListener('click', () => this.leisteSchliessen());
    $('#tab-chat').addEventListener('click', () => this.leisteOeffnen('chat'));
    $('#tab-leute').addEventListener('click', () => this.leisteOeffnen('leute'));
    $('#tab-umfragen').addEventListener('click', () => this.leisteOeffnen('umfragen'));

    $('#s-mic').addEventListener('click', () => this.micUmschalten());
    $('#s-cam').addEventListener('click', () => this.kameraUmschalten());
    $('#s-teilen').addEventListener('click', () => this.teilenUmschalten());
    $('#s-hand').addEventListener('click', () => this.handUmschalten());
    $('#s-reaktion').addEventListener('click', (e) => this.reaktionWahl(e.currentTarget));
    $('#s-chat').addEventListener('click', () => this.leisteUmschalten('chat'));
    $('#s-leute').addEventListener('click', () => this.leisteUmschalten('leute'));
    $('#s-mehr').addEventListener('click', (e) => this.mehrMenue(e.currentTarget));
    $('#s-verlassen').addEventListener('click', (e) => this.verlassenMenue(e.currentTarget));
    if (!navigator.mediaDevices?.getDisplayMedia) $('#s-teilen').hidden = true;
    document.addEventListener('keydown', (e) => this.taste(e));
    this.steuerAktualisieren();
  }

  steuerKnopf(id, sym, text, { gedrueckt, klasse = '', label = text } = {}) {
    const b = $(id);
    b.innerHTML = `${icon(sym)}<span class="lbl"></span>`;
    b.querySelector('.lbl').textContent = text;
    b.setAttribute('aria-label', label);
    b.title = label;
    if (gedrueckt !== undefined) b.setAttribute('aria-pressed', String(gedrueckt));
    b.className = `steuer ${klasse}`.trim();
    return b;
  }

  steuerAktualisieren() {
    const lp = this.room?.localParticipant;
    const mic = !!lp?.isMicrophoneEnabled, cam = !!lp?.isCameraEnabled, teilt = !!lp?.isScreenShareEnabled;
    this.steuerKnopf('#s-mic', mic ? 'mic' : 'micAus', mic ? t('steuer.stumm') : t('steuer.entstummen'),
      { gedrueckt: mic, klasse: mic ? '' : 'aus', label: `${mic ? t('steuer.micAus') : t('steuer.micAn')} (M)` });
    if (mic) $('#s-mic').append(h('span', { class: 'pegel-punkt', 'aria-hidden': 'true' }));
    this.steuerKnopf('#s-cam', cam ? 'kamera' : 'kameraAus', cam ? t('steuer.videoStopp') : t('steuer.videoStart'),
      { gedrueckt: cam, klasse: cam ? '' : 'aus', label: `${cam ? t('steuer.camAus') : t('steuer.camAn')} (V)` });
    const teilenErlaubt = this.istMod() || this.rechte.bildschirm !== false;
    const s = this.steuerKnopf('#s-teilen', teilt ? 'teilenAus' : 'teilen', teilt ? t('steuer.teilenStopp') : t('steuer.teilen'),
      { gedrueckt: teilt, klasse: teilt ? 'aktiv-gruen' : '', label: `${teilt ? t('steuer.teilenStopp') : t('steuer.teilen')} (S)` });
    s.disabled = !teilt && !teilenErlaubt;
    this.steuerKnopf('#s-hand', 'hand', this.hand ? t('steuer.handSenken') : t('steuer.handHeben'),
      { gedrueckt: this.hand, klasse: this.hand ? 'an' : '', label: `${this.hand ? t('steuer.handSenken') : t('steuer.handHeben')} (H)` });
    this.steuerKnopf('#s-reaktion', 'smiley', t('steuer.reaktion'));
    this.steuerKnopf('#s-chat', 'chat', t('steuer.chat'), { label: `${t('steuer.chat')} (C)`, gedrueckt: this.leisteOffen === 'chat' });
    this.steuerKnopf('#s-leute', 'leute', t('steuer.leute'), { label: `${t('steuer.leute')} (P)`, gedrueckt: this.leisteOffen === 'leute' });
    this.steuerKnopf('#s-mehr', 'mehr', t('steuer.mehr'));
    this.steuerKnopf('#s-verlassen', 'auflegen', t('steuer.verlassen'), { klasse: 'auflegen' });
    this.zaehlerSetzen();
    this.bannerSetzen('teilen', teilt ? 'teilen' : null, t('meeting.duTeilst'), { text: t('steuer.teilenStopp'), fn: () => this.teilenUmschalten() });
    this.pegelSchleife(mic);
  }

  /** Grüner Ring am Mikro-Knopf zeigt, dass man gehört wird. */
  pegelSchleife(an) {
    clearInterval(this.pegelTimer);
    if (!an || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    this.pegelTimer = setInterval(() => {
      const lp = this.room?.localParticipant, punkt = $('#s-mic .pegel-punkt');
      if (!lp || !punkt) return;
      punkt.style.setProperty('--pegel', Math.min(1, (lp.audioLevel || 0) * 4).toFixed(2));
    }, 120);
  }

  zaehlerSetzen() {
    const setz = (id, n, label) => {
      const b = $(id);
      b.querySelector('.zaehler')?.remove();
      if (n > 0) b.append(h('span', { class: 'zaehler', 'aria-hidden': 'true' }, n > 99 ? '99+' : String(n)));
      const basis = b.getAttribute('aria-label').replace(/ – .*$/, '');
      b.setAttribute('aria-label', n > 0 ? `${basis} – ${label}` : basis);
    };
    setz('#s-chat', this.chat?.ungelesen || 0, t('meeting.ungelesen', { n: this.chat?.ungelesen || 0 }));
    setz('#s-leute', this.wartend, t('meeting.wartendN', { n: this.wartend }));
    const tabLeute = $('#tab-leute');
    tabLeute.textContent = this.wartend ? `${t('steuer.leute')} (${this.wartend})` : t('steuer.leute');
  }

  kopfAktualisieren() {
    const schloss = $('#m-schloss');
    schloss.hidden = !this.gesperrt;
    schloss.innerHTML = `${icon('schloss')}<span class="lang"></span>`;
    schloss.querySelector('.lang').textContent = t('meeting.gesperrtKurz');
    schloss.setAttribute('aria-label', t('meeting.gesperrtKurz'));
    const rec = $('#m-rec');
    rec.hidden = !this.aufnahmeInfo;
    if (this.aufnahmeInfo) {
      const eigene = this.rekorder?.laeuft;
      rec.textContent = eigene ? `${t('aufnahme.kurz')} ${this.rekorder.laufzeit()}` : t('aufnahme.kurz');
      rec.title = this.aufnahmeInfo.von_name ? t('aufnahme.von', { name: this.aufnahmeInfo.von_name }) : t('aufnahme.laeuft');
      rec.setAttribute('aria-label', rec.title);
    }
  }

  ansichtKnopf() {
    const b = $('#k-ansicht');
    const galerie = this.buehne.effektiveAnsicht() === 'galerie';
    b.innerHTML = icon(galerie ? 'sprecher' : 'galerie');
    const label = galerie ? t('meeting.sprecheransicht') : t('meeting.galerieansicht');
    b.setAttribute('aria-label', label); b.title = label;
  }

  zeitAnzeigen() {
    $('#m-zeit').textContent = laufzeitText((Date.now() - this.startZeit) / 1000);
    if (this.rekorder?.laeuft) this.kopfAktualisieren();
  }

  bannerSetzen(id, art, text, aktion) {
    if (!art) this.banner.delete(id); else this.banner.set(id, { art, text, aktion });
    const el = $('#m-banner');
    el.hidden = !this.banner.size;
    el.replaceChildren();
    const [erst] = [...this.banner.values()].sort((a, b) => (b.art === 'err') - (a.art === 'err'));
    if (!erst) return;
    el.className = `m-banner ${erst.art}`;
    el.append(h('span', {}, erst.text));
    if (erst.aktion) el.append(h('button', { type: 'button', class: 'btn', onclick: erst.aktion.fn }, erst.aktion.text));
  }

  verbindung(weg) {
    const pille = $('#m-verbindung');
    pille.hidden = !weg;
    pille.textContent = t('meeting.verbindet');
    this.bannerSetzen('verbindung', weg ? 'warn' : null, t('meeting.verbindungWeg'));
  }

  // ---------------------------------------------------------------- Mikro, Kamera, Geräte
  async micUmschalten() {
    const lp = this.room.localParticipant;
    const an = !lp.isMicrophoneEnabled;
    if (an && !this.istMod() && this.rechte.selbst_unmute === false && this.vomHostStumm) { toast(t('meeting.entstummenGesperrt'), 'warn'); return; }
    try { await lp.setMicrophoneEnabled(an); geraeteMerken({ micAn: an }); } catch (e) { toast(geraeteFehler(e, 'mikrofon'), 'err', { dauer: 7000 }); }
    this.steuerAktualisieren();
  }

  async kameraUmschalten() {
    const lp = this.room.localParticipant;
    const an = !lp.isCameraEnabled;
    if (an && this.sparmodus === 'audio') toast(t('meeting.sparmodusKamera'), '', { dauer: 3000 });
    try { await lp.setCameraEnabled(an); geraeteMerken({ camAn: an }); } catch (e) { toast(geraeteFehler(e, 'kamera'), 'err', { dauer: 7000 }); }
    this.steuerAktualisieren();
  }

  async geraeteGeaendert() {
    // LiveKit wechselt selbst auf das Standardgerät, wenn das aktive verschwindet; wir melden neue Geräte
    let alle = [];
    try { alle = await Room.getLocalDevices(undefined, false); } catch { return; }
    const ids = new Set(alle.map((d) => d.kind + d.deviceId));
    if (this.bekannteGeraete) {
      const neu = alle.filter((d) => d.deviceId && d.deviceId !== 'default' && !this.bekannteGeraete.has(d.kind + d.deviceId) && d.label);
      if (neu.length) {
        const d = neu[0];
        toast(t('meeting.neuesGeraet', { name: d.label }), '', { aktion: { text: t('meeting.verwenden'), fn: () => this.geraetWechseln(d.kind, d.deviceId) }, dauer: 8000 });
      }
    }
    this.bekannteGeraete = ids;
  }

  async geraetWechseln(art, id) {
    try {
      await this.room.switchActiveDevice(art, id);
      geraeteMerken({ [art]: id });
    } catch (e) { toast(e.message, 'err'); }
  }

  async einstellungen() {
    const { inhalt } = blatt(t('einst.titel'));
    const auswahl = async (art, label) => {
      let liste = [];
      try { liste = (await Room.getLocalDevices(art, false)).filter((d) => d.deviceId); } catch { /* keine */ }
      const aktiv = this.room.getActiveDevice(art);
      const sel = h('select', { onchange: (e) => this.geraetWechseln(art, e.target.value) },
        liste.length ? liste.map((d, i) => h('option', { value: d.deviceId, selected: d.deviceId === aktiv }, d.label || `${label} ${i + 1}`))
          : h('option', { value: '' }, t('einst.keinGeraet')));
      sel.disabled = !liste.length;
      return h('label', { class: 'feld' }, h('span', {}, label), sel);
    };
    inhalt.append(
      await auswahl('videoinput', t('geraet.kamera')),
      await auswahl('audioinput', t('geraet.mikrofon')),
      supportsAudioOutputSelection() ? await auswahl('audiooutput', t('geraet.lautsprecher')) : null,
      h('label', { class: 'feld' }, h('span', {}, t('einst.sparmodus')),
        h('select', { onchange: (e) => this.sparmodusSetzen(e.target.value) },
          [['aus', t('einst.sparAus')], ['niedrig', t('einst.sparNiedrig')], ['audio', t('einst.sparAudio')]]
            .map(([w, text]) => h('option', { value: w, selected: this.sparmodus === w }, text))),
        h('small', {}, t('einst.sparHinweis'))),
      h('label', { class: 'schalter' }, h('span', { class: 'txt' }, h('span', {}, t('einst.selbstAusblenden'))),
        h('input', { type: 'checkbox', role: 'switch', checked: this.buehne.selbstAus, onchange: () => this.selbstUmschalten() })),
    );
  }

  // ---------------------------------------------------------------- Bildschirm teilen
  async teilenUmschalten() {
    const lp = this.room.localParticipant;
    if (lp.isScreenShareEnabled) { await lp.setScreenShareEnabled(false).catch(() => {}); this.steuerAktualisieren(); return; }
    if (!this.istMod() && this.rechte.bildschirm === false) { toast(t('meeting.teilenAus'), 'warn'); return; }
    if (!this.rechte.mehrfach_freigabe) {
      const anderer = [...this.room.remoteParticipants.values()].find((p) => p.isScreenShareEnabled);
      if (anderer) { toast(t('meeting.teiltSchon', { name: this.name(anderer) }), 'warn'); return; }
    }
    try {
      await lp.setScreenShareEnabled(true, {
        audio: true, systemAudio: 'include', selfBrowserSurface: 'exclude', surfaceSwitching: 'include', contentHint: 'detail',
        resolution: { width: 1920, height: 1080, frameRate: 15 },
      });
    } catch (e) {
      if (e?.name !== 'NotAllowedError' && e?.name !== 'AbortError') toast(t('meeting.teilenFehler', { text: e?.message || '' }), 'err');
    }
    this.steuerAktualisieren();
  }

  // ---------------------------------------------------------------- Hand, Reaktionen, Hervorheben
  async handUmschalten() {
    this.hand = !this.hand;
    this.steuerAktualisieren();
    try { await this.room.localParticipant.setAttributes({ hand: this.hand ? '1' : '' }); } catch (e) {
      this.hand = !this.hand; this.steuerAktualisieren(); toast(t('meeting.handFehler'), 'err');
    }
    this.buehne.aktualisieren(this.room.localParticipant);
    this.leute.planen();
  }

  reaktionWahl(anker) {
    const raster = h('div', { class: 'emoji-raster' }, EMOJIS.slice(0, 8).map((e) => h('button', { type: 'button', 'aria-label': e, onclick: () => { menueSchliessen(); this.reagieren(e); } }, e)));
    menue(anker, null, { inhalt: raster, label: t('steuer.reaktion') });
  }

  reagieren(emoji) {
    this.room.localParticipant.publishData(kodieren({ emoji }), { reliable: false, topic: 'reaktion' }).catch(() => {});
    this.reaktionZeigen(this.room.localParticipant, emoji);
  }

  reaktionZeigen(p, emoji) {
    this.buehne.reaktion(p.identity, emoji);
    const ebene = $('#reaktionen');
    const el = h('div', { class: 'flug' }, h('b', {}, emoji), h('span', {}, p.isLocal ? t('allg.du') : this.name(p)));
    el.style.left = `${8 + Math.random() * 22}%`;
    ebene.append(el);
    while (ebene.children.length > 20) ebene.firstElementChild.remove();
    setTimeout(() => el.remove(), 3700);
  }

  spotlightSetzen(key) {
    const identity = key ? key.split(':')[0] : null;
    this.buehne.spotlight(identity);
    this.room.localParticipant.publishData(kodieren({ identity }), { reliable: true, topic: 'spotlight' }).catch(() => toast(t('meeting.spotlightFehler'), 'err'));
  }

  // ---------------------------------------------------------------- Moderation
  async steuern(aktion, extra = {}, okText) {
    try {
      await this.api(`/api/raeume/${this.code}/steuerung`, { methode: 'POST', json: { aktion, ...extra } });
      if (okText) toast(okText, 'ok', { dauer: 2500 });
      return true;
    } catch (e) { toast(e.message, 'err'); return false; }
  }

  aktionenFuer(p) {
    const e = [];
    if (p.isLocal) {
      if (this.darfUmbenennen(p)) e.push({ text: t('leute.umbenennen'), symbol: 'stift', aktion: () => this.umbenennenDialog(p) });
      e.push({ text: this.hand ? t('steuer.handSenken') : t('steuer.handHeben'), symbol: 'hand', aktion: () => this.handUmschalten() });
      return e;
    }
    if (!this.darfModerieren(p)) return e;
    const name = this.name(p);
    if (p.isMicrophoneEnabled) e.push({ text: t('mod.stumm'), symbol: 'micAus', aktion: () => this.steuern('stumm', { ziel: p.identity }, t('mod.stummOk', { name })) });
    if (p.isCameraEnabled) e.push({ text: t('mod.kameraAus'), symbol: 'kameraAus', aktion: () => this.steuern('kamera_aus', { ziel: p.identity }) });
    else e.push({ text: t('mod.kameraBitten'), symbol: 'kamera', aktion: () => this.steuern('kamera_bitten', { ziel: p.identity }, t('mod.gebeten', { name })) });
    e.push({ text: t('leute.umbenennen'), symbol: 'stift', aktion: () => this.umbenennenDialog(p) });
    if (this.rolle === 'host') {
      const co = this.rolleVon(p) === 'cohost';
      e.push({ text: co ? t('mod.cohostWeg') : t('mod.cohost'), symbol: 'schild', aktion: () => this.steuern('rolle', { ziel: p.identity, rolle: co ? 'teilnehmer' : 'cohost' }) });
      e.push({ text: t('mod.hostUebertragen'), symbol: 'krone', aktion: async () => {
        if (await bestaetigen(t('mod.hostUebertragen'), t('mod.hostUebertragenText', { name }), { ja: t('mod.uebertragen') })) this.steuern('host_uebertragen', { ziel: p.identity });
      } });
    }
    e.push('-', { text: t('mod.entfernen'), symbol: 'tuer', gefahr: true, aktion: async () => {
      if (await bestaetigen(t('mod.entfernenTitel', { name }), t('mod.entfernenText'), { ja: t('mod.entfernen'), gefahr: true })) this.steuern('entfernen', { ziel: p.identity }, t('mod.entferntOk', { name }));
    } });
    return e;
  }

  umbenennenDialog(p) {
    const { inhalt, schliessen } = blatt(t('leute.umbenennen'));
    const feld = h('input', { name: 'name', maxlength: '60', required: true, value: this.name(p), autocomplete: p.isLocal ? 'name' : 'off' });
    const f = h('form', { class: 'formular' }, h('label', { class: 'feld' }, h('span', {}, t('leute.neuerName')), feld),
      h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')), h('button', { type: 'submit', class: 'btn primary' }, t('allg.speichern'))));
    inhalt.append(f);
    feld.select();
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const name = feld.value.trim();
      if (!name) return;
      if (await this.steuern('umbenennen', { ziel: p.identity, name })) schliessen();
    });
  }

  // ---------------------------------------------------------------- Seitenleiste
  leisteOeffnen(welche, fokus = true) {
    this.leisteOffen = welche;
    $('#seitenleiste').hidden = false;
    for (const p of ['chat', 'leute', 'umfragen']) {
      $(`#panel-${p}`).hidden = welche !== p;
      $(`#tab-${p}`).setAttribute('aria-selected', String(welche === p));
    }
    this.chat.zeigen(welche === 'chat');
    this.leute.zeigen(welche === 'leute');
    this.umfragen.zeigen(welche === 'umfragen');
    this.steuerAktualisieren();
    if (!fokus) { /* automatisch geöffnet (z. B. neue Umfrage) – Fokus bleibt, wo er ist */ } else if (welche === 'chat') this.chat.feld.focus({ preventScroll: true });
    else $(`#tab-${welche}`).focus({ preventScroll: true });
    this.buehne.planen();
  }

  leisteSchliessen() {
    this.leisteOffen = null;
    $('#seitenleiste').hidden = true;
    this.chat.zeigen(false);
    this.leute.zeigen(false);
    this.umfragen.zeigen(false);
    this.steuerAktualisieren();
    this.buehne.planen();
  }

  leisteUmschalten(welche) { if (this.leisteOffen === welche) this.leisteSchliessen(); else this.leisteOeffnen(welche); }

  // ---------------------------------------------------------------- Mehr, Info, Hilfe, Ansicht
  mehrMenue(anker) {
    const aufnahmeMoeglich = this.istMod() && kannAufnehmen();
    menue(anker, [
      { text: this.hand ? t('steuer.handSenken') : t('steuer.handHeben'), symbol: 'hand', aktion: () => this.handUmschalten() },
      { text: t('steuer.reaktion'), symbol: 'smiley', aktion: () => this.reaktionWahl(anker) },
      { text: t('umfrage.tab'), symbol: 'umfrage', aktion: () => this.leisteOeffnen('umfragen') },
      ...this.breakout.menueEintraege(),
      '-',
      this.istMod() ? { text: this.rekorder?.laeuft ? t('aufnahme.stoppen') : t('aufnahme.starten'), symbol: this.rekorder?.laeuft ? 'stopp' : 'aufnahme',
        deaktiviert: !aufnahmeMoeglich || (!!this.aufnahmeInfo && !this.rekorder?.laeuft), aktion: () => this.aufnahmeUmschalten() } : null,
      { text: t('effekte.titel'), symbol: 'hintergrund', aktion: () => effektePanel(anker) },
      { text: t('meeting.bildImBild'), symbol: 'bildImBild', deaktiviert: !document.pictureInPictureEnabled, aktion: () => this.bildImBild() },
      { text: t('meeting.sprecheransicht'), symbol: 'sprecher', an: this.buehne.effektiveAnsicht() === 'sprecher', aktion: () => this.buehne.ansichtSetzen('sprecher') },
      { text: t('meeting.galerieansicht'), symbol: 'galerie', an: this.buehne.effektiveAnsicht() === 'galerie', aktion: () => this.buehne.ansichtSetzen('galerie') },
      { text: t('einst.selbstAusblenden'), symbol: 'augeAus', an: this.buehne.selbstAus, aktion: () => this.selbstUmschalten() },
      { text: t('einst.sparmodus'), symbol: 'sparen', an: this.sparmodus !== 'aus', aktion: () => this.sparmodusSetzen(this.sparmodus === 'aus' ? 'audio' : 'aus') },
      '-',
      { text: t('meeting.linkKopieren'), symbol: 'link', aktion: () => kopieren(this.raum.link || `${location.origin}/r/${this.code}`, t('meeting.linkKopiert')) },
      { text: t('einst.titel'), symbol: 'zahnrad', aktion: () => this.einstellungen() },
      { text: t('meeting.tastenkuerzel'), symbol: 'tastatur', aktion: () => this.hilfe() },
    ], { label: t('steuer.mehr') });
  }

  infoMenue(anker) {
    const link = this.raum.link || `${location.origin}/r/${this.code}`;
    const inhalt = h('div', { class: 'formular', style: { padding: '8px', width: 'min(340px, calc(100vw - 32px))' } },
      h('b', {}, this.raum.titel || t('meeting.ohneTitel')),
      h('span', { class: 'sec klein' }, t('meeting.gastgeber', { name: this.raum.besitzer_name || '' })),
      h('div', { class: 'link-feld' }, link),
      h('span', { class: 'sec klein' }, `${t('meeting.code')}: `, h('span', { class: 'mono' }, this.code)),
      this.raum.passcode_noetig ? h('span', { class: 'sec klein' }, t('meeting.mitPasscode')) : null,
      h('button', { type: 'button', class: 'btn primary klein', onclick: () => { kopieren(link, t('meeting.linkKopiert')); menueSchliessen(); } }, t('meeting.linkKopieren')));
    menue(anker, null, { inhalt, label: t('meeting.info'), oben: false });
  }

  hilfe() {
    const { inhalt } = blatt(t('meeting.tastenkuerzel'));
    const zeilen = [['M', t('steuer.micAn') + ' / ' + t('steuer.micAus')], ['V', t('steuer.camAn') + ' / ' + t('steuer.camAus')], ['H', t('steuer.handHeben')],
      ['C', t('steuer.chat')], ['P', t('steuer.leute')], ['S', t('steuer.teilen')], ['G', t('meeting.ansichtWechseln')], ['F', t('meeting.vollbild')], ['?', t('meeting.tastenkuerzel')], ['Esc', t('meeting.leisteZu')]];
    inhalt.append(h('table', { class: 'tasten' }, h('tbody', {}, zeilen.map(([k, text]) => h('tr', {}, h('td', {}, h('kbd', {}, k)), h('td', {}, text))))),
      h('p', { class: 'sec klein' }, t('meeting.tastenHinweis')));
  }

  taste(e) {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || !this.room) return;
    const ziel = e.target;
    if (ziel.closest?.('input, textarea, select, [contenteditable="true"], dialog[open]')) { if (e.key === 'Escape' && ziel.closest('#seitenleiste')) this.leisteSchliessen(); return; }
    const k = e.key.toLowerCase();
    const aktionen = {
      m: () => this.micUmschalten(), v: () => this.kameraUmschalten(), h: () => this.handUmschalten(), c: () => this.leisteUmschalten('chat'),
      p: () => this.leisteUmschalten('leute'), s: () => !$('#s-teilen').disabled && !$('#s-teilen').hidden && this.teilenUmschalten(),
      g: () => this.buehne.ansichtSetzen(this.buehne.effektiveAnsicht() === 'galerie' ? 'sprecher' : 'galerie'), f: () => this.vollbild(), '?': () => this.hilfe(),
      escape: () => this.leisteOffen && this.leisteSchliessen(),
    };
    if (aktionen[k] && !e.repeat) { e.preventDefault(); aktionen[k](); }
  }

  vollbild() {
    const el = $('#v-meeting');
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else (el.requestFullscreen?.() || el.webkitRequestFullscreen?.())?.catch?.(() => {});
  }

  selbstUmschalten() {
    this.buehne.selbstAus = !this.buehne.selbstAus;
    this.buehne.anordnen();
    toast(this.buehne.selbstAus ? t('meeting.selbstAus') : t('meeting.selbstAn'), '', { dauer: 2500, aktion: this.buehne.selbstAus ? { text: t('meeting.rueckgaengig'), fn: () => this.selbstUmschalten() } : undefined });
  }

  /** Bild-im-Bild: Video der großen Kachel bzw. des aktiven Sprechers. */
  async bildImBild(video) {
    try {
      if (document.pictureInPictureElement) { await document.exitPictureInPicture(); if (!video) return; }
      const ziel = video || $('#gross .kv.mit-video video') || $('.kv.spricht.mit-video:not(.spiegeln) video') || $('#raster .kv.mit-video:not(.spiegeln) video');
      if (!ziel) { toast(t('meeting.keinVideo'), 'warn'); return; }
      await ziel.requestPictureInPicture();
    } catch (e) { toast(t('meeting.pipFehler', { text: e.message }), 'err'); }
  }

  // ---------------------------------------------------------------- Datensparmodus
  sparmodusSetzen(modus) {
    this.sparmodus = modus;
    effekte.pausieren(modus !== 'aus');     // Effekte kosten Rechenzeit und Akku – im Sparmodus aus
    for (const p of this.room.remoteParticipants.values()) for (const pub of p.videoTrackPublications.values()) this.sparenAnwenden(pub);
    if (modus === 'audio' && this.room.localParticipant.isCameraEnabled) this.room.localParticipant.setCameraEnabled(false).then(() => this.steuerAktualisieren());
    toast(t(`einst.sparToast.${modus}`), '', { dauer: 3000 });
  }

  sparenAnwenden(pub) {
    if (!pub || pub.kind !== Track.Kind.Video || pub.isLocal) return;
    const bildschirm = pub.source === Track.Source.ScreenShare;
    if (this.sparmodus === 'audio' && !bildschirm) { if (pub.isSubscribed || pub.isDesired) pub.setSubscribed(false); return; }
    if (!pub.isDesired) pub.setSubscribed(true);
    if (!pub.isSubscribed) return;
    const q = this.sparmodus === 'aus' ? VideoQuality.HIGH : (bildschirm ? VideoQuality.MEDIUM : VideoQuality.LOW);
    pub.setVideoQuality(q);
  }

  // ---------------------------------------------------------------- Aufzeichnung
  async aufnahmeUmschalten(grund) {
    if (this.rekorder?.laeuft) {
      const r = this.rekorder;
      const blob = await r.stoppen();
      this.api(`/api/raeume/${this.code}/aufnahme`, { methode: 'POST', json: { aktiv: false } }).catch(() => {});
      this.aufnahmeSetzen(null);
      if (grund) toast(grund, 'warn');
      if (blob?.size) this.aufnahmeSichern(blob, r.dauer);
      return;
    }
    if (!this.istMod()) return;
    const ok = await bestaetigen(t('aufnahme.startTitel'), t('aufnahme.startText') + (this.ich ? '' : ' ' + t('aufnahme.startOhneKonto')), { ja: t('aufnahme.starten') });
    if (!ok) return;
    this.rekorder = new Rekorder(this);
    try {
      await this.api(`/api/raeume/${this.code}/aufnahme`, { methode: 'POST', json: { aktiv: true } });
      await this.rekorder.starten();
      this.aufnahmeSetzen({ von_name: this.name(this.room.localParticipant) });
      toast(t('aufnahme.gestartet'), 'ok', { dauer: 3000 });
    } catch (e) {
      this.rekorder = null;
      this.api(`/api/raeume/${this.code}/aufnahme`, { methode: 'POST', json: { aktiv: false } }).catch(() => {});
      toast(e instanceof ApiFehler ? e.message : t('aufnahme.startFehler', { text: e.message }), 'err');
    }
  }

  aufnahmeSichern(blob, dauer, ziel) {
    let box = ziel;
    if (!box) { box = $('.upload-schwebend') || h('div', { class: 'upload-schwebend', role: 'status' }); document.body.append(box); }
    const datum = new Intl.DateTimeFormat('de-AT', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date());
    aufnahmeAblegen(box, blob, { code: this.code, titel: `${this.raum.titel || this.code} – ${datum}`, dauer, angemeldet: !!this.ich });
  }

  // ---------------------------------------------------------------- Verlassen / Ende
  verlassenMenue(anker) {
    const eintraege = [{ text: t('meeting.verlassen'), symbol: 'tuer', aktion: () => this.verlassen() }];
    if (this.rolle === 'host') {
      eintraege.push({ text: t('meeting.fuerAlleBeenden'), symbol: 'auflegen', gefahr: true, aktion: async () => {
        if (await bestaetigen(t('meeting.fuerAlleBeenden'), t('meeting.fuerAlleBeendenText'), { ja: t('meeting.beenden'), gefahr: true })) {
          this.grund = 'beendetSelbst';
          if (await this.steuern('beenden')) { if (this.room.state !== ConnectionState.Disconnected) this.room.disconnect(); }
          else this.grund = null;
        }
      } });
      if (this.room.remoteParticipants.size) eintraege.push({ titel: t('meeting.hostTipp') });
    }
    menue(anker, eintraege, { label: t('steuer.verlassen') });
  }

  async verlassen() {
    this.grund = this.grund || 'verlassen';
    await this.room.disconnect();
  }

  /** Breakout: Verbindung vom aktuellen LiveKit-Raum lösen und mit dem Ziel ({url, token}) verbinden; Mikro/Kamera wie vorher. */
  async raumWechseln(ziel) {
    const alt = this.room, lp = alt.localParticipant;
    const mic = lp.isMicrophoneEnabled, cam = lp.isCameraEnabled;
    const geraet = (art) => alt.getActiveDevice(art) || undefined;
    if (this.rekorder?.laeuft) await this.aufnahmeUmschalten(t('breakout.aufnahmeGestoppt'));
    menueSchliessen();
    this.room = new Room({
      ...alt.options,
      videoCaptureDefaults: { ...alt.options.videoCaptureDefaults, deviceId: geraet('videoinput') },
      audioCaptureDefaults: { ...alt.options.audioCaptureDefaults, deviceId: geraet('audioinput') },
      audioOutput: geraet('audiooutput') ? { deviceId: geraet('audiooutput') } : alt.options.audioOutput,
    });
    this.ereignisse();
    await alt.disconnect();
    $('#audio-ablage').replaceChildren();
    this.buehne.spotlight(null);
    this.buehne.sync();
    await this.room.connect(ziel.url, ziel.token);
    this.metadatenLesen(this.room.metadata);
    try {
      if (mic) await this.room.localParticipant.setMicrophoneEnabled(true);
      if (cam && this.sparmodus !== 'audio') await this.room.localParticipant.setCameraEnabled(true);
    } catch (e) { toast(t('meeting.veroeffentlichenFehler', { text: e.message }), 'err'); }
    this.buehne.sync();
    this.chat.empfaengerAktualisieren();
    this.leute.planen();
    this.steuerAktualisieren();
    this.zustandLaden();
    document.dispatchEvent(new CustomEvent('agora:raumwechsel', { detail: { room: this.room } }));
  }

  async getrennt(grundLk) {
    if (await this.breakout?.getrennt(grundLk, this.grund)) return;   // Breakout-Raum gelöscht → zurück in den Hauptraum
    if (this.beendet) return;
    this.beendet = true;
    clearInterval(this.uhr);
    clearInterval(this.pegelTimer);
    menueSchliessen();
    let blob = null, dauer = 0;
    if (this.rekorder?.laeuft) {
      dauer = Math.round((Date.now() - this.rekorder.start) / 1000);
      blob = await this.rekorder.stoppen();
      this.api(`/api/raeume/${this.code}/aufnahme`, { methode: 'POST', json: { aktiv: false } }).catch(() => {});
    }
    let art = this.grund;
    if (!art) {
      if (grundLk === DisconnectReason.PARTICIPANT_REMOVED) art = 'entfernt';
      else if (grundLk === DisconnectReason.ROOM_DELETED) art = 'beendet';
      else if (grundLk === DisconnectReason.DUPLICATE_IDENTITY) art = 'doppelt';
      else art = 'verbindung';
    }
    try { await this.wake?.release(); } catch { /* egal */ }
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    if (document.pictureInPictureElement) document.exitPictureInPicture().catch(() => {});
    $('#audio-ablage').replaceChildren();
    $('#v-meeting').hidden = true;
    document.body.classList.remove('im-meeting');
    ruhe(false);
    const laufenderUpload = document.querySelector('.upload-schwebend');
    laufenderUpload?.classList.remove('upload-schwebend');
    this.onEnde(art, blob?.size ? (ziel) => this.aufnahmeSichern(blob, dauer, ziel) : null, laufenderUpload);
  }
}
