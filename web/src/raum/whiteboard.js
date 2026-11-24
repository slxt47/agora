// Gemeinsames Whiteboard als Bühnen-Ansicht: Stift, Linie, Pfeil, Rechteck, Ellipse, Text, Notizzettel, Radierer,
// Auswählen/Verschieben, Rückgängig/Wiederholen (nur eigene Schritte), Farben, Strichstärke, Zoom/Verschieben, PNG.
// Jede Änderung geht als Operation an den Server (POST …/whiteboard/ops); er nummeriert (seq) und verteilt sie über
// Topic „whiteboard“ (nur vom Server angenommen). Lücke in seq → Stand neu laden (GET). Eigene Schritte werden sofort
// angezeigt; das Echo vom Server ändert nichts mehr (neu/aendern setzen, loeschen entfernt).
import { $, h, toast, bestaetigen } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import {
  TAFEL, FARBEN, NOTIZ_FARBEN, BREITEN, neueId, vereinfachen, grenzen, verschoben, oberstes, zeichnen, textMass, textGroesse,
  exportCanvas, anwenden, gegenteil, passend, schrift,
} from './whiteboard-zeichnen.js';

const WERKZEUGE = ['auswahl', 'stift', 'linie', 'pfeil', 'rechteck', 'ellipse', 'text', 'notiz', 'radierer'];
const SYMBOL = { auswahl: 'zeiger', stift: 'stift', linie: 'linie', pfeil: 'pfeil', rechteck: 'rechteck', ellipse: 'ellipse', text: 'text', notiz: 'notiz', radierer: 'radierer' };
const ZOOM = [0.25, 4];

export class Whiteboard {
  constructor(m) {
    this.m = m;
    this.objekte = new Map();
    this.seq = 0;
    this.gesperrt = false;
    this.offen = false;
    this.werkzeug = 'stift';
    this.farbe = FARBEN[0];
    this.breite = BREITEN[1];
    this.sicht = { x: 0, y: 0, z: 1 };
    this.auswahl = null;
    this.undo = []; this.redo = [];
    this.warteschlange = [];
    this.vorschau = null;          // Objekt, das gerade entsteht (nur lokal)
    this.hinweisZeit = 0;
    this.bauen();
  }

  darfZeichnen() { return this.m.istMod() || !this.gesperrt; }

