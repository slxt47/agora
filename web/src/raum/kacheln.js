// Bühne: Kacheln für Kamera und Bildschirmfreigabe jeder Person, Galerie- und Sprecheransicht (groß + Filmstreifen),
// Anheften / Hervorheben, Seiten bei vielen Teilnehmenden, eigenes Bild schwebend (verschiebbar) oder ausgeblendet,
// Sprecher-Ring, Verbindungsqualität, Hand, Reaktionen. Videos hängen nur an sichtbaren Kacheln → mit adaptiveStream
// abonniert LiveKit nur, was man sieht, in passender Größe.
import { Track } from 'livekit-client';
import { $, h } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import { initialen, galerieRaster, seite, kachelnProSeite } from '../helfer.js';
import { menue } from './menue.js';

const QUALITAET = { excellent: 'quali.sehrGut', good: 'quali.gut', poor: 'quali.schwach', lost: 'quali.weg', unknown: 'quali.unbekannt' };

class Kachel {
  constructor(buehne, p, quelle) {
    this.b = buehne;
    this.p = p;
    this.quelle = quelle;                         // 'kamera' | 'bildschirm'
    this.key = `${p.identity}:${quelle}`;
    this.gebunden = null;                         // aktuell angehängter Track
    this.video = h('video', { playsinline: true, autoplay: true, muted: true, disablepictureinpicture: quelle === 'kamera' && p.isLocal ? true : null });
    this.video.muted = true;                      // Ton läuft über eigene <audio>-Elemente
    this.avatarEl = h('span');
    this.nameEl = h('span');
    this.micEl = h('i', { class: 'kv-mic-icon' });
    this.menueKnopf = h('button', { type: 'button', class: 'kv-menue', html: icon('mehr'), onclick: (e) => { e.stopPropagation(); this.menueOeffnen(); } });
    this.el = h('div', { class: `kv ${quelle === 'kamera' ? 'kamera' : 'bildschirm'}`, dataset: { key: this.key }, role: 'group' },
      this.video,
      h('div', { class: 'kv-avatar', 'aria-hidden': 'true' }, this.avatarEl),
      h('div', { class: 'kv-hinweis' }, t('kachel.eigeneFreigabe')),
      h('div', { class: 'kv-oben' }, h('span', { class: 'kv-hand', 'aria-hidden': 'true' }, '✋'), h('span', { class: 'kv-pin', html: icon('pin') })),
      h('div', { class: 'kv-unten' }, h('span', { class: 'kv-name' }, this.micEl, this.nameEl)),
      h('div', { class: 'kv-qualitaet', role: 'img' }, h('i'), h('i'), h('i')),
      this.menueKnopf);
    if (p.isLocal && quelle === 'kamera') this.el.classList.add('spiegeln');
    if (p.isLocal && quelle === 'bildschirm') this.el.classList.add('eigene-freigabe');
    this.el.addEventListener('dblclick', () => this.b.anheften(this.b.angeheftet === this.key ? null : this.key));
    this.aktualisieren();
  }

  pub() { return this.p.getTrackPublication(this.quelle === 'kamera' ? Track.Source.Camera : Track.Source.ScreenShare); }

  /** Video anhängen, wenn sichtbar und vorhanden – sonst lösen. */
  binden() {
    const pub = this.pub();
    const track = this.p.isLocal && this.quelle === 'bildschirm' ? null : (pub && !pub.isMuted ? pub.track : null);
    if (track !== this.gebunden) {
      this.gebunden?.detach(this.video);
      this.gebunden = track || null;
      if (track) track.attach(this.video);
    }
    if (track && this.video.paused) this.video.play().catch(() => {});
    this.el.classList.toggle('mit-video', !!track);
  }

  loesen() {
    this.gebunden?.detach(this.video);
    this.gebunden = null;
    this.el.classList.remove('mit-video');
  }

