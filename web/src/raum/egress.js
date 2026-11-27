// Server-Aufnahme und Livestream (LiveKit Egress) – nur sichtbar, wenn der Server es anbietet (AGORA_EGRESS=1).
// Host/Co-Host: „Auf dem Server aufzeichnen“ (Galerie/Sprecher → MP4 in der Bibliothek des Besitzers) und „Livestream“
// (RTMP(S)-Adresse + Stream-Schlüssel). Alle sehen REC (Metadaten „aufnahme“) bzw. LIVE (Metadaten „livestream“).
// Status und Fehler: Ereignis „egress“ vom Server + Abfrage alle 5 s, solange etwas läuft.
// Webinare (mit AGORA_HLS=1): „Öffentliche Übertragung (HLS)“ – Zuschauer ohne WebRTC auf /h/<code> (app/hls.py).
import { $, h, blatt, toast, beschaeftigt, status, kopieren } from '../common.js';
import { t } from '../i18n.js';
import './egress.css';

const LAEUFT = new Set(['startet', 'laeuft', 'endet']);
const ARTEN = ['aufnahme', 'stream', 'hls'];

export class ServerAufnahme {
  constructor(m) {
    this.m = m;
    this.verfuegbar = false;
    this.zustand = { aufnahme: null, stream: null, hls: null };
    this.hlsVerfuegbar = false;
    this.hlsAnmeldung = false;
    this.live = null;
    this.gesehen = new Set();        // Lauf-IDs, die in diesem Meeting liefen
    this.gemeldet = new Set();       // „id:status“ – jede Meldung nur einmal
    this.pille = h('span', { class: 'm-pille live', id: 'm-live', role: 'status', hidden: true }, 'LIVE');
    $('#m-rec')?.after(this.pille);
  }

  async laden() {
    if (!this.m.istMod()) return;
    try {
      const z = await this.m.api(`/api/raeume/${this.m.code}/egress`);
      this.verfuegbar = !!z.verfuegbar;
      if (!this.verfuegbar) return;
      const vorher = this.zustand;
      this.hlsVerfuegbar = !!z.hls_verfuegbar;
      this.hlsAnmeldung = !!z.hls_anmeldung;
      this.zustand = { aufnahme: z.aufnahme || null, stream: z.stream || null, hls: z.hls || null };
      for (const art of ARTEN) this.wechsel(art, vorher[art], this.zustand[art]);
    } catch { /* still */ }
    clearTimeout(this.timer);
    if (ARTEN.some((a) => this.laeuft(a))) this.timer = setTimeout(() => this.laden(), this.laeuft('hls') ? 15000 : 5000);
  }

  laeuft(art) { return LAEUFT.has(this.zustand[art]?.status); }

  /** Rückmeldung bei Statuswechsel (nur für Host/Co-Hosts). */
  wechsel(art, alt, neu) {
    if (!neu || (alt && alt.id === neu.id && alt.status === neu.status)) return;
    if ((!alt || alt.id !== neu.id) && !LAEUFT.has(neu.status) && !this.gesehen.has(neu.id)) return;   // alter Lauf beim Laden
    this.gesehen.add(neu.id);
    const schluessel = `${neu.id}:${neu.status}`;
    if (this.gemeldet.has(schluessel)) return;
    this.gemeldet.add(schluessel);
    if (neu.status === 'fehler') this.fehler(art, neu.fehler);
    else if (neu.status === 'fertig' && art === 'aufnahme') toast(t('egress.inBibliothek'), 'ok', { dauer: 8000 });
    else if (neu.status === 'laeuft') toast(t(`egress.${art}Laeuft`), 'ok', { dauer: 3000 });
    if (neu.status !== 'fehler') this.m.bannerSetzen(`egress-${art}`);
  }

