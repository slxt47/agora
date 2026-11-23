// Melden und Blockieren im Meeting: Einträge im Aktionsmenü einer Person (Teilnehmerliste, Kachel),
// Melde-Dialog (Grund + optionaler Text), Blockieren (nur angemeldet, nur Personen mit Konto),
// Hinweis an Host/Co-Hosts, wenn jemand gemeldet wurde (Server-Ereignis „meldung“).
import './melden.css';
import { h, toast, blatt, beschaeftigt, bestaetigen } from '../common.js';
import { t } from '../i18n.js';
import { GRUENDE, kontoVon } from './melden-regeln.js';

export class Melden {
  constructor(m) { this.m = m; this.gemeldet = new Set(); }

  /** Menüeinträge für eine andere Person. */
  eintraege(p) {
    if (!p || p.isLocal) return [];
    const e = [{ text: t('melden.melden'), symbol: 'flagge', aktion: () => this.dialog(p) }];
    const ziel = kontoVon(p.identity);
    if (this.m.ich && ziel && ziel !== this.m.ich.id) e.push({ text: t('melden.blockieren'), symbol: 'blockieren', gefahr: true, aktion: () => this.blockieren(p) });
    return e;
  }

  dialog(p) {
    const name = this.m.name(p);
    if (this.gemeldet.has(p.identity)) { toast(t('melden.schonGemeldet', { name }), 'warn'); return; }
    const { inhalt, schliessen } = blatt(t('melden.titel', { name }));
    const st = h('div', { class: 'status', role: 'status', hidden: true });
    const f = h('form', { class: 'formular melden-form' },
      h('p', { class: 'sec klein' }, t('melden.text')),
      h('fieldset', { class: 'feld ohne-rahmen' }, h('legend', {}, h('b', {}, t('melden.grund'))),
        h('div', { class: 'melden-gruende' }, GRUENDE.map((g) => h('label', {},
          h('input', { type: 'radio', name: 'grund', value: g, required: true }), h('span', {}, t(`melden.grund.${g}`)))))),
      h('label', { class: 'feld' }, h('span', {}, t('melden.details')),
        h('textarea', { name: 'text', rows: '3', maxlength: '1000', placeholder: t('melden.detailsPlatzhalter') })),
      st,
      h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
        h('button', { type: 'submit', class: 'btn gefahr voll' }, t('melden.senden'))));
    inhalt.append(f);
    f.querySelector('input[name=grund]').focus();
    f.addEventListener('submit', (e) => {
      e.preventDefault();
      const grund = new FormData(f).get('grund');
      if (!grund) { f.reportValidity(); return; }
      beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
        await this.m.api(`/api/raeume/${this.m.code}/melden`, { methode: 'POST', json: { ziel: p.identity, grund, text: f.text.value.trim() } });
        this.gemeldet.add(p.identity);
        schliessen();
        toast(t('melden.danke'), 'ok', { dauer: 6000 });
        if (this.m.ich && kontoVon(p.identity) && kontoVon(p.identity) !== this.m.ich.id) {
          toast(t('melden.auchBlockieren', { name }), '', { dauer: 9000, aktion: { text: t('melden.blockieren'), fn: () => this.blockieren(p) } });
        }
      }, st);
    });
  }

  async blockieren(p) {
    const name = this.m.name(p);
    if (!await bestaetigen(t('melden.blockierenTitel', { name }), t('melden.blockierenText'), { ja: t('melden.blockieren'), gefahr: true })) return;
    try {
      await this.m.api(`/api/raeume/${this.m.code}/blockieren`, { methode: 'POST', json: { ziel: p.identity } });
      toast(t('melden.blockiert', { name }), 'ok');
    } catch (e) { toast(e.message, 'err'); }
  }

  /** Server-Ereignis an Host/Co-Hosts. */
  ereignis(d) {
    if (!this.m.istMod()) return;
    toast(t('melden.hostHinweis', { name: d.ziel_name || t('allg.unbekannt'), grund: t(`melden.grund.${d.grund}`) }), 'warn',
      { dauer: 12000, aktion: { text: t('meeting.ansehen'), fn: () => this.m.leisteOeffnen('leute') } });
  }
}