  // ---------------------------------------------------------------- Aufbau
  bauen() {
    const wk = WERKZEUGE.map((w) => h('button', { type: 'button', class: 'wb-knopf', dataset: { werkzeug: w }, 'aria-label': t(`wb.wz.${w}`), title: t(`wb.wz.${w}`),
      'aria-pressed': String(w === this.werkzeug), html: icon(SYMBOL[w]), onclick: () => this.werkzeugSetzen(w) }));
    const farben = FARBEN.map((f) => {
      const b = h('button', { type: 'button', class: 'wb-farbe', dataset: { farbe: f }, 'aria-label': t(`wb.farbe.${f.slice(1)}`), title: t(`wb.farbe.${f.slice(1)}`),
        'aria-pressed': String(f === this.farbe), onclick: () => this.farbeSetzen(f) });
      b.style.setProperty('--f', f);
      return b;
    });
    const breiten = BREITEN.map((b) => {
      const k = h('button', { type: 'button', class: 'wb-breite', dataset: { breite: String(b) }, 'aria-label': t('wb.breite', { n: b }), title: t('wb.breite', { n: b }),
        'aria-pressed': String(b === this.breite), onclick: () => this.breiteSetzen(b) }, h('i'));
      k.style.setProperty('--b', `${b + 2}px`);
      return k;
    });
    const kn = (sym, label, fn, extra = {}) => h('button', { type: 'button', class: 'wb-knopf', 'aria-label': label, title: label, html: icon(sym), onclick: fn, ...extra });
    this.undoKnopf = kn('rueckgaengig', t('wb.rueckgaengig'), () => this.rueckgaengig());
    this.redoKnopf = kn('wiederholen', t('wb.wiederholen'), () => this.wiederholen());
    this.zoomText = h('button', { type: 'button', class: 'wb-zoomtext', title: t('wb.zoomZurueck'), 'aria-label': t('wb.zoomZurueck'), onclick: () => this.einpassen() }, '100 %');
    this.modKnoepfe = h('div', { class: 'wb-gruppe' },
      this.sperrKnopf = kn('schloss', t('wb.sperren'), () => this.sperrenUmschalten(), { dataset: { aktion: 'sperren' } }),
      kn('leute', t('wb.zeigen'), () => this.fuerAlleZeigen(), { dataset: { aktion: 'zeigen' } }),
      kn('muell', t('wb.leeren'), () => this.leeren(), { dataset: { aktion: 'leeren' } }));
    this.werkzeugLeiste = h('div', { class: 'wb-leiste', role: 'toolbar', 'aria-label': t('wb.werkzeuge') },
      h('div', { class: 'wb-gruppe' }, wk),
      h('div', { class: 'wb-gruppe' }, farben),
      h('div', { class: 'wb-gruppe' }, breiten),
      h('div', { class: 'wb-gruppe' }, this.undoKnopf, this.redoKnopf));
    this.status = h('span', { class: 'wb-status', role: 'status' });
    const oben = h('div', { class: 'wb-oben' },
      h('b', { class: 'wb-titel' }, t('wb.titel')), this.status,
      h('div', { class: 'wb-rechts' }, this.modKnoepfe,
        kn('herunter', t('wb.export'), () => this.exportieren(), { dataset: { aktion: 'export' } }),
        kn('schliessen', t('wb.schliessen'), () => this.schliessen(), { dataset: { aktion: 'schliessen' } })));
    const zoom = h('div', { class: 'wb-zoom' },
      kn('zoomMinus', t('wb.zoomRaus'), () => this.zoomen(1 / 1.25)), this.zoomText, kn('zoomPlus', t('wb.zoomRein'), () => this.zoomen(1.25)));
    this.canvas = h('canvas', { class: 'wb-tafel', role: 'img', 'aria-label': t('wb.tafelLabel'), tabindex: '0' });
    this.el = h('section', { class: 'wb', hidden: true, 'aria-label': t('wb.titel') }, this.canvas, oben, this.werkzeugLeiste, zoom);
    $('#buehne').append(this.el);
    this.ctx = this.canvas.getContext('2d');
    new ResizeObserver(() => this.groesse()).observe(this.el);
    this.zeiger();
    this.el.addEventListener('keydown', (e) => this.taste(e));
    this.knoepfeAktualisieren();
  }

  groesse() {
    const r = this.el.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
    this.canvas.width = Math.max(1, Math.round(r.width * dpr));
    this.canvas.height = Math.max(1, Math.round(r.height * dpr));
    this.dpr = dpr;
    this.planen();
  }

  knoepfeAktualisieren() {
    const darf = this.darfZeichnen();
    for (const b of this.werkzeugLeiste.querySelectorAll('button')) {
      if (b.dataset.werkzeug) b.setAttribute('aria-pressed', String(b.dataset.werkzeug === this.werkzeug));
      if (b.dataset.farbe) b.setAttribute('aria-pressed', String(b.dataset.farbe === this.farbe));
      if (b.dataset.breite) b.setAttribute('aria-pressed', String(Number(b.dataset.breite) === this.breite));
      b.disabled = !darf && b.dataset.werkzeug !== 'auswahl';
    }
    this.undoKnopf.disabled = !darf || !this.undo.length;
    this.redoKnopf.disabled = !darf || !this.redo.length;
    this.modKnoepfe.hidden = !this.m.istMod();
    this.sperrKnopf.innerHTML = icon(this.gesperrt ? 'schlossOffen' : 'schloss');
    const sl = this.gesperrt ? t('wb.entsperren') : t('wb.sperren');
    this.sperrKnopf.setAttribute('aria-label', sl); this.sperrKnopf.title = sl;
    this.status.textContent = this.gesperrt ? (darf ? t('wb.gesperrtMod') : t('wb.nurAnsehen')) : '';
    this.status.hidden = !this.gesperrt;
    if (!darf && this.werkzeug !== 'auswahl') this.werkzeug = 'auswahl';
    this.canvas.dataset.werkzeug = this.werkzeug;
    this.zoomText.textContent = `${Math.round(this.sicht.z * 100)} %`;
  }