  fehler(art, text) {
    const meldung = t(`egress.${art}Fehler`, { text: text || t('egress.unbekannt') });
    toast(meldung, 'err', { dauer: 10000 });
    this.m.bannerSetzen(`egress-${art}`, 'err', meldung, { text: t('allg.schliessen'), fn: () => this.m.bannerSetzen(`egress-${art}`) });
  }

  /** Server-Ereignis {typ: 'egress', art, status, fehler}. */
  ereignis(d) {
    if (!this.m.istMod() || !d.art) return;
    this.laden();
  }

  /** Raum-Metadaten: LIVE-Hinweis für alle. */
  metadaten(md) {
    const live = md.livestream || md.hls || null;
    this.pille.hidden = !live;
    if (live) {
      this.pille.title = t(md.livestream ? 'egress.liveVon' : 'egress.hlsVon', { name: live.von_name || t('rolle.host') });
      this.pille.setAttribute('aria-label', this.pille.title);
      if (!this.live && !this.m.istMod()) toast(t('egress.liveHinweis', { name: live.von_name || t('rolle.host') }), 'warn', { dauer: 8000 });
    }
    this.live = live;
  }

  menueEintraege() {
    if (!this.verfuegbar || !this.m.istMod()) return [];
    const auf = this.laeuft('aufnahme'), str = this.laeuft('stream');
    return [
      { text: auf ? t('egress.aufnahmeStoppen') : t('egress.aufnahmeStarten'), symbol: auf ? 'stopp' : 'aufnahme',
        deaktiviert: !auf && (!!this.m.aufnahmeInfo || this.zustand.aufnahme?.status === 'endet'), aktion: () => (auf ? this.stoppen('aufnahme') : this.aufnahmeDialog()) },
      { text: str ? t('egress.streamStoppen') : t('egress.streamStarten'), symbol: str ? 'stopp' : 'teilenLink',
        aktion: () => (str ? this.stoppen('stream') : this.streamDialog()) },
      ...this.hlsEintraege(),
    ];
  }

  hlsEintraege() {
    if (!this.hlsVerfuegbar) return [];
    const z = this.zustand.hls, an = this.laeuft('hls');
    const liste = [{ text: an ? t('egress.hlsStoppen') : t('egress.hlsStarten'), symbol: an ? 'stopp' : 'teilenLink',
      aktion: () => (an ? this.stoppen('hls') : this.hlsDialog()) }];
    if (an && z?.link) liste.push({ text: t('egress.hlsLink', { n: z.zuschauer ?? 0 }), symbol: 'kopie', aktion: () => kopieren(z.link) });
    return liste;
  }

