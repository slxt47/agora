// Konto: Webinar-Einstellungen eines Raums (Webinar an/aus, Chat und Fragen der Zuschauer, Registrierung mit
// Kapazität und Zusatzfragen), Panel-Links und die Liste der Anmeldungen (mit CSV-Export).
// API: GET/PATCH /api/raeume/{code}/webinar, POST/DELETE …/webinar/panel, GET/DELETE …/webinar/anmeldungen[.csv]
import { h, api, blatt, knopf, kopieren, beschaeftigt, bestaetigen, toast, status } from './common.js';
import { t } from './i18n.js';
import { icon } from './icons.js';
import { zeitText } from './helfer.js';
import './webinar-konto.css';


const zone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Berlin';

function schalter(name, text, hinweis, an, extra = {}) {
  return h('label', { class: 'schalter' },
    h('span', { class: 'txt' }, h('span', {}, text), hinweis ? h('small', {}, hinweis) : null),
    h('input', { type: 'checkbox', role: 'switch', name, checked: an, ...extra }));
}

/** Knopf „Webinar“ für einen Raum im Konto. */
export function webinarKnopf(raum, klasse = 'btn klein') {
  return knopf(t('webinar.knopf'), { klasse, symbol: 'leute', 'data-webinar': raum.code, onclick: () => webinarDialog(raum) });
}

/** Marke „Webinar“ in der Raumliste (oder null). */
export function webinarMarke(raum) {
  return raum.webinar ? h('span', { class: 'marker' }, raum.webinar.registrierung ? t('webinar.markeRegistrierung') : t('webinar.marke')) : null;
}

