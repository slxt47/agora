// Admin-Bereich (/admin, nur für Admins – der Server liefert sonst 404): Übersicht, Konten (suchen, sperren,
// 2FA zurücksetzen, löschen), Meldungen (bearbeiten mit Notiz), laufende Meetings (beenden), Protokoll (Filter).
import './admin.css';
import { $, $$, h, api, toast, blatt, bestaetigen, beschaeftigt, status, knopf } from './common.js';
import { t, gebiet } from './i18n.js';
import { icon } from './icons.js';
import { groesseText, zeitText } from './helfer.js';
import { adminZahl } from './admin-format.js';

const ZONE = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Vienna'; } catch { return 'Europe/Vienna'; } })();
const zeit = (iso) => (iso ? zeitText(iso, ZONE) : '–');

// ---------------------------------------------------------------- Reiter
const REITER = ['uebersicht', 'konten', 'meldungen', 'meetings', 'protokoll'];
const LADEN = { uebersicht: uebersichtLaden, konten: () => kontenLaden(), meldungen: meldungenLaden, meetings: meetingsLaden, protokoll: () => protokollLaden() };
function reiter(name, fokus = false) {
  if (!REITER.includes(name)) name = 'uebersicht';
  for (const r of REITER) {
    $(`#p-${r}`).hidden = r !== name;
    $(`#tab-${r}`).setAttribute('aria-selected', String(r === name));
    $(`#tab-${r}`).tabIndex = r === name ? 0 : -1;
  }
  if (fokus) $(`#tab-${name}`).focus();
  if (location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
  LADEN[name]();
}
for (const r of REITER) $(`#tab-${r}`).addEventListener('click', () => reiter(r));
$('.admin-reiter').addEventListener('keydown', (e) => {
  const i = REITER.findIndex((r) => $(`#tab-${r}`).getAttribute('aria-selected') === 'true');
  if (e.key === 'ArrowRight') reiter(REITER[(i + 1) % REITER.length], true);
  if (e.key === 'ArrowLeft') reiter(REITER[(i + REITER.length - 1) % REITER.length], true);
});
addEventListener('hashchange', () => reiter(location.hash.slice(1)));

const fehlerZeile = (ul, e) => ul.replaceChildren(h('li', { class: 'leer' }, e.message));

// ---------------------------------------------------------------- Übersicht
function kachel(titel, wert, unter, art = '') {
  return h('div', { class: `admin-kachel${art ? ' ' + art : ''}` }, h('span', { class: 'titel' }, titel), h('b', {}, wert), unter ? h('span', { class: 'sec klein' }, unter) : null);
}

async function uebersichtLaden() {
  const box = $('#zahlen');
  try {
    const u = await api('/api/admin/uebersicht');
    $('#stand').textContent = t('admin.stand', { zeit: new Intl.DateTimeFormat(gebiet(), { timeStyle: 'short' }).format(new Date()) });
    meldungenZahl(u.meldungen_offen);
    box.replaceChildren(
      kachel(t('admin.laufend'), adminZahl(u.laufend.meetings), u.laufend.livekit ? t('admin.teilnehmerLive', { n: adminZahl(u.laufend.teilnehmer) }) : t('admin.livekitAus'), u.laufend.livekit ? '' : 'warn'),
      kachel(t('admin.meetings7'), adminZahl(u.meetings.tage_7), t('admin.meetings30', { n: adminZahl(u.meetings.tage_30) })),
      kachel(t('admin.konten'), adminZahl(u.konten.bestaetigt), t('admin.kontenUnter', { gesamt: adminZahl(u.konten.gesamt), neu: adminZahl(u.konten.neu_7) })),
      kachel(t('admin.sicherheit'), adminZahl(u.konten.zwei_faktor), t('admin.sicherheitUnter', { gesperrt: adminZahl(u.konten.gesperrt) })),
      kachel(t('admin.aufnahmen'), groesseText(u.aufnahmen.belegt), t('admin.aufnahmenUnter', { n: adminZahl(u.aufnahmen.anzahl) })),
      kachel(t('admin.meldungenOffen'), adminZahl(u.meldungen_offen), t('admin.meldungenUnter'), u.meldungen_offen ? 'warn' : ''),
    );
  } catch (e) { box.replaceChildren(h('p', { class: 'leer' }, e.message)); }
}

function meldungenZahl(n) {
  const z = $('#meldungen-zahl');
  z.hidden = !n;
  z.textContent = String(n || '');
}

// ---------------------------------------------------------------- Konten
let suche = '', kontenSeite = 0;
$('#f-suche').addEventListener('submit', (e) => { e.preventDefault(); suche = $('#suche').value.trim(); kontenLaden(0); });

async function kontenLaden(seite = kontenSeite) {
  kontenSeite = seite;
  const ul = $('#liste-konten');
  try {
    const r = await api(`/api/admin/benutzer?${new URLSearchParams({ q: suche, seite })}`);
    $('#konten-info').textContent = t('admin.kontenGefunden', { n: adminZahl(r.gesamt) });
    ul.replaceChildren(...(r.benutzer.length ? r.benutzer.map(kontoEintrag) : [h('li', { class: 'leer' }, t('admin.keineKonten'))]));
    seiten($('#konten-seiten'), seite, (seite + 1) * r.seite_groesse < r.gesamt, kontenLaden);
  } catch (e) { fehlerZeile(ul, e); }
}

function seiten(box, seite, weitere, laden) {
  box.replaceChildren(
    seite > 0 ? knopf(t('admin.zurueck'), { klasse: 'btn klein', symbol: 'pfeilLinks', onclick: () => laden(seite - 1) }) : null,
    weitere ? knopf(t('admin.weiter'), { klasse: 'btn klein', symbol: 'pfeilRechts', onclick: () => laden(seite + 1) }) : null);
}

function kontoEintrag(b) {
  const marker = [];
  if (b.admin) marker.push(h('span', { class: 'marker ok' }, t('admin.istAdmin')));
  if (b.gesperrt) marker.push(h('span', { class: 'marker warn' }, t('admin.gesperrt')));
  if (!b.bestaetigt) marker.push(h('span', { class: 'marker' }, t('admin.unbestaetigt')));
  if (b.zwei_faktor) marker.push(h('span', { class: 'marker' }, t('admin.zweiFaktor')));
  const info = [t('admin.seit', { zeit: zeit(b.erstellt) }), t('admin.letzteAnmeldung', { zeit: zeit(b.letzte_anmeldung) }),
    t('admin.raeumeN', { n: b.raeume }), groesseText(b.aufnahmen_belegt)];
  const aktionen = h('div', { class: 'aktionen' });
  if (!b.admin) {
    aktionen.append(
      knopf(b.gesperrt ? t('admin.entsperren') : t('admin.sperren'), { klasse: 'btn klein' + (b.gesperrt ? '' : ' gefahr'), symbol: b.gesperrt ? 'schlossOffen' : 'schloss',
        dataset: { aktion: 'sperren' }, onclick: () => sperren(b) }),
      b.zwei_faktor ? knopf(t('admin.zweiFaktorZurueck'), { klasse: 'btn klein', onclick: () => zweiFaktorZurueck(b) }) : null,
      knopf(t('admin.loeschen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: () => kontoLoeschen(b) }));
  }
  return h('li', { dataset: { konto: b.id } },
    h('div', { class: 'kopf' }, h('div', { class: 'text' },
      h('span', { class: 'name' }, b.name, h('span', { class: 'sec' }, ` · #${b.id}`)),
      h('span', { class: 'info mono' }, b.email),
      h('span', { class: 'info' }, info.join(' · ')))),
    marker.length ? h('div', { class: 'zeile' }, marker) : null,
    aktionen);
}

async function sperren(b) {
  const an = !b.gesperrt;
  if (an && !await bestaetigen(t('admin.sperrenTitel', { name: b.name }), t('admin.sperrenText'), { ja: t('admin.sperren'), gefahr: true })) return;
  await beschaeftigt(null, async () => {
    await api(`/api/admin/benutzer/${b.id}/sperren`, { methode: 'POST', json: { an } });
    toast(an ? t('admin.gesperrtOk', { name: b.name }) : t('admin.entsperrtOk', { name: b.name }), 'ok');
    kontenLaden();
  });
}

async function zweiFaktorZurueck(b) {
  if (!await bestaetigen(t('admin.zweiFaktorZurueck'), t('admin.zweiFaktorZurueckText', { name: b.name }), { ja: t('admin.zuruecksetzen'), gefahr: true })) return;
  await beschaeftigt(null, async () => { await api(`/api/admin/benutzer/${b.id}/2fa-zuruecksetzen`, { methode: 'POST' }); toast(t('admin.erledigt'), 'ok'); kontenLaden(); });
}

function kontoLoeschen(b) {
  const { inhalt, schliessen } = blatt(t('admin.loeschenTitel', { name: b.name }));
  const st = h('div', { class: 'status', role: 'status', hidden: true });
  const feld = h('input', { name: 'email', autocomplete: 'off', spellcheck: 'false', autocapitalize: 'none', required: true });
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec' }, t('admin.loeschenText')),
    h('label', { class: 'feld' }, h('span', {}, t('admin.loeschenEmail', { email: b.email })), feld),
    st,
    h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn gefahr voll' }, t('admin.endgueltigLoeschen'))));
  inhalt.append(f);
  feld.focus();
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    if (feld.value.trim().toLowerCase() !== b.email.toLowerCase()) { status(st, t('admin.loeschenFalsch'), 'err'); return; }
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      await api(`/api/admin/benutzer/${b.id}`, { methode: 'DELETE' });
      schliessen();
      toast(t('admin.geloescht'), 'ok');
      kontenLaden();
    }, st);
  });
}

