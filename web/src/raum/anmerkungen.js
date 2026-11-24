// Anmerkungen auf geteilten Bildschirmen: Zeichenebene (Canvas) über jeder Bildschirm-Kachel, skaliert mit dem
// sichtbaren Videobild (auch im Vollbild), Werkzeugleiste (Stift, Marker, Pfeil, Text, Radierer, Farben, löschen) mit
// Tastatur bedienbar. Striche gehen normiert (0…1) über den LiveKit-Datenkanal (Topic „anmerkung“, zuverlässig, beim
// Senden auf ~12 Nachrichten/s gebündelt, beim Empfang je Absender begrenzt). Angenommen wird nur von Teilnehmenden im
// Raum; ob jemand zeichnen darf, steht in den Raum-Metadaten (Recht „anmerkungen“, nur per Server änderbar).
// Die teilende Person sieht die Anmerkungen auf einer Vorschau ihres Bildschirms in ihrer eigenen Kachel.
import { RoomEvent, Track } from 'livekit-client';
import { h, toast } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import { jsonLesen } from '../helfer.js';
import { Begrenzer, FARBEN, GRENZEN, Striche, darf, inhaltRechteck, nachrichtPruefen, neueId, runden } from './anmerkungen-logik.js';

const TOPIC = 'anmerkung';
const kodieren = (o) => new TextEncoder().encode(JSON.stringify(o));
const WERKZEUG_SYMBOL = { stift: 'stift', marker: 'sparen', pfeil: 'pfeilRechts', text: 'tastatur', radierer: 'muell' };

class Ebene {
  /** Zeichenebene in einer Bildschirm-Kachel (s = identity der teilenden Person). */
  constructor(a, kachel) {
    this.a = a;
    this.kachel = kachel;
    this.s = kachel.p.identity;
    this.eigen = kachel.p.isLocal;
    this.striche = a.striche(this.s);
    this.canvas = h('canvas', { class: 'anm-flaeche', 'aria-hidden': 'true' });
    this.vorschau = this.eigen ? h('video', { class: 'anm-vorschau', muted: true, playsinline: true, autoplay: true }) : null;
    if (this.vorschau) this.vorschau.muted = true;
    this.knopf = h('button', { type: 'button', class: 'anm-knopf', html: icon('stift'), onclick: (e) => { e.stopPropagation(); a.zeichnenUmschalten(this); } });
    this.el = h('div', { class: 'anm-ebene', dataset: { striche: '0' } }, this.vorschau, this.canvas, this.knopf);
    kachel.el.append(this.el);
    this.beobachter = new ResizeObserver(() => this.zeichnen());
    this.beobachter.observe(kachel.el);
    this.videoEl().addEventListener('resize', this.neu = () => this.zeichnen());
    this.videoEl().addEventListener('loadedmetadata', this.neu);
    this.zeiger();
    this.aktualisieren();
  }

  videoEl() { return this.vorschau || this.kachel.video; }

  /** Rechteck des sichtbaren Bildes in Kachel-Pixeln. */
  rechteck() {
    const v = this.videoEl();
    return inhaltRechteck(this.kachel.el.clientWidth, this.kachel.el.clientHeight, v.videoWidth, v.videoHeight);
  }

  aktualisieren() {
    const darfIch = this.a.darfZeichnen(this.s);
    this.knopf.hidden = !darfIch && !this.aktiv;
    const label = this.aktiv ? t('anm.beenden') : t('anm.starten');
    this.knopf.setAttribute('aria-label', label); this.knopf.title = label;
    this.knopf.setAttribute('aria-pressed', String(!!this.aktiv));
    // Vorschau des eigenen Bildschirms nur, wenn es etwas zu sehen gibt (sonst Spiegel-im-Spiegel)
    if (this.vorschau) {
      const zeigen = this.aktiv || this.striche.groesse > 0;
      const track = this.kachel.p.getTrackPublication(Track.Source.ScreenShare)?.track;
      if (zeigen && track && this.gebunden !== track) { track.attach(this.vorschau); this.gebunden = track; }
      if (!zeigen && this.gebunden) { this.gebunden.detach(this.vorschau); this.gebunden = null; }
      this.el.classList.toggle('mit-vorschau', zeigen);
    }
    this.el.dataset.striche = String(this.striche.groesse);
    this.zeichnen();
  }

  zeichnen() {
    if (this.geplant) return;
    this.geplant = requestAnimationFrame(() => { this.geplant = 0; this.malen(); });
  }

