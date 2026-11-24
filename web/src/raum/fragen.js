// Seitenleiste „Fragen“ (Q&A): Fragen stellen (auf Wunsch anonym – auch der Host sieht dann keinen Namen), hochstimmen,
// sortieren nach Stimmen oder Zeit. Host/Co-Hosts markieren beantwortet (optional mit Antwort), heben hervor oder
// verwerfen. Ereignisse vom Server: {typ:"frage", aktion, frage} (Topic „agora“).
import { h, knopf, toast, blatt, bestaetigen } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import { uhrzeitText } from '../helfer.js';
import { fragenSortieren } from './fragen-rechnen.js';

export class Fragen {
  constructor(m, panel) {
    this.m = m;
    this.typen = ['frage'];
    this.menueEintrag = { text: t('fragen.tab'), symbol: 'fragen' };
    this.fragen = new Map();
    this.meine = new Set();             // ids eigener Fragen (Server-Ereignisse tragen keine identity)
    this.gestimmt = new Set();
    this.sortierung = 'stimmen';
    this.neu = 0;
    this.sichtbar = false;
    this.scroll = h('div', { class: 'panel-scroll' });
    this.feld = h('textarea', { rows: '2', maxlength: '500', name: 'frage', placeholder: t('fragen.platzhalter'), 'aria-label': t('fragen.deineFrage') });
    this.anonym = h('input', { type: 'checkbox', role: 'switch', name: 'anonym' });
    const senden = h('button', { type: 'submit', class: 'btn primary klein' }, t('fragen.stellen'));
    this.form = h('form', { class: 'fragen-form' }, this.feld,
      h('div', { class: 'fragen-form-zeile' },
        h('label', { class: 'fragen-anonym' }, this.anonym, h('span', {}, t('fragen.anonym'))), senden));
    this.form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = this.feld.value.trim();
      if (!text) { this.feld.focus(); return; }
      senden.disabled = true;
      try {
        const r = await this.m.api(this.url(), { methode: 'POST', json: { text, anonym: this.anonym.checked } });
        this.meine.add(r.frage.id);
        this.setzen(r.frage);
        this.feld.value = '';
        toast(this.anonym.checked ? t('fragen.gestelltAnonym') : t('fragen.gestellt'), 'ok', { dauer: 2500 });
      } catch (err) { toast(err.message, 'err'); } finally { senden.disabled = false; }
    });
    this.feld.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.form.requestSubmit(); } });
    this.sortWahl = h('div', { class: 'umschalter fragen-sortierung', role: 'radiogroup', 'aria-label': t('fragen.sortierung') },
      ['stimmen', 'zeit'].map((s) => h('label', {}, h('input', { type: 'radio', name: 'fragen-sort', value: s, checked: s === this.sortierung,
        onchange: () => { this.sortierung = s; this.render(); } }), h('span', {}, t(`fragen.sort.${s}`)))));
    panel.append(h('div', { class: 'fragen-kopf' }, this.sortWahl), this.scroll, h('div', { class: 'leute-fuss' }, this.form));
    panel.classList.add('fragen-panel');
  }

  url(p = '') { return `/api/raeume/${this.m.code}/fragen${p}`; }

  async laden() {
    try {
      const r = await this.m.api(this.url());
      this.fragen = new Map();
      for (const f of r.fragen) { if (f.meins) this.meine.add(f.id); if (f.ich_gestimmt) this.gestimmt.add(f.id); this.fragen.set(f.id, f); }
    } catch { /* still */ }
    this.render();
  }

  setzen(f) {
    if (f.meins !== undefined) { if (f.meins) this.meine.add(f.id); this.gestimmt[f.ich_gestimmt ? 'add' : 'delete'](f.id); }
    this.fragen.set(f.id, f);
    this.render();
  }

  ereignis(d) {
    const f = d.frage;
    if (!f || typeof f.id !== 'number') return;
    if (d.aktion === 'geloescht') this.fragen.delete(f.id);
    else {
      const neu = !this.fragen.has(f.id);
      this.fragen.set(f.id, f);
      if (neu && !this.meine.has(f.id) && !this.sichtbar && f.status === 'offen') { this.neu++; }
    }
    this.render();
  }

  zeigen(an) { this.sichtbar = an; if (an) { this.neu = 0; this.render(); } }
  rolleGeaendert() { this.laden(); }

  zaehler() {
    const tab = document.getElementById('tab-fragen');
    if (!tab) return;
    tab.textContent = this.neu ? `${t('fragen.tab')} (${this.neu})` : t('fragen.tab');
    tab.setAttribute('aria-label', this.neu ? `${t('fragen.tab')} – ${t('fragen.neuN', { n: this.neu })}` : t('fragen.tab'));
  }

  async aufruf(f, pfad, json, methode = 'POST') {
    try {
      const r = await this.m.api(this.url(`/${f.id}${pfad}`), { methode, json });
      if (r.frage) this.setzen(r.frage); else { this.fragen.delete(f.id); this.render(); }
    } catch (e) { toast(e.message, 'err'); }
  }

  render() {
    this.zaehler();
    const fokus = document.activeElement?.dataset?.fokus;
    const liste = fragenSortieren([...this.fragen.values()], this.sortierung);
    if (!liste.length) this.scroll.replaceChildren(h('p', { class: 'chat-leer' }, t('fragen.leer')));
    else this.scroll.replaceChildren(h('ol', { class: 'fragen-liste', 'aria-label': t('fragen.liste') }, liste.map((f) => this.karte(f))));
    if (fokus) this.scroll.querySelector(`[data-fokus="${CSS.escape(fokus)}"]`)?.focus();
  }

  karte(f) {
    const mod = this.m.istMod(), meins = this.meine.has(f.id), gestimmt = this.gestimmt.has(f.id);
    const wer = meins ? (f.anonym ? t('fragen.duAnonym') : t('allg.du')) : (f.anonym ? t('fragen.anonymWer') : f.name);
    const stimmKnopf = h('button', { type: 'button', class: `fragen-stimme${gestimmt ? ' an' : ''}`, 'aria-pressed': String(gestimmt), dataset: { fokus: `stimme-${f.id}` },
      disabled: meins || f.status === 'verworfen', 'aria-label': t('fragen.stimmeLabel', { n: f.stimmen }), title: meins ? t('fragen.eigene') : t('fragen.hochstimmen'),
      onclick: () => { this.gestimmt[gestimmt ? 'delete' : 'add'](f.id); this.aufruf(f, '/stimme', { an: !gestimmt }); } },
    h('span', { html: icon('daumen') }), h('b', {}, String(f.stimmen)));
    const marker = [
      f.hervorgehoben ? h('span', { class: 'marker warn' }, t('fragen.hervorgehoben')) : null,
      f.status === 'beantwortet' ? h('span', { class: 'marker ok' }, t('fragen.beantwortet')) : null,
      f.status === 'verworfen' ? h('span', { class: 'marker' }, t('fragen.verworfen')) : null,
    ];
    const k = [];
    const b = (text, fn, fokusName, klasse = 'btn klein') => h('button', { type: 'button', class: klasse, dataset: { fokus: `${fokusName}-${f.id}` }, onclick: fn }, text);
    if (mod) {
      if (f.status !== 'beantwortet') k.push(b(t('fragen.alsBeantwortet'), () => this.beantworten(f), 'beantworten', 'btn klein primary'));
      if (f.status !== 'verworfen') k.push(b(f.hervorgehoben ? t('fragen.nichtHervorheben') : t('fragen.hervorheben'), () => this.aufruf(f, '/moderieren', { aktion: f.hervorgehoben ? 'nicht_hervorheben' : 'hervorheben' }), 'hervorheben'));
      if (f.status === 'offen') k.push(b(t('fragen.verwerfen'), () => this.aufruf(f, '/moderieren', { aktion: 'verworfen' }), 'verwerfen'));
      else k.push(b(t('fragen.wiederOffen'), () => this.aufruf(f, '/moderieren', { aktion: 'offen' }), 'offen'));
    }
    if (meins && f.status === 'offen' && !mod) k.push(b(t('fragen.zurueckziehen'), () => this.zurueckziehen(f), 'weg'));
    return h('li', { class: `frage ${f.status}${f.hervorgehoben ? ' hervor' : ''}`, dataset: { frage: String(f.id) } },
      stimmKnopf,
      h('div', { class: 'frage-inhalt' },
        h('div', { class: 'frage-kopf' }, h('b', {}, wer), h('span', {}, uhrzeitText(f.erstellt)), ...marker),
        h('p', { class: 'frage-text' }, f.text),
        f.antwort ? h('p', { class: 'frage-antwort' }, h('b', {}, `${f.antwort_von || t('rolle.host')}: `), f.antwort) : null,
        k.length ? h('div', { class: 'frage-aktionen' }, k) : null));
  }

  beantworten(f) {
    const { inhalt, schliessen } = blatt(t('fragen.alsBeantwortet'));
    const feld = h('textarea', { name: 'antwort', rows: '3', maxlength: '1000', placeholder: t('fragen.antwortPlatzhalter') });
    const form = h('form', { class: 'formular' }, h('p', { class: 'sec' }, f.text),
      h('label', { class: 'feld' }, h('span', {}, t('fragen.antwortOptional')), feld),
      h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
        h('button', { type: 'submit', class: 'btn primary' }, t('fragen.alsBeantwortet'))));
    inhalt.append(form);
    feld.focus();
    form.addEventListener('submit', async (e) => { e.preventDefault(); await this.aufruf(f, '/moderieren', { aktion: 'beantwortet', antwort: feld.value.trim() }); schliessen(); });
  }

  async zurueckziehen(f) {
    if (!await bestaetigen(t('fragen.zurueckziehen'), f.text, { ja: t('fragen.zurueckziehen'), gefahr: true })) return;
    this.aufruf(f, '', undefined, 'DELETE');
  }
}