  werkzeugSetzen(w) { this.werkzeug = w; this.auswahl = null; this.knoepfeAktualisieren(); this.planen(); }
  farbeSetzen(f) {
    this.farbe = f;
    const o = this.auswahl && this.objekte.get(this.auswahl);
    if (o && this.darfZeichnen()) this.ausfuehren([{ art: 'aendern', objekt: { ...o, farbe: o.art === 'notiz' ? NOTIZ_FARBEN[f] : f } }]);
    this.knoepfeAktualisieren();
  }
  breiteSetzen(b) {
    this.breite = b;
    const o = this.auswahl && this.objekte.get(this.auswahl);
    if (o && o.art !== 'notiz' && this.darfZeichnen()) {
      const neu = { ...o, breite: b };
      if (o.art === 'text') Object.assign(neu, textMass(this.ctx, o.text, b));
      this.ausfuehren([{ art: 'aendern', objekt: neu }]);
    }
    this.knoepfeAktualisieren();
  }

  // ---------------------------------------------------------------- Öffnen/Schließen
  oeffnen() {
    if (this.offen) return;
    this.offen = true;
    this.el.hidden = false;
    $('#v-meeting').classList.add('wb-offen');
    this.knoepfeAktualisieren();
    this.groesse();
    this.canvas.focus({ preventScroll: true });
  }

  schliessen() {
    this.editorSchliessen(true);
    this.offen = false;
    this.el.hidden = true;
    $('#v-meeting').classList.remove('wb-offen');
    this.m.buehne?.planen?.();
  }

  umschalten() { if (this.offen) this.schliessen(); else this.oeffnen(); }

  // ---------------------------------------------------------------- Server
  url(p = '') { return `/api/raeume/${this.m.code}/whiteboard${p}`; }

  async laden() {
    try {
      const r = await this.m.api(this.url());
      this.objekte = new Map(r.objekte.map((o) => [o.id, o]));
      this.seq = r.seq;
      this.gesperrt = !!r.gesperrt;
      if (this.auswahl && !this.objekte.has(this.auswahl)) this.auswahl = null;
      this.knoepfeAktualisieren();
      this.planen();
    } catch { /* still – nächster Versuch beim nächsten Paket */ }
  }

  /** Paket vom Server (Topic „whiteboard“). */
  daten(d) {
    if (!d || typeof d !== 'object') return;
    const ich = this.m.room?.localParticipant.identity;
    if (d.typ === 'ops' && Array.isArray(d.ops)) {
      if (d.seq <= this.seq) return;                                  // schon bekannt (z. B. nach Neuladen)
      if (d.von_seq !== this.seq + 1) { this.laden(); return; }      // Lücke → ganzen Stand holen
      for (const op of d.ops) if (op && (op.objekt || op.id)) anwenden(this.objekte, op);
      this.seq = d.seq;
      if (this.auswahl && !this.objekte.has(this.auswahl)) this.auswahl = null;
      this.planen();
      if (d.von !== ich) this.hinweis(d.von_name);
    } else if (d.typ === 'neu_laden') {
      this.laden();
      if (d.von !== ich) this.hinweis(d.von_name);
    } else if (d.typ === 'geleert') {
      this.objekte.clear();
      this.seq = d.seq;
      this.undo = []; this.redo = []; this.auswahl = null;
      this.knoepfeAktualisieren();
      this.planen();
      if (this.offen) toast(t('wb.geleertVon', { name: d.von_name || '' }), '', { dauer: 3000 });
    } else if (d.typ === 'gesperrt') {
      this.gesperrt = !!d.an;
      this.knoepfeAktualisieren();
      if (!this.m.istMod()) toast(d.an ? t('wb.gesperrtToast') : t('wb.entsperrtToast'), '', { dauer: 3000 });
    } else if (d.typ === 'zeigen' && d.von !== ich) {
      this.oeffnen();
      toast(t('wb.gezeigtVon', { name: d.von_name || '' }), '', { dauer: 3500 });
    }
  }

