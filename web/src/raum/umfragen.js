// Seitenleiste „Umfragen“: Host/Co-Host legen Umfragen an (Dialog), starten, beenden und teilen das Ergebnis;
// alle stimmen ab (Server erzwingt eine Stimme je Teilnahme). Live-Ergebnis nur für die Moderation, geteilte Ergebnisse
// für alle (nur Zahlen). Ereignisse kommen vom Server über Topic „agora“ ({typ:"umfrage", aktion, umfrage}).
import { $, h, knopf, toast, blatt, bestaetigen } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import { anteile, spitzenreiter, optionenPruefen } from './umfrage-rechnen.js';
import './umfragen.css';

export class Umfragen {
  constructor(m) {
    this.m = m;
    this.umfragen = new Map();      // id → umfrage (Server-Sicht + meine_auswahl)
    this.auswahl = new Map();       // id → Set noch nicht abgeschickter Auswahl (übersteht Neuzeichnen)
    this.sichtbar = false;
    const panel = $('#panel-umfragen');
    this.scroll = h('div', { class: 'panel-scroll' });
    this.fuss = h('div', { class: 'leute-fuss' });
    panel.append(this.scroll, this.fuss);
  }

  async laden() {
    try {
      const r = await this.m.api(`/api/raeume/${this.m.code}/umfragen`);
      this.umfragen = new Map((r.umfragen || []).map((u) => [u.id, u]));
    } catch { /* still */ }
    this.zaehlerSetzen();
    this.planen();
  }

  zeigen(an) { this.sichtbar = an; if (an) this.render(); }

  planen() {
    if (!this.sichtbar || this.geplant) return;
    this.geplant = requestAnimationFrame(() => { this.geplant = 0; this.render(); });
  }

  /** Offene Umfragen, bei denen ich noch nicht abgestimmt habe. */
  offen() { return this.m.istMod() ? 0 : [...this.umfragen.values()].filter((u) => u.status === 'laeuft' && !u.meine_auswahl).length; }

  zaehlerSetzen() {
    const n = this.offen();
    const tab = $('#tab-umfragen');
    if (tab) {
      tab.textContent = n ? `${t('umfrage.tab')} (${n})` : t('umfrage.tab');
      tab.setAttribute('aria-label', n ? `${t('umfrage.tab')} – ${t('umfrage.offenN', { n })}` : t('umfrage.tab'));
    }
  }

  /** Server-Ereignis {aktion, umfrage}. */
  ereignis(d) {
    const u = d.umfrage;
    if (!u || typeof u.id !== 'number') return;
    if (d.aktion === 'geloescht') { this.umfragen.delete(u.id); this.auswahl.delete(u.id); }
    else {
      const alt = this.umfragen.get(u.id);
      const neu = { ...u, meine_auswahl: 'meine_auswahl' in u ? u.meine_auswahl : (alt?.meine_auswahl ?? null) };
      if (!neu.ergebnis && alt?.ergebnis && this.m.istMod()) neu.ergebnis = alt.ergebnis;   // Live-Sicht nicht verlieren
      this.umfragen.set(u.id, neu);
      if (!this.m.istMod() && d.aktion === 'gestartet') {
        toast(t('umfrage.neuToast', { frage: u.frage }), '', { dauer: 8000, aktion: { text: t('umfrage.abstimmen'), fn: () => this.m.leisteOeffnen('umfragen') } });
        if (!this.m.leisteOffen) this.m.leisteOeffnen('umfragen', false);
      }
      if (!this.m.istMod() && d.aktion === 'geteilt' && this.m.leisteOffen !== 'umfragen') {
        toast(t('umfrage.ergebnisToast', { frage: u.frage }), '', { dauer: 8000, aktion: { text: t('umfrage.ansehen'), fn: () => this.m.leisteOeffnen('umfragen') } });
      }
    }
    this.zaehlerSetzen();
    this.planen();
  }

  /** Rolle geändert → Sicht neu laden (Entwürfe/Live-Ergebnis nur für Host/Co-Host). */
  rolleGeaendert() { this.laden(); }

  async aufruf(pfad, opts, okText) {
    try {
      const r = await this.m.api(`/api/raeume/${this.m.code}/umfragen${pfad}`, { methode: 'POST', ...opts });
      if (r.umfrage) this.umfragen.set(r.umfrage.id, r.umfrage);
      if (okText) toast(okText, 'ok', { dauer: 2500 });
      this.zaehlerSetzen();
      this.planen();
      return r;
    } catch (e) { toast(e.message, 'err'); return null; }
  }

