// Seitenleiste „Notizen“: ein gemeinsamer Text je Meeting (Markdown light), automatisch gespeichert.
// Letzter Schreiber gewinnt mit Versionsprüfung (siehe app/notizen.py): Bei 409 werden beide Fassungen zeilenweise
// zusammengeführt (notizen-rechnen.js); überschneiden sie sich, erscheint ein Konflikt-Hinweis mit Wahl.
import { h, knopf, toast } from '../common.js';
import { t } from '../i18n.js';
import { uhrzeitText } from '../helfer.js';
import { zusammenfuehren, cursorNachher, bloecke } from './notizen-rechnen.js';

const WARTEN_MS = 700;

export function bloeckeDom(text) {
  const teile = (ts) => ts.map((x) => (x.fett ? h('b', {}, x.text) : x.code ? h('code', {}, x.text) : x.text));
  return bloecke(text).map((b) => (b.art === 'liste'
    ? h('ul', {}, b.punkte.map((p) => h('li', { class: p.erledigt === null ? '' : `aufgabe${p.erledigt ? ' erledigt' : ''}` },
      p.erledigt === null ? null : h('span', { class: 'kasten', 'aria-hidden': 'true' }, p.erledigt ? '☑' : '☐'), ...teile(p.teile))))
    : h(b.art === 'absatz' ? 'p' : { h1: 'h3', h2: 'h4', h3: 'h5' }[b.art], {}, ...teile(b.teile))));
}

export class Notizen {
  constructor(m, panel) {
    this.m = m;
    this.typen = ['notizen'];
    this.menueEintrag = { text: t('notizen.tab'), symbol: 'notizen' };
    this.basis = { text: '', version: 0, von_name: null, geaendert: null };
    this.sichtbar = false;
    this.feld = h('textarea', { class: 'notizen-feld', maxlength: '100000', spellcheck: 'true', 'aria-label': t('notizen.label'), placeholder: t('notizen.platzhalter') });
    this.vorschauEl = h('div', { class: 'notizen-vorschau', hidden: true, 'aria-live': 'off' });
    this.konfliktEl = h('div', { class: 'notizen-konflikt', role: 'alert', hidden: true });
    this.status = h('span', { class: 'notizen-status', 'aria-live': 'polite' });
    this.vorschauKnopf = knopf(t('notizen.vorschau'), { klasse: 'btn klein', 'aria-pressed': 'false', onclick: () => this.vorschauUmschalten() });
    const fuss = h('div', { class: 'notizen-fuss' }, this.status,
      h('div', { class: 'knoepfe' }, this.vorschauKnopf, knopf(t('notizen.export'), { klasse: 'btn klein', symbol: 'herunter', onclick: () => this.exportieren() })));
    panel.append(h('div', { class: 'panel-scroll notizen-flaeche' }, this.konfliktEl, this.feld, this.vorschauEl), fuss);
    this.feld.addEventListener('input', () => { this.statusSetzen('ungespeichert'); this.planen(); });
    this.feld.addEventListener('blur', () => { if (this.geaendert()) this.speichern(); });
  }

  geaendert() { return this.feld.value !== this.basis.text; }

  async laden() {
    try { this.uebernehmen(await this.m.api(`/api/raeume/${this.m.code}/notizen`), true); } catch { /* still */ }
  }

  /** Server-Stand übernehmen; eigene ungespeicherte Änderungen werden dabei eingemischt. */
  uebernehmen(s, ersetzen = false) {
    const alt = this.basis;
    if (!ersetzen && s.version <= alt.version) return;
    if (!this.geaendert() || ersetzen) this.wertSetzen(s.text);
    else {
      const z = zusammenfuehren(alt.text, this.feld.value, s.text);
      if (z.ok) this.wertSetzen(z.text);
      else { this.konflikt(s); return; }
    }
    this.basis = { ...s };
    this.statusSetzen(this.geaendert() ? 'ungespeichert' : 'gespeichert');
    if (this.geaendert()) this.planen();
    this.vorschauAktualisieren();
  }

  wertSetzen(text) {
    if (this.feld.value === text) return;
    const fokus = document.activeElement === this.feld;
    const a = this.feld.selectionStart, e = this.feld.selectionEnd, alt = this.feld.value;
    this.feld.value = text;
    if (fokus) this.feld.setSelectionRange(cursorNachher(alt, text, a), cursorNachher(alt, text, e));
  }