  hinweis(name) {
    if (this.offen || Date.now() - this.hinweisZeit < 60000) return;
    this.hinweisZeit = Date.now();
    toast(t('wb.zeichnetToast', { name: name || t('allg.unbekannt') }), '', { dauer: 6000, aktion: { text: t('wb.oeffnen'), fn: () => this.oeffnen() } });
  }

  rolleGeaendert() { this.knoepfeAktualisieren(); }

  /** Operationen sofort lokal anwenden und (gebündelt, je 20) an den Server schicken. */
  senden(ops) {
    for (const op of ops) anwenden(this.objekte, op);
    this.warteschlange.push(...ops);
    this.planen();
    if (!this.sendet) this.abschicken();
  }

  async abschicken() {
    this.sendet = true;
    while (this.warteschlange.length) {
      const teil = this.warteschlange.splice(0, 20);
      try { await this.m.api(this.url('/ops'), { methode: 'POST', json: { ops: teil } }); } catch (e) {
        toast(e.message, 'err');
        this.warteschlange = [];
        await this.laden();                  // Server-Stand gilt
      }
    }
    this.sendet = false;
  }

  /** Eigener Schritt: hin-Operationen schicken und für Rückgängig merken. */
  ausfuehren(hin) {
    const kopie = new Map(this.objekte);
    const zurueck = [];
    for (const op of hin) { const g = gegenteil(kopie, op); if (g) zurueck.unshift(g); anwenden(kopie, op); }
    this.senden(hin);
    this.undo.push({ hin, zurueck });
    if (this.undo.length > 100) this.undo.shift();
    this.redo = [];
    this.knoepfeAktualisieren();
  }

  schritt(von, nach, feld) {
    const s = von.pop();
    if (!s) return;
    const ops = s[feld].map((op) => passend(this.objekte, op)).filter(Boolean);
    if (ops.length) this.senden(ops);
    nach.push(s);
    this.auswahl = null;
    this.knoepfeAktualisieren();
  }

  rueckgaengig() { if (this.darfZeichnen()) this.schritt(this.undo, this.redo, 'zurueck'); }
  wiederholen() { if (this.darfZeichnen()) this.schritt(this.redo, this.undo, 'hin'); }

  async sperrenUmschalten() {
    try {
      const r = await this.m.api(this.url('/sperren'), { methode: 'POST', json: { an: !this.gesperrt } });
      this.gesperrt = r.gesperrt;
      this.knoepfeAktualisieren();
      toast(this.gesperrt ? t('wb.gesperrtOk') : t('wb.entsperrtOk'), 'ok', { dauer: 2500 });
    } catch (e) { toast(e.message, 'err'); }
  }

  async leeren() {
    if (!await bestaetigen(t('wb.leerenTitel'), t('wb.leerenText'), { ja: t('wb.leeren'), gefahr: true })) return;
    try { await this.m.api(this.url('/leeren'), { methode: 'POST' }); } catch (e) { toast(e.message, 'err'); }
  }

  async fuerAlleZeigen() {
    try { await this.m.api(this.url('/zeigen'), { methode: 'POST' }); toast(t('wb.gezeigtOk'), 'ok', { dauer: 2500 }); } catch (e) { toast(e.message, 'err'); }
  }

  exportieren() {
    const c = exportCanvas([...this.objekte.values()], (w, hh) => Object.assign(document.createElement('canvas'), { width: w, height: hh }));
    if (!c) { toast(t('wb.leer'), 'warn'); return; }
    c.toBlob((blob) => {
      if (!blob) return;
      const a = h('a', { href: URL.createObjectURL(blob), download: `whiteboard-${this.m.code}.png` });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    }, 'image/png');
  }

  // ---------------------------------------------------------------- Ansicht
  welt(e) {
    const r = this.canvas.getBoundingClientRect();
    return { x: (e.clientX - r.left) / this.sicht.z + this.sicht.x, y: (e.clientY - r.top) / this.sicht.z + this.sicht.y };
  }

