// Fernsteuerung im Meeting (app/fernsteuerung.py, Desktop-App desktop/src/main.js):
// • Teilende Person – nur in der Desktop-App (window.agoraDesktop.fernsteuerung): meldet „bereit“, solange der eigene
//   Bildschirm geteilt wird, reicht Anfragen an den Dialog der App weiter (die App entscheidet, nicht die Seite), gibt
//   Raum-Metadaten und empfangene Datenpakete (Absender = LiveKit-identity, verschlüsselt ja/nein) an die App.
// • Andere: „Steuerung anfragen“ im Menü einer geteilten Bildschirm-Kachel. Mit Freigabe (Raum-Metadaten) liegt eine
//   Eingabe-Ebene über der Kachel: Maus, Rad und Tasten gehen als Datenpakete (zuverlässig, Topic „fernsteuerung“) nur
//   an die teilende Person. Esc gibt die Steuerung zurück. Anzeige „Du steuerst …“ als Banner.
import { RoomEvent, Track } from 'livekit-client';
import { h, toast } from '../common.js';
import { t } from '../i18n.js';
import { Drossel, TOPIC, entpacken, knopf, meineSteuerung, normiert, rollen, tastenEreignis, verpacken } from './fernsteuerung-logik.js';
import { fernsteuerungSchluessel } from '../e2ee-schluessel.js';
import './fernsteuerung.css';

const kodieren = (o) => new TextEncoder().encode(JSON.stringify(o));

export class Fernsteuerung {
  constructor(m, bruecke = globalThis.agoraDesktop?.fernsteuerung) {
    this.m = m;
    this.bruecke = bruecke && typeof bruecke.freigabe === 'function' ? bruecke : null;
    this.bereit = [];             // identities, die Fernsteuerung anbieten (Metadaten)
    this.steuerung = null;        // {von, an_name} – ich steuere
    this.ebene = null;
    this.drossel = new Drossel(33);
    this.ichBereit = false;
    // E2EE: Eingaben zusätzlich selbst verschlüsseln (siehe e2ee-schluessel.js); Warteschlangen halten die Reihenfolge
    this.schluessel = m.schluessel ? fernsteuerungSchluessel(m.schluessel).catch(() => null) : null;
    this.sendeKette = Promise.resolve();
    this.empfangsKette = Promise.resolve();
    this.bruecke?.beiEnde?.((grund) => this.beendenServer(grund));
    document.addEventListener('agora:raumwechsel', (e) => { this.stoppen(false); this.verbinden(e.detail.room); });
    if (m.room) this.verbinden(m.room);
  }

  verbinden(room) {
    const aktiv = () => room === this.m.room;
    room.on(RoomEvent.LocalTrackPublished, (pub) => { if (aktiv() && pub.source === Track.Source.ScreenShare) this.eigeneFreigabe(true); })
      .on(RoomEvent.LocalTrackUnpublished, (pub) => { if (aktiv() && pub.source === Track.Source.ScreenShare) this.eigeneFreigabe(false); })
      .on(RoomEvent.Disconnected, () => { if (aktiv()) this.eigeneFreigabe(false); });
  }

  // ---------------------------------------------------------------- Teilende Person (Desktop-App)
  async eigeneFreigabe(an) {
    if (!this.bruecke) return;
    this.bruecke.freigabe(an, { ich: this.m.zugang.identity, e2ee: !!this.m.schluessel });
    if (an === this.ichBereit) return;
    this.ichBereit = an;
    if (an && !this.bruecke.verfuegbar?.()) return;           // App ohne Eingabe-Adapter: nichts anbieten
    // Der Server prüft die Bildschirmspur bei LiveKit – direkt nach dem Veröffentlichen kennt er sie evtl. noch nicht
    for (let versuch = 0; versuch < 5 && an === this.ichBereit; versuch++) {
      try {
        const r = await this.m.api(`/api/raeume/${this.m.code}/fernsteuerung`, { methode: 'POST', json: { aktion: 'bereit', an } });
        if (r.ok !== false || !an) return;
      } catch { return; }
      await new Promise((r) => setTimeout(r, 1500));
    }
  }

