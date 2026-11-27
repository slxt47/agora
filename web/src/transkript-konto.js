// Konto: Transkripte der Live-Untertitel je Raum (Schalter „Transkript speichern“, Download .txt/.vtt, löschen) und
// „Transkript aus Aufnahme erstellen“ (Whisper, nur wenn der Server AGORA_WHISPER_URL hat).
import { h, api, blatt, knopf, beschaeftigt, bestaetigen, toast } from './common.js';
import { t } from './i18n.js';
import { zeitText } from './helfer.js';
import { zusammenfassungKnopf } from './zusammenfassung-konto.js';    // KI-Zusammenfassung / Stichwort-Kapitel

const zone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Berlin';

export function transkriptKnopf(raum, klasse = 'btn klein') {
  return knopf(t('tk.knopf'), { klasse, symbol: 'chat', 'data-transkripte': raum.code, onclick: () => transkriptDialog(raum) });
}

export async function transkriptDialog(raum) {
  const { inhalt } = blatt(t('tk.titel', { titel: raum.titel || raum.code }));
  const liste = h('ul', { class: 'liste webinar-panel' });
  const schalter = h('input', { type: 'checkbox', role: 'switch', name: 'speichern' });
  inhalt.append(
    h('label', { class: 'schalter' }, h('span', { class: 'txt' }, h('span', {}, t('tk.speichern')), h('small', {}, t('tk.speichernHinweis'))), schalter),
    h('h4', {}, t('tk.liste')), liste);
  async function laden() {
    let r;
    try { r = await api(`/api/raeume/${raum.code}/transkripte`); } catch (e) { liste.replaceChildren(h('li', { class: 'leer' }, e.message)); return; }
    schalter.checked = r.speichern;
    liste.replaceChildren(...(r.transkripte.length ? r.transkripte.map((x) => h('li', { dataset: { sitzung: x.sitzung } },
      h('div', { class: 'text' }, h('b', {}, zeitText(x.beginn, zone())), h('div', { class: 'sec klein' }, t('tk.abschnitte', { n: x.abschnitte }))),
      h('div', { class: 'aktionen' },
        h('a', { class: 'btn klein', href: `/api/raeume/${raum.code}/transkript?sitzung=${x.sitzung}&format=txt`, download: '' }, '.txt'),
        h('a', { class: 'btn klein', href: `/api/raeume/${raum.code}/transkript?sitzung=${x.sitzung}&format=vtt`, download: '' }, '.vtt'),
        zusammenfassungKnopf({ art: 'raum', code: raum.code, sitzung: x.sitzung, titel: raum.titel }),
        knopf(t('konto.loeschen'), { klasse: 'icon-btn', symbol: 'muell', 'aria-label': t('tk.loeschen'), onclick: async () => {
          if (!await bestaetigen(t('tk.loeschen'), null, { ja: t('konto.loeschen'), gefahr: true })) return;
          beschaeftigt(null, async () => { await api(`/api/raeume/${raum.code}/transkript?sitzung=${x.sitzung}`, { methode: 'DELETE' }); laden(); });
        } })))) : [h('li', { class: 'leer' }, t('tk.leer'))]));
  }
  schalter.addEventListener('change', () => beschaeftigt(null, async () => {
    try { await api(`/api/raeume/${raum.code}/transkript`, { methode: 'PATCH', json: { speichern: schalter.checked } }); } catch (e) { schalter.checked = !schalter.checked; throw e; }
    toast(schalter.checked ? t('ut.speichernAn') : t('ut.speichernAus'), 'ok', { dauer: 2500 });
  }));
  laden();
}

/** Knöpfe für eine Aufnahme der Bibliothek (oder []): erstellen / läuft / herunterladen. */
export function aufnahmeTranskript(a, neuLaden) {
  if (!a.transkript_moeglich && a.transkript?.status !== 'fertig') return [];
  const s = a.transkript?.status;
  if (s === 'fertig') {
    return [h('a', { class: 'btn klein', href: `/api/aufnahmen/${a.id}/transkript?format=vtt`, download: '' }, t('tk.aufnahmeVtt')),
      h('a', { class: 'btn klein', href: `/api/aufnahmen/${a.id}/transkript?format=txt`, download: '' }, t('tk.aufnahmeTxt')),
      zusammenfassungKnopf({ art: 'aufnahme', id: a.id, titel: a.titel })];
  }
  if (s === 'laeuft') return [h('span', { class: 'marker' }, t('tk.aufnahmeLaeuft'))];
  return [knopf(s === 'fehler' ? t('tk.aufnahmeNochmal') : t('tk.aufnahmeErstellen'), { klasse: 'btn klein', symbol: 'stift', title: a.transkript?.fehler || undefined,
    onclick: (e) => beschaeftigt(e.currentTarget, async () => {
      await api(`/api/aufnahmen/${a.id}/transkript`, { methode: 'POST' });
      toast(t('tk.aufnahmeGestartet'), 'ok', { dauer: 5000 });
      neuLaden?.();
    }) })];
}
