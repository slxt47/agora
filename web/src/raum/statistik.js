// Verbindungsqualität: alle 10 s die WebRTC-Statistik der eigenen Verbindungen lesen (getStats der LiveKit-
// PeerConnections für Senden und Empfangen), Messwerte rechnen (statistik-rechnen.js), im Popover „Verbindung“ an der
// eigenen Kachel zeigen, bei dauerhaft schlechter Verbindung mit Tipps warnen und etwa alle 30 s eine kleine
// Zusammenfassung an den Server schicken (POST …/statistik). Host/Co-Host sehen die Werte aller in der Teilnehmerliste.
import { h, toast, blatt } from '../common.js';
import { t, gebiet } from '../i18n.js';
import { menue } from './menue.js';
import { schnappschuss, auswerten, dauerhaftSchlecht, schnitt } from './statistik-rechnen.js';
import './statistik.css';

const TAKT_MS = 10000;          // messen
const MELDEN_JEDE = 3;          // jede dritte Messung (≈ 30 s) an den Server
const WARN_PAUSE_MS = 5 * 60 * 1000;
const LEUTE_MS = 10000;         // Host: Werte der anderen nachladen, solange die Liste offen ist
const zahl = new Intl.NumberFormat(gebiet(), { maximumFractionDigits: 1 });

export class Statistik {
  constructor(m) {
    this.m = m;
    this.verlauf = [];           // Messwerte (auswerten) – für Warnung und Schnitt
    this.letzte = null;
    this.vorher = null;
    this.raumVorher = null;
    this.n = 0;
    this.fremd = {};             // identity → letzte Werte (nur Host/Co-Host)
    this.leuteZeit = 0;
    this.warnZeit = 0;
    this.popover = null;
  }

  starten() {
    this.anmelden();
    this.messen();
    this.erst = setTimeout(() => this.messen(), 3000);           // erste Werte schnell, dann im Takt
    this.timer = setInterval(() => this.messen(), TAKT_MS);
    this.leuteTimer = setInterval(() => this.leuteLaden(), LEUTE_MS);
  }

  stoppen() {
    clearTimeout(this.erst);
    clearInterval(this.timer);
    clearInterval(this.leuteTimer);
  }

  /** Beim Server melden, dass gemessen wird (ohne Werte). */
  async anmelden() {
    try { this.token((await this.m.api(`/api/raeume/${this.m.code}/statistik`, { methode: 'POST', json: {} })).feedback); } catch { /* egal */ }
  }

  /** getStats() der PeerConnections (Senden + Empfangen; bei nur einer Verbindung eben die). */
  async berichte() {
    const pcm = this.m.room?.engine?.pcManager;
    const verbindungen = [...new Set([pcm?.publisher, pcm?.subscriber].filter(Boolean))];
    const berichte = [];
    for (const v of verbindungen) {
      try { const r = await v.getStats(); if (r) berichte.push(r); } catch { /* geschlossen */ }
    }
    return berichte;
  }

  async messen() {
    if (!this.m.room) return;
    if (this.raumVorher !== this.m.room) { this.vorher = null; this.raumVorher = this.m.room; }   // Raumwechsel: Zähler neu
    const s = schnappschuss(await this.berichte());
    if (this.vorher) {
      const w = auswerten(this.vorher, s);
      this.letzte = w;
      this.verlauf.push(w);
      if (this.verlauf.length > 360) this.verlauf.shift();       // höchstens eine Stunde
      if (this.n++ % MELDEN_JEDE === 0) this.melden(w);
      this.warnen();
      this.popoverAktualisieren();
      this.m.leute?.planen();
    }
    this.vorher = s;
  }

  async melden(w) {
    const { bewertung: _b, ...werte } = w;
    try { this.token((await this.m.api(`/api/raeume/${this.m.code}/statistik`, { methode: 'POST', json: werte })).feedback); } catch { /* nächstes Mal */ }
  }

  /** Rückmelde-Token für den Ende-Bildschirm merken (gilt 24 h, auch nach dem Meeting). */
  token(t_) { if (typeof t_ === 'string' && t_) this.feedbackToken = t_; }

  /** Für den Ende-Bildschirm: {token, statistik} oder null. */
  feedback() { return this.feedbackToken ? { token: this.feedbackToken, statistik: this.schnitt() } : null; }

  /** Schnitt über das ganze Meeting (für die Rückmeldung am Ende). */
  schnitt() { return schnitt(this.verlauf); }