// ---------------------------------------------------------------- Meldungen
for (const r of $$('input[name="m-status"]')) r.addEventListener('change', meldungenLaden);

async function meldungenLaden() {
  const ul = $('#liste-meldungen');
  const st = $('input[name="m-status"]:checked').value;
  try {
    const { meldungen } = await api(`/api/admin/meldungen?status=${st}`);
    if (st === 'offen') meldungenZahl(meldungen.length);
    ul.replaceChildren(...(meldungen.length ? meldungen.map(meldungEintrag) : [h('li', { class: 'leer' }, t('admin.keineMeldungen'))]));
  } catch (e) { fehlerZeile(ul, e); }
}

function meldungEintrag(m) {
  const wer = (name, k) => `${name}${k ? ` (${k.email}${k.gesperrt ? `, ${t('admin.gesperrt')}` : ''})` : ` (${t('admin.gast')})`}`;
  return h('li', { class: 'admin-meldung', dataset: { meldung: m.id } },
    h('div', { class: 'kopf' },
      h('div', { class: 'symbol', html: icon('flagge') }),
      h('div', { class: 'text' },
        h('span', { class: 'name' }, m.grund_text, h('span', { class: 'sec' }, ` · #${m.id}`)),
        h('span', { class: 'info' }, `${zeit(m.zeit)} · ${m.raum_titel || ''} `, h('span', { class: 'mono' }, m.raum_code)))),
    h('dl', { class: 'admin-dl' },
      h('dt', {}, t('admin.gemeldet')), h('dd', {}, wer(m.ziel_name, m.ziel_konto)),
      h('dt', {}, t('admin.von')), h('dd', {}, wer(m.von_name, m.von_konto)),
      m.text ? [h('dt', {}, t('admin.textFeld')), h('dd', { class: 'admin-text' }, m.text)] : null,
      m.notiz ? [h('dt', {}, t('admin.notiz')), h('dd', {}, `${m.notiz} (${zeit(m.bearbeitet)})`)] : null),
    h('div', { class: 'aktionen' },
      m.status === 'offen'
        ? knopf(t('admin.bearbeiten'), { klasse: 'btn primary klein', symbol: 'haken', onclick: () => meldungDialog(m) })
        : h('span', { class: 'marker ok' }, t('admin.erledigtMarker')),
      m.status !== 'offen' ? knopf(t('admin.wiederOeffnen'), { klasse: 'btn klein', onclick: () => meldungSetzen(m, 'offen', m.notiz) }) : null,
      m.ziel_konto ? knopf(t('admin.kontoZeigen'), { klasse: 'btn klein', symbol: 'person', onclick: () => { suche = m.ziel_konto.email; $('#suche').value = suche; reiter('konten'); } }) : null));
}

