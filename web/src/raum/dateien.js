// Seitenleiste „Dateien“: im Meeting Dateien teilen (bis 25 MB, zusammen 200 MB), Liste mit Bildvorschau, Herunterladen
// nur für Teilnehmende (X-Teilnahme – deshalb per fetch → Blob, kein direkter Link). Hinweis, ob der Server auf Viren
// prüft. Ereignisse: {typ:"datei", aktion: neu|geloescht, datei} (Topic „agora“).
import { h, knopf, toast, hochladen, bestaetigen } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import { groesseText, uhrzeitText } from '../helfer.js';
import { dateiPruefen } from './dateien-rechnen.js';

export class Dateien {
  constructor(m, panel) {
    this.m = m;
    this.typen = ['datei'];
    this.menueEintrag = { text: t('dateien.tab'), symbol: 'datei' };
    this.dateien = new Map();
    this.meine = new Set();
    this.vorschauen = new Map();      // id → blob:-URL
    this.info = { belegt: 0, grenze_meeting: 200 * 1024 ** 2, grenze_datei: 25 * 1024 ** 2, virenpruefung: false, endungen: [] };
    this.neu = 0;
    this.sichtbar = false;
    this.hinweis = h('p', { class: 'dateien-hinweis' });
    this.scroll = h('div', { class: 'panel-scroll' });
    this.auswahl = h('input', { type: 'file', multiple: true, hidden: true, onchange: () => { this.alleHochladen([...this.auswahl.files]); this.auswahl.value = ''; } });
    this.fortschritt = h('div', { class: 'dateien-laufend', 'aria-live': 'polite' });
    this.belegtEl = h('small', { class: 'dateien-belegt' });
    panel.append(h('div', { class: 'dateien-kopf' }, this.hinweis), this.scroll,
      h('div', { class: 'leute-fuss' }, this.fortschritt,
        h('div', { class: 'knoepfe' }, knopf(t('dateien.hochladen'), { klasse: 'btn primary', symbol: 'plus', onclick: () => this.auswahl.click() })),
        this.belegtEl, this.auswahl));
    panel.classList.add('dateien-panel');
    panel.addEventListener('dragover', (e) => { if (e.dataTransfer?.types.includes('Files')) { e.preventDefault(); panel.classList.add('ziehen'); } });
    panel.addEventListener('dragleave', (e) => { if (!panel.contains(e.relatedTarget)) panel.classList.remove('ziehen'); });
    panel.addEventListener('drop', (e) => { e.preventDefault(); panel.classList.remove('ziehen'); this.alleHochladen([...e.dataTransfer.files]); });
  }

  url(p = '') { return `/api/raeume/${this.m.code}/dateien${p}`; }

  async laden() {
    try {
      const r = await this.m.api(this.url());
      this.info = r;
      this.dateien = new Map(r.dateien.map((d) => [d.id, d]));
      for (const d of r.dateien) if (d.meins) this.meine.add(d.id);
    } catch { /* still */ }
    this.render();
  }

  ereignis(d) {
    const x = d.datei;
    if (!x?.id) return;
    if (d.aktion === 'geloescht') { this.dateien.delete(x.id); this.vorschauWeg(x.id); this.info.belegt = [...this.dateien.values()].reduce((s, y) => s + y.groesse, 0); }
    else if (!this.dateien.has(x.id)) {
      this.dateien.set(x.id, x);
      this.info.belegt += x.groesse;
      if (!this.meine.has(x.id) && !this.sichtbar) {
        this.neu++;
        toast(t('dateien.neuToast', { name: x.von_name, datei: x.name }), '', { dauer: 6000, aktion: { text: t('dateien.ansehen'), fn: () => this.m.leisteOeffnen('dateien') } });
      }
    }
    this.render();
  }

  zeigen(an) { this.sichtbar = an; if (an) { this.neu = 0; this.render(); } }

  vorschauWeg(id) { const u = this.vorschauen.get(id); if (u) URL.revokeObjectURL(u); this.vorschauen.delete(id); }

  async alleHochladen(liste) {
    for (const f of liste) {
      const fehler = dateiPruefen(f, this.info);
      if (fehler) { toast(t(fehler.text, fehler.werte), 'err', { dauer: 6000 }); continue; }
      await this.eineHochladen(f);
    }
  }