  aktualisieren() {
    const m = this.b.m, p = this.p;
    const name = m.name(p);
    const selbst = p.isLocal;
    const anzeige = this.quelle === 'bildschirm' ? t('kachel.bildschirmVon', { name: selbst ? t('allg.du') : name }) : (selbst ? t('kachel.du', { name }) : name);
    this.nameEl.textContent = anzeige;
    this.avatarEl.textContent = initialen(name);
    this.avatarEl.style.background = m.farbe(p);
    const micAn = p.isMicrophoneEnabled;
    this.micEl.innerHTML = this.quelle === 'kamera' ? icon(micAn ? 'mic' : 'micAus', micAn ? '' : 'stumm') : icon('teilen');
    this.el.classList.toggle('hand', this.quelle === 'kamera' && p.attributes?.hand === '1');
    const q = p.connectionQuality || 'unknown';
    this.el.dataset.qualitaet = q;
    const qEl = this.el.querySelector('.kv-qualitaet');
    qEl.setAttribute('aria-label', t('quali.label', { wert: t(QUALITAET[q] || QUALITAET.unknown) }));
    qEl.title = qEl.getAttribute('aria-label');
    this.el.setAttribute('aria-label', [anzeige, this.quelle === 'kamera' && !micAn ? t('kachel.stumm') : '', p.attributes?.hand === '1' ? t('kachel.hand') : ''].filter(Boolean).join(', '));
    this.menueKnopf.setAttribute('aria-label', t('kachel.optionen', { name: anzeige }));
    this.el.classList.toggle('angeheftet', this.b.angeheftet === this.key);
    this.el.classList.toggle('spotlight', this.b.spotlightKey() === this.key);
    if (this.b.platziert.has(this.key)) this.binden();
  }

  menueOeffnen() {
    const b = this.b, m = b.m;
    const eintraege = [
      { text: b.angeheftet === this.key ? t('kachel.loesen') : t('kachel.anheften'), symbol: 'pin', aktion: () => b.anheften(b.angeheftet === this.key ? null : this.key) },
    ];
    if (m.istMod()) {
      const sp = b.spotlightKey() === this.key;
      eintraege.push({ text: sp ? t('kachel.spotlightAus') : t('kachel.spotlightAn'), symbol: 'stern', aktion: () => m.spotlightSetzen(sp ? null : this.key) });
    }
    if (this.gebunden && document.pictureInPictureEnabled && !(this.p.isLocal && this.quelle === 'kamera')) {
      eintraege.push({ text: t('kachel.bildImBild'), symbol: 'bildImBild', aktion: () => m.bildImBild(this.video) });
    }
    if (this.el.requestFullscreen) eintraege.push({ text: t('kachel.vollbild'), symbol: 'vollbild', aktion: () => this.el.requestFullscreen().catch(() => {}) });
    if (this.quelle === 'kamera') {
      const weitere = m.aktionenFuer(this.p);
      if (weitere.length) eintraege.push('-', ...weitere);
    }
    menue(this.menueKnopf, eintraege, { oben: false });
  }

  reaktion(emoji) {
    this.el.querySelector('.kv-reaktion')?.remove();
    const r = h('span', { class: 'kv-reaktion', 'aria-hidden': 'true' }, emoji);
    this.el.append(r);
    setTimeout(() => r.remove(), 4000);
  }
}

export class Buehne {
  constructor(m) {
    this.m = m;
    this.kacheln = new Map();
    this.platziert = new Set();
    this.ansicht = 'galerie';
    this.seiteNr = 0;
    this.angeheftet = null;
    this.spotlightIdentity = null;
    this.sprecherKey = null;
    this.sprecherSeit = 0;
    this.selbstAus = false;
    this.el = $('#buehne');
    this.gross = $('#gross');
    this.raster = $('#raster');
    this.selbstBox = $('#selbst');
    this.geplant = 0;
    new ResizeObserver(() => this.planen()).observe(this.el);
    $('#seite-zurueck').innerHTML = icon('pfeilLinks');
    $('#seite-weiter').innerHTML = icon('pfeilRechts');
    $('#seite-zurueck').setAttribute('aria-label', t('buehne.seiteZurueck'));
    $('#seite-weiter').setAttribute('aria-label', t('buehne.seiteWeiter'));
    $('#seite-zurueck').addEventListener('click', () => { this.seiteNr--; this.anordnen(); });
    $('#seite-weiter').addEventListener('click', () => { this.seiteNr++; this.anordnen(); });
    this.selbstVerschiebbar();
  }

  spotlightKey() { return this.spotlightIdentity ? `${this.spotlightIdentity}:kamera` : null; }

  /** Kacheln mit den Teilnehmenden im Raum abgleichen und neu anordnen. */
  sync() {
    const room = this.m.room;
    const soll = new Map();
    for (const p of [room.localParticipant, ...room.remoteParticipants.values()]) {
      soll.set(`${p.identity}:kamera`, [p, 'kamera']);
      if (p.getTrackPublication(Track.Source.ScreenShare)) soll.set(`${p.identity}:bildschirm`, [p, 'bildschirm']);
    }
    for (const [key, k] of this.kacheln) {
      if (!soll.has(key) || soll.get(key)[0] !== k.p) { k.loesen(); k.el.remove(); this.kacheln.delete(key); this.platziert.delete(key); }
    }
    for (const [key, [p, quelle]] of soll) if (!this.kacheln.has(key)) this.kacheln.set(key, new Kachel(this, p, quelle));
    if (this.angeheftet && !this.kacheln.has(this.angeheftet)) this.angeheftet = null;
    this.planen();
  }

