// Meeting-Verlauf im Konto: „Vergangene Meetings“ mit Datum, Dauer, Spitzenzahl und – wenn der Chat gespeichert
// wurde – Chat und Umfragen genau dieses Meetings.
import { $, h, api, blatt, bestaetigen, beschaeftigt, toast, knopf } from './common.js';
import { t } from './i18n.js';
import { icon } from './icons.js';
import { zeitText, laufzeitText, uhrzeitText, linkTeile } from './helfer.js';
import './planung.css';
import { qualitaetBereit, qualitaetVerlaufMarke } from './qualitaet-konto.js';
import { zusammenarbeitVerlauf } from './zusammenarbeit-verlauf.js';   // Whiteboard, Notizen, Fragen, Dateien

let zone = () => undefined;

export async function verlaufEinrichten({ zeitzone } = {}) {
  if (zeitzone) zone = zeitzone;
  await verlaufLaden();
}

export async function verlaufLaden() {
  const ul = $('#liste-verlauf');
  if (!ul) return;
  let meetings = [];
  try { [{ meetings = [] }] = await Promise.all([api('/api/verlauf'), qualitaetBereit()]); } catch (e) { ul.replaceChildren(h('li', { class: 'leer' }, e.message)); return; }
  ul.replaceChildren();
  if (!meetings.length) { ul.append(h('li', { class: 'leer' }, t('verlauf.leer'))); return; }
  for (const m of meetings) ul.append(eintrag(m));
}

function eintrag(m) {
  const info = [zeitText(m.start, zone())];
  if (m.dauer_s != null) info.push(t('verlauf.dauer', { zeit: laufzeitText(m.dauer_s) }));
  return h('li', { 'data-verlauf': m.id },
    h('div', { class: 'kopf' },
      h('div', { class: 'symbol', html: icon('uhr') }),
      h('div', { class: 'text' },
        h('span', { class: 'name' }, m.titel || t('konto.ohneTitel')),
        h('span', { class: 'info' }, info.join(' · ')),
        h('span', { class: 'info mono' }, m.code))),
    h('div', { class: 'zeile' },
      h('span', { class: 'marker', title: t('verlauf.spitzeLang') }, t('verlauf.spitze', { n: m.spitze })),
      h('span', { class: 'marker' }, t('verlauf.beitritte', { n: m.teilnahmen })),
      qualitaetVerlaufMarke(m)),     // Sterne-Schnitt und Verbindung (qualitaet-konto.js)
    h('div', { class: 'aktionen' },
      m.chat ? knopf(t('verlauf.inhalt'), { klasse: 'btn klein', symbol: 'chat', onclick: () => inhaltDialog(m) }) : null,
      knopf(t('verlauf.entfernen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: () => entfernen(m) })));
}

async function entfernen(m) {
  if (!await bestaetigen(t('verlauf.entfernenTitel'), t('verlauf.entfernenText'), { ja: t('verlauf.entfernen'), gefahr: true })) return;
  await beschaeftigt(null, async () => { await api(`/api/verlauf/${m.id}`, { methode: 'DELETE' }); toast(t('konto.geloescht'), 'ok'); await verlaufLaden(); });
}

async function inhaltDialog(m) {
  const { inhalt } = blatt(t('verlauf.inhaltTitel', { titel: m.titel || m.code }));
  inhalt.append(h('p', { class: 'sec' }, t('allg.laden')));
  try {
    const r = await api(`/api/verlauf/${m.id}/inhalt`);
    inhalt.replaceChildren();
    inhalt.append(h('h4', {}, t('verlauf.chat')));
    if (!r.nachrichten.length) inhalt.append(h('p', { class: 'leer' }, t('konto.verlaufLeer')));
    else {
      inhalt.append(h('ol', { class: 'chatverlauf' }, r.nachrichten.map((n) => h('li', {},
        h('span', { class: 'sec klein' }, `${uhrzeitText(n.zeit, zone())} · `),
        h('b', {}, n.von_name), n.an ? h('span', { class: 'sec' }, ` → ${n.an_name || t('chat.privat')}`) : null, ': ',
        ...linkTeile(n.text).map((x) => (x.link ? h('a', { href: x.link, target: '_blank', rel: 'noopener noreferrer' }, x.text) : x.text))))));
    }
    if (r.umfragen.length) {
      inhalt.append(h('h4', {}, t('verlauf.umfragen')));
      for (const u of r.umfragen) {
        const z = u.ergebnis?.zaehler || [];
        inhalt.append(h('div', { class: 'verlauf-umfrage' }, h('b', {}, u.frage),
          h('ul', {}, u.optionen.map((o, i) => h('li', {}, `${o}: ${z[i] ?? 0}`))),
          h('small', { class: 'sec' }, t('verlauf.stimmen', { n: u.ergebnis?.stimmen ?? 0 }))));
      }
    }
    zusammenarbeitVerlauf(inhalt, r, m);
  } catch (e) { inhalt.replaceChildren(h('p', { class: 'leer' }, e.message)); }
}
