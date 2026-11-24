// Breakout-Räume im Client.
//  • Alle: Server-Ereignis {typ:"breakout"} → Zustand holen → bei (neuer) Zuteilung automatisch wechseln (Token vom
//    Server, Meeting.raumWechseln trennt den Hauptraum und verbindet den Breakout-Raum). Kopfzeile zeigt
//    „Breakout-Raum n“ + Timer, Knopf „Hilfe rufen“, Countdown beim Schließen, „Zurück zum Hauptraum“ (wenn erlaubt).
//  • Host/Co-Host: Dialog zum Anlegen (Anzahl, automatisch/von Hand, Timer, Rückkehr), Zuteilen per Ziehen oder Auswahl,
//    Öffnen, Räume besuchen, Nachricht an alle Räume, Schließen (60 s Countdown).
import { DisconnectReason } from 'livekit-client';
import { $, h, knopf, toast, blatt, bestaetigen } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import { initialen, laufzeitText } from '../helfer.js';
import { menue } from './menue.js';
import { wohin, groessen, vorschlagAnzahl } from './breakout-rechnen.js';
import './breakout.css';

export class Breakout {
  constructor(m) {
    this.m = m;
    this.z = null;                                   // Server-Zustand (oder null)
    this.empfangen = Date.now();                     // für Restzeiten (Server liefert Sekunden)
    this.lokal = { ort: 0, runde: null, zuteilung: null };
    this.wechselt = false;
    this.dlg = null;
    this.pille = h('button', { type: 'button', class: 'm-pille breakout-pille', id: 'm-breakout', hidden: true, onclick: () => this.pillenMenue() });
    this.hilfeKnopf = h('button', { type: 'button', class: 'm-pille breakout-hilfe', id: 'm-breakout-hilfe', hidden: true, onclick: () => this.hilfe() });
    this.hilfeKnopf.innerHTML = `${icon('hilfe')}<span class="lang"></span>`;
    this.hilfeKnopf.querySelector('.lang').textContent = t('breakout.hilfe');
    this.hilfeKnopf.setAttribute('aria-label', t('breakout.hilfe'));
    $('#m-verbindung').after(this.pille, this.hilfeKnopf);
  }

  restS(feld) {
    const v = this.z?.[feld];
    return v == null ? null : Math.max(0, Math.round(v - (Date.now() - this.empfangen) / 1000));
  }

  offen() { return !!this.z && (this.z.status === 'offen' || this.z.status === 'schliesst'); }

  darfZurueck() {
    const z = this.z;
    return this.lokal.ort > 0 && (!this.offen() || this.m.istMod() || z.rueckkehr || !z.mein_raum || z.status === 'schliesst');
  }

  // ---------------------------------------------------------------- Zustand
  async laden() {
    if (this.m.beendet || this.m.grund) return;              // nach dem Ende nichts mehr nachladen (sonst 403)
    try {
      const r = await this.m.api(`/api/raeume/${this.m.code}/breakout`);
      this.z = r.breakout || null;
      this.empfangen = Date.now();
    } catch { return; }
    this.anzeigen();
    if (this.dlg) this.dialogRendern();
    await this.abgleichen();
  }

  planen() {
    clearTimeout(this.ladeTimer);
    this.ladeTimer = setTimeout(() => this.laden(), 120);
  }

  ereignis(d) {
    if (d.typ === 'breakout') { this.planen(); return; }
    if (d.typ === 'breakout_hilfe' && this.m.istMod()) {
      toast(t('breakout.hilfeToast', { name: d.von_name || t('allg.unbekannt'), n: d.nr }), 'warn',
        { dauer: 0, aktion: Number.isInteger(d.nr) && d.nr > 0 ? { text: t('breakout.besuchen'), fn: () => this.wechseln(d.nr) } : undefined });
    } else if (d.typ === 'breakout_nachricht' && typeof d.text === 'string') {
      toast(t('breakout.nachrichtVon', { name: d.von_name || t('rolle.host'), text: d.text }), '', { dauer: 15000 });
    }
  }

  async abgleichen() {
    if (this.wechselt) return;
    const ziel = wohin(this.z, this.lokal);
    if (this.z) { this.lokal.runde = this.z.runde; this.lokal.zuteilung = this.z.mein_raum || 0; }
    if (ziel !== null) await this.wechseln(ziel);
  }