  async anfrageBekommen(d) {
    const ablehnen = () => this.m.api(`/api/raeume/${this.m.code}/fernsteuerung`, { methode: 'POST', json: { aktion: 'ablehnen', an: d.von } }).catch(() => {});
    if (!this.bruecke) { ablehnen(); return; }
    let antwort = null;
    try { antwort = await this.bruecke.anfrage(d.von, d.name || ''); } catch { antwort = null; }
    if (!antwort?.ok) { ablehnen(); return; }
    try {
      await this.m.api(`/api/raeume/${this.m.code}/fernsteuerung`, { methode: 'POST', json: { aktion: 'erteilen', an: d.von, kennung: antwort.kennung } });
    } catch (e) { toast(e.message, 'err'); this.bruecke.beenden(); }
  }

  /** Datenpaket (Topic „fernsteuerung“): nur weiterreichen – prüfen tut die Desktop-App (Absender, Freigabe, E2EE).
   *  Absender ist der LiveKit-Teilnehmer (vom Medienserver gesetzt), nie ein Feld im Inhalt. Im E2EE-Meeting gilt ein Paket
   *  nur als verschlüsselt, wenn es sich mit dem Fernsteuerungs-Schlüssel und dieser identity entschlüsseln lässt. */
  empfangen(daten, p) {
    if (!this.bruecke || !p || p.isLocal || !(daten instanceof Uint8Array)) return;
    const identity = p.identity;
    this.empfangsKette = this.empfangsKette.then(async () => {
      const s = this.schluessel ? await this.schluessel : null;
      const klar = s ? await entpacken(s, daten, identity) : null;
      this.bruecke.ereignis(identity, !!klar, klar || daten);
    });
  }

  // ---------------------------------------------------------------- Metadaten und Ereignisse vom Server
  metadaten(md) {
    if (!('fernsteuerung' in md) && !('fernsteuerung_bereit' in md)) return;
    this.bereit = Array.isArray(md.fernsteuerung_bereit) ? md.fernsteuerung_bereit : [];
    if (this.bruecke) this.bruecke.metadaten(JSON.stringify({ fernsteuerung: md.fernsteuerung || [] }));
    const s = meineSteuerung(md, this.m.zugang.identity);
    if (s && s.von !== this.steuerung?.von) this.starten(s);
    else if (!s && this.steuerung) this.stoppen();
  }

  ereignis(d) {
    if (d.art === 'anfrage' && d.von) this.anfrageBekommen(d);
    else if (d.art === 'abgelehnt') toast(t('fs.abgelehnt'), 'warn');
    else if (d.art === 'erteilt') toast(t('fs.erteilt', { name: d.name || '' }), 'ok');
    else if (d.art === 'beendet' && d.an === this.m.zugang.identity) toast(t('fs.beendet'), '');
  }

  // ---------------------------------------------------------------- Steuernde Person
  menueEintraege(kachel) {
    const p = kachel.p;
    if (kachel.quelle !== 'bildschirm' || p.isLocal || !this.bereit.includes(p.identity) || this.m.rolle === 'zuschauer') return [];
    if (this.steuerung?.von === p.identity) return [{ text: t('fs.zurueckgeben'), symbol: 'stopp', aktion: () => this.beendenServer('esc') }];
    return [{ text: t('fs.anfragen'), symbol: 'tastatur', aktion: () => this.anfragen(p.identity) }];
  }

  async anfragen(ziel) {
    try {
      await this.m.api(`/api/raeume/${this.m.code}/fernsteuerung`, { methode: 'POST', json: { aktion: 'anfragen', ziel } });
      toast(t('fs.angefragt'), 'ok');
    } catch (e) { toast(e.message, 'err'); }
  }

