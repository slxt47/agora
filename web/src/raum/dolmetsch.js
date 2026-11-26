// Dolmetscher-Kanäle im Meeting (Server: app/dolmetsch.py).
// • Zuhörende wählen „Sprachkanal“: Der Browser abonniert nur die Spur `dolmetsch:<sprache>` der Person, die laut
//   Server-Metadaten gerade auf Sendung ist, und spielt den Saal leiser (20 %). Alle anderen Dolmetsch-Spuren werden
//   abbestellt (LiveKit abonniert sonst automatisch alles). Ohne Dolmetscher auf dem Kanal: Originalton, mit Hinweis.
// • Dolmetscher bekommen ein Pult: Sprachpaar, Kanal wechseln (a ↔ b), Dolmetschen starten/stoppen (eigene Spur aus dem
//   Mikrofon, nie automatisch), Übergabe an die Bereitschaft bzw. übernehmen. Solange sie senden, ist ihr Saal-Mikrofon stumm.
// • Host/Co-Host: „Dolmetschen verwalten“ – Sprachen festlegen, Dolmetscher mit Sprachpaar zuweisen und entfernen.
import { createLocalAudioTrack, Track } from 'livekit-client';
import { $, h, toast, blatt, beschaeftigt } from '../common.js';
import { t, gebiet } from '../i18n.js';
import { icon } from '../icons.js';
import { SPUR, LEISE, kanalSpur, abonnieren, partner, sprachName, SPRACHEN_VORSCHLAG } from './dolmetsch-logik.js';
import './dolmetsch.css';

const LEER = { sprachen: [], dolmetscher: {}, kanaele: {} };

export class Dolmetschen {
  constructor(m) {
    this.m = m;
    this.z = LEER;
    this.kanal = '';               // gewählter Sprachkanal ('' = Original)
    this.spur = null;              // eigene Dolmetsch-Spur (LocalAudioTrack)
    this.spurSprache = '';
    this.senden = false;           // Dolmetscher hat „Dolmetschen starten“ gedrückt
    this.pille = h('span', { class: 'm-pille dolmetsch-pille', id: 'm-dolmetsch', role: 'status', hidden: true });
    $('#m-rec')?.after(this.pille);
    this.pult = h('div', { class: 'dolmetsch-pult', id: 'dolmetsch-pult', role: 'region', 'aria-label': t('dm.pult'), hidden: true });
    $('#buehne')?.append(this.pult);
  }

  name(code) { return sprachName(code, gebiet()); }
  meineId() { return this.m.room?.localParticipant.identity; }
  ich() { return this.z.dolmetscher?.[this.meineId()] || null; }

  metadaten(md) { if ('dolmetsch' in md) this.setzen(md.dolmetsch); }
  ereignis(d) { this.setzen(d.zustand); }

  setzen(z) {
    const vorher = this.ich();
    this.z = { ...LEER, ...(z || {}) };
    if (this.kanal && !this.z.sprachen.includes(this.kanal)) this.kanal = '';
    const jetzt = this.ich();
    if (jetzt && !vorher) toast(t('dm.duBist', { a: this.name(jetzt.a), b: this.name(jetzt.b) }), 'ok', { dauer: 8000 });
    if (!jetzt && vorher) { this.senden = false; toast(t('dm.nichtMehr'), '', { dauer: 5000 }); }
    this.abgleichen();
    this.spurAbgleichen();
    this.pultZeigen();
  }

  // ---------------------------------------------------------------- Zuhören
  istSpur(pub) { return pub?.kind === Track.Kind.Audio && kanalSpur(pub.trackName) !== null; }
  gewuenscht(pub, p) { return abonnieren(this.z, this.kanal, p.identity, pub.trackName); }

  /** Dolmetsch-Spuren an- bzw. abbestellen (nach Kanalwahl, neuen Spuren, neuen Metadaten). */
  abgleichen() {
    if (!this.m.room) return;
    for (const p of this.m.room.remoteParticipants.values()) {
      for (const pub of p.trackPublications.values()) {
        if (!this.istSpur(pub)) continue;
        const soll = this.gewuenscht(pub, p);
        if (pub.isDesired !== soll) pub.setSubscribed(soll);
        // Abbestellt: sofort verstummen – das Ereignis TrackUnsubscribed kommt erst, wenn der Server die Spur entfernt
        if (!soll && pub.track) pub.track.detach().forEach((el) => el.remove());
      }
    }
    this.lautstaerke();
    this.pilleZeigen();
  }