function meldungDialog(m) {
  const { inhalt, schliessen } = blatt(t('admin.meldungTitel', { id: m.id }));
  const st = h('div', { class: 'status', role: 'status', hidden: true });
  const notiz = h('textarea', { name: 'notiz', rows: '3', maxlength: '2000', placeholder: t('admin.notizPlatzhalter') }, m.notiz || '');
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec' }, `${m.grund_text}: ${m.ziel_name}`),
    h('label', { class: 'feld' }, h('span', {}, t('admin.notiz')), notiz),
    st,
    h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn primary' }, t('admin.abschliessen'))));
  inhalt.append(f);
  notiz.focus();
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => { await meldungSetzen(m, 'erledigt', notiz.value.trim()); schliessen(); }, st);
  });
}

async function meldungSetzen(m, st, notiz) {
  await api(`/api/admin/meldungen/${m.id}`, { methode: 'POST', json: { status: st, notiz: notiz || '' } });
  toast(t('admin.erledigt'), 'ok');
  meldungenLaden();
}

// ---------------------------------------------------------------- Meetings
$('#meetings-neu').addEventListener('click', meetingsLaden);

async function meetingsLaden() {
  const ul = $('#liste-meetings');
  try {
    const { meetings } = await api('/api/admin/meetings');
    ul.replaceChildren(...(meetings.length ? meetings.map((r) => h('li', { dataset: { code: r.code } },
      h('div', { class: 'kopf' }, h('div', { class: 'text' },
        h('span', { class: 'name' }, r.titel),
        h('span', { class: 'info' }, [t('admin.seit', { zeit: zeit(r.gestartet) }), t('admin.teilnehmerN', { n: r.teilnehmer }),
          `${r.besitzer_name} (${r.besitzer_email})`].join(' · ')),
        h('span', { class: 'info mono' }, r.code))),
      r.aufnahme ? h('div', { class: 'zeile' }, h('span', { class: 'marker warn' }, t('admin.aufnahmeLaeuft'))) : null,
      h('div', { class: 'aktionen' }, knopf(t('admin.meetingBeenden'), { klasse: 'btn klein gefahr', symbol: 'auflegen', onclick: () => meetingBeenden(r) }))))
      : [h('li', { class: 'leer' }, t('admin.keineMeetings'))]));
  } catch (e) { fehlerZeile(ul, e); }
}