  // ---------------------------------------------------------------- Darstellung
  render() {
    const m = this.m;
    const fokusId = document.activeElement?.dataset?.fokus;
    const liste = [...this.umfragen.values()].sort((a, b) => (a.status === 'laeuft' ? 0 : 1) - (b.status === 'laeuft' ? 0 : 1) || b.id - a.id);
    if (!liste.length) {
      this.scroll.replaceChildren(h('p', { class: 'chat-leer' }, m.istMod() ? t('umfrage.leerMod') : t('umfrage.leer')));
    } else {
      this.scroll.replaceChildren(h('ul', { class: 'umfragen-liste', 'aria-label': t('umfrage.liste') }, liste.map((u) => this.karte(u))));
    }
    this.fuss.hidden = !m.istMod();
    this.fuss.replaceChildren(...(m.istMod() ? [h('div', { class: 'knoepfe' },
      knopf(t('umfrage.neu'), { klasse: 'btn primary', symbol: 'plus', dataset: { fokus: 'neu' }, onclick: () => this.neuDialog() }))] : []));
    if (fokusId) (this.scroll.querySelector(`[data-fokus="${CSS.escape(fokusId)}"]`) || this.fuss.querySelector(`[data-fokus="${CSS.escape(fokusId)}"]`))?.focus();
  }

  karte(u) {
    const m = this.m;
    const titelId = `umfrage-${u.id}`;
    const status = h('span', { class: `marker ${u.status === 'laeuft' ? 'ok' : ''}` }, t(`umfrage.status.${u.status}`));
    const art = [u.mehrfach ? t('umfrage.mehrfachKurz') : t('umfrage.einfachKurz'), u.anonym ? t('umfrage.anonymKurz') : t('umfrage.namentlichKurz')].join(' · ');
    const teile = [h('div', { class: 'umfrage-kopf' }, h('h3', { id: titelId }, u.frage), h('div', { class: 'zeile' }, status,
      u.geteilt ? h('span', { class: 'marker' }, t('umfrage.geteilt')) : null, h('small', { class: 'umfrage-art' }, art)))];

    const darfAbstimmen = u.status === 'laeuft' && !u.meine_auswahl;
    if (m.istMod() && u.status !== 'entwurf') {
      // Moderation: Live-Ergebnis immer sichtbar; selbst abstimmen geht über „Selbst abstimmen“
      teile.push(this.ergebnis(u));
      if (darfAbstimmen) {
        const det = h('details', { class: 'umfrage-selbst', open: this.selbstOffen?.has(u.id) || null }, h('summary', {}, t('umfrage.selbst')), this.abstimmForm(u, titelId));
        det.addEventListener('toggle', () => { (this.selbstOffen ||= new Set())[det.open ? 'add' : 'delete'](u.id); });
        teile.push(det);
      }
    } else if (m.istMod()) teile.push(h('ol', { class: 'umfrage-vorschau' }, u.optionen.map((o) => h('li', {}, o))));
    else if (darfAbstimmen) teile.push(this.abstimmForm(u, titelId));
    else if (u.ergebnis) teile.push(this.ergebnis(u));
    else if (u.meine_auswahl) teile.push(h('p', { class: 'umfrage-hinweis' }, t('umfrage.wartenErgebnis')), this.eigeneWahl(u));
    else if (u.status === 'beendet') teile.push(h('p', { class: 'umfrage-hinweis' }, t('umfrage.vorbeiOhne')));

    if (m.istMod()) {
      const k = [];
      const b = (text, aktion, { klasse = 'btn klein', fokus } = {}) => h('button', { type: 'button', class: klasse, dataset: { fokus: `${fokus}-${u.id}` }, onclick: aktion }, text);
      if (u.status === 'entwurf') k.push(b(t('umfrage.starten'), () => this.aufruf(`/${u.id}/starten`), { klasse: 'btn klein primary', fokus: 'starten' }));
      if (u.status === 'laeuft') k.push(b(t('umfrage.beenden'), () => this.aufruf(`/${u.id}/beenden`), { fokus: 'beenden' }));
      if (u.status !== 'entwurf' && !u.geteilt) k.push(b(t('umfrage.teilen'), () => this.aufruf(`/${u.id}/teilen`), { klasse: `btn klein${u.status === 'beendet' ? ' primary' : ''}`, fokus: 'teilen' }));
      k.push(h('button', { type: 'button', class: 'icon-btn', 'aria-label': `${t('umfrage.loeschen')}: ${u.frage}`, title: t('umfrage.loeschen'), html: icon('muell'),
        dataset: { fokus: `loeschen-${u.id}` }, onclick: () => this.loeschen(u) }));
      teile.push(h('div', { class: 'umfrage-aktionen' }, k));
    }
    return h('li', { class: `umfrage ${u.status}`, 'aria-labelledby': titelId }, teile);
  }