  malen() {
    const r = this.rechteck();
    const dpr = Math.min(2, globalThis.devicePixelRatio || 1);
    Object.assign(this.canvas.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
    if (this.vorschau) Object.assign(this.vorschau.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
    const b = Math.max(1, Math.round(r.w * dpr)), hh = Math.max(1, Math.round(r.h * dpr));
    if (this.canvas.width !== b || this.canvas.height !== hh) { this.canvas.width = b; this.canvas.height = hh; }
    const c = this.canvas.getContext('2d');
    c.clearRect(0, 0, b, hh);
    const basis = Math.max(2, b * 0.003);
    for (const s of this.striche.m.values()) {
      c.save();
      c.strokeStyle = c.fillStyle = FARBEN[s.f];
      if (s.text !== undefined) {
        const groesse = Math.max(12, b * 0.022);
        c.font = `600 ${groesse}px -apple-system, "Segoe UI", Roboto, sans-serif`;
        c.textBaseline = 'top';
        c.lineWidth = Math.max(2, groesse / 6); c.strokeStyle = s.f === 5 ? '#ffffff' : 'rgba(0,0,0,.75)'; c.lineJoin = 'round';
        c.strokeText(s.text, s.x * b, s.y * hh);
        c.fillText(s.text, s.x * b, s.y * hh);
        c.restore();
        continue;
      }
      const p = s.p;
      if (!p.length) { c.restore(); continue; }
      c.lineCap = s.w === 'marker' ? 'butt' : 'round'; c.lineJoin = 'round';
      c.lineWidth = s.w === 'marker' ? basis * 5 : basis;
      if (s.w === 'marker') c.globalAlpha = 0.35;
      c.beginPath();
      c.moveTo(p[0][0] * b, p[0][1] * hh);
      for (const [x, y] of p.slice(1)) c.lineTo(x * b, y * hh);
      if (p.length === 1) c.lineTo(p[0][0] * b + 0.1, p[0][1] * hh);
      c.stroke();
      if (s.w === 'pfeil' && p.length > 1) {
        const [x1, y1] = [p[0][0] * b, p[0][1] * hh], [x2, y2] = [p.at(-1)[0] * b, p.at(-1)[1] * hh];
        const w = Math.atan2(y2 - y1, x2 - x1), l = basis * 6;
        c.beginPath();
        c.moveTo(x2, y2); c.lineTo(x2 - l * Math.cos(w - 0.45), y2 - l * Math.sin(w - 0.45));
        c.lineTo(x2 - l * Math.cos(w + 0.45), y2 - l * Math.sin(w + 0.45)); c.closePath(); c.fill();
      }
      c.restore();
    }
  }

  /** Zeiger-Ereignisse: zeichnen, Pfeil, Text setzen, radieren (nur im Zeichenmodus). */
  zeiger() {
    const pos = (e) => {
      const r = this.canvas.getBoundingClientRect();
      return [runden((e.clientX - r.left) / (r.width || 1)), runden((e.clientY - r.top) / (r.height || 1))];
    };
    this.canvas.addEventListener('pointerdown', (e) => {
      if (!this.aktiv || e.button > 0) return;
      e.preventDefault();
      const w = this.a.werkzeug, p = pos(e);
      if (w === 'text') { this.textFeld(p); return; }
      this.canvas.setPointerCapture(e.pointerId);
      if (w === 'radierer') { this.radieren(p); this.zug = { radierer: true }; return; }
      this.zug = { id: neueId(), w, p: [p], gesendet: 0 };
      this.a.strich(this, this.zug);
    });
    this.canvas.addEventListener('pointermove', (e) => {
      if (!this.zug) return;
      const p = pos(e);
      if (this.zug.radierer) { this.radieren(p); return; }
      const letzter = this.zug.p.at(-1);
      if (Math.hypot(p[0] - letzter[0], p[1] - letzter[1]) < 0.002) return;
      if (this.zug.w === 'pfeil') this.zug.p = [this.zug.p[0], p];
      else if (this.zug.p.length < GRENZEN.punkte) this.zug.p.push(p);
      this.a.strich(this, this.zug);
    });
    const ende = () => { if (this.zug && !this.zug.radierer) this.a.strich(this, this.zug, true); this.zug = null; };
    this.canvas.addEventListener('pointerup', ende);
    this.canvas.addEventListener('pointercancel', ende);
  }

  radieren([x, y]) {
    const ich = this.a.ich();
    const s = this.striche.treffer(x, y, 0.015, this.a.darfAlleLoeschen(this.s) ? null : ich);
    if (s) this.a.senden(this, { t: 'weg', s: this.s, id: s.id, von: s.von });
  }

  textFeld([x, y]) {
    this.textEl?.remove();
    const r = this.rechteck();
    const feld = h('input', { class: 'anm-text', maxlength: String(GRENZEN.text), 'aria-label': t('anm.textEingeben'), placeholder: t('anm.textEingeben') });
    Object.assign(feld.style, { left: `${r.x + x * r.w}px`, top: `${r.y + y * r.h}px` });
    const weg = () => { feld.remove(); this.textEl = null; };
    feld.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Escape') { weg(); this.a.leiste?.querySelector('[aria-pressed="true"]')?.focus(); }
      if (e.key === 'Enter') {
        const text = feld.value.trim();
        if (text) this.a.senden(this, { t: 'text', s: this.s, id: neueId(), f: this.a.farbe, x, y, text });
        weg();
      }
    });
    feld.addEventListener('blur', () => setTimeout(weg, 150));
    this.el.append(feld);
    this.textEl = feld;
    feld.focus();
  }