  zoomen(faktor, um) {
    const r = this.canvas.getBoundingClientRect();
    const px = um ? um.clientX - r.left : r.width / 2, py = um ? um.clientY - r.top : r.height / 2;
    const z = Math.min(ZOOM[1], Math.max(ZOOM[0], this.sicht.z * faktor));
    const wx = px / this.sicht.z + this.sicht.x, wy = py / this.sicht.z + this.sicht.y;
    this.sicht = { x: wx - px / z, y: wy - py / z, z };
    this.knoepfeAktualisieren();
    this.planen();
  }

  einpassen() { this.sicht = { x: 0, y: 0, z: 1 }; this.knoepfeAktualisieren(); this.planen(); }

  planen() {
    if (!this.offen || this.geplant) return;
    this.geplant = requestAnimationFrame(() => { this.geplant = 0; this.malen(); });
  }

  malen() {
    const c = this.ctx, { x, y, z } = this.sicht, d = this.dpr || 1;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.fillStyle = TAFEL;
    c.fillRect(0, 0, this.canvas.width, this.canvas.height);
    c.setTransform(d * z, 0, 0, d * z, -x * d * z, -y * d * z);
    // Punktraster (alle 40 Einheiten) zur Orientierung
    const w = this.canvas.width / d / z, hh = this.canvas.height / d / z;
    if (z > 0.4) {
      c.fillStyle = 'rgba(29, 29, 31, .12)';
      for (let gx = Math.floor(x / 40) * 40; gx < x + w; gx += 40) for (let gy = Math.floor(y / 40) * 40; gy < y + hh; gy += 40) c.fillRect(gx - 1, gy - 1, 2, 2);
    }
    for (const o of this.objekte.values()) if (o.id !== this.bearbeitet) zeichnen(c, this.ziehen?.id === o.id ? this.ziehen.jetzt : o);
    if (this.vorschau) zeichnen(c, this.vorschau);
    const sel = this.auswahl && (this.ziehen?.id === this.auswahl ? this.ziehen.jetzt : this.objekte.get(this.auswahl));
    if (sel) {
      const g = grenzen(sel), rand = 6 / z + sel.breite / 2;
      c.save();
      c.strokeStyle = '#6d4aff'; c.lineWidth = 1.5 / z; c.setLineDash([6 / z, 4 / z]);
      c.strokeRect(g.x1 - rand, g.y1 - rand, g.x2 - g.x1 + 2 * rand, g.y2 - g.y1 + 2 * rand);
      c.restore();
    }
  }