  abstimmForm(u, titelId) {
    const gewaehlt = this.auswahl.get(u.id) || new Set();
    this.auswahl.set(u.id, gewaehlt);
    const typ = u.mehrfach ? 'checkbox' : 'radio';
    const name = `umfrage-${u.id}-wahl`;
    const fehlerEl = h('p', { class: 'umfrage-fehler', role: 'alert', hidden: true });
    const senden = h('button', { type: 'submit', class: 'btn klein primary', dataset: { fokus: `abstimmen-${u.id}` } }, t('umfrage.abstimmen'));
    const form = h('form', { class: 'umfrage-form' },
      h('fieldset', { class: 'ohne-rahmen' },
        h('legend', { class: 'nur-sr' }, u.frage),
        u.optionen.map((text, i) => h('label', { class: 'umfrage-option' },
          h('input', { type: typ, name, value: String(i), checked: gewaehlt.has(i), 'aria-describedby': titelId,
            onchange: (e) => {
              if (!u.mehrfach) gewaehlt.clear();
              if (e.target.checked) gewaehlt.add(i); else gewaehlt.delete(i);
              fehlerEl.hidden = true;
            } }),
          h('span', {}, text)))),
      fehlerEl, h('div', { class: 'knoepfe' }, senden));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!gewaehlt.size) { fehlerEl.textContent = t('umfrage.waehlen'); fehlerEl.hidden = false; return; }
      senden.disabled = true;
      const r = await this.aufruf(`/${u.id}/abstimmen`, { json: { auswahl: [...gewaehlt].sort((a, b) => a - b) } }, t('umfrage.abgestimmt'));
      if (r) this.auswahl.delete(u.id); else senden.disabled = false;
    });
    return form;
  }

  eigeneWahl(u) {
    return h('ul', { class: 'umfrage-eigene' }, (u.meine_auswahl || []).map((i) => h('li', {}, h('span', { html: icon('haken') }), u.optionen[i] ?? '')));
  }

  ergebnis(u) {
    const e = u.ergebnis || { stimmen: 0, zaehler: u.optionen.map(() => 0) };
    const p = anteile(e.zaehler, e.stimmen, u.mehrfach);
    const vorne = new Set(spitzenreiter(e.zaehler));
    const mein = new Set(u.meine_auswahl || []);
    const zeilen = u.optionen.map((text, i) => {
      const fuell = h('i');
      fuell.style.setProperty('--anteil', String((p[i] || 0) / 100));
      const namen = e.namen?.[i];
      return h('li', { class: `${vorne.has(i) ? 'vorne' : ''}${mein.has(i) ? ' mein' : ''}`.trim(),
        'aria-label': t('umfrage.zeileLabel', { text, n: e.zaehler[i] || 0, p: p[i] || 0 }) + (mein.has(i) ? ` (${t('umfrage.deineWahl')})` : '') },
      h('div', { class: 'umfrage-zeile', 'aria-hidden': 'true' },
        h('span', { class: 'umfrage-text' }, text, mein.has(i) ? h('span', { class: 'umfrage-mein' }, ` · ${t('umfrage.deineWahl')}`) : null),
        h('span', { class: 'umfrage-zahl' }, `${e.zaehler[i] || 0} · ${p[i] || 0} %`)),
      h('div', { class: 'umfrage-balken', 'aria-hidden': 'true' }, fuell),
      namen ? h('small', { class: 'umfrage-namen' }, namen.length ? namen.join(', ') : t('umfrage.niemand')) : null);
    });
    return h('div', { class: 'umfrage-ergebnis' },
      h('p', { class: 'umfrage-summe' }, t('umfrage.abgestimmtN', { n: e.stimmen || 0 })),
      h('ol', { 'aria-label': t('umfrage.ergebnis') }, zeilen));
  }

  async loeschen(u) {
    if (!await bestaetigen(t('umfrage.loeschenTitel'), t('umfrage.loeschenText', { frage: u.frage }), { ja: t('umfrage.loeschen'), gefahr: true })) return;
    try {
      await this.m.api(`/api/raeume/${this.m.code}/umfragen/${u.id}`, { methode: 'DELETE' });
      this.umfragen.delete(u.id);
      this.zaehlerSetzen();
      this.planen();
    } catch (e) { toast(e.message, 'err'); }
  }

  // ---------------------------------------------------------------- Neue Umfrage (Dialog)
  neuDialog() {
    const { inhalt, schliessen } = blatt(t('umfrage.neu'));
    const frage = h('input', { name: 'frage', maxlength: '300', required: true, placeholder: t('umfrage.fragePlatzhalter'), autocomplete: 'off' });
    const liste = h('ol', { class: 'umfrage-antworten' });
    const status = h('div', { class: 'status err', role: 'alert', hidden: true });
    const dazu = knopf(t('umfrage.antwortDazu'), { klasse: 'btn klein leise', symbol: 'plus' });
    const nummern = () => {
      [...liste.children].forEach((li, i) => {
        li.querySelector('input').setAttribute('aria-label', t('umfrage.antwortN', { n: i + 1 }));
        li.querySelector('input').placeholder = t('umfrage.antwortN', { n: i + 1 });
        const weg = li.querySelector('button');
        weg.setAttribute('aria-label', t('umfrage.antwortWeg', { n: i + 1 }));
        weg.disabled = liste.children.length <= 2;
      });
      dazu.disabled = liste.children.length >= 10;
    };
    const zeile = (wert = '') => {
      const feld = h('input', { maxlength: '200', value: wert, autocomplete: 'off' });
      const li = h('li', {}, feld, h('button', { type: 'button', class: 'icon-btn', html: icon('schliessen'), onclick: () => {
        const naechstes = li.nextElementSibling || li.previousElementSibling;
        li.remove(); nummern(); naechstes?.querySelector('input')?.focus();
      } }));
      liste.append(li);
      nummern();
      return feld;
    };
    zeile(); zeile();
    dazu.addEventListener('click', () => zeile().focus());
    const mehrfach = h('input', { type: 'checkbox', role: 'switch', name: 'mehrfach' });
    const anonym = h('input', { type: 'checkbox', role: 'switch', name: 'anonym', checked: true });
    const entwurf = h('button', { type: 'submit', class: 'btn', value: 'entwurf' }, t('umfrage.entwurf'));
    const starten = h('button', { type: 'submit', class: 'btn primary', value: 'starten' }, t('umfrage.speichernStarten'));
    const f = h('form', { class: 'formular', novalidate: true },
      h('label', { class: 'feld' }, h('span', {}, t('umfrage.frage')), frage),
      h('fieldset', { class: 'ohne-rahmen feld' }, h('legend', {}, h('span', { class: 'umfrage-legende' }, t('umfrage.antworten'))), liste, h('div', {}, dazu)),
      h('label', { class: 'schalter' }, h('span', { class: 'txt' }, h('span', {}, t('umfrage.mehrfach')), h('small', {}, t('umfrage.mehrfachHinweis'))), mehrfach),
      h('label', { class: 'schalter' }, h('span', { class: 'txt' }, h('span', {}, t('umfrage.anonym')), h('small', {}, t('umfrage.anonymHinweis'))), anonym),
      status,
      h('div', { class: 'knoepfe' }, entwurf, starten));
    inhalt.append(f);
    frage.focus();
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const art = e.submitter?.value || 'entwurf';
      const fehler = (text, feld) => { status.textContent = text; status.hidden = false; feld?.focus(); };
      if (!frage.value.trim()) { fehler(t('umfrage.frageFehlt'), frage); return; }
      const { optionen, fehler: fk } = optionenPruefen([...liste.querySelectorAll('input')].map((i) => i.value));
      if (fk) { fehler(t(fk), liste.querySelector('input')); return; }
      entwurf.disabled = starten.disabled = true;
      try {
        const r = await this.m.api(`/api/raeume/${this.m.code}/umfragen`, { methode: 'POST',
          json: { frage: frage.value.trim(), optionen, mehrfach: mehrfach.checked, anonym: anonym.checked } });
        this.umfragen.set(r.umfrage.id, r.umfrage);
        if (art === 'starten') await this.aufruf(`/${r.umfrage.id}/starten`);
        schliessen();
        this.m.leisteOeffnen('umfragen');
        this.planen();
      } catch (err) { fehler(err.message); entwurf.disabled = starten.disabled = false; }
    });
  }
}