  /** Aus meeting.js (TrackSubscribed): true, wenn es eine Dolmetsch-Spur war (dann hier erledigt). */
  abonniert(track, pub, p) {
    if (!this.istSpur(pub)) return false;
    if (!this.gewuenscht(pub, p)) { pub.setSubscribed(false); return true; }
    const el = track.attach();
    el.dataset.sid = pub.trackSid;
    el.dataset.dolmetsch = kanalSpur(pub.trackName);
    $('#audio-ablage').append(el);
    this.lautstaerke();
    this.pilleZeigen();
    return true;
  }

  /** Saal leiser, solange eine Dolmetsch-Spur läuft. */
  lautstaerke() {
    if (!this.m.room) return;
    const alle = [...this.m.room.remoteParticipants.values()];
    const laeuft = alle.some((p) => [...p.trackPublications.values()].some((pub) => this.istSpur(pub) && pub.track && this.gewuenscht(pub, p)));
    for (const p of alle) {
      for (const pub of p.trackPublications.values()) {
        if (pub.kind !== Track.Kind.Audio || !pub.track) continue;
        pub.track.setVolume(this.istSpur(pub) ? 1 : (laeuft ? LEISE : 1));
      }
    }
  }

  kanalWaehlen(code) {
    this.kanal = code;
    this.abgleichen();
    if (!code) toast(t('dm.original'), '', { dauer: 2500 });
    else if (!this.z.kanaele[code]) toast(t('dm.kanalLeer', { sprache: this.name(code) }), 'warn', { dauer: 5000 });
    else toast(t('dm.kanalAn', { sprache: this.name(code) }), 'ok', { dauer: 2500 });
  }

  pilleZeigen() {
    this.pille.hidden = !this.kanal;
    if (!this.kanal) return;
    const da = !!this.z.kanaele[this.kanal];
    this.pille.innerHTML = `${icon('sprache')}<span></span>`;
    this.pille.querySelector('span').textContent = da ? this.name(this.kanal) : t('dm.pilleOriginal', { sprache: this.name(this.kanal) });
    this.pille.classList.toggle('leer', !da);
  }

  // ---------------------------------------------------------------- Dolmetschen (eigenes Pult)
  async spurAbgleichen() {
    const ich = this.ich();
    const soll = ich && ich.aktiv && this.senden ? ich.sprache : '';
    if (soll === this.spurSprache) return;
    const lp = this.m.room?.localParticipant;
    if (!lp) return;
    const alt = this.spur;
    this.spur = null;
    this.spurSprache = soll;
    if (alt) { await lp.unpublishTrack(alt).catch(() => {}); alt.stop(); }
    if (!soll) { this.pultZeigen(); return; }
    try {
      if (lp.isMicrophoneEnabled) { await lp.setMicrophoneEnabled(false); toast(t('dm.saalStumm'), '', { dauer: 5000 }); }
      if (!lp.getTrackPublication(Track.Source.Microphone)) {      // Saal-Mikro zuerst (stumm) – sonst hielte LiveKit die
        const saal = await createLocalAudioTrack();                // Dolmetsch-Spur für „das Mikrofon“ (Stummschalten)
        await saal.mute();
        await lp.publishTrack(saal, { source: Track.Source.Microphone });
      }
      const tr = await createLocalAudioTrack({ deviceId: this.m.medien?.wahl?.audioinput || undefined, echoCancellation: true, noiseSuppression: true });
      if (this.spurSprache !== soll) { tr.stop(); return; }          // inzwischen gewechselt
      await lp.publishTrack(tr, { name: SPUR + soll, source: Track.Source.Microphone, dtx: false, red: true });
      this.spur = tr;
    } catch (e) {
      this.spurSprache = '';
      this.senden = false;
      toast(t('dm.fehler', { text: e.message }), 'err', { dauer: 7000 });
    }
    this.pultZeigen();
  }