  /** In Raum nr wechseln (0 = Hauptraum). */
  async wechseln(nr) {
    if (this.wechselt || nr === this.lokal.ort) return;
    this.wechselt = true;
    this.dlg?.schliessen();
    if (nr) toast(t('breakout.wechselnNach', { n: nr }), '', { dauer: 2500 });
    try {
      const { zugang } = await this.m.api(`/api/raeume/${this.m.code}/breakout/wechseln`, { methode: 'POST', json: { nr } });
      await this.m.raumWechseln(zugang);
      this.lokal.ort = zugang.nr;
      toast(nr ? t('breakout.bistIn', { n: nr }) : t('breakout.bistZurueck'), 'ok', { dauer: 3500 });
    } catch (e) {
      toast(t('breakout.wechselFehler', { text: e?.message || '' }), 'err', { dauer: 7000 });
      if (this.m.room.state === 'disconnected') {          // weder alter noch neuer Raum verbunden
        if (nr !== 0) { this.wechselt = false; this.lokal.ort = -1; await this.wechseln(0); return; }
        this.m.getrennt(DisconnectReason.UNKNOWN_REASON);
      }
    } finally { this.wechselt = false; }
    this.anzeigen();
    this.laden();                                            // inzwischen Geändertes nachholen
  }

  /** Meeting meldet „getrennt“: wurde nur der Breakout-Raum gelöscht, zurück in den Hauptraum (true = erledigt). */
  async getrennt(grundLk, grund) {
    if (grund || this.lokal.ort <= 0 || grundLk !== DisconnectReason.ROOM_DELETED) return false;
    this.lokal.ort = -1;                                     // ≠ 0, damit wechseln(0) läuft
    await this.wechseln(0);
    return true;
  }

  async hilfe() {
    try {
      await this.m.api(`/api/raeume/${this.m.code}/breakout/hilfe`, { methode: 'POST' });
      toast(t('breakout.hilfeGesendet'), 'ok');
    } catch (e) { toast(e.message, 'err'); }
  }

  // ---------------------------------------------------------------- Kopfzeile, Countdown, Menüs
  anzeigen() {
    const z = this.z, ort = this.lokal.ort, mod = this.m.istMod();
    const sichtbar = this.offen() && (ort > 0 || mod);
    this.pille.hidden = !sichtbar;
    this.hilfeKnopf.hidden = !(this.offen() && ort > 0 && !mod);
    clearInterval(this.uhr);
    if (!sichtbar && !(this.offen() && ort > 0)) { this.m.bannerSetzen('breakout'); return; }
    const tick = () => {
      if (!this.offen()) return;
      const rest = z.status === 'schliesst' ? this.restS('schliesst_in_s') : this.restS('rest_s');
      const text = ort > 0 ? t('breakout.raumN', { n: ort }) : t('breakout.offenKurz');
      this.pille.replaceChildren(h('span', { html: icon('raeume') }), h('span', { class: 'lang' }, text), rest != null ? h('span', { class: 'zeit' }, laufzeitText(rest)) : null);
      this.pille.setAttribute('aria-label', [text, rest != null ? laufzeitText(rest) : ''].filter(Boolean).join(', '));
      this.pille.classList.toggle('warn', z.status === 'schliesst');
      if (z.status === 'schliesst' && ort > 0) {
        this.m.bannerSetzen('breakout', 'warn', t('breakout.schliesstIn', { zeit: laufzeitText(rest || 0) }),
          { text: t('breakout.jetztZurueck'), fn: () => this.wechseln(0) });
        if (rest === 0 && !this.nachgefragt) { this.nachgefragt = true; setTimeout(() => { this.nachgefragt = false; this.laden(); }, 4000); }
      } else this.m.bannerSetzen('breakout');
      if (this.dlg) this.dialogZeit();
    };
    tick();
    this.uhr = setInterval(tick, 1000);
  }

  menueEintraege() {
    const z = this.z, ort = this.lokal.ort, mod = this.m.istMod();
    const e = [];
    if (mod) e.push({ text: t('breakout.titel'), symbol: 'raeume', aktion: () => this.verwalten() });
    if (this.offen() && ort > 0 && !mod) e.push({ text: t('breakout.hilfe'), symbol: 'hilfe', aktion: () => this.hilfe() });
    if (this.darfZurueck()) e.push({ text: t('breakout.zurueck'), symbol: 'tuer', aktion: () => this.wechseln(0) });
    if (z?.status === 'offen' && ort === 0 && z.mein_raum && !mod) e.push({ text: t('breakout.betreten', { n: z.mein_raum }), symbol: 'raeume', aktion: () => this.wechseln(z.mein_raum) });
    return e;
  }

