// Kalender ohne OAuth: „Zum Kalender“-Dialog je geplantem Meeting (Google, Outlook.com, Outlook/Office 365, .ics) und
// das private Kalender-Abo (iCal-Feed) im Profil. Die Links baut der Server (raum.kalender), hier wird nur angezeigt.
import { $, h, api, blatt, bestaetigen, kopieren, beschaeftigt, toast, knopf } from './common.js';
import { t } from './i18n.js';
import { icon } from './icons.js';
import { zeitText } from './helfer.js';
import './planung.css';

/** Dialog mit allen Wegen, ein Meeting in den eigenen Kalender zu übernehmen. */
export function kalenderDialog(raum) {
  const k = raum.kalender;
  if (!k) return;
  const { inhalt } = blatt(t('kal.titel'));
  const wdh = raum.geplant?.wiederholung && raum.geplant.wiederholung !== 'keine';
  const eintrag = (name, url, hinweis, extra = {}) => h('a', { class: 'kal-link', href: url, rel: 'noopener noreferrer', ...extra },
    h('span', { class: 'symbol', html: icon('kalender') }),
    h('span', { class: 'text' }, h('b', {}, name), hinweis ? h('small', {}, hinweis) : null));
  inhalt.append(
    h('p', { class: 'sec klein' }, t('kal.text')),
    h('div', { class: 'kal-links' },
      eintrag(t('kal.google'), k.google, wdh ? t('kal.mitWiederholung') : null, { target: '_blank' }),
      eintrag(t('kal.office365'), k.office365, wdh ? t('kal.nurNaechster') : null, { target: '_blank' }),
      eintrag(t('kal.outlook'), k.outlook, wdh ? t('kal.nurNaechster') : null, { target: '_blank' }),
      eintrag(t('kal.ics'), k.ics, t('kal.icsHinweis'), { download: `${raum.code}.ics` })),
    h('p', { class: 'sec klein' }, t('kal.aboHinweis')));
}

/** Profil: Kalender-Abo ein-/ausschalten, Link kopieren, neu erzeugen. */
export async function kalenderAboEinrichten(box = $('#kalender-abo')) {
  if (!box) return;
  const zeigen = (abo) => {
    box.replaceChildren(h('p', { class: 'sec klein' }, t('kal.aboText')));
    if (!abo?.aktiv) {
      box.append(h('div', { class: 'knoepfe oben' }, knopf(t('kal.aboAn'), { klasse: 'btn primary', symbol: 'kalender', id: 'kal-abo-an',
        onclick: (e) => beschaeftigt(e.currentTarget, async () => zeigen((await api('/api/konto/kalender', { methode: 'POST' })).abo)) })));
      return;
    }
    box.append(
      h('div', { class: 'link-feld oben', id: 'kal-abo-url' }, abo.url),
      h('p', { class: 'sec klein' }, abo.abgerufen ? t('kal.abgerufen', { zeit: zeitText(abo.abgerufen) }) : t('kal.nochNie')),
      h('div', { class: 'aktionen oben' },
        knopf(t('konto.linkKopieren'), { klasse: 'btn primary', symbol: 'kopie', onclick: () => kopieren(abo.url, t('konto.linkKopiert')) }),
        h('a', { class: 'btn', href: abo.webcal, html: icon('kalender') }, h('span', {}, t('kal.aboOeffnen'))),
        knopf(t('kal.aboNeu'), { symbol: 'schloss', onclick: async (e) => {
          const el = e.currentTarget;
          if (!await bestaetigen(t('kal.aboNeuTitel'), t('kal.aboNeuText'), { ja: t('kal.aboNeu') })) return;
          beschaeftigt(el, async () => { zeigen((await api('/api/konto/kalender', { methode: 'POST' })).abo); toast(t('kal.aboNeuOk'), 'ok'); });
        } }),
        knopf(t('kal.aboAus'), { klasse: 'btn gefahr', symbol: 'schliessen', onclick: async (e) => {
          const el = e.currentTarget;
          if (!await bestaetigen(t('kal.aboAusTitel'), t('kal.aboAusText'), { ja: t('kal.aboAus'), gefahr: true })) return;
          beschaeftigt(el, async () => zeigen((await api('/api/konto/kalender', { methode: 'DELETE' })).abo));
        } })),
      h('p', { class: 'sec klein' }, t('kal.aboSchutz')));
  };
  try { zeigen((await api('/api/konto/kalender')).abo); } catch (e) { box.replaceChildren(h('p', { class: 'leer' }, e.message)); }
}
