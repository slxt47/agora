// Webinar im Meeting: Bühne nur mit dem Panel (Host, Co-Hosts, Panelisten), Zuschauer senden nichts (Mikro/Kamera/
// Teilen ausgeblendet), Zuschauerzahl in der Kopfzeile fürs Panel, „Aufs Podium holen“ / „Zu den Zuschauern“ für
// Host und Co-Hosts. Rollen kommen vom Server (/zustand, Ereignis „rolle“); das LiveKit-Attribut „rolle“ dient nur
// als Vorschlag für neu Hinzugekommene (es kann nichts freischalten, weil der Server die Rechte im Token setzt).
import { $, h, toast } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import './webinar.css';

const PANEL = new Set(['host', 'cohost', 'panelist']);

export class Webinar {
  constructor(m) {
    this.m = m;
    this.an = !!m.raum.webinar;
    this.einst = { chat: 'alle', fragen_an: true };
    this.zahlen = null;
    if (!this.an) return;
    document.body.classList.add('webinar');
    this.pille = h('span', { class: 'm-pille webinar-pille', id: 'm-webinar', role: 'status', hidden: true });
    $('#m-rec')?.after(this.pille);
    this.rolleAnwenden(null, m.rolle);
  }

  istPanel(rolle = this.m.rolle) { return PANEL.has(rolle); }

  /** Kachel auf der Bühne? Im Webinar nur das Panel. */
  aufBuehne(p) { return !this.an || this.m.rolleVon(p) !== 'zuschauer'; }

  async laden() {
    if (!this.an) return;
    try {
      const z = await this.m.api(`/api/raeume/${this.m.code}/webinar/zustand`);
      this.einst = { chat: z.chat, fragen_an: z.fragen_an };
      this.zahlen = 'zuschauer' in z ? z : null;
    } catch { /* nächster Versuch */ }
    this.kopf();
    clearTimeout(this.timer);
    if (this.istPanel()) this.timer = setTimeout(() => this.laden(), 20000);
  }

  /** Zuschauerzahl aus der Server-Zählung, zwischendurch lokal aus den bekannten Rollen. */
  kopf() {
    if (!this.an) return;
    const lokal = [...this.m.room.remoteParticipants.values()].filter((p) => this.m.rolleVon(p) === 'zuschauer').length
      + (this.m.rolle === 'zuschauer' ? 1 : 0);
    const n = this.istPanel() ? lokal : null;
    this.pille.hidden = n === null;
    if (n !== null) {
      this.pille.innerHTML = `${icon('leute')}<span></span>`;
      this.pille.querySelector('span').textContent = t('webinar.zuschauerN', { n });
      this.pille.setAttribute('aria-label', t('webinar.zuschauerN', { n }));
    }
  }

  /** Eigene Rolle hat sich geändert (Server-Ereignis). */
  async rolleAnwenden(war, neu) {
    if (!this.an) return;
    document.body.classList.toggle('webinar-zuschauer', neu === 'zuschauer');
    const lp = this.m.room?.localParticipant;
    if (neu === 'zuschauer' && lp) {
      for (const pub of [...lp.trackPublications.values()]) if (pub.track) await lp.unpublishTrack(pub.track).catch(() => {});
      if (war && war !== 'zuschauer') toast(t('webinar.zurueckZuschauer'), '', { dauer: 5000 });
    } else if (war === 'zuschauer' && neu === 'panelist') {
      toast(t('webinar.aufsPodium'), 'ok', { dauer: 10000, aktion: { text: t('webinar.micAn'), fn: () => this.m.micUmschalten() } });
    }
    if (war !== null) this.laden();
  }

  /** Jemand (auch ich) hat eine neue Rolle. */
  rolleEreignis(identity, war, neu) {
    if (!this.an) return;
    const ich = identity === this.m.room.localParticipant.identity;
    if (ich) this.rolleAnwenden(war, neu);
    this.m.buehne.sync();
    this.kopf();
  }

  /** Menüeinträge für Host/Co-Host an einer anderen Person. */
  eintraege(p) {
    if (!this.an || p.isLocal || !this.m.istMod()) return [];
    const r = this.m.rolleVon(p);
    const name = this.m.name(p);
    if (r === 'zuschauer') return [{ text: t('webinar.holen'), symbol: 'mic', aktion: () => this.rolle(p, 'panelist', t('webinar.geholt', { name })) }];
    if (r === 'panelist') return [{ text: t('webinar.zuruecksetzen'), symbol: 'leute', aktion: () => this.rolle(p, 'zuschauer', t('webinar.zurueckgesetzt', { name })) }];
    return [];
  }

  async rolle(p, rolle, okText) {
    try {
      await this.m.api(`/api/raeume/${this.m.code}/webinar/rolle`, { methode: 'POST', json: { ziel: p.identity, rolle } });
      toast(okText, 'ok', { dauer: 2500 });
    } catch (e) { toast(e.message, 'err'); }
  }
}