  planen() { clearTimeout(this.timer); this.timer = setTimeout(() => this.speichern(), WARTEN_MS); }

  async speichern() {
    clearTimeout(this.timer);
    if (this.laeuft) { this.nochmal = true; return; }
    if (!this.geaendert() || !this.konfliktEl.hidden) return;
    this.laeuft = true;
    const text = this.feld.value;
    this.statusSetzen('speichert');
    try {
      const r = await this.m.api(`/api/raeume/${this.m.code}/notizen`, { methode: 'PUT', json: { text, version: this.basis.version } });
      this.basis = r;
      this.statusSetzen(this.geaendert() ? 'ungespeichert' : 'gespeichert');
      if (this.geaendert()) this.nochmal = true;
    } catch (e) {
      if (e.status === 409 && e.daten?.konflikt) this.uebernehmen(e.daten.konflikt);
      else { this.statusSetzen('fehler'); toast(e.message, 'err'); }
    } finally {
      this.laeuft = false;
      if (this.nochmal) { this.nochmal = false; this.planen(); }
    }
  }

  konflikt(s) {
    this.deren = s;
    this.statusSetzen('konflikt');
    this.konfliktEl.replaceChildren(
      h('p', {}, t('notizen.konflikt', { name: s.von_name || t('allg.unbekannt') })),
      h('div', { class: 'knoepfe' },
        knopf(t('notizen.meineBehalten'), { klasse: 'btn klein primary', onclick: () => this.loesen(true) }),
        knopf(t('notizen.derenUebernehmen'), { klasse: 'btn klein', onclick: () => this.loesen(false) })));
    this.konfliktEl.hidden = false;
  }

  loesen(meine) {
    const s = this.deren;
    this.konfliktEl.hidden = true;
    this.basis = { ...s };
    if (!meine) this.wertSetzen(s.text);
    this.vorschauAktualisieren();
    if (this.geaendert()) this.speichern(); else this.statusSetzen('gespeichert');
  }

  /** Server-Ereignis {typ:"notizen", version, von, von_name, text?}. */
  ereignis(d) {
    if (d.von === this.m.room?.localParticipant.identity || d.version <= this.basis.version) return;
    if (typeof d.text === 'string') this.uebernehmen({ text: d.text, version: d.version, von_name: d.von_name, geaendert: new Date().toISOString() });
    else this.laden();
    if (!this.sichtbar) this.markieren(true);
  }

  markieren(an) {
    const tab = document.getElementById('tab-notizen');
    if (tab) { tab.classList.toggle('neu', an); tab.setAttribute('aria-label', an ? `${t('notizen.tab')} – ${t('notizen.neu')}` : t('notizen.tab')); }
  }

  zeigen(an) { this.sichtbar = an; if (an) this.markieren(false); }

  statusSetzen(art) {
    this.status.dataset.art = art;
    const b = this.basis;
    this.status.textContent = art === 'gespeichert'
      ? (b.von_name ? t('notizen.zuletzt', { name: b.von_name, zeit: uhrzeitText(b.geaendert) }) : t('notizen.gespeichert'))
      : t(`notizen.status.${art}`);
  }

  vorschauUmschalten() {
    const an = this.vorschauEl.hidden;
    this.vorschauEl.hidden = !an;
    this.feld.hidden = an;
    this.vorschauKnopf.setAttribute('aria-pressed', String(an));
    this.vorschauKnopf.querySelector('span').textContent = an ? t('notizen.bearbeiten') : t('notizen.vorschau');
    this.vorschauAktualisieren();
  }

  vorschauAktualisieren() {
    if (this.vorschauEl.hidden) return;
    const teile = bloeckeDom(this.feld.value);
    this.vorschauEl.replaceChildren(...(teile.length ? teile : [h('p', { class: 'chat-leer' }, t('notizen.leer'))]));
  }

  exportieren() {
    const blob = new Blob([this.feld.value.endsWith('\n') ? this.feld.value : `${this.feld.value}\n`], { type: 'text/markdown;charset=utf-8' });
    const a = h('a', { href: URL.createObjectURL(blob), download: `notizen-${this.m.code}.md` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }
}