async function meetingBeenden(r) {
  if (!await bestaetigen(t('admin.meetingBeenden'), t('admin.meetingBeendenText', { titel: r.titel }), { ja: t('admin.meetingBeenden'), gefahr: true })) return;
  await beschaeftigt(null, async () => { await api(`/api/admin/meetings/${r.code}/beenden`, { methode: 'POST' }); toast(t('admin.erledigt'), 'ok'); meetingsLaden(); });
}

// ---------------------------------------------------------------- Protokoll
let protokollSeite = 0;
$('#f-protokoll').addEventListener('submit', (e) => { e.preventDefault(); protokollLaden(0); });

async function protokollLaden(seite = protokollSeite) {
  protokollSeite = seite;
  const f = $('#f-protokoll');
  const params = new URLSearchParams({ q: f.q.value.trim(), benutzer: f.benutzer.value.trim(), raum: f.raum.value.trim(), seite });
  if (f.nur_admin.checked) params.set('nur_admin', '1');
  const tb = $('#tabelle-protokoll tbody');
  try {
    const r = await api(`/api/admin/protokoll?${params}`);
    tb.replaceChildren(...(r.eintraege.length ? r.eintraege.map((x) => h('tr', { class: x.ereignis.startsWith('Admin:') ? 'admin-zeile' : '' },
      h('td', { class: 'nowrap' }, zeit(x.zeit)),
      h('td', {}, x.email || (x.benutzer_id ? `#${x.benutzer_id}` : '–')),
      h('td', { class: 'mono' }, x.raum_code || ''),
      h('td', {}, x.ereignis))) : [h('tr', {}, h('td', { colspan: '4', class: 'leer' }, t('admin.keineEintraege')))]));
    seiten($('#protokoll-seiten'), seite, r.weitere, protokollLaden);
  } catch (e) { tb.replaceChildren(h('tr', {}, h('td', { colspan: '4', class: 'leer' }, e.message))); }
}

// ---------------------------------------------------------------- Start
reiter(location.hash.slice(1));