export async function webinarDialog(raum, nachher) {
  let w;
  try { w = (await api(`/api/raeume/${raum.code}/webinar`)).webinar; } catch (e) { toast(e.message, 'err'); return; }
  const { inhalt } = blatt(t('webinar.titel', { titel: raum.titel || raum.code }), { breit: true, onclose: nachher });
  const statusEl = h('div', { class: 'status', role: 'status', hidden: true });
  const fragen = h('ol', { class: 'webinar-fragen' });
  const frageZeile = (text = '') => h('li', {}, h('input', { name: 'frage', maxlength: '200', value: text, 'aria-label': t('webinar.frage') }),
    h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('webinar.frageWeg'), html: icon('schliessen'), onclick: (e) => e.currentTarget.closest('li').remove() }));
  for (const q of w.fragen || []) fragen.append(frageZeile(q));
  const f = h('form', { class: 'formular webinar-form' },
    h('p', { class: 'sec klein' }, t('webinar.erklaerung')),
    schalter('an', t('webinar.an'), raum.aktiv ? t('webinar.laeuftHinweis') : t('webinar.anHinweis'), w.an, { disabled: raum.aktiv || null }),
    h('label', { class: 'feld' }, h('span', {}, t('webinar.chat')),
      h('select', { name: 'chat' }, ['alle', 'panel', 'aus'].map((k) => h('option', { value: k, selected: w.chat === k }, t(`webinar.chat.${k}`))))),
    schalter('fragen_an', t('webinar.fragenAn'), t('webinar.fragenAnHinweis'), w.fragen_an),
    h('h4', {}, t('webinar.registrierung')),
    schalter('registrierung', t('webinar.registrierungAn'), t('webinar.registrierungHinweis'), w.registrierung),
    h('label', { class: 'feld' }, h('span', {}, t('webinar.kapazitaet')),
      h('input', { name: 'kapazitaet', type: 'number', min: '1', max: '100000', inputmode: 'numeric', value: w.kapazitaet ?? '' }),
      h('small', {}, t('webinar.kapazitaetHinweis'))),
    h('fieldset', { class: 'feld ohne-rahmen' }, h('legend', {}, h('b', {}, t('webinar.fragen'))), fragen,
      knopf(t('webinar.frageNeu'), { klasse: 'btn klein', symbol: 'plus', onclick: () => { if (fragen.children.length < 5) fragen.append(frageZeile()); fragen.lastElementChild?.querySelector('input')?.focus(); } })),
    statusEl,
    h('div', { class: 'knoepfe' }, h('button', { type: 'submit', class: 'btn primary' }, t('allg.speichern'))));
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    const kap = f.kapazitaet.value.trim();
    const daten = { chat: f.chat.value, fragen_an: f.fragen_an.checked, registrierung: f.registrierung.checked,
      kapazitaet: kap ? Number(kap) : null, fragen: [...fragen.querySelectorAll('input')].map((i) => i.value.trim()).filter(Boolean) };
    if (!raum.aktiv) daten.an = f.an.checked;
    beschaeftigt(f.querySelector('button[type=submit]'), async () => {
      w = (await api(`/api/raeume/${raum.code}/webinar`, { methode: 'PATCH', json: daten })).webinar;
      raum.webinar = w.an ? { registrierung: w.registrierung } : false;
      status(statusEl, t('webinar.gespeichert'), 'ok');
      registrierungZeigen();
    }, statusEl);
  });

  // Panel-Links
  const panelListe = h('ul', { class: 'liste webinar-panel' });
  const panelZeigen = () => {
    panelListe.replaceChildren(...(w.panel.length ? w.panel.map((p) => h('li', { dataset: { panel: p.id } },
      h('div', { class: 'text' }, h('b', {}, p.name), h('div', { class: 'link-feld' }, p.link)),
      h('div', { class: 'aktionen' },
        knopf(t('konto.linkKopieren'), { klasse: 'btn klein', symbol: 'kopie', onclick: () => kopieren(p.link, t('konto.linkKopiert')) }),
        knopf(t('konto.loeschen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: () => beschaeftigt(null, async () => {
          await api(`/api/raeume/${raum.code}/webinar/panel/${p.id}`, { methode: 'DELETE' });
          w.panel = w.panel.filter((x) => x.id !== p.id); panelZeigen();
        }) })))) : [h('li', { class: 'leer' }, t('webinar.keinPanel'))]));
  };
  const panelForm = h('form', { class: 'zeile webinar-panel-neu' },
    h('input', { name: 'name', maxlength: '60', placeholder: t('webinar.panelName'), 'aria-label': t('webinar.panelName') }),
    h('button', { type: 'submit', class: 'btn klein' }, t('webinar.panelNeu')));
  panelForm.addEventListener('submit', (e) => {
    e.preventDefault();
    beschaeftigt(panelForm.querySelector('button'), async () => {
      const r = await api(`/api/raeume/${raum.code}/webinar/panel`, { methode: 'POST', json: { name: panelForm.name.value.trim() } });
      w.panel.push(r.panel); panelForm.name.value = ''; panelZeigen();
    });
  });
  panelZeigen();

  // Anmeldungen
  const reg = h('section', { class: 'webinar-anmeldungen' });
  async function registrierungZeigen() {
    reg.replaceChildren();
    if (!w.registrierung) return;
    reg.append(h('h4', {}, t('webinar.anmeldungen')),
      h('div', { class: 'link-feld' }, w.anmelde_link),
      h('div', { class: 'aktionen oben' },
        knopf(t('webinar.anmeldeLinkKopieren'), { klasse: 'btn klein', symbol: 'kopie', onclick: () => kopieren(w.anmelde_link, t('konto.linkKopiert')) }),
        h('a', { class: 'btn klein', href: `/api/raeume/${raum.code}/webinar/anmeldungen.csv`, download: `anmeldungen-${raum.code}.csv` }, t('webinar.csv'))));
    const ul = h('ul', { class: 'liste webinar-anmeldeliste' });
    reg.append(ul);
    let r;
    try { r = await api(`/api/raeume/${raum.code}/webinar/anmeldungen`); } catch (e) { ul.append(h('li', { class: 'leer' }, e.message)); return; }
    const zahl = r.kapazitaet ? t('webinar.anmeldungenVon', { n: r.anmeldungen.length, max: r.kapazitaet }) : t('webinar.anmeldungenN', { n: r.anmeldungen.length });
    reg.querySelector('h4').textContent = `${t('webinar.anmeldungen')} · ${zahl}`;
    if (!r.anmeldungen.length) { ul.append(h('li', { class: 'leer' }, t('webinar.keineAnmeldungen'))); return; }
    for (const a of r.anmeldungen) {
      ul.append(h('li', { dataset: { anmeldung: a.id } },
        h('div', { class: 'text' }, h('b', {}, a.name), h('span', { class: 'sec klein' }, ` ${a.email}`),
          h('div', { class: 'sec klein' }, [t('webinar.angemeldetAm', { zeit: zeitText(a.angemeldet, zone()) }), a.beigetreten ? t('webinar.warDabei') : ''].filter(Boolean).join(' · ')),
          ...a.antworten.map((x, i) => (x ? h('div', { class: 'klein' }, h('span', { class: 'sec' }, `${r.fragen[i] || ''} `), x) : null))),
        knopf(t('konto.loeschen'), { klasse: 'icon-btn', symbol: 'muell', 'aria-label': t('webinar.anmeldungLoeschen', { name: a.name }), onclick: async () => {
          if (!await bestaetigen(t('webinar.anmeldungLoeschen', { name: a.name }), null, { ja: t('konto.loeschen'), gefahr: true })) return;
          beschaeftigt(null, async () => { await api(`/api/raeume/${raum.code}/webinar/anmeldungen/${a.id}`, { methode: 'DELETE' }); registrierungZeigen(); });
        } })));
    }
  }
  inhalt.append(f, h('h4', {}, t('webinar.panel')), h('p', { class: 'sec klein' }, t('webinar.panelHinweis')), panelListe, panelForm, reg);
  registrierungZeigen();
}