  // ---------------------------------------------------------------- Warnung mit Tipps
  warnen() {
    if (!dauerhaftSchlecht(this.verlauf) || Date.now() - this.warnZeit < WARN_PAUSE_MS) return;
    this.warnZeit = Date.now();
    toast(t('stat.warnung'), 'warn', { dauer: 15000, aktion: { text: t('stat.tippsKnopf'), fn: () => this.tipps() } });
  }

  tipps() {
    const { inhalt, schliessen } = blatt(t('stat.tippsTitel'));
    inhalt.append(
      h('p', { class: 'sec' }, t('stat.tippsText')),
      h('ul', { class: 'stat-tipps' }, ['stat.tipp.wlan', 'stat.tipp.andere', 'stat.tipp.kamera', 'stat.tipp.teilen', 'stat.tipp.neu'].map((k) => h('li', {}, t(k)))),
      h('div', { class: 'knoepfe' },
        h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.schliessen')),
        this.m.sparmodus === 'aus' ? h('button', { type: 'button', class: 'btn primary', onclick: () => { schliessen(); this.m.sparmodusSetzen('niedrig'); } }, t('stat.sparen')) : null));
  }

  // ---------------------------------------------------------------- Popover „Verbindung“ (eigene Kachel)
  zeigen(anker) {
    this.popover = h('div', { class: 'stat-popover' });
    this.popoverAktualisieren();
    menue(anker, null, { inhalt: this.popover, label: t('stat.titel'), oben: false });
  }

  popoverAktualisieren() {
    const el = this.popover;
    if (!el || (!el.isConnected && el.childElementCount)) return;     // nur das offene Popover (oder das neue) füllen
    const w = this.letzte;
    const kopf = h('div', { class: 'stat-kopf' }, h('b', {}, t('stat.titel')),
      w ? h('span', { class: `stat-bewertung ${w.bewertung}` }, t(`stat.bewertung.${w.bewertung}`)) : null);
    if (!w) { el.replaceChildren(kopf, h('p', { class: 'sec klein' }, t('stat.misst'))); return; }
    const zeile = (label, wert, titel) => [h('dt', { title: titel || null }, label), h('dd', {}, wert)];
    el.replaceChildren(kopf,
      h('dl', { class: 'stat-liste' },
        zeile(t('stat.weg'), t(`stat.kandidat.${w.kandidat || 'unbekannt'}`)),
        zeile(t('stat.rtt'), `${zahl.format(w.rtt)} ms`, t('stat.rttHinweis')),
        zeile(t('stat.verlust'), `${zahl.format(w.verlust)} %`),
        zeile(t('stat.jitter'), `${zahl.format(w.jitter)} ms`),
        zeile(t('stat.aus'), `${zahl.format(w.aus)} kbit/s`),
        zeile(t('stat.ein'), `${zahl.format(w.ein)} kbit/s`),
        zeile(t('stat.video'), w.aufloesung ? `${w.aufloesung.replace('x', ' × ')} · ${w.fps} fps` : t('stat.keinVideo'))),
      h('p', { class: 'sec klein' }, t('stat.fuss')),
      ...(w.bewertung !== 'gut' ? [h('button', { type: 'button', class: 'btn klein', onclick: () => this.tipps() }, t('stat.tippsKnopf'))] : []));
  }

  // ---------------------------------------------------------------- Host: Qualität je Person in der Teilnehmerliste
  async leuteLaden() {
    if (!this.m.istMod() || !this.m.leute?.sichtbar) return;
    this.leuteZeit = Date.now();
    try {
      const r = await this.m.api(`/api/raeume/${this.m.code}/statistik`);
      this.fremd = r.teilnehmer || {};
      this.m.leute.planen();
    } catch { /* still */ }
  }

  /** Kleiner Punkt (grün/gelb/rot) neben einer Person – nur für Host/Co-Host. */
  marke(p) {
    if (!this.m.istMod()) return null;
    if (Date.now() - this.leuteZeit > LEUTE_MS) this.leuteLaden();
    const w = p.isLocal ? this.letzte : this.fremd[p.identity];
    if (!w) return null;
    const text = t('stat.leuteLabel', { wert: t(`stat.bewertung.${w.bewertung}`), rtt: zahl.format(w.rtt), verlust: zahl.format(w.verlust),
      weg: t(`stat.kandidat.${w.kandidat || 'unbekannt'}`) });
    return h('span', { class: `stat-punkt ${w.bewertung}`, role: 'img', 'aria-label': text, title: text });
  }
}