  pillenMenue() {
    const e = this.menueEintraege();
    if (e.length) menue(this.pille, e, { label: this.pille.getAttribute('aria-label'), oben: false });
  }

  // ---------------------------------------------------------------- Verwaltung (Host/Co-Host)
  verwalten() {
    if (!this.m.istMod()) return;
    const { inhalt, schliessen } = blatt(t('breakout.titel'), { breit: true, onclose: () => { this.dlg = null; } });
    inhalt.classList.add('breakout-dialog');
    this.dlg = { inhalt, schliessen, neu: false };
    this.dialogRendern();
    this.laden();
  }

  dialogRendern() {
    const d = this.dlg;
    if (!d) return;
    const fokus = document.activeElement?.dataset?.fokus;
    const z = this.z;
    const modus = !z || z.status === 'geschlossen' || (d.neu && z.status === 'entwurf') ? 'formular' : 'uebersicht';
    if (modus === 'formular' && d.modus === 'formular') return;      // Eingaben nicht wegwerfen
    d.modus = modus;
    d.inhalt.replaceChildren(...(modus === 'formular' ? this.formular() : this.uebersicht()));
    if (fokus) d.inhalt.querySelector(`[data-fokus="${CSS.escape(fokus)}"]`)?.focus();
  }

  dialogZeit() {
    const el = this.dlg?.inhalt.querySelector('.breakout-status');
    if (el) el.textContent = this.statusText();
  }

  statusText() {
    const z = this.z;
    if (!z) return '';
    if (z.status === 'schliesst') return t('breakout.status.schliesst', { zeit: laufzeitText(this.restS('schliesst_in_s') || 0) });
    if (z.status === 'offen') return z.rest_s != null ? t('breakout.status.offenTimer', { zeit: laufzeitText(this.restS('rest_s')) }) : t('breakout.status.offen');
    return t(`breakout.status.${z.status}`);
  }

  async aufruf(pfad, methode, json) {
    try {
      const r = await this.m.api(`/api/raeume/${this.m.code}/breakout${pfad}`, { methode, json });
      if (r.breakout !== undefined) { this.z = r.breakout; this.empfangen = Date.now(); }
      this.anzeigen();
      this.dialogRendern();
      return r;
    } catch (e) { toast(e.message, 'err'); return null; }
  }

