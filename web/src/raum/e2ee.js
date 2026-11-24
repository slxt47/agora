// Ende-zu-Ende-Verschlüsselung im Meeting (livekit-client: ExternalE2EEKeyProvider + eigener E2EE-Worker, von Vite
// gebündelt und selbst ausgeliefert). Jeder LiveKit-Raum (Hauptraum, Breakout) bekommt einen eigenen Worker und
// Key-Provider – LiveKit beendet den Worker beim Trennen –, aber denselben Schlüssel (aus dem Geheimnis im Link).
// Verschlüsselt werden Bild, Ton und Bildschirm sowie Datenkanal-Nachrichten zwischen Browsern (Reaktionen,
// Hervorheben, Anmerkungen). Chat, Umfragen und Host-Ereignisse laufen über den Server (ADR 0002) und nicht E2EE.
import { ExternalE2EEKeyProvider, RoomEvent, isE2EESupported } from 'livekit-client';
import E2eeWorker from 'livekit-client/e2ee-worker?worker';
import { $ } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import { medienSchluessel } from '../e2ee-schluessel.js';

export const e2eeMoeglich = () => { try { return isE2EESupported(); } catch { return false; } };

/** Room-Optionen + Aktivierung für einen neuen LiveKit-Raum. Ohne Schlüssel: nichts. */
export function e2eeVorbereiten(schluessel) {
  if (!schluessel) return { optionen: {}, aktivieren: async () => {} };
  const keyProvider = new ExternalE2EEKeyProvider({ keySize: 256 });
  const worker = new E2eeWorker();
  return {
    optionen: { encryption: { keyProvider, worker } },
    /** Vor room.connect(): Schlüssel setzen und lokale Verschlüsselung einschalten. */
    aktivieren: async (room) => {
      await keyProvider.setKey(await medienSchluessel(schluessel));
      await room.setE2EEEnabled(true);
    },
  };
}

/** Anzeige im Meeting: Schloss-Pille „Ende-zu-Ende verschlüsselt“, Banner bei Entschlüsselungsfehlern. */
export class E2eeAnzeige {
  constructor(m) {
    this.m = m;
    this.fehlerVon = new Map();       // identity → Zeit des letzten Fehlers
    this.pille = $('#m-e2ee');
    this.pille.innerHTML = `${icon('schloss')}<span class="lang"></span>`;
    this.pille.querySelector('.lang').textContent = t('e2ee.kurz');
    this.pille.title = t('e2ee.pilleTitel');
    this.pille.setAttribute('aria-label', t('e2ee.pilleTitel'));
    this.verbinden(m.room);
    document.addEventListener('agora:raumwechsel', (e) => { this.fehlerVon.clear(); this.m.bannerSetzen('e2ee'); this.verbinden(e.detail.room); });
  }

  verbinden(room) {
    this.room = room;
    const aktiv = () => room === this.m.room;
    room.on(RoomEvent.ParticipantEncryptionStatusChanged, (an, p) => { if (aktiv() && p?.isLocal) this.zeigen(); })
      .on(RoomEvent.EncryptionError, (e, p) => aktiv() && this.fehler(e, p))
      .on(RoomEvent.ParticipantDisconnected, (p) => { if (aktiv() && this.fehlerVon.delete(p.identity)) this.bannerAktualisieren(); });
    this.zeigen();
  }

  zeigen() {
    const an = !!this.room?.isE2EEEnabled;
    this.pille.hidden = !an;
    document.body.dataset.e2ee = an ? 'an' : 'aus';
  }

  fehler(_e, p) {
    if (!p || p.isLocal) { this.m.bannerSetzen('e2ee', 'err', t('e2ee.fehlerEigen')); return; }
    this.fehlerVon.set(p.identity, Date.now());
    this.bannerAktualisieren();
  }

  bannerAktualisieren() {
    const jetzt = Date.now();
    for (const [id, z] of this.fehlerVon) if (jetzt - z > 30000) this.fehlerVon.delete(id);
    const namen = [...this.fehlerVon.keys()].map((id) => this.room.getParticipantByIdentity(id)).filter(Boolean).map((p) => this.m.name(p));
    if (!namen.length) { this.m.bannerSetzen('e2ee'); return; }
    this.m.bannerSetzen('e2ee', 'err', t('e2ee.fehlerAndere', { namen: namen.join(', ') }));
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.bannerAktualisieren(), 31000);
  }
}