  pultZeigen() {
    const ich = this.ich();
    this.pult.hidden = !ich;
    if (!ich) { this.pult.replaceChildren(); return; }
    const andere = partner(this.z, this.meineId());
    const aktivPartner = andere.find((v) => v.aktiv);
    const anderer = ich.sprache === ich.a ? ich.b : ich.a;
    const k = (text, sym, fn, extra = {}) => h('button', { type: 'button', class: 'btn klein', ...extra, onclick: (e) => beschaeftigt(e.currentTarget, fn) },
      h('span', { html: icon(sym) }), h('span', {}, text));
    const zustand = ich.aktiv ? (this.spur ? 'sendet' : 'bereit') : 'bereitschaft';
    this.pult.replaceChildren(
      h('div', { class: 'dm-kopf' }, h('span', { html: icon('sprache') }), h('b', {}, `${this.name(ich.a)} ⇄ ${this.name(ich.b)}`)),
      h('p', { class: 'dm-status', dataset: { zustand } },
        zustand === 'sendet' ? t('dm.sendet', { sprache: this.name(ich.sprache) })
          : zustand === 'bereit' ? t('dm.aufSendung', { sprache: this.name(ich.sprache) }) : t('dm.bereitschaft', { name: aktivPartner?.name || '–' })),
      h('div', { class: 'dm-knoepfe' },
        ich.aktiv ? k(this.senden ? t('dm.stoppen') : t('dm.starten'), this.senden ? 'micAus' : 'mic', async () => {
          this.senden = !this.senden; await this.spurAbgleichen(); this.pultZeigen();
        }, { class: `btn klein ${this.senden ? '' : 'primary'}`, id: 'dm-senden' }) : null,
        k(t('dm.kanalWechseln', { sprache: this.name(anderer) }), 'pfeilRechts', async () => {
          await this.m.api(`/api/raeume/${this.m.code}/dolmetsch/kanal`, { methode: 'POST', json: { sprache: anderer } });
        }, { id: 'dm-kanal' }),
        andere.length ? k(ich.aktiv ? t('dm.uebergeben') : t('dm.uebernehmen'), 'hand', async () => {
          if (!ich.aktiv) this.senden = true;
          await this.m.api(`/api/raeume/${this.m.code}/dolmetsch/uebergabe`, { methode: 'POST', json: {} });
        }, { id: 'dm-uebergabe' }) : null));
  }

  stoppen() {
    this.senden = false;
    if (this.spur) { this.spur.stop(); this.spur = null; this.spurSprache = ''; }
  }

  // ---------------------------------------------------------------- Menü und Verwaltung
  menueEintraege() {
    const e = [];
    if (this.z.sprachen.length) {
      e.push({ text: this.kanal ? t('dm.kanalMenueAn', { sprache: this.name(this.kanal) }) : t('dm.kanalMenue'), symbol: 'sprache',
        aktion: () => this.kanalMenue() });
    }
    if (this.m.istMod()) e.push({ text: t('dm.verwalten'), symbol: 'sprache', aktion: () => this.verwalten() });
    return e;
  }

  kanalMenue() {
    const { inhalt, schliessen } = blatt(t('dm.kanalTitel'));
    const wahl = (code, text, hinweis) => h('button', { type: 'button', class: `dm-kanal ${this.kanal === code ? 'an' : ''}`, dataset: { kanal: code },
      'aria-pressed': String(this.kanal === code), onclick: () => { this.kanalWaehlen(code); schliessen(); } },
    h('b', {}, text), hinweis ? h('small', {}, hinweis) : null);
    inhalt.append(
      h('p', { class: 'sec klein' }, t('dm.kanalText')),
      h('div', { class: 'dm-kanaele' }, wahl('', t('dm.originalton')),
        ...this.z.sprachen.map((s) => wahl(s, this.name(s), this.z.kanaele[s] ? null : t('dm.keinDolmetscher')))));
  }