  formular() {
    const personen = this.m.room?.remoteParticipants.size || 0;
    const anzahl = h('input', { type: 'number', name: 'anzahl', min: '2', max: '50', step: '1', required: true, value: String(this.z?.anzahl || vorschlagAnzahl(personen)), inputmode: 'numeric' });
    const vorschau = h('small', { 'aria-live': 'polite' });
    const neuVorschau = () => {
      const n = Math.floor(Number(anzahl.value));
      const g = [...new Set(groessen(personen, n >= 2 && n <= 50 ? n : 2))].sort((a, b) => a - b);
      vorschau.textContent = t('breakout.vorschau', { p: personen, liste: g.join('–') });
    };
    anzahl.addEventListener('input', neuVorschau);
    neuVorschau();
    const art = (wert, titel, hinweis, an) => h('label', { class: 'breakout-art' },
      h('input', { type: 'radio', name: 'art', value: wert, checked: an }), h('span', { class: 'txt' }, h('b', {}, titel), h('small', {}, hinweis)));
    const timerAn = h('input', { type: 'checkbox', role: 'switch', name: 'timer', checked: !!this.z?.dauer_min });
    const minuten = h('input', { type: 'number', name: 'minuten', min: '1', max: '240', step: '1', value: String(this.z?.dauer_min || 10), inputmode: 'numeric', 'aria-label': t('breakout.minuten') });
    const minutenFeld = h('label', { class: 'feld breakout-minuten', hidden: !timerAn.checked }, h('span', {}, t('breakout.minuten')), minuten);
    timerAn.addEventListener('change', () => { minutenFeld.hidden = !timerAn.checked; if (timerAn.checked) minuten.focus(); });
    const rueckkehr = h('input', { type: 'checkbox', role: 'switch', name: 'rueckkehr', checked: this.z ? !!this.z.rueckkehr : true });
    const status = h('div', { class: 'status err', role: 'alert', hidden: true });
    const f = h('form', { class: 'formular', novalidate: true },
      h('label', { class: 'feld' }, h('span', {}, t('breakout.anzahl')), anzahl, vorschau),
      h('fieldset', { class: 'ohne-rahmen' }, h('legend', {}, h('b', {}, t('breakout.zuteilung'))),
        h('div', { class: 'breakout-arten' }, art('auto', t('breakout.automatisch'), t('breakout.automatischHinweis'), true),
          art('hand', t('breakout.manuell'), t('breakout.manuellHinweis'), false))),
      h('label', { class: 'schalter' }, h('span', { class: 'txt' }, h('span', {}, t('breakout.timer')), h('small', {}, t('breakout.timerHinweis'))), timerAn),
      minutenFeld,
      h('label', { class: 'schalter' }, h('span', { class: 'txt' }, h('span', {}, t('breakout.rueckkehr')), h('small', {}, t('breakout.rueckkehrHinweis'))), rueckkehr),
      status,
      h('div', { class: 'knoepfe' }, h('button', { type: 'submit', class: 'btn primary', dataset: { fokus: 'erstellen' } }, t('breakout.erstellen'))));
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const n = Number(anzahl.value), min = Number(minuten.value);
      const fehler = (text, feld) => { status.textContent = text; status.hidden = false; feld.focus(); };
      if (!Number.isInteger(n) || n < 2 || n > 50) { fehler(t('breakout.zuWenige'), anzahl); return; }
      if (timerAn.checked && (!Number.isInteger(min) || min < 1 || min > 240)) { fehler(t('breakout.timerFalsch'), minuten); return; }
      if (this.dlg) this.dlg.neu = false;
      await this.aufruf('', 'POST', { anzahl: n, dauer_min: timerAn.checked ? min : null, rueckkehr: rueckkehr.checked,
        automatisch: f.querySelector('input[name=art]:checked')?.value === 'auto' });
    });
    setTimeout(() => { if (!document.activeElement?.closest?.('.breakout-dialog')) anzahl.focus(); });
    return [f];
  }

  uebersicht() {
    const z = this.z, offen = this.offen();
    const ichId = this.m.room?.localParticipant.identity;
    const spalten = [{ nr: 0, titel: t('breakout.ohneRaum'), teilnehmer: z.ohne_raum || [] },
      ...(z.raeume || []).map((r) => ({ nr: r.nr, titel: t('breakout.raumN', { n: r.nr }), teilnehmer: r.teilnehmer, anwesend: r.anwesend }))];
    const karten = spalten.map((s) => {
      const liste = h('ul', { class: 'breakout-leute' }, s.teilnehmer.map((p) => this.chip(p, z, ichId)));
      if (!s.teilnehmer.length) liste.append(h('li', { class: 'breakout-leer' }, t('breakout.leerRaum')));
      const kopf = h('div', { class: 'breakout-karte-kopf' }, h('h4', {}, s.titel),
        h('span', { class: 'breakout-zahl' }, t('breakout.personenN', { n: s.teilnehmer.length }) + (offen && s.nr ? ` · ${t('breakout.anwesendN', { n: s.anwesend || 0 })}` : '')));
      const fuss = offen && s.nr ? h('div', { class: 'breakout-karte-fuss' }, this.lokal.ort === s.nr
        ? h('span', { class: 'marker ok' }, t('breakout.duBistHier'))
        : h('button', { type: 'button', class: 'btn klein', dataset: { fokus: `besuchen-${s.nr}` }, onclick: () => this.wechseln(s.nr) }, t('breakout.besuchen'))) : null;
      const karte = h('section', { class: `breakout-karte${s.nr ? '' : ' ohne'}`, 'aria-label': s.titel, dataset: { nr: String(s.nr) } }, kopf, liste, fuss);
      karte.addEventListener('dragover', (e) => { if (z.status === 'entwurf' || z.status === 'offen') { e.preventDefault(); karte.classList.add('ziel'); } });
      karte.addEventListener('dragleave', () => karte.classList.remove('ziel'));
      karte.addEventListener('drop', (e) => {
        e.preventDefault(); karte.classList.remove('ziel');
        const id = e.dataTransfer.getData('text/plain');
        if (id) this.aufruf('/zuteilung', 'PUT', { zuteilung: { [id]: s.nr } });
      });
      return karte;
    });
    const knoepfe = [];
    if (z.status === 'entwurf') {
      knoepfe.push(knopf(t('breakout.neuAnlegen'), { klasse: 'btn', dataset: { fokus: 'neu' }, onclick: () => { this.dlg.neu = true; this.dialogRendern(); } }),
        knopf(t('breakout.verteilen'), { klasse: 'btn', dataset: { fokus: 'verteilen' }, onclick: () => this.aufruf('/zuteilung', 'PUT', { automatisch: true }) }),
        knopf(t('breakout.oeffnen'), { klasse: 'btn primary', symbol: 'raeume', dataset: { fokus: 'oeffnen' }, onclick: () => this.aufruf('/oeffnen', 'POST') }));
    } else if (z.status === 'offen') {
      if (this.lokal.ort > 0) knoepfe.push(knopf(t('breakout.zurueck'), { klasse: 'btn', symbol: 'tuer', onclick: () => this.wechseln(0) }));
      knoepfe.push(knopf(t('breakout.schliessen'), { klasse: 'btn gefahr', dataset: { fokus: 'schliessen' }, onclick: async () => {
        if (await bestaetigen(t('breakout.schliessen'), t('breakout.schliessenText'), { ja: t('breakout.schliessen'), gefahr: true })) this.aufruf('/schliessen', 'POST');
      } }));
    }
    const teile = [h('p', { class: 'breakout-status', role: 'status' }, this.statusText()), h('div', { class: 'breakout-raster' }, karten)];
    if (offen) teile.push(this.dlg.nachricht ||= this.nachrichtForm());     // bleibt beim Neuzeichnen erhalten
    if (knoepfe.length) teile.push(h('div', { class: 'knoepfe breakout-knoepfe' }, knoepfe));
    return teile;
  }

  chip(p, z, ichId) {
    const anderswo = this.offen() && p.ort > 0;
    const sel = h('select', { class: 'eingabe', 'aria-label': t('breakout.raumFuer', { name: p.name }), dataset: { fokus: `wahl-${p.identity}` },
      disabled: z.status !== 'entwurf' && z.status !== 'offen',
      onchange: (e) => this.aufruf('/zuteilung', 'PUT', { zuteilung: { [p.identity]: Number(e.target.value) } }) },
    h('option', { value: '0', selected: p.nr === 0 }, t('breakout.ohneRaum')),
    Array.from({ length: z.anzahl }, (_, i) => h('option', { value: String(i + 1), selected: p.nr === i + 1 }, t('breakout.raumN', { n: i + 1 }))));
    const li = h('li', { class: 'breakout-chip', draggable: 'true' },
      h('span', { class: 'mini-avatar', 'aria-hidden': 'true' }, initialen(p.name)),
      h('span', { class: 'wer' }, h('b', {}, p.identity === ichId ? `${p.name} (${t('allg.du')})` : p.name),
        anderswo ? h('small', { class: 'da' }, p.ort === p.nr ? t('breakout.istDa') : t('breakout.raumN', { n: p.ort })) : null),
      sel);
    li.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/plain', p.identity); e.dataTransfer.effectAllowed = 'move'; li.classList.add('zieht'); });
    li.addEventListener('dragend', () => li.classList.remove('zieht'));
    return li;
  }

  nachrichtForm() {
    const feld = h('input', { name: 'nachricht', maxlength: '500', placeholder: t('breakout.nachrichtPlatzhalter'), autocomplete: 'off', 'aria-label': t('breakout.nachricht') });
    const f = h('form', { class: 'breakout-nachricht' }, h('label', { class: 'feld' }, h('span', {}, t('breakout.nachricht')),
      h('div', { class: 'zeile' }, feld, h('button', { type: 'submit', class: 'btn klein primary' }, t('breakout.nachrichtSenden')))));
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = feld.value.trim();
      if (!text) { feld.focus(); return; }
      try {
        await this.m.api(`/api/raeume/${this.m.code}/breakout/nachricht`, { methode: 'POST', json: { text } });
        feld.value = '';
        toast(t('breakout.nachrichtGesendet'), 'ok', { dauer: 2500 });
      } catch (err) { toast(err.message, 'err'); }
    });
    return f;
  }
}
