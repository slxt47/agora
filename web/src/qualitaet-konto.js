// Konto: Qualität der eigenen Meetings – Sterne-Schnitt aus den Rückmeldungen und Verbindungswerte (Schnitt je
// Meeting, ohne Namen). Übersicht über GET /api/qualitaet, Einzelheiten je Raum über GET /api/raeume/{code}/qualitaet.
import { h, api, blatt, knopf } from './common.js';
import { t } from './i18n.js';
import { datumText, uhrzeitText } from './helfer.js';
import './qualitaet-konto.css';

let uebersicht = {};
let jeMeeting = {};
let geladen = null;
const zahl = new Intl.NumberFormat('de-AT', { maximumFractionDigits: 1 });

export function qualitaetLaden() {
  geladen = (async () => {
    try { const r = await api('/api/qualitaet'); uebersicht = r.raeume || {}; jeMeeting = r.meetings || {}; } catch { uebersicht = {}; jeMeeting = {}; }
  })();
  return geladen;
}

/** Wartet auf die laufende (oder startet eine) Abfrage – für den Meeting-Verlauf (verlauf.js). */
export function qualitaetBereit() { return geladen || qualitaetLaden(); }

/** Marke für einen Eintrag in „Vergangene Meetings“ (meeting_verlauf: code + sitzung) – oder null. */
export function qualitaetVerlaufMarke(m) {
  const q = jeMeeting[`${m.code}:${m.sitzung}`];
  if (!q) return null;
  const teile = [];
  if (q.sterne != null) teile.push(`★ ${zahl.format(q.sterne)} (${q.bewertungen})`);
  if (q.verlust != null) teile.push(t('qk.kurzVerbindung', { rtt: zahl.format(q.rtt), verlust: zahl.format(q.verlust) }));
  const text = q.sterne != null ? t('qk.label', { sterne: zahl.format(q.sterne), n: q.bewertungen }) : t('qk.ohneBewertung');
  return h('span', { class: 'marker qualitaet-verlauf', title: text, 'aria-label': `${text}. ${teile.slice(1).join('')}` }, teile.join(' · '));
}

/** Marke „★ 4,5 · 3 Bewertungen“ + Knopf für die Einzelheiten – oder null, wenn es nichts gibt. */
export function qualitaetMarke(raum, zone) {
  const q = uebersicht[raum.code];
  if (!q) return null;
  const teile = [];
  if (q.sterne != null) teile.push(h('span', { class: 'stern', 'aria-hidden': 'true' }, '★'), h('b', {}, zahl.format(q.sterne)), h('span', { class: 'sec' }, t('qk.bewertungenN', { n: q.bewertungen })));
  else teile.push(h('span', { class: 'sec' }, t('qk.ohneBewertung')));
  if (q.verlust != null) teile.push(h('span', { class: 'sec' }, `· ${t('qk.kurzVerbindung', { rtt: zahl.format(q.rtt), verlust: zahl.format(q.verlust) })}`));
  return h('div', { class: 'zeile' },
    h('span', { class: 'qualitaet-marke', 'aria-label': q.sterne != null ? t('qk.label', { sterne: zahl.format(q.sterne), n: q.bewertungen }) : t('qk.ohneBewertung') }, teile),
    knopf(t('qk.details'), { klasse: 'btn klein', symbol: 'stern', onclick: () => qualitaetDialog(raum, zone) }));
}

async function qualitaetDialog(raum, zone) {
  const { inhalt } = blatt(t('qk.titel', { titel: raum.titel || raum.code }), { breit: true });
  inhalt.append(h('p', { class: 'sec' }, t('allg.laden')));
  try {
    const r = await api(`/api/raeume/${raum.code}/qualitaet`);
    const meetings = r.meetings || [];
    inhalt.replaceChildren(h('p', { class: 'sec klein' }, t('qk.hinweis')));
    if (!meetings.length) { inhalt.append(h('p', { class: 'leer' }, t('qk.leer'))); return; }
    const zeilen = meetings.map((m) => {
      const v = m.verbindung, f = m.feedback;
      const probleme = Object.entries(f?.probleme || {}).filter(([, n]) => n).map(([k, n]) => `${t(`fb.problem.${k}`)} (${n})`).join(', ');
      return h('tr', {},
        h('td', {}, `${datumText(m.beginn, zone)} ${uhrzeitText(m.beginn, zone)}`),
        h('td', { class: 'zahl' }, f?.anzahl ? `★ ${zahl.format(f.sterne)} (${f.anzahl})` : '–'),
        h('td', { class: 'zahl' }, v ? `${zahl.format(v.rtt)} ms · ${zahl.format(v.verlust)} % · ${zahl.format(v.jitter)} ms` : '–'),
        h('td', { class: 'zahl' }, v ? `${v.schlecht_anteil} %` : '–'),
        h('td', {}, probleme || '–', f?.kommentare?.length ? h('ul', { class: 'qualitaet-kommentare' }, f.kommentare.map((k) => h('li', {}, k))) : null));
    });
    inhalt.append(h('div', { class: 'qualitaet-scroll' }, h('table', { class: 'qualitaet-tabelle' },
      h('thead', {}, h('tr', {}, ['qk.wann', 'qk.sterne', 'qk.verbindung', 'qk.schlecht', 'qk.probleme'].map((k) => h('th', { scope: 'col' }, t(k))))),
      h('tbody', {}, zeilen))));
  } catch (e) {
    inhalt.replaceChildren(h('p', { class: 'leer' }, e.message));
  }
}