  verwalten() {
    const { inhalt } = blatt(t('dm.verwaltenTitel'), { breit: true });
    const pfad = `/api/raeume/${this.m.code}/dolmetsch`;
    const zeichnen = () => {
      const sprachen = this.z.sprachen;
      const auswahl = [...new Set([...SPRACHEN_VORSCHLAG, ...sprachen])];
      const haken = auswahl.map((s) => h('label', { class: 'dm-sprache' },
        h('input', { type: 'checkbox', value: s, checked: sprachen.includes(s) }), h('span', {}, `${this.name(s)} (${s})`)));
      const eigene = h('input', { type: 'text', maxlength: '3', placeholder: t('dm.codePlatzhalter'), 'aria-label': t('dm.codeAndere') });
      const leute = [this.m.room.localParticipant, ...this.m.room.remoteParticipants.values()];
      const sel = (feld) => h('select', { name: feld, 'aria-label': t(feld === 'a' ? 'dm.von' : 'dm.nach') }, sprachen.map((s) => h('option', { value: s }, this.name(s))));
      const wer = h('select', { name: 'ziel', 'aria-label': t('dm.person') }, leute.map((p) => h('option', { value: p.identity }, this.m.name(p))));
      const a = sel('a'), b = sel('b');
      if (sprachen.length > 1) b.value = sprachen[1];
      const liste = Object.entries(this.z.dolmetscher);
      inhalt.replaceChildren(
        h('p', { class: 'sec klein' }, t('dm.verwaltenText')),
        h('h4', {}, t('dm.sprachen')),
        h('div', { class: 'dm-sprachen' }, haken, h('label', { class: 'dm-sprache' }, eigene)),
        h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', id: 'dm-sprachen-speichern', onclick: (e) => beschaeftigt(e.currentTarget, async () => {
          const wahl = haken.map((l) => l.querySelector('input')).filter((i) => i.checked).map((i) => i.value);
          if (eigene.value.trim()) wahl.push(eigene.value.trim().toLowerCase());
          this.setzen(await this.m.api(`${pfad}/sprachen`, { methode: 'PUT', json: { sprachen: wahl } }));
          zeichnen();
        }) }, t('dm.sprachenSpeichern'))),
        h('h4', {}, t('dm.dolmetscher')),
        sprachen.length < 2 ? h('p', { class: 'leer' }, t('dm.erstSprachen')) : h('form', { class: 'dm-zuweisen', onsubmit: (e) => {
          e.preventDefault();
          beschaeftigt(e.submitter, async () => {
            this.setzen(await this.m.api(pfad, { methode: 'POST', json: { ziel: wer.value, a: a.value, b: b.value } }));
            zeichnen();
          });
        } }, wer, a, h('span', { 'aria-hidden': 'true' }, '⇄'), b, h('button', { type: 'submit', class: 'btn primary klein' }, t('dm.zuweisen'))),
        h('ul', { class: 'liste dm-liste' }, liste.length ? liste.map(([id, v]) => h('li', { dataset: { dolmetscher: id } },
          h('div', { class: 'text' }, h('b', {}, v.name),
            h('div', { class: 'sec klein' }, `${this.name(v.a)} ⇄ ${this.name(v.b)} · ${v.aktiv ? t('dm.listeSendung', { sprache: this.name(v.sprache) }) : t('dm.listeBereitschaft')}`)),
          h('div', { class: 'aktionen' },
            v.aktiv ? null : h('button', { type: 'button', class: 'btn klein', onclick: (e) => beschaeftigt(e.currentTarget, async () => {
              this.setzen(await this.m.api(`${pfad}/uebergabe`, { methode: 'POST', json: { an: id } })); zeichnen();
            }) }, t('dm.aufSendungSetzen')),
            h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('dm.entfernen'), title: t('dm.entfernen'), html: icon('muell'),
              onclick: (e) => beschaeftigt(e.currentTarget, async () => {
                this.setzen(await this.m.api(`${pfad}/${encodeURIComponent(id)}`, { methode: 'DELETE' })); zeichnen();
              }) })))) : [h('li', { class: 'leer' }, t('dm.keine'))]));
    };
    zeichnen();
  }
}
