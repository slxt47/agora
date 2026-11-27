// Konto → Profil: verknüpfte Anmeldungen (Google, Microsoft, GitHub, Firmen-Login) – verknüpfen (mit Passwort), trennen
// (nur mit Passwort im Konto). Konten ohne Passwort bekommen statt „Passwort ändern“ einen Link zum Festlegen per Mail.
// Eigenes Modul (eigenes <script> in konto.html).
import { $, h, api, ichLaden, toast, blatt, beschaeftigt, knopf, bestaetigen } from './common.js';
import { t } from './i18n.js';
import { datumText } from './helfer.js';
import { starten, fehlerText } from './anmeldung-start.js';

let ich = null;
const statusFeld = () => h('div', { class: 'status', role: 'status', hidden: true });
const marke = (id, name) => h('span', { class: `extern-marke extern-${id}`, 'aria-hidden': 'true' }, name.slice(0, 1));

async function laden() {
  const box = $('#anmeldungen');
  const ul = $('#liste-anmeldungen');
  if (!box || !ul) return;
  let d;
  try { d = await api('/api/konto/anmeldungen'); } catch (e) { ul.replaceChildren(h('li', { class: 'leer' }, e.message)); return; }
  const offen = d.verfuegbar.filter((a) => !d.verknuepft.some((v) => v.anbieter === a.id));
  box.hidden = !d.verknuepft.length && !offen.length;
  ul.replaceChildren(
    ...d.verknuepft.map((v) => h('li', { dataset: { anbieter: v.anbieter } },
      h('div', { class: 'kopf' }, marke(v.anbieter, v.name),
        h('div', { class: 'text' }, h('span', { class: 'name' }, v.name),
          h('span', { class: 'info' }, t('anmeldung.verknuepftSeit', { email: v.email, datum: datumText(v.erstellt, ich?.zeitzone) })))),
      h('div', { class: 'aktionen' }, d.passwort_gesetzt
        ? knopf(t('anmeldung.trennen'), { klasse: 'btn klein', onclick: () => trennen(v) })
        : h('span', { class: 'sec klein' }, t('anmeldung.trennenOhnePasswort'))))),
    ...offen.map((a) => h('li', { dataset: { anbieter: a.id } },
      h('div', { class: 'kopf' }, marke(a.id, a.name), h('div', { class: 'text' }, h('span', { class: 'name' }, a.name),
        h('span', { class: 'info' }, t('anmeldung.nichtVerknuepft')))),
      h('div', { class: 'aktionen' }, knopf(t('anmeldung.verknuepfen'), { klasse: 'btn klein', onclick: () => verknuepfen(a, d.passwort_gesetzt) })))));
}

function verknuepfen(a, mitPasswort) {
  if (!mitPasswort) { beschaeftigt(null, () => starten(a.id, { verknuepfen: true })); return; }
  const { inhalt, schliessen } = blatt(t('anmeldung.verknuepfenMit', { name: a.name }));
  const st = statusFeld();
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec' }, t('anmeldung.verknuepfenProfilText', { name: a.name })),
    h('input', { type: 'text', name: 'benutzer', autocomplete: 'username', hidden: true, value: ich?.email || '' }),
    h('label', { class: 'feld' }, h('span', {}, t('konto.passwortBestaetigen')),
      h('input', { name: 'passwort', type: 'password', autocomplete: 'current-password', required: true })),
    st,
    h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn primary' }, t('anmeldung.weiterZu', { name: a.name }))));
  inhalt.append(f);
  f.passwort.focus();
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!f.reportValidity()) return;
    beschaeftigt(f.querySelector('button[type="submit"]'), () => starten(a.id, { verknuepfen: true, passwort: f.passwort.value }), st);
  });
}

async function trennen(v) {
  if (!await bestaetigen(t('anmeldung.trennenTitel', { name: v.name }), t('anmeldung.trennenText', { name: v.name }), { ja: t('anmeldung.trennen'), gefahr: true })) return;
  await beschaeftigt(null, async () => {
    await api(`/api/konto/anmeldungen/${v.id}`, { methode: 'DELETE' });
    toast(t('anmeldung.getrennt', { name: v.name }), 'ok');
    dispatchEvent(new Event('agora:sicherheit-neu'));
    laden();
  });
}

/** Konto ohne Passwort: „Passwort ändern“ geht nicht – stattdessen Link zum Festlegen per Mail (beweist das Postfach). */
function passwortFestlegen() {
  const f = $('#f-pw');
  if (!f || ich?.passwort_gesetzt !== false) return;
  f.replaceWith(h('div', { class: 'formular', id: 'pw-festlegen' },
    h('p', { class: 'sec klein' }, t('anmeldung.ohnePasswort')),
    h('div', { class: 'knoepfe' }, knopf(t('anmeldung.passwortLink'), { klasse: 'btn', symbol: 'mail', onclick: (e) => beschaeftigt(e.currentTarget, async () => {
      await api('/api/konto/passwort-vergessen', { methode: 'POST', json: { email: ich.email } });
      toast(t('anmeldung.passwortLinkGesendet', { email: ich.email }), 'ok', { dauer: 8000 });
    }) }))));
  $('#h-pw').textContent = t('anmeldung.passwortFestlegen');
}

// ---------------------------------------------------------------- Start
(async () => {
  ich = await ichLaden();
  if (!ich) return;               // konto.js leitet zur Anmeldung weiter
  passwortFestlegen();
  const p = new URLSearchParams(location.search);
  const namen = { google: 'Google', microsoft: 'Microsoft', github: 'GitHub', firma: t('anmeldung.firmenkonto') };
  if (p.has('verknuepft')) toast(t('anmeldung.verknuepftOk', { name: namen[p.get('verknuepft')] || '' }), 'ok');
  if (p.get('anmeldung') === 'fehler') toast(fehlerText(p.get('grund'), namen[p.get('anbieter')] || ''), 'err', { dauer: 9000 });
  if (p.has('verknuepft') || p.has('anmeldung')) {
    for (const k of ['verknuepft', 'anmeldung', 'grund', 'anbieter']) p.delete(k);
    history.replaceState(null, '', location.pathname + (p.toString() ? `?${p}` : '') + location.hash);
  }
  await laden();
})();
