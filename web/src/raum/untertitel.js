// Live-Untertitel im Meeting.
// • „Untertitel anzeigen“: blendet erkannte Sätze der anderen mit Namen über der Bühne ein (nur Anzeige, schickt nichts).
// • „Meine Sprache transkribieren“: Web Speech API im eigenen Browser. In Chrome geht der Ton dafür an Google – deshalb
//   immer erst nach ausdrücklicher Zustimmung (jedes Mal neu, nie automatisch). Agora bekommt nur den erkannten Text,
//   schickt ihn über den Server an alle (Topic „untertitel“, nur vom Server angenommen) und speichert ihn nur, wenn der
//   Besitzer „Transkript speichern“ eingeschaltet hat (Metadaten „transkript“, alle sehen einen Hinweis).
// • Läuft nur, solange das eigene Mikrofon an ist.
import { $, h, api, toast, bestaetigen } from '../common.js';
import { t } from '../i18n.js';
import { jsonLesen } from '../helfer.js';
import './untertitel.css';

const Erkennung = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition;
const HALTEN_MS = 6000;

export class Untertitel {
  constructor(m) {
    this.m = m;
    this.anzeigen = false;
    this.erkennung = null;
    this.aktiv = false;
    this.transkript = false;
    this.zeilen = new Map();          // identity → {name, text, el, timer}
    this.ebene = h('div', { class: 'untertitel', id: 'untertitel', 'aria-live': 'polite', hidden: true });
    $('#buehne')?.append(this.ebene);
    this.letzteZwischen = 0;
  }

  static unterstuetzt() { return !!Erkennung; }

  /** Server-Abschnitt {identity, name, text, final}. */
  empfangen(daten) {
    const d = jsonLesen(daten, null);
    if (!d || typeof d.text !== 'string' || !this.anzeigen) return;
    const ich = d.identity === this.m.room.localParticipant.identity;
    let z = this.zeilen.get(d.identity);
    if (!z) {
      z = { el: h('p', { class: 'ut-zeile' }, h('b', {}), h('span', {})) };
      this.zeilen.set(d.identity, z);
      this.ebene.append(z.el);
    }
    z.el.querySelector('b').textContent = `${ich ? t('allg.du') : d.name}: `;
    z.el.querySelector('span').textContent = d.text;
    z.el.classList.toggle('zwischen', !d.final);
    clearTimeout(z.timer);
    z.timer = setTimeout(() => { z.el.remove(); this.zeilen.delete(d.identity); this.sichtbar(); }, HALTEN_MS);
    while (this.ebene.children.length > 3) {
      const erst = this.ebene.firstElementChild;
      for (const [k, v] of this.zeilen) if (v.el === erst) { clearTimeout(v.timer); this.zeilen.delete(k); }
      erst.remove();
    }
    this.sichtbar();
  }

  sichtbar() { this.ebene.hidden = !this.anzeigen || !this.ebene.children.length; }

  anzeigeUmschalten() {
    this.anzeigen = !this.anzeigen;
    if (!this.anzeigen) { this.ebene.replaceChildren(); this.zeilen.clear(); }
    this.sichtbar();
    toast(this.anzeigen ? t('ut.anzeigeAn') : t('ut.anzeigeAus'), '', { dauer: 2500 });
  }

  /** Metadaten: Transkript wird gespeichert → Hinweis für alle (einmal). */
  metadaten(md) {
    const an = !!md.transkript;
    if (an && !this.transkript) toast(t('ut.transkriptHinweis'), 'warn', { dauer: 8000 });
    this.transkript = an;
  }

  async transkribierenUmschalten() {
    if (this.aktiv) { this.stoppen(); toast(t('ut.eigeneAus'), '', { dauer: 2500 }); return; }
    if (!Erkennung) { toast(t('ut.nichtUnterstuetzt'), 'warn', { dauer: 6000 }); return; }
    const ok = await bestaetigen(t('ut.zustimmungTitel'), t('ut.zustimmungText') + (this.transkript ? ` ${t('ut.zustimmungGespeichert')}` : ''),
      { ja: t('ut.zustimmen') });
    if (!ok) return;
    this.starten();
  }

  starten() {
    const e = new Erkennung();
    e.lang = (document.documentElement.lang || 'de').slice(0, 2) === 'de' ? 'de-DE' : 'en-US';
    e.continuous = true;
    e.interimResults = true;
    let satzStart = 0;
    e.onresult = (ev) => {
      if (!this.m.room?.localParticipant.isMicrophoneEnabled) return;      // stumm → nichts schicken
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i], text = r[0].transcript.trim();
        if (!text) continue;
        if (!satzStart) satzStart = Date.now();
        if (r.isFinal) { this.schicken(text, true, Date.now() - satzStart); satzStart = 0; } else if (Date.now() - this.letzteZwischen > 800) {
          this.letzteZwischen = Date.now(); this.schicken(text, false, 0);
        }
      }
    };
    e.onerror = (ev) => {
      if (ev.error === 'no-speech' || ev.error === 'aborted') return;
      this.stoppen();
      toast(t('ut.fehler', { text: ev.error || '' }), 'err', { dauer: 7000 });
    };
    e.onend = () => { if (this.aktiv && this.erkennung === e) { try { e.start(); } catch { this.stoppen(); } } };   // Chrome beendet nach Pausen selbst
    this.erkennung = e;
    this.aktiv = true;
    try { e.start(); } catch (err) { this.stoppen(); toast(t('ut.fehler', { text: err.message }), 'err'); return; }
    if (!this.anzeigen) this.anzeigeUmschalten();
    toast(t('ut.eigeneAn'), 'ok', { dauer: 3000 });
    this.m.steuerAktualisieren();
  }

  stoppen() {
    this.aktiv = false;
    const e = this.erkennung;
    this.erkennung = null;
    try { e?.abort(); } catch { /* egal */ }
  }

  async schicken(text, final, dauer) {
    try {
      await this.m.api(`/api/raeume/${this.m.code}/untertitel`, { methode: 'POST', json: { text: text.slice(0, 500), final, dauer_ms: Math.min(120000, Math.max(0, dauer)) } });
    } catch { /* einzelne Abschnitte dürfen fehlen */ }
  }

  menueEintraege() {
    const e = [{ text: this.anzeigen ? t('ut.ausblenden') : t('ut.anzeigen'), symbol: 'chat', an: this.anzeigen, aktion: () => this.anzeigeUmschalten() }];
    if (this.m.rolle !== 'zuschauer') {
      e.push({ text: this.aktiv ? t('ut.eigeneStoppen') : t('ut.eigeneStarten'), symbol: 'mic', an: this.aktiv, aktion: () => this.transkribierenUmschalten() });
    }
    if (this.m.raum.ist_host && this.m.ich) {
      e.push({ text: t('ut.speichern'), symbol: 'stift', an: this.transkript, aktion: () => this.speichernUmschalten() });
    }
    return e;
  }

  async speichernUmschalten() {
    try {
      const r = await api(`/api/raeume/${this.m.code}/transkript`, { methode: 'PATCH', json: { speichern: !this.transkript } });
      this.transkript = r.speichern;
      toast(r.speichern ? t('ut.speichernAn') : t('ut.speichernAus'), 'ok', { dauer: 3000 });
    } catch (e) { toast(e.message, 'err'); }
  }
}
