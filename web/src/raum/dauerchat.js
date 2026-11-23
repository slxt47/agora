// Dauer-Chat im persönlichen Raum: Hinweis im Chat-Panel („bleibt nach dem Meeting“), für den Besitzer „Leeren“,
// und Ereignis `chat_geleert` (Server → alle) leert die Liste bei allen.
import { h, api, bestaetigen, toast } from '../common.js';
import { t } from '../i18n.js';
import './dauerchat.css';

export function dauerChatEinrichten(m) {
  if (!m.raum?.dauer_chat || !m.chat) return;
  const hinweis = h('div', { class: 'dauerchat-hinweis', id: 'dauerchat-hinweis' }, h('span', {}, t('dauerchat.hinweis')),
    m.raum.ist_host ? h('button', { type: 'button', class: 'btn klein', onclick: async () => {
      if (!await bestaetigen(t('dauerchat.leerenTitel'), t('dauerchat.leerenText'), { ja: t('dauerchat.leeren'), gefahr: true })) return;
      try { await api(`/api/raeume/${m.code}/dauer-chat`, { methode: 'DELETE' }); } catch (e) { toast(e.message, 'err'); }
    } }, t('dauerchat.leeren')) : null);
  m.chat.scroll.prepend(hinweis);
}

/** Ereignis `chat_geleert`: Liste leeren (private Nachrichten dieses Meetings lädt der Chat neu). */
export function dauerChatGeleert(m) {
  if (!m.chat) return;
  m.chat.nachrichten.clear();
  m.chat.liste.replaceChildren();
  m.chat.leer.hidden = false;
  m.chat.laden();
  toast(t('dauerchat.geleert'), '', { dauer: 3000 });
}