  hlsDialog() {
    const { inhalt, schliessen } = blatt(t('egress.hlsTitel'));
    const st = h('div', { class: 'status', role: 'status', hidden: true });
    const f = h('form', { class: 'formular egress-form' },
      h('p', { class: 'sec' }, t('egress.hlsText')),
      h('label', { class: 'feld' }, h('span', {}, t('egress.hlsZugang')),
        h('select', { name: 'zugang' }, h('option', { value: 'oeffentlich' }, t('egress.hlsOeffentlich')),
          h('option', { value: 'anmeldung', disabled: !this.hlsAnmeldung }, t('egress.hlsNurAngemeldete')))),
      this.hlsAnmeldung ? null : h('p', { class: 'sec klein' }, t('egress.hlsOhneAnmeldung')),
      h('label', { class: 'schalter' }, h('span', { class: 'txt' }, h('span', {}, t('egress.hls360')), h('small', {}, t('egress.hls360Hinweis'))),
        h('input', { type: 'checkbox', role: 'switch', name: 'klein' })),
      this.layoutFeld(), st,
      h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
        h('button', { type: 'submit', class: 'btn primary' }, t('egress.hlsStartKnopf'))));
    inhalt.append(f);
    f.addEventListener('submit', (e) => {
      e.preventDefault();
      beschaeftigt(f.querySelector('button[type=submit]'), async () => {
        const r = await this.m.api(`/api/raeume/${this.m.code}/egress/hls`, { methode: 'POST', json: {
          aktion: 'start', zugang: f.zugang.value, layout: f.layout.value, stufen: f.klein.checked ? ['720p', '360p'] : ['720p'] } });
        schliessen();
        toast(t('egress.hlsStartet'), 'ok', { dauer: 8000, aktion: { text: t('konto.linkKopieren'), fn: () => kopieren(r.link) } });
        this.laden();
      }, st);
    });
  }

  layoutFeld() {
    return h('label', { class: 'feld' }, h('span', {}, t('egress.layout')),
      h('select', { name: 'layout' }, h('option', { value: 'galerie' }, t('meeting.galerieansicht')), h('option', { value: 'sprecher' }, t('meeting.sprecheransicht'))));
  }

  aufnahmeDialog() {
    const { inhalt, schliessen } = blatt(t('egress.aufnahmeTitel'));
    const st = h('div', { class: 'status', role: 'status', hidden: true });
    const f = h('form', { class: 'formular egress-form' },
      h('p', { class: 'sec' }, t('egress.aufnahmeText', { name: this.m.raum.besitzer_name || '' })),
      this.layoutFeld(), st,
      h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
        h('button', { type: 'submit', class: 'btn primary' }, t('egress.aufnahmeStarten'))));
    inhalt.append(f);
    f.addEventListener('submit', (e) => {
      e.preventDefault();
      beschaeftigt(f.querySelector('button[type=submit]'), async () => {
        this.m.aufnahmeHinweisGezeigt = true;          // wer startet, braucht den Hinweis nicht
        await this.m.api(`/api/raeume/${this.m.code}/egress/aufnahme`, { methode: 'POST', json: { aktion: 'start', layout: f.layout.value } });
        schliessen(); toast(t('egress.aufnahmeStartet'), 'ok', { dauer: 4000 }); this.laden();
      }, st);
    });
  }

  streamDialog() {
    const { inhalt, schliessen } = blatt(t('egress.streamTitel'));
    const st = h('div', { class: 'status', role: 'status', hidden: true });
    const f = h('form', { class: 'formular egress-form' },
      h('p', { class: 'sec' }, t('egress.streamText')),
      h('label', { class: 'feld' }, h('span', {}, t('egress.url')),
        h('input', { name: 'url', type: 'url', required: true, placeholder: 'rtmps://a.rtmp.youtube.com/live2', autocomplete: 'off', spellcheck: 'false', maxlength: '500' })),
      h('label', { class: 'feld' }, h('span', {}, t('egress.schluessel')),
        h('input', { name: 'schluessel', type: 'password', autocomplete: 'off', spellcheck: 'false', maxlength: '500' }),
        h('small', {}, t('egress.schluesselHinweis'))),
      this.layoutFeld(), st,
      h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
        h('button', { type: 'submit', class: 'btn primary' }, t('egress.streamStarten'))));
    inhalt.append(f);
    f.url.focus();
    f.addEventListener('submit', (e) => {
      e.preventDefault();
      if (!/^rtmps?:\/\//i.test(f.url.value.trim())) { status(st, t('egress.urlFalsch'), 'err'); f.url.focus(); return; }
      beschaeftigt(f.querySelector('button[type=submit]'), async () => {
        await this.m.api(`/api/raeume/${this.m.code}/egress/stream`, { methode: 'POST',
          json: { aktion: 'start', url: f.url.value.trim(), schluessel: f.schluessel.value.trim(), layout: f.layout.value } });
        f.schluessel.value = '';
        schliessen(); toast(t('egress.streamStartet'), 'ok', { dauer: 4000 }); this.laden();
      }, st);
    });
  }

  async stoppen(art) {
    await beschaeftigt(null, async () => {
      await this.m.api(`/api/raeume/${this.m.code}/egress/${art}`, { methode: 'POST', json: { aktion: 'stopp' } });
      toast(t(`egress.${art}Gestoppt`), 'ok', { dauer: 4000 });
      this.laden();
    });
  }
}