  /** Teilnehmer-Zustand (Name, Mikro, Hand, Qualität, Video) neu zeichnen. */
  aktualisieren(p) {
    for (const k of this.kacheln.values()) if (!p || k.p === p) k.aktualisieren();
  }

  planen() {
    if (this.geplant) return;
    this.geplant = requestAnimationFrame(() => { this.geplant = 0; this.anordnen(); });
  }

  anheften(key) {
    this.angeheftet = key;
    this.seiteNr = 0;
    this.aktualisieren();
    this.anordnen();
    this.m.ansichtKnopf();
  }

  spotlight(identity) {
    this.spotlightIdentity = identity;
    this.aktualisieren();
    this.anordnen();
  }

  ansichtSetzen(a) {
    this.ansicht = a;
    if (a === 'galerie') this.angeheftet = null;
    this.seiteNr = 0;
    this.aktualisieren();
    this.anordnen();
  }

  /** Aktive Sprecher: Ring setzen, in der Sprecheransicht nach kurzer Haltezeit wechseln. */
  sprecher(liste) {
    const ids = new Set(liste.map((p) => p.identity));
    for (const k of this.kacheln.values()) k.el.classList.toggle('spricht', k.quelle === 'kamera' && ids.has(k.p.identity));
    const erster = liste.find((p) => !p.isLocal);
    if (!erster) return;
    const key = `${erster.identity}:kamera`;
    if (key === this.sprecherKey) return;
    const jetzt = performance.now();
    if (jetzt - this.sprecherSeit < 1500 && this.sprecherKey) { clearTimeout(this.sprecherWarten); this.sprecherWarten = setTimeout(() => this.sprecher(this.m.room.activeSpeakers), 1600); return; }
    this.sprecherKey = key;
    this.sprecherSeit = jetzt;
    if (this.effektiveAnsicht() === 'sprecher') this.planen();
    else this.sprecherNachVorne(key);
  }

  /** Galerie: wer spricht, aber auf einer anderen Seite ist, rückt auf Seite 1 (wie Zoom). */
  sprecherNachVorne(key) {
    if (this.seiteNr !== 0) return;
    const k = this.kacheln.get(key);
    if (k && !this.platziert.has(key)) { k.vorgezogen = performance.now(); this.planen(); }
  }

  effektiveAnsicht() {
    const bildschirm = [...this.kacheln.values()].some((k) => k.quelle === 'bildschirm');
    return this.ansicht === 'sprecher' || this.angeheftet || bildschirm || (this.spotlightIdentity && this.kacheln.has(this.spotlightKey())) ? 'sprecher' : 'galerie';
  }