  async eineHochladen(f) {
    const balken = h('progress', { max: '100', value: '0', 'aria-label': t('dateien.laedtHoch', { name: f.name }) });
    const zeile = h('div', { class: 'dateien-zeile-laufend' }, h('span', {}, f.name), balken);
    this.fortschritt.append(zeile);
    try {
      const { promise } = hochladen(this.url(), f, { 'X-Teilnahme': this.m.zugang.teilnahme, 'X-Dateiname': encodeURIComponent(f.name), 'Content-Type': 'application/octet-stream' },
        (anteil) => { balken.value = Math.round(anteil * 100); });
      const r = await promise;
      this.meine.add(r.datei.id);
      if (!this.dateien.has(r.datei.id)) { this.dateien.set(r.datei.id, r.datei); this.info.belegt += r.datei.groesse; }
      toast(t('dateien.geteilt', { name: f.name }), 'ok', { dauer: 2500 });
      this.render();
    } catch (e) { toast(e.message, 'err', { dauer: 7000 }); } finally { zeile.remove(); }
  }

  async blob(d, vorschau = false) {
    const r = await fetch(this.url(`/${d.id}${vorschau ? '?vorschau=1' : ''}`), { headers: { 'X-Teilnahme': this.m.zugang.teilnahme }, credentials: 'same-origin', cache: 'no-store' });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).fehler || t('fehler.status', { status: r.status }));
    return r.blob();
  }

  async herunterladen(d) {
    try {
      const b = await this.blob(d);
      const a = h('a', { href: URL.createObjectURL(b), download: d.name });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    } catch (e) { toast(e.message, 'err'); }
  }

  async vorschauLaden(d, img) {
    if (this.vorschauen.has(d.id)) { img.src = this.vorschauen.get(d.id); return; }
    try {
      const u = URL.createObjectURL(await this.blob(d, true));
      this.vorschauen.set(d.id, u);
      img.src = u;
    } catch { img.remove(); }
  }

  async loeschen(d) {
    if (!await bestaetigen(t('dateien.loeschenTitel'), d.name, { ja: t('dateien.loeschen'), gefahr: true })) return;
    try { await this.m.api(this.url(`/${d.id}`), { methode: 'DELETE' }); this.ereignis({ aktion: 'geloescht', datei: { id: d.id } }); } catch (e) { toast(e.message, 'err'); }
  }

  render() {
    const tab = document.getElementById('tab-dateien');
    if (tab) tab.textContent = this.neu ? `${t('dateien.tab')} (${this.neu})` : t('dateien.tab');
    this.hinweis.replaceChildren(h('span', { html: icon(this.info.virenpruefung ? 'schild' : 'info') }),
      h('span', {}, this.info.virenpruefung ? t('dateien.geprueft') : t('dateien.ungeprueft')));
    this.hinweis.classList.toggle('warn', !this.info.virenpruefung);
    this.belegtEl.textContent = t('dateien.belegt', { belegt: groesseText(this.info.belegt), grenze: groesseText(this.info.grenze_meeting) });
    const liste = [...this.dateien.values()].sort((a, b) => (b.erstellt || '').localeCompare(a.erstellt || ''));
    if (!liste.length) { this.scroll.replaceChildren(h('p', { class: 'chat-leer' }, t('dateien.leer'))); return; }
    this.scroll.replaceChildren(h('ul', { class: 'dateien-liste', 'aria-label': t('dateien.liste') }, liste.map((d) => {
      const darf = this.meine.has(d.id) || this.m.istMod();
      let bild = null;
      if (d.bild && this.sichtbar) { bild = h('img', { class: 'dateien-bild', alt: '', loading: 'lazy' }); this.vorschauLaden(d, bild); }
      return h('li', { class: 'datei', dataset: { datei: d.id } },
        bild || h('span', { class: 'dateien-symbol', html: icon('datei') }),
        h('div', { class: 'datei-text' },
          h('b', { class: 'datei-name', title: d.name }, d.name),
          h('small', {}, [groesseText(d.groesse), d.von_name, uhrzeitText(d.erstellt)].filter(Boolean).join(' · ')),
          d.gefaehrlich ? h('small', { class: 'datei-warnung' }, t('dateien.gefaehrlich')) : null),
        h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('dateien.herunterladenName', { name: d.name }), title: t('dateien.herunterladen'),
          html: icon('herunter'), dataset: { aktion: 'laden' }, onclick: () => this.herunterladen(d) }),
        darf ? h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('dateien.loeschenName', { name: d.name }), title: t('dateien.loeschen'),
          html: icon('muell'), onclick: () => this.loeschen(d) }) : null);
    })));
  }
}