  kachelVon(identity) { return this.m.buehne.kacheln.get(`${identity}:bildschirm`) || null; }

  starten(s) {
    this.stoppen(false);
    this.steuerung = s;
    const name = this.m.room.getParticipantByIdentity(s.von)?.name || '';
    this.m.bannerSetzen('fernsteuerung', 'warn', t('fs.duSteuerst', { name }), { text: t('fs.zurueckgeben'), fn: () => this.beendenServer('esc') });
    this.ebeneAnbringen();
  }

  /** Eingabe-Ebene über der (sichtbaren) Bildschirm-Kachel; wird bei jedem Neuanordnen wieder angebracht. */
  ebeneAnbringen() {
    if (!this.steuerung) return;
    const k = this.kachelVon(this.steuerung.von);
    if (!k) return;
    if (this.ebene?.parentNode === k.el) return;
    this.ebene?.remove();
    const ziel = this.steuerung.von, room = this.m.room, ich = this.m.zugang.identity;
    const senden = (o) => {
      this.sendeKette = this.sendeKette.then(async () => {
        const s = this.schluessel ? await this.schluessel : null;
        const daten = s ? await verpacken(s, o, ich) : kodieren(o);
        await room.localParticipant.publishData(daten, { reliable: true, topic: TOPIC, destinationIdentities: [ziel] });
      }).catch(() => {});
    };
    const punkt = (e) => normiert(e.clientX, e.clientY, k.el.getBoundingClientRect(), k.video.videoWidth, k.video.videoHeight);
    const ebene = h('div', { class: 'fs-ebene', tabindex: '0', role: 'application', 'aria-label': t('fs.ebene') });
    ebene.addEventListener('pointermove', (e) => { const p = punkt(e); if (p && this.drossel.jetztSenden()) senden({ t: 'bewegen', ...p }); });
    ebene.addEventListener('pointerdown', (e) => {
      ebene.focus({ preventScroll: true });
      const p = punkt(e), kn = knopf(e.button);
      if (p && kn) { e.preventDefault(); ebene.setPointerCapture?.(e.pointerId); senden({ t: 'druecken', ...p, knopf: kn, mod: [] }); }
    });
    ebene.addEventListener('pointerup', (e) => { const p = punkt(e), kn = knopf(e.button); if (p && kn) senden({ t: 'loslassen', ...p, knopf: kn, mod: [] }); });
    ebene.addEventListener('contextmenu', (e) => e.preventDefault());
    ebene.addEventListener('dblclick', (e) => e.stopPropagation());    // nicht anheften – der Doppelklick geht als zwei Klicks
    ebene.addEventListener('wheel', (e) => { e.preventDefault(); const r = rollen(e.deltaX, e.deltaY, e.deltaMode); if (r.dx || r.dy) senden(r); }, { passive: false });
    ebene.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); this.beendenServer('esc'); return; }
      const ev = tastenEreignis(e);
      if (ev) { e.preventDefault(); e.stopPropagation(); senden(ev); }
    });
    k.el.append(ebene);
    k.el.classList.add('fs-gesteuert');
    this.ebene = ebene;
    ebene.focus({ preventScroll: true });
  }

  stoppen(melden = true) {
    if (!this.steuerung) return;
    this.ebene?.parentNode?.classList.remove('fs-gesteuert');
    this.ebene?.remove();
    this.ebene = null;
    this.steuerung = null;
    this.m.bannerSetzen('fernsteuerung');
    if (melden) toast(t('fs.beendet'), '');
  }

  async beendenServer(_grund) {
    this.bruecke?.beenden?.();
    this.stoppen(false);
    try { await this.m.api(`/api/raeume/${this.m.code}/fernsteuerung`, { methode: 'POST', json: { aktion: 'beenden' } }); } catch { /* schon weg */ }
  }
}