  entfernen() {
    cancelAnimationFrame(this.geplant);
    this.beobachter.disconnect();
    this.videoEl().removeEventListener('resize', this.neu);
    this.videoEl().removeEventListener('loadedmetadata', this.neu);
    if (this.gebunden) this.gebunden.detach(this.vorschau);
    this.el.remove();
  }
}

export class Anmerkungen {
  constructor(m) {
    this.m = m;
    this.erlaubt = true;
    this.werkzeug = 'stift';
    this.farbe = 0;
    this.daten = new Map();          // s → Striche
    this.ebenen = new Map();         // s → Ebene
    this.aktiv = null;               // Ebene im Zeichenmodus
    this.empfang = new Begrenzer();
    this.senden_ = new Begrenzer(12, 24);
    this.warteschlange = new Map();  // Strich-ID → {ebene, zug} (gebündelt alle 80 ms)
    this.verbinden(m.room);
    document.addEventListener('agora:raumwechsel', (e) => { this.alleWeg(); this.verbinden(e.detail.room); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && this.aktiv && !e.defaultPrevented) { e.preventDefault(); this.zeichnenUmschalten(this.aktiv); } });
  }

  verbinden(room) {
    room.on(RoomEvent.ParticipantDisconnected, (p) => { if (room === this.m.room) this.vonWeg(p.identity); });
  }

  ich() { return this.m.room.localParticipant.identity; }
  striche(s) { if (!this.daten.has(s)) this.daten.set(s, new Striche()); return this.daten.get(s); }
  istMod() { return this.m.istMod(); }
  darfZeichnen(s) { return this.erlaubt || this.istMod() || s === this.ich(); }
  darfAlleLoeschen(s) { return this.istMod() || s === this.ich(); }

  // ---------------------------------------------------------------- Kacheln (aus Buehne.anordnen)
  kachelnAktualisiert() {
    const b = this.m.buehne;
    const sichtbar = new Map();
    for (const k of b.kacheln.values()) if (k.quelle === 'bildschirm' && b.platziert.has(k.key)) sichtbar.set(k.p.identity, k);
    for (const [s, e] of this.ebenen) {
      if (sichtbar.get(s) !== e.kachel) { if (this.aktiv === e) this.zeichnenAus(); e.entfernen(); this.ebenen.delete(s); }
    }
    for (const [s, k] of sichtbar) if (!this.ebenen.has(s)) this.ebenen.set(s, new Ebene(this, k));
    // Bildschirmfreigabe beendet → Anmerkungen dazu verwerfen
    const geteilt = new Set([...b.kacheln.values()].filter((k) => k.quelle === 'bildschirm').map((k) => k.p.identity));
    for (const s of this.daten.keys()) if (!geteilt.has(s)) this.daten.delete(s);
  }

  alleWeg() {
    this.zeichnenAus();
    for (const e of this.ebenen.values()) e.entfernen();
    this.ebenen.clear(); this.daten.clear();
  }

  vonWeg(identity) {
    for (const [s, d] of this.daten) if (d.anwenden({ t: 'leeren', s, nur: identity })) this.ebenen.get(s)?.aktualisieren();
  }

  // ---------------------------------------------------------------- Recht (Raum-Metadaten / Server-Ereignis)
  erlaubtSetzen(an, melden) {
    const vorher = this.erlaubt;
    this.erlaubt = an !== false;
    if (melden && vorher !== this.erlaubt) toast(this.erlaubt ? t('anm.erlaubtAn') : t('anm.erlaubtAus'), '', { dauer: 3000 });
    if (this.aktiv && !this.darfZeichnen(this.aktiv.s)) this.zeichnenAus();
    for (const e of this.ebenen.values()) e.aktualisieren();
    this.leisteZeichnen();
  }

  // Server-Ereignis: immer melden (die Raum-Metadaten können vorher ankommen und den Wert schon still gesetzt haben)
  ereignis(d) {
    this.erlaubtSetzen(d.erlaubt, false);
    toast(this.erlaubt ? t('anm.erlaubtAn') : t('anm.erlaubtAus'), '', { dauer: 3000 });
  }

  async erlaubtUmschalten() {
    try {
      await this.m.api(`/api/raeume/${this.m.code}/anmerkungen`, { methode: 'POST', json: { erlaubt: !this.erlaubt } });
    } catch (e) { toast(e.message, 'err'); }
  }

  // ---------------------------------------------------------------- Zeichenmodus + Werkzeugleiste
  zeichnenUmschalten(ebene) {
    if (this.aktiv === ebene) { this.zeichnenAus(); ebene.knopf.focus(); return; }
    this.zeichnenAus();
    if (!this.darfZeichnen(ebene.s)) { toast(t('anm.nichtErlaubt'), 'warn'); return; }
    this.aktiv = ebene;
    ebene.aktiv = true;
    ebene.el.classList.add('zeichnet');
    this.leiste = h('div', { class: 'anm-leiste', role: 'toolbar', 'aria-label': t('anm.leiste'), 'aria-orientation': 'horizontal',
      onkeydown: (e) => this.leisteTaste(e), onpointerdown: (e) => e.stopPropagation() });
    ebene.el.append(this.leiste);
    this.leisteZeichnen();
    ebene.aktualisieren();
    this.leiste.querySelector('[aria-pressed="true"]')?.focus();
  }

  zeichnenAus() {
    const e = this.aktiv;
    if (!e) return;
    this.aktiv = null;
    e.aktiv = false;
    e.el.classList.remove('zeichnet');
    e.textEl?.remove();
    this.leiste?.remove();
    this.leiste = null;
    e.aktualisieren();
  }

  leisteZeichnen() {
    const l = this.leiste, e = this.aktiv;
    if (!l || !e) return;
    const fokus = document.activeElement?.dataset?.anm;
    const knopf = (key, sym, label, fn, gedrueckt) => h('button', { type: 'button', class: 'anm-werkzeug', tabindex: '-1', dataset: { anm: key },
      'aria-label': label, title: label, 'aria-pressed': gedrueckt === undefined ? null : String(gedrueckt), html: icon(sym), onclick: fn });
    const teile = ['stift', 'marker', 'pfeil', 'text', 'radierer'].map((w) => knopf(w, WERKZEUG_SYMBOL[w], t(`anm.w.${w}`), () => { this.werkzeug = w; this.leisteZeichnen(); }, this.werkzeug === w));
    teile.push(h('span', { class: 'anm-trenner', 'aria-hidden': 'true' }));
    FARBEN.forEach((f, i) => {
      const b = h('button', { type: 'button', class: 'anm-farbe', tabindex: '-1', dataset: { anm: `f${i}` }, 'aria-label': t(`anm.farbe.${i}`), title: t(`anm.farbe.${i}`),
        'aria-pressed': String(this.farbe === i), onclick: () => { this.farbe = i; this.leisteZeichnen(); } });
      b.style.setProperty('--f', f);
      teile.push(b);
    });
    teile.push(h('span', { class: 'anm-trenner', 'aria-hidden': 'true' }),
      knopf('meine', 'schliessen', t('anm.meineLoeschen'), () => this.senden(e, { t: 'leeren', s: e.s, nur: this.ich() })));
    if (this.darfAlleLoeschen(e.s)) {
      teile.push(knopf('alle', 'muell', t('anm.alleLoeschen'), () => this.senden(e, { t: 'leeren', s: e.s })),
        knopf('recht', this.erlaubt ? 'schlossOffen' : 'schloss', this.erlaubt ? t('anm.sperren') : t('anm.erlauben'), () => this.erlaubtUmschalten(), !this.erlaubt));
    }
    teile.push(knopf('fertig', 'haken', t('anm.beenden'), () => this.zeichnenUmschalten(e)));
    l.replaceChildren(...teile);
    l.dataset.werkzeug = this.werkzeug;
    e.canvas.dataset.werkzeug = this.werkzeug;
    const ziel = (fokus && l.querySelector(`[data-anm="${fokus}"]`)) || l.querySelector('[aria-pressed="true"]') || l.firstElementChild;
    ziel.tabIndex = 0;
    if (fokus) ziel.focus();
  }

  /** Werkzeugleiste: Pfeiltasten/Pos1/Ende bewegen den Fokus (roving tabindex), Esc beendet. */
  leisteTaste(e) {
    const knoepfe = [...this.leiste.querySelectorAll('button')];
    const i = knoepfe.indexOf(document.activeElement);
    let neu = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') neu = knoepfe[(i + 1) % knoepfe.length];
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') neu = knoepfe[(i - 1 + knoepfe.length) % knoepfe.length];
    else if (e.key === 'Home') neu = knoepfe[0];
    else if (e.key === 'End') neu = knoepfe.at(-1);
    if (neu) { e.preventDefault(); for (const k of knoepfe) k.tabIndex = -1; neu.tabIndex = 0; neu.focus(); }
    e.stopPropagation();       // Tastenkürzel des Meetings (M, V, …) nicht auslösen
  }

  // ---------------------------------------------------------------- Senden (gebündelt) und Empfangen
  /** Strich wird gezeichnet: lokal sofort, über das Netz alle 80 ms die neuen Punkte. */
  strich(ebene, zug, fertig = false) {
    const n = { t: 'strich', s: ebene.s, id: zug.id, w: zug.w, f: this.farbe, i: 0, p: zug.p };
    ebene.striche.anwenden(n, this.ich());
    ebene.aktualisieren();
    zug.f = this.farbe;
    this.warteschlange.set(zug.id, { ebene, zug });
    if (fertig) this.abschicken();
    else this.timer ||= setTimeout(() => this.abschicken(), 80);
  }

  abschicken() {
    clearTimeout(this.timer); this.timer = 0;
    for (const [id, { ebene, zug }] of this.warteschlange) {
      const ab = zug.w === 'pfeil' ? 0 : Math.max(0, zug.gesendet - 1);     // Pfeil: immer beide Punkte neu
      const p = zug.p.slice(ab, ab + 200);
      if (!p.length) { this.warteschlange.delete(id); continue; }
      if (!this.senden_.ok('ich')) { this.timer = setTimeout(() => this.abschicken(), 120); return; }
      this.netz({ t: 'strich', s: ebene.s, id, w: zug.w, f: zug.f, i: ab, p });
      zug.gesendet = ab + p.length;
      if (zug.gesendet >= zug.p.length || zug.w === 'pfeil') this.warteschlange.delete(id);
    }
    if (this.warteschlange.size) this.timer = setTimeout(() => this.abschicken(), 80);
  }

  /** Einzelne Nachricht (Text, Radierer, Löschen): lokal anwenden und senden. */
  senden(ebene, n) {
    if (!this.senden_.ok('ich')) { toast(t('anm.zuSchnell'), 'warn', { dauer: 2000 }); return; }
    ebene.striche.anwenden(n, this.ich());
    ebene.aktualisieren();
    this.netz(n);
  }

  netz(n) {
    const bytes = kodieren(n);
    if (bytes.length > GRENZEN.bytes) return;
    this.m.room.localParticipant.publishData(bytes, { reliable: true, topic: TOPIC }).catch(() => {});
  }

  /** Vom Datenkanal (nur mit Absender p im Raum – meeting.js prüft das und E2EE). */
  empfangen(daten, p) {
    if (!p || p.isLocal || daten.length > GRENZEN.bytes || !this.m.room.remoteParticipants.has(p.identity)) return;
    if (!this.empfang.ok(p.identity)) return;
    const n = nachrichtPruefen(jsonLesen(daten, null));
    if (!n) return;
    const praesentierend = n.s === this.ich() ? this.m.room.localParticipant : this.m.room.getParticipantByIdentity(n.s);
    if (!praesentierend?.isScreenShareEnabled) return;
    if (!darf(n, p.identity, { erlaubt: this.erlaubt, rolle: (id) => this.m.rolleVon(this.m.room.getParticipantByIdentity(id) || { identity: id }) })) return;
    if (this.striche(n.s).anwenden(n, p.identity)) this.ebenen.get(n.s)?.aktualisieren();
  }
}