  anordnen() {
    const m = this.m;
    if (!m.room) return;
    const alle = [...this.kacheln.values()];
    const selbstKey = `${m.room.localParticipant.identity}:kamera`;
    const selbst = this.kacheln.get(selbstKey);
    const modus = this.effektiveAnsicht();
    // Reihenfolge: eigenes Bild, Bildschirme, vorgezogene Sprecher, dann Beitrittsreihenfolge (Map-Reihenfolge)
    const sortiert = alle.slice().sort((a, b) => (b === selbst) - (a === selbst)
      || (b.quelle === 'bildschirm') - (a.quelle === 'bildschirm')
      || (b.vorgezogen || 0) - (a.vorgezogen || 0));

    let grossKey = null;
    if (modus === 'sprecher') {
      const bildschirme = alle.filter((k) => k.quelle === 'bildschirm');
      const fremdeBildschirme = bildschirme.filter((k) => !k.p.isLocal);
      grossKey = [this.angeheftet, this.kacheln.has(this.spotlightKey()) ? this.spotlightKey() : null,
        (fremdeBildschirme.at(-1) || bildschirme.at(-1))?.key,
        this.kacheln.has(this.sprecherKey) ? this.sprecherKey : null,
        alle.find((k) => !k.p.isLocal && k.quelle === 'kamera')?.key, selbstKey].find((k) => k && this.kacheln.has(k)) || null;   // beim Raumwechsel gibt es kurz keine Kachel
    }
    let rest = sortiert.filter((k) => k.key !== grossKey);
    let schwebend = null;
    if (selbst && grossKey !== selbstKey) {
      if (this.selbstAus && rest.length > 1) rest = rest.filter((k) => k !== selbst);
      else if (modus === 'sprecher' && !this.selbstAus) { schwebend = selbst; rest = rest.filter((k) => k !== selbst); }
    }

    // Seiten
    const breite = this.el.clientWidth, hoehe = this.el.clientHeight;
    const proSeite = modus === 'galerie' ? kachelnProSeite(breite) : (breite < 720 ? 8 : 12);
    const s = seite(rest, proSeite, this.seiteNr);
    this.seiteNr = s.seite;
    const neu = new Set([...(grossKey ? [grossKey] : []), ...s.eintraege.map((k) => k.key), ...(schwebend ? [schwebend.key] : [])]);

    // Nicht mehr sichtbare Kacheln lösen
    for (const key of this.platziert) if (!neu.has(key)) { const k = this.kacheln.get(key); k?.loesen(); k?.el.remove(); }
    this.platziert = neu;

    this.el.classList.toggle('galerie', modus === 'galerie');
    this.el.classList.toggle('sprecher', modus === 'sprecher');
    this.el.classList.toggle('ohne-streifen', modus === 'sprecher' && !s.eintraege.length);
    einsetzen(this.gross, grossKey ? [this.kacheln.get(grossKey).el] : []);
    einsetzen(this.raster, s.eintraege.map((k) => k.el));
    for (const k of s.eintraege) k.el.classList.toggle('klein', modus === 'sprecher' || s.eintraege.length > 9);
    if (grossKey) this.kacheln.get(grossKey).el.classList.remove('klein');

    // Schwebendes eigenes Bild
    this.selbstBox.hidden = !schwebend;
    if (schwebend) {
      if (!this.selbstZu) {
        this.selbstZu = h('button', { type: 'button', class: 'selbst-zu', 'aria-label': t('buehne.selbstAus'), title: t('buehne.selbstAus'), html: icon('augeAus'),
          onclick: (e) => { e.stopPropagation(); m.selbstUmschalten(); } });
      }
      einsetzen(this.selbstBox, [schwebend.el, this.selbstZu]);
      schwebend.el.classList.add('klein');
    }

    // Galerie-Raster berechnen
    const seitenleiste = $('#seiten');
    seitenleiste.hidden = s.seiten <= 1;
    $('#seite-text').textContent = t('buehne.seite', { nr: s.seite + 1, von: s.seiten });
    $('#seite-zurueck').disabled = s.seite === 0;
    $('#seite-weiter').disabled = s.seite >= s.seiten - 1;
    if (modus === 'galerie') {
      const r = galerieRaster(Math.max(1, s.eintraege.length), breite, hoehe - (s.seiten > 1 ? 52 : 0));
      this.raster.style.setProperty('--spalten', r.spalten);
      this.raster.style.setProperty('--kb', `${r.breite}px`);
    } else {
      this.raster.style.removeProperty('--spalten');
      this.raster.style.removeProperty('--kb');
    }
    for (const key of neu) this.kacheln.get(key)?.aktualisieren();
    m.ansichtKnopf();
  }

  reaktion(identity, emoji) {
    this.kacheln.get(`${identity}:kamera`)?.reaktion(emoji);
  }

  /** Das schwebende eigene Bild lässt sich ziehen und rastet an der nächsten Ecke ein. */
  selbstVerschiebbar() {
    const box = this.selbstBox;
    let start = null;
    box.addEventListener('pointerdown', (e) => {
      if (e.target.closest('button')) return;
      const r = box.getBoundingClientRect(), b = this.el.getBoundingClientRect();
      start = { x: e.clientX, y: e.clientY, l: r.left - b.left, t: r.top - b.top, b };
      box.setPointerCapture(e.pointerId);
      box.classList.add('zieht');
    });
    box.addEventListener('pointermove', (e) => {
      if (!start) return;
      const l = start.l + e.clientX - start.x, o = start.t + e.clientY - start.y;
      box.style.left = `${l}px`; box.style.top = `${o}px`; box.style.right = 'auto'; box.style.bottom = 'auto';
    });
    const ende = () => {
      if (!start) return;
      box.classList.remove('zieht');
      const r = box.getBoundingClientRect(), b = start.b;
      const links = r.left + r.width / 2 < b.left + b.width / 2, oben = r.top + r.height / 2 < b.top + b.height / 2;
      box.style.left = `${links ? 12 : b.width - r.width - 12}px`;
      box.style.top = `${oben ? 12 : b.height - r.height - 12}px`;
      start = null;
    };
    box.addEventListener('pointerup', ende);
    box.addEventListener('pointercancel', ende);
  }
}

/** Kinder eines Containers in die gewünschte Reihenfolge bringen – nur bewegen, was nicht schon stimmt. */
function einsetzen(container, els) {
  els.forEach((el, i) => { if (container.children[i] !== el) container.insertBefore(el, container.children[i] || null); });
  while (container.children.length > els.length) container.lastElementChild.remove();
}
