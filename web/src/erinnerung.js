// Erinnerungs-Mails: Einstellung im Profil (aus, 10/15/30/60 Minuten vorher) und der Haken „Erinnerung schicken“
// im Einladen-Dialog (nur geplante Meetings; Adressen werden nur dann gespeichert und nach dem Termin gelöscht).
import { $, h, api, beschaeftigt, toast } from './common.js';
import { t } from './i18n.js';

export const MINUTEN = [10, 15, 30, 60];

export async function erinnerungEinrichten(box = $('#erinnerung')) {
  if (!box) return;
  const wahl = h('select', { name: 'minuten', id: 'erinnerung-minuten' },
    h('option', { value: '0' }, t('erinnerung.aus')), ...MINUTEN.map((m) => h('option', { value: String(m) }, t('erinnerung.vorher', { n: m }))));
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec klein' }, t('erinnerung.text')),
    h('label', { class: 'feld' }, h('span', {}, t('erinnerung.feld')), wahl),
    h('button', { type: 'submit', class: 'btn primary' }, t('allg.speichern')));
  box.replaceChildren(f);
  try { wahl.value = String((await api('/api/konto/erinnerung')).minuten || 0); } catch { /* Standard aus */ }
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const r = await api('/api/konto/erinnerung', { methode: 'PUT', json: { minuten: Number(wahl.value) } });
      toast(r.minuten ? t('erinnerung.an', { n: r.minuten }) : t('erinnerung.ausOk'), 'ok');
    });
  });
}

/** Felder für den Einladen-Dialog. werte(form) → {erinnerung, erinnerung_min} oder {}. */
export function einladenErinnerung(raum) {
  if (!raum.geplant) return { feld: null, werte: () => ({}) };
  const wahl = h('select', { name: 'erinnerung_min', 'aria-label': t('erinnerung.feld') },
    MINUTEN.map((m) => h('option', { value: String(m), selected: m === 15 }, t('erinnerung.vorher', { n: m }))));
  const haken = h('input', { type: 'checkbox', role: 'switch', name: 'erinnerung' });
  const feld = h('div', { class: 'formular' },
    h('label', { class: 'schalter' },
      h('span', { class: 'txt' }, h('span', {}, t('erinnerung.schicken')), h('small', {}, t('erinnerung.schickenHinweis'))), haken),
    h('label', { class: 'feld', hidden: true }, h('span', {}, t('erinnerung.feld')), wahl));
  haken.addEventListener('change', () => { feld.lastElementChild.hidden = !haken.checked; });
  return { feld, werte: () => (haken.checked ? { erinnerung: true, erinnerung_min: Number(wahl.value) } : {}) };
}