  // ---------------------------------------------------------------- Zeiger
  zeiger() {
    const cv = this.canvas;
    let aktiv = null;              // {art, id, start, ...}
    cv.addEventListener('pointerdown', (e) => {
      if (aktiv || (e.button !== 0 && e.button !== 1)) return;
      this.editorSchliessen(true);
      cv.setPointerCapture(e.pointerId);
      const p = this.welt(e);
      const darf = this.darfZeichnen();
      const w = e.button === 1 ? 'schieben' : this.werkzeug;
      if (w === 'auswahl' || w === 'schieben' || !darf) {
        const o = w === 'auswahl' && darf ? oberstes(this.objekte.values(), p.x, p.y, 6 / this.sicht.z) : null;
        this.auswahl = o?.id || null;
        aktiv = o ? { art: 'ziehen', start: p, o } : { art: 'schieben', sx: e.clientX, sy: e.clientY, sicht: { ...this.sicht } };
        if (o) this.ziehen = { id: o.id, jetzt: o };
        this.planen();
      } else if (w === 'radierer') {
        aktiv = { art: 'radieren', weg: [] };
        this.radieren(aktiv, p);
      } else if (w === 'stift') {
        aktiv = { art: 'stift', punkte: [p.x, p.y] };
        this.vorschau = { id: 'vorschau', art: 'stift', farbe: this.farbe, breite: this.breite, punkte: aktiv.punkte };
      } else if (w === 'text' || w === 'notiz') {
        aktiv = { art: 'klick' };
        const vorhanden = oberstes(this.objekte.values(), p.x, p.y, 4 / this.sicht.z);
        if (vorhanden && vorhanden.art === w) this.editor(vorhanden);
        else if (w === 'text') this.editor({ id: neueId(), art: 'text', farbe: this.farbe, breite: this.breite, x: p.x, y: p.y, w: 0, h: 0, text: '' }, true);
        else this.editor({ id: neueId(), art: 'notiz', farbe: NOTIZ_FARBEN[this.farbe], breite: 1, x: p.x - 100, y: p.y - 20, w: 200, h: 150, text: '' }, true);
      } else {
        aktiv = { art: 'form', start: p };
        this.vorschau = { id: 'vorschau', art: w, farbe: this.farbe, breite: this.breite, x: p.x, y: p.y, w: 0, h: 0 };
      }
    });
    cv.addEventListener('pointermove', (e) => {
      if (!aktiv) return;
      const p = this.welt(e);
      if (aktiv.art === 'schieben') {
        this.sicht = { ...aktiv.sicht, x: aktiv.sicht.x - (e.clientX - aktiv.sx) / this.sicht.z, y: aktiv.sicht.y - (e.clientY - aktiv.sy) / this.sicht.z };
      } else if (aktiv.art === 'ziehen') {
        this.ziehen.jetzt = verschoben(aktiv.o, p.x - aktiv.start.x, p.y - aktiv.start.y);
        aktiv.bewegt = true;
      } else if (aktiv.art === 'radieren') this.radieren(aktiv, p);
      else if (aktiv.art === 'stift') {
        for (const ev of e.getCoalescedEvents?.() || [e]) { const q = this.welt(ev); aktiv.punkte.push(q.x, q.y); }
      } else if (aktiv.art === 'form') {
        let dx = p.x - aktiv.start.x, dy = p.y - aktiv.start.y;
        if (e.shiftKey && (this.vorschau.art === 'rechteck' || this.vorschau.art === 'ellipse')) { const s = Math.max(Math.abs(dx), Math.abs(dy)); dx = Math.sign(dx) * s; dy = Math.sign(dy) * s; }
        this.vorschau.w = dx; this.vorschau.h = dy;
      }
      this.planen();
    });
    const ende = () => {
      if (!aktiv) return;
      const a = aktiv;
      aktiv = null;
      if (a.art === 'ziehen') {
        const jetzt = this.ziehen.jetzt;
        this.ziehen = null;
        if (a.bewegt) this.ausfuehren([{ art: 'aendern', objekt: jetzt }]);
      } else if (a.art === 'radieren' && a.weg.length) {
        for (const id of a.weg) this.objekte.set(id, a.vorher.get(id));   // Schritt als Ganzes (ein Rückgängig)
        this.ausfuehren(a.weg.map((id) => ({ art: 'loeschen', id })));
      } else if (a.art === 'stift') {
        this.ausfuehren([{ art: 'neu', objekt: { ...this.vorschau, id: neueId(), punkte: vereinfachen(a.punkte, 1.5 / this.sicht.z) } }]);
      } else if (a.art === 'form') {
        const v = this.vorschau;
        if (Math.hypot(v.w, v.h) * this.sicht.z > 4) {
          this.ausfuehren([{ art: 'neu', objekt: { ...v, id: neueId(), x: Math.round(v.x * 10) / 10, y: Math.round(v.y * 10) / 10, w: Math.round(v.w * 10) / 10, h: Math.round(v.h * 10) / 10 } }]);
        }
      }
      this.vorschau = null;
      this.planen();
    };
    cv.addEventListener('pointerup', ende);
    cv.addEventListener('pointercancel', ende);
    cv.addEventListener('dblclick', (e) => {
      if (!this.darfZeichnen()) return;
      const p = this.welt(e);
      const o = oberstes(this.objekte.values(), p.x, p.y, 6 / this.sicht.z);
      if (o && (o.art === 'text' || o.art === 'notiz')) this.editor(o);
    });
    cv.addEventListener('wheel', (e) => {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) this.zoomen(Math.exp(-e.deltaY / 300), e);
      else { this.sicht = { ...this.sicht, x: this.sicht.x + e.deltaX / this.sicht.z, y: this.sicht.y + e.deltaY / this.sicht.z }; this.planen(); }
    }, { passive: false });
  }

  radieren(a, p) {
    a.vorher ||= new Map(this.objekte);
    const o = oberstes(this.objekte.values(), p.x, p.y, 8 / this.sicht.z);
    if (!o) return;
    this.objekte.delete(o.id);            // nur lokal ausblenden; geschickt wird am Ende
    a.weg.push(o.id);
  }

  taste(e) {
    if (e.target.closest('textarea')) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); if (e.shiftKey) this.wiederholen(); else this.rueckgaengig(); }
    else if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); this.wiederholen(); }
    else if ((e.key === 'Delete' || e.key === 'Backspace') && this.auswahl && this.darfZeichnen()) {
      e.preventDefault();
      this.ausfuehren([{ art: 'loeschen', id: this.auswahl }]);
      this.auswahl = null;
    } else if (e.key === 'Escape') {
      e.preventDefault();
      if (this.auswahl) { this.auswahl = null; this.planen(); } else this.schliessen();
    }
  }

  // ---------------------------------------------------------------- Text und Notizen bearbeiten
  editor(o, neu = false) {
    this.editorSchliessen(true);
    if (!this.darfZeichnen()) return;
    const z = this.sicht.z;
    const notiz = o.art === 'notiz';
    const feld = h('textarea', { class: `wb-editor${notiz ? ' notiz' : ''}`, maxlength: '2000', 'aria-label': notiz ? t('wb.notizText') : t('wb.textEingabe'),
      placeholder: notiz ? t('wb.notizPlatzhalter') : t('wb.textPlatzhalter') });
    feld.value = o.text || '';
    const px = notiz ? 18 : textGroesse(o);
    Object.assign(feld.style, {
      left: `${(o.x - this.sicht.x) * z}px`, top: `${(o.y - this.sicht.y) * z}px`, font: schrift(px * z),
      color: notiz ? '#1d1d1f' : o.farbe, background: notiz ? o.farbe : 'transparent',
      width: notiz ? `${o.w * z}px` : `${Math.max(160, (o.w || 0) + 40) * z}px`, height: notiz ? `${o.h * z}px` : `${Math.max(px * 1.4, (o.h || 0) + px) * z}px`,
      padding: notiz ? `${12 * z}px` : '0',
    });
    this.el.append(feld);
    this.bearbeitet = o.id;
    this.offenerEditor = { feld, o, neu };
    this.planen();
    // Erst nach dem Klick fokussieren: sonst holt sich die Tafel (mousedown) den Fokus und der Editor schließt sofort
    setTimeout(() => { if (this.offenerEditor?.feld !== feld) return; feld.focus(); feld.addEventListener('blur', () => this.editorSchliessen(true)); }, 0);
    feld.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' || (e.key === 'Enter' && (e.ctrlKey || e.metaKey))) { e.preventDefault(); e.stopPropagation(); this.editorSchliessen(true); this.canvas.focus(); }
    });
  }

  editorSchliessen(speichern) {
    const ed = this.offenerEditor;
    if (!ed) return;
    this.offenerEditor = null;
    this.bearbeitet = null;
    ed.feld.remove();
    const text = ed.feld.value.replace(/\s+$/, '');
    if (speichern) {
      const neu = { ...ed.o, text };
      if (neu.art === 'text') Object.assign(neu, textMass(this.ctx, text, neu.breite));
      if (!text && neu.art === 'text') { if (!ed.neu) this.ausfuehren([{ art: 'loeschen', id: neu.id }]); }
      else if (ed.neu) this.ausfuehren([{ art: 'neu', objekt: neu }]);
      else if (text !== (ed.o.text || '')) this.ausfuehren([{ art: 'aendern', objekt: neu }]);
    }
    this.planen();
  }
}
