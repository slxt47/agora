// Konto: Meetings (persönlicher Raum, anstehend, vergangen; planen/bearbeiten/löschen/einladen/.ics),
// Aufnahmen (Player, Download, Freigabe-Link mit Ablauf/Passwort, Speicherbalken), Profil (Name, Zeitzone, Sprache),
// Passwort ändern, Export, Konto löschen.
import { $, h, api, ichLaden, toast, blatt, bestaetigen, kopieren, beschaeftigt, status, knopf } from './common.js';
import { t } from './i18n.js';
import { icon } from './icons.js';
import {
  initialen, zeitText, minutenText, wiederholungText, lokalZuUtc, utcZuLokal, groesseText, datumText, uhrzeitText,
  passcodeVorschlag, emailsAusText, linkTeile,
} from './helfer.js';
import { kalenderDialog, kalenderAboEinrichten } from './kalender.js';
import { vorlagenEinrichten, alsVorlage, passcodeZeigen } from './vorlagen.js';
import { erinnerungEinrichten, einladenErinnerung } from './erinnerung.js';
import { verlaufEinrichten } from './verlauf.js';
import { qualitaetLaden, qualitaetMarke } from './qualitaet-konto.js';
import { webinarKnopf, webinarMarke } from './webinar-konto.js';      // Webinar + Registrierung
import { transkriptKnopf, aufnahmeTranskript } from './transkript-konto.js';   // Untertitel-Transkripte, Whisper

let ich = null;
const browserZone = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Vienna'; } catch { return 'Europe/Vienna'; } })();
const zone = () => ich?.zeitzone || browserZone;
const raumLink = (raum) => raum.link || `${location.origin}/r/${raum.code}`;

// ---------------------------------------------------------------- Reiter (#meetings, #aufnahmen, #profil, #planen)
const REITER = ['meetings', 'aufnahmen', 'profil'];
function reiter(name, fokus = false) {
  if (!REITER.includes(name)) name = 'meetings';
  for (const r of REITER) {
    $(`#p-${r}`).hidden = r !== name;
    $(`#tab-${r}`).setAttribute('aria-selected', String(r === name));
    $(`#tab-${r}`).tabIndex = r === name ? 0 : -1;
  }
  if (fokus) $(`#tab-${name}`).focus();
  if (location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
  if (name === 'aufnahmen') aufnahmenLaden();
}
for (const r of REITER) $(`#tab-${r}`).addEventListener('click', () => reiter(r));
$('.reiter').addEventListener('keydown', (e) => {
  const i = REITER.findIndex((r) => $(`#tab-${r}`).getAttribute('aria-selected') === 'true');
  if (e.key === 'ArrowRight') reiter(REITER[(i + 1) % REITER.length], true);
  if (e.key === 'ArrowLeft') reiter(REITER[(i + REITER.length - 1) % REITER.length], true);
});
addEventListener('hashchange', () => {
  const ziel = location.hash.slice(1);
  if (ziel === 'planen') { reiter('meetings'); planenDialog(); } else reiter(ziel);
});

// ---------------------------------------------------------------- Meetings
async function meetingsLaden() {
  const [r] = await Promise.all([api('/api/raeume'), qualitaetLaden()]);
  persoenlichZeigen(r.persoenlich);
  listeZeigen($('#liste-geplant'), r.geplant || [], false);
  listeZeigen($('#liste-vergangen'), r.vergangen || [], true);
}

function merkmale(raum) {
  const m = [];
  if (raum.aktiv) m.push(h('span', { class: 'marker ok' }, t('konto.laeuft')));
  if (raum.passcode_noetig) m.push(h('span', { class: 'marker' }, t('konto.passcode')));
  if (raum.warteraum) m.push(h('span', { class: 'marker' }, t('konto.warteraum')));
  if (raum.chat_speichern) m.push(h('span', { class: 'marker' }, t('konto.chatGespeichert')));
  if (raum.dauer_chat) m.push(h('span', { class: 'marker' }, t('dauerchat.marker')));
  if (webinarMarke(raum)) m.push(webinarMarke(raum));
  return m.length ? h('div', { class: 'zeile' }, m) : null;
}

function persoenlichZeigen(raum) {
  const box = $('#persoenlich');
  box.replaceChildren();
  if (!raum) { box.append(h('p', { class: 'leer' }, t('konto.keinPersoenlich'))); return; }
  box.append(
    h('p', { class: 'sec klein' }, t('konto.persoenlichText')),
    h('div', { class: 'link-feld oben' }, raumLink(raum)),
    ...(merkmale(raum) ? [h('div', { class: 'oben' }, merkmale(raum))] : []),
    ...(qualitaetMarke(raum, zone()) ? [h('div', { class: 'oben' }, qualitaetMarke(raum, zone()))] : []),
    h('div', { class: 'aktionen oben' },
      h('a', { class: 'btn primary', href: `/r/${raum.code}` }, t('konto.starten')),
      knopf(t('konto.linkKopieren'), { symbol: 'kopie', onclick: () => kopieren(raumLink(raum), t('konto.linkKopiert')) }),
      knopf(t('konto.einladen'), { symbol: 'mail', onclick: () => einladenDialog(raum) }),
      knopf(t('konto.einstellungen'), { symbol: 'zahnrad', onclick: () => planenDialog(raum, { persoenlich: true }) }),
      webinarKnopf(raum, 'btn'),
      transkriptKnopf(raum, 'btn'),
      raum.dauer_chat ? knopf(t('dauerchat.leeren'), { klasse: 'btn gefahr', symbol: 'muell', onclick: async () => {
        if (!await bestaetigen(t('dauerchat.leerenTitel'), t('dauerchat.leerenText'), { ja: t('dauerchat.leeren'), gefahr: true })) return;
        await beschaeftigt(null, async () => { await api(`/api/raeume/${raum.code}/dauer-chat`, { methode: 'DELETE' }); toast(t('dauerchat.geleert'), 'ok'); });
      } }) : null,
    ),
  );
}

function listeZeigen(ul, raeume, vergangen) {
  ul.replaceChildren();
  if (!raeume.length) {
    ul.append(h('li', { class: 'leer' }, vergangen ? t('konto.keineVergangenen') : t('konto.keineGeplanten'),
      vergangen ? null : h('div', { class: 'oben' }, knopf(t('konto.planen'), { klasse: 'btn primary', symbol: 'kalender', onclick: () => planenDialog() }))));
    return;
  }
  for (const raum of raeume) ul.append(raumEintrag(raum, vergangen));
}

function raumEintrag(raum, vergangen) {
  const g = raum.geplant;
  const termin = raum.naechster_termin || g?.start;
  const zeilen = [];
  if (termin) zeilen.push(zeitText(termin, zone()));
  if (g?.dauer_min) zeilen.push(minutenText(g.dauer_min));
  if (g) zeilen.push(wiederholungText(g));
  if (g?.zeitzone && g.zeitzone !== zone()) zeilen.push(t('konto.inZone', { zone: g.zeitzone.replace(/_/g, ' ') }));
  const aktionen = h('div', { class: 'aktionen' },
    h('a', { class: 'btn primary klein', href: `/r/${raum.code}` }, vergangen ? t('konto.erneutStarten') : t('konto.starten')),
    knopf(t('konto.linkKopieren'), { klasse: 'btn klein', symbol: 'kopie', onclick: () => kopieren(raumLink(raum), t('konto.linkKopiert')) }),
  );
  if (!vergangen) {
    aktionen.append(
      knopf(t('konto.einladen'), { klasse: 'btn klein', symbol: 'mail', onclick: () => einladenDialog(raum) }),
      raum.kalender
        ? knopf(t('kal.knopf'), { klasse: 'btn klein', symbol: 'kalender', 'data-kalender': raum.code, onclick: () => kalenderDialog(raum) })
        : null,
      knopf(t('konto.bearbeiten'), { klasse: 'btn klein', symbol: 'stift', onclick: () => planenDialog(raum) }),
      knopf(t('vorlage.alsVorlage'), { klasse: 'btn klein', symbol: 'stern', onclick: () => alsVorlage(raum) }),
      webinarKnopf(raum),
      transkriptKnopf(raum),
    );
  } else {
    aktionen.append(knopf(t('konto.chatverlauf'), { klasse: 'btn klein', symbol: 'chat', onclick: () => verlaufDialog(raum) }));
  }
  aktionen.append(knopf(t('konto.loeschen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: () => raumLoeschen(raum) }));
  return h('li', {},
    h('div', { class: 'kopf' },
      h('div', { class: 'symbol', html: icon(vergangen ? 'uhr' : 'kalender') }),
      h('div', { class: 'text' },
        h('span', { class: 'name' }, raum.titel || t('konto.ohneTitel')),
        h('span', { class: 'info' }, zeilen.join(' · ')),
        h('span', { class: 'info mono' }, raum.code))),
    g?.beschreibung ? h('p', { class: 'sec klein' }, g.beschreibung) : null,
    merkmale(raum),
    qualitaetMarke(raum, zone()),
    aktionen);
}

async function raumLoeschen(raum) {
  if (!await bestaetigen(t('konto.loeschenTitel'), t('konto.loeschenText', { titel: raum.titel || raum.code }), { ja: t('konto.loeschen'), gefahr: true })) return;
  await beschaeftigt(null, async () => {
    await api(`/api/raeume/${raum.code}`, { methode: 'DELETE' });
    toast(t('konto.geloescht'), 'ok');
    await meetingsLaden();
  });
}

// ---------------------------------------------------------------- Planen / Bearbeiten
function zonenListe() {
  let zonen = [];
  try { zonen = Intl.supportedValuesOf('timeZone'); } catch { /* alt */ }
  if (!zonen.length) zonen = ['Europe/Vienna', 'Europe/Berlin', 'Europe/Zurich', 'Europe/London', 'America/New_York', 'UTC'];
  for (const z of [browserZone, ich?.zeitzone, 'UTC']) if (z && !zonen.includes(z)) zonen.unshift(z);
  return zonen;
}
function zonenSelect(name, wert) {
  return h('select', { name }, zonenListe().map((z) => h('option', { value: z, selected: z === wert }, z.replace(/_/g, ' '))));
}
function naechsteHalbeStunde() {
  const d = new Date(Date.now() + 15 * 60000);
  d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0);
  return d.toISOString();
}

function planenDialog(raum = null, { persoenlich = false, vorlage = null } = {}) {
  if (location.hash === '#planen') history.replaceState(null, '', '#meetings');
  const ve = vorlage?.einstellungen;          // Planen aus Vorlage: Felder vorbelegen, Rechte/Stumm setzt der Server
  if (ve) raum = null;
  const g = raum?.geplant || (ve ? { dauer_min: ve.dauer_min, beschreibung: ve.beschreibung } : null);
  const z = g?.zeitzone || browserZone;
  const start = utcZuLokal(raum?.naechster_termin || g?.start || naechsteHalbeStunde(), z);
  const bis = g?.bis ? utcZuLokal(g.bis, z).datum : '';
  const titel = persoenlich ? t('konto.persoenlichEinstellungen') : raum ? t('konto.bearbeitenTitel')
    : ve ? t('vorlage.planenTitel', { name: vorlage.name }) : t('konto.planenTitel');
  const { inhalt, schliessen } = blatt(titel);
  const dauer = g?.dauer_min || 60;
  const dauerWerte = [15, 30, 45, 60, 90, 120, 180, 240, 480];
  if (!dauerWerte.includes(dauer)) dauerWerte.push(dauer);
  const statusEl = h('div', { class: 'status', role: 'status', hidden: true });
  const f = h('form', { class: 'formular', novalidate: true },
    persoenlich ? null : h('label', { class: 'feld' }, h('span', {}, t('konto.feldTitel')),
      h('input', { name: 'titel', required: true, maxlength: '120', value: raum?.titel || ve?.titel || '', placeholder: t('konto.titelBeispiel') })),
    persoenlich ? null : h('div', { class: 'zwei' },
      h('label', { class: 'feld' }, h('span', {}, t('konto.feldDatum')), h('input', { name: 'datum', type: 'date', required: true, value: start.datum })),
      h('label', { class: 'feld' }, h('span', {}, t('konto.feldUhrzeit')), h('input', { name: 'zeit', type: 'time', required: true, step: '300', value: start.zeit }))),
    persoenlich ? null : h('div', { class: 'zwei' },
      h('label', { class: 'feld' }, h('span', {}, t('konto.feldDauer')),
        h('select', { name: 'dauer' }, dauerWerte.sort((a, b) => a - b).map((m) => h('option', { value: m, selected: m === dauer }, minutenText(m))))),
      h('label', { class: 'feld' }, h('span', {}, t('konto.feldZeitzone')), zonenSelect('zeitzone', z))),
    persoenlich ? null : h('label', { class: 'feld' }, h('span', {}, t('konto.feldBeschreibung')),
      h('textarea', { name: 'beschreibung', maxlength: '2000', rows: '3' }, g?.beschreibung || '')),
    persoenlich ? null : h('div', { class: 'zwei' },
      h('label', { class: 'feld' }, h('span', {}, t('konto.feldWiederholung')),
        h('select', { name: 'wiederholung' },
          [['keine', t('konto.wdhKeine')], ['taeglich', t('konto.wdhTaeglich')], ['woechentlich', t('konto.wdhWoechentlich')]]
            .map(([w, text]) => h('option', { value: w, selected: (g?.wiederholung || 'keine') === w }, text)))),
      h('label', { class: 'feld', 'data-bis': '' }, h('span', {}, t('konto.feldBis')), h('input', { name: 'bis', type: 'date', value: bis }),
        h('small', {}, t('konto.bisHinweis')))),
    h('label', { class: 'feld' }, h('span', {}, t('konto.feldPasscode')),
      h('div', { class: 'beitreten' },
        h('input', { name: 'passcode', autocomplete: 'off', maxlength: '32', spellcheck: 'false', placeholder: raum?.passcode_noetig ? t('konto.passcodeUnveraendert') : t('konto.passcodeOptional') }),
        knopf(t('konto.passcodeErzeugen'), { klasse: 'btn klein', onclick: () => { f.passcode.value = passcodeVorschlag(); } })),
      h('small', {}, t('konto.passcodeHinweis'))),
    raum?.passcode_noetig ? schalter('passcode_weg', t('konto.passcodeEntfernen'), '', false) : null,
    schalter('warteraum', t('konto.feldWarteraum'), t('konto.warteraumHinweis'), raum ? !!raum.warteraum : !!ve?.warteraum),
    schalter('chat_speichern', t('konto.feldChatSpeichern'), t('konto.chatSpeichernHinweis'), raum ? !!raum.chat_speichern : !!ve?.chat_speichern),
    persoenlich ? schalter('dauer_chat', t('dauerchat.feld'), t('dauerchat.feldHinweis'), !!raum?.dauer_chat) : null,
    statusEl,
    h('div', { class: 'knoepfe' },
      h('button', { type: 'button', class: 'btn', onclick: () => schliessen() }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn primary' }, raum ? t('allg.speichern') : t('konto.planenSpeichern'))),
  );
  inhalt.append(f);
  const bisFeld = f.querySelector('[data-bis]');
  const wdh = () => { if (bisFeld) bisFeld.hidden = f.wiederholung.value === 'keine'; };
  f.wiederholung?.addEventListener('change', wdh); wdh();
  if (ve?.passcode) f.passcode.value = passcodeVorschlag();
  (f.titel || f.passcode).focus();

  f.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!f.reportValidity()) return;
    const body = { warteraum: f.warteraum.checked, chat_speichern: f.chat_speichern.checked };
    if (persoenlich) body.dauer_chat = f.dauer_chat.checked;
    if (!persoenlich) {
      body.titel = f.titel.value.trim();
      const startIso = lokalZuUtc(f.datum.value, f.zeit.value, f.zeitzone.value);
      if (!startIso) { status(statusEl, t('konto.zeitUngueltig'), 'err'); return; }
      if (!raum && new Date(startIso).getTime() < Date.now() - 5 * 60000) { status(statusEl, t('konto.zeitVergangen'), 'err'); return; }
      const bisIso = f.wiederholung.value !== 'keine' && f.bis.value ? f.bis.value : null;
      if (bisIso && lokalZuUtc(bisIso, '23:59', f.zeitzone.value) < startIso) { status(statusEl, t('konto.bisVorStart'), 'err'); return; }
      body.geplant = {
        start: startIso, dauer_min: Number(f.dauer.value), zeitzone: f.zeitzone.value,
        beschreibung: f.beschreibung.value.trim(), wiederholung: f.wiederholung.value, bis: bisIso,
      };
    }
    const pc = f.passcode.value.trim();
    if (pc) body.passcode = pc;
    else if (f.passcode_weg?.checked) body.passcode = '';
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const r = raum
        ? await api(`/api/raeume/${raum.code}`, { methode: 'PATCH', json: body })
        : await api(ve ? `/api/vorlagen/${vorlage.id}/raum` : '/api/raeume', { methode: 'POST', json: body });
      schliessen();
      if (ve && r.passcode) passcodeZeigen(r.raum, r.passcode);
      toast(raum ? t('konto.gespeichert') : t('konto.geplant'), 'ok', raum ? {} : { aktion: { text: t('konto.einladen'), fn: () => einladenDialog(r.raum) } });
      await meetingsLaden();
    }, statusEl);
  });
}

function schalter(name, text, hinweis, an) {
  return h('label', { class: 'schalter' },
    h('span', { class: 'txt' }, h('span', {}, text), hinweis ? h('small', {}, hinweis) : null),
    h('input', { type: 'checkbox', role: 'switch', name, checked: an }));
}

// ---------------------------------------------------------------- Einladen
function einladenDialog(raum) {
  const { inhalt, schliessen } = blatt(t('konto.einladenTitel'));
  const statusEl = h('div', { class: 'status', role: 'status', hidden: true });
  const feld = h('textarea', { name: 'emails', rows: '4', placeholder: 'anna@beispiel.at, ben@beispiel.de', autocapitalize: 'none', spellcheck: 'false' });
  const erinnerung = einladenErinnerung(raum);
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec klein' }, t('konto.einladenText', { titel: raum.titel || raum.code })),
    h('label', { class: 'feld' }, h('span', {}, t('konto.feldEmails')), feld, h('small', {}, t('konto.emailsHinweis'))),
    erinnerung.feld,
    h('div', { class: 'link-feld' }, raumLink(raum)),
    statusEl,
    h('div', { class: 'knoepfe' },
      knopf(t('konto.linkKopieren'), { symbol: 'kopie', onclick: () => kopieren(raumLink(raum), t('konto.linkKopiert')) }),
      h('button', { type: 'submit', class: 'btn primary' }, t('konto.einladungSenden'))));
  inhalt.append(f);
  feld.focus();
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    const { gueltig, ungueltig, zuViele } = emailsAusText(feld.value);
    if (ungueltig.length) { status(statusEl, t('konto.emailsUngueltig', { liste: ungueltig.slice(0, 3).join(', ') }), 'err'); return; }
    if (!gueltig.length) { status(statusEl, t('konto.emailsLeer'), 'err'); return; }
    if (zuViele) { status(statusEl, t('konto.emailsZuViele'), 'err'); return; }
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const r = await api(`/api/raeume/${raum.code}/einladen`, { methode: 'POST', json: { emails: gueltig, ...erinnerung.werte() } });
      schliessen();
      toast(t('konto.einladungenGesendet', { n: r.gesendet ?? gueltig.length }), 'ok');
    }, statusEl);
  });
}

// ---------------------------------------------------------------- Chatverlauf (gespeicherte Chats)
async function verlaufDialog(raum) {
  const { inhalt } = blatt(t('konto.verlaufTitel', { titel: raum.titel || raum.code }));
  inhalt.append(h('p', { class: 'sec' }, t('allg.laden')));
  try {
    const r = await api(`/api/raeume/${raum.code}/verlauf`);
    const n = r.nachrichten || [];
    inhalt.replaceChildren();
    if (!n.length) { inhalt.append(h('p', { class: 'leer' }, t('konto.verlaufLeer'))); return; }
    inhalt.append(h('ol', { class: 'chatverlauf' }, n.map((m) => h('li', {},
      h('span', { class: 'sec klein' }, `${datumText(m.zeit, zone())} ${uhrzeitText(m.zeit, zone())} · `),
      h('b', {}, m.von_name), m.an ? h('span', { class: 'sec' }, ` → ${m.an_name || t('chat.privat')}`) : null, ': ',
      ...linkTeile(m.text).map((x) => (x.link ? h('a', { href: x.link, target: '_blank', rel: 'noopener noreferrer' }, x.text) : x.text)),
    ))));
  } catch (e) {
    inhalt.replaceChildren(h('p', { class: 'leer' }, e.status === 404 ? t('konto.verlaufNichtGespeichert') : e.message));
  }
}

// ---------------------------------------------------------------- Aufnahmen
async function aufnahmenLaden() {
  const ul = $('#liste-aufnahmen');
  try {
    const r = await api('/api/aufnahmen');
    const belegt = r.belegt || 0, kontingent = r.kontingent || 1;
    const p = Math.min(100, Math.round((belegt / kontingent) * 100));
    $('#speicher-text').textContent = t('konto.speicherText', { belegt: groesseText(belegt), gesamt: groesseText(kontingent) });
    $('#speicher-prozent').textContent = `${p} %`;
    const balken = $('#speicher-balken');
    balken.setAttribute('aria-valuenow', String(p));
    balken.classList.toggle('voll', p >= 85);
    balken.firstElementChild.style.width = `${p}%`;
    ul.replaceChildren();
    if (!r.aufnahmen?.length) { ul.append(h('li', { class: 'leer' }, t('konto.keineAufnahmen'))); return; }
    for (const a of r.aufnahmen) ul.append(aufnahmeEintrag(a));
  } catch (e) {
    ul.replaceChildren(h('li', { class: 'leer' }, e.message));
  }
}

function aufnahmeEintrag(a) {
  const datei = `/api/aufnahmen/${a.id}/datei?download=1`;
  const endung = /mp4/.test(a.typ || '') ? 'mp4' : 'webm';
  const info = [zeitText(a.erstellt, zone()), groesseText(a.groesse)];
  if (a.ablauf) info.push(t('konto.loeschtAm', { datum: datumText(a.ablauf, zone()) }));
  const li = h('li', {},
    h('div', { class: 'kopf' },
      h('div', { class: 'symbol', html: icon('aufnahme') }),
      h('div', { class: 'text' },
        h('span', { class: 'name' }, a.titel || t('konto.ohneTitel')),
        h('span', { class: 'info' }, info.join(' · ')),
        a.raum_code ? h('span', { class: 'info mono' }, a.raum_code) : null)),
  );
  if (a.freigabe) {
    li.append(h('div', { class: 'einsatz' },
      h('div', { class: 'zeile' }, h('span', { class: 'marker ok' }, t('konto.freigegeben')),
        h('span', { class: 'sec klein' }, t('konto.freigabeBis', { datum: zeitText(a.freigabe.bis, zone()) })),
        a.freigabe.passwort ? h('span', { class: 'marker' }, t('konto.mitPasswort')) : null),
      h('div', { class: 'link-feld' }, absolut(a.freigabe.link))));
  }
  li.append(h('div', { class: 'aktionen' },
    knopf(t('konto.abspielen'), { klasse: 'btn primary klein', symbol: 'abspielen', onclick: () => spielerDialog(a) }),
    h('a', { class: 'btn klein', href: datei, download: `${(a.titel || 'aufnahme').replace(/[^\p{L}\p{N}\- ]/gu, '_')}.${endung}`, html: icon('herunter') }, h('span', {}, t('konto.herunterladen'))),
    a.freigabe
      ? knopf(t('konto.linkKopieren'), { klasse: 'btn klein', symbol: 'kopie', onclick: () => kopieren(absolut(a.freigabe.link), t('konto.linkKopiert')) })
      : null,
    a.freigabe
      ? knopf(t('konto.freigabeBeenden'), { klasse: 'btn klein', symbol: 'schliessen', onclick: () => freigabeBeenden(a) })
      : knopf(t('konto.teilen'), { klasse: 'btn klein', symbol: 'teilenLink', onclick: () => freigabeDialog(a) }),
    ...aufnahmeTranskript(a, aufnahmenLaden),
    knopf(t('konto.loeschen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: () => aufnahmeLoeschen(a) })));
  return li;
}
const absolut = (link) => (/^https?:/.test(link) ? link : `${location.origin}${link}`);

function spielerDialog(a) {
  const { inhalt } = blatt(a.titel || t('konto.ohneTitel'), { breit: true });
  inhalt.append(h('video', { src: `/api/aufnahmen/${a.id}/datei`, controls: true, playsinline: true, autoplay: true, preload: 'metadata' }));
}

function freigabeDialog(a) {
  const { inhalt, schliessen } = blatt(t('konto.teilenTitel'));
  const statusEl = h('div', { class: 'status', role: 'status', hidden: true });
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec klein' }, t('konto.teilenText')),
    h('fieldset', { class: 'feld ohne-rahmen' }, h('legend', {}, h('b', {}, t('konto.gueltig'))),
      h('div', { class: 'umschalter', role: 'radiogroup', 'aria-label': t('konto.gueltig') },
        [1, 7, 30].map((tage) => h('label', {}, h('input', { type: 'radio', name: 'tage', value: tage, checked: tage === 7 }), t('konto.tage', { n: tage }))))),
    h('label', { class: 'feld' }, h('span', {}, t('konto.freigabePasswort')),
      h('input', { name: 'passwort', type: 'text', autocomplete: 'off', maxlength: '64', placeholder: t('konto.passcodeOptional') })),
    statusEl,
    h('div', { class: 'knoepfe' }, h('button', { type: 'submit', class: 'btn primary' }, t('konto.linkErstellen'))));
  inhalt.append(f);
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const body = { tage: Number(new FormData(f).get('tage')) };
      if (f.passwort.value.trim()) body.passwort = f.passwort.value.trim();
      const r = await api(`/api/aufnahmen/${a.id}/freigabe`, { methode: 'POST', json: body });
      const link = absolut(r.link);
      f.replaceChildren(
        h('p', { class: 'sec' }, t('konto.linkFertig')),
        h('div', { class: 'link-feld' }, link),
        h('div', { class: 'knoepfe' },
          knopf(t('konto.linkKopieren'), { klasse: 'btn primary', symbol: 'kopie', onclick: () => kopieren(link, t('konto.linkKopiert')) }),
          navigator.share ? knopf(t('konto.teilen'), { symbol: 'teilenLink', onclick: () => navigator.share({ title: a.titel, url: link }).catch(() => {}) }) : null,
          h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.fertig'))));
      aufnahmenLaden();
    }, statusEl);
  });
}

async function freigabeBeenden(a) {
  if (!await bestaetigen(t('konto.freigabeBeenden'), t('konto.freigabeBeendenText'), { ja: t('konto.freigabeBeenden'), gefahr: true })) return;
  await beschaeftigt(null, async () => { await api(`/api/aufnahmen/${a.id}/freigabe`, { methode: 'DELETE' }); aufnahmenLaden(); });
}

async function aufnahmeLoeschen(a) {
  if (!await bestaetigen(t('konto.aufnahmeLoeschenTitel'), t('konto.aufnahmeLoeschenText', { titel: a.titel || '' }), { ja: t('konto.loeschen'), gefahr: true })) return;
  await beschaeftigt(null, async () => { await api(`/api/aufnahmen/${a.id}`, { methode: 'DELETE' }); toast(t('konto.geloescht'), 'ok'); aufnahmenLaden(); });
}

// ---------------------------------------------------------------- Profil, Passwort, Konto
function kopfZeigen() {
  $('#ich-name').textContent = ich.name;
  $('#ich-email').textContent = ich.email;
  const av = $('#ich-avatar');
  av.textContent = initialen(ich.name);
  av.style.background = ich.farbe || '';
  const f = $('#f-profil');
  f.name.value = ich.name;
  f.zeitzone.replaceChildren(...zonenListe().map((z) => h('option', { value: z, selected: z === zone() }, z.replace(/_/g, ' '))));
  f.sprache.value = ich.sprache || 'de';
  const box = $('#farben');
  box.replaceChildren(...FARBEN.map((farbe) => {
    const l = h('label', { title: farbe }, h('input', { type: 'radio', name: 'farbe', value: farbe, checked: farbe.toLowerCase() === (ich.farbe || '').toLowerCase(), 'aria-label': farbe }));
    l.style.background = farbe;
    return l;
  }));
}
const FARBEN = ['#6d4aff', '#3b6ff6', '#0e8a7a', '#b4237a', '#c2410c', '#7c3aed', '#0f766e', '#be123c', '#1d4ed8', '#9333ea'];

$('#f-profil').addEventListener('submit', (e) => {
  e.preventDefault();
  const f = e.currentTarget;
  if (!f.reportValidity()) return;
  beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
    const r = await api('/api/konto/ich', { methode: 'PATCH', json: { name: f.name.value.trim(), zeitzone: f.zeitzone.value, sprache: f.sprache.value, ...(new FormData(f).get('farbe') ? { farbe: new FormData(f).get('farbe') } : {}) } });
    ich = r.ich || { ...ich, name: f.name.value.trim(), zeitzone: f.zeitzone.value, sprache: f.sprache.value };
    kopfZeigen();
    toast(t('konto.gespeichert'), 'ok');
    meetingsLaden();
  });
});

$('#f-pw').addEventListener('submit', (e) => {
  e.preventDefault();
  const f = e.currentTarget;
  if (!f.reportValidity()) return;
  beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
    await api('/api/konto/passwort-aendern', { methode: 'POST', json: { alt: f.alt.value, neu: f.neu.value } });
    f.reset();
    dispatchEvent(new Event('agora:sicherheit-neu'));      // Liste „Sicherheit“ (konto-sicherheit.js) neu laden
    toast(t('konto.passwortGeaendert'), 'ok');
  });
});

$('#abmelden').addEventListener('click', (e) => beschaeftigt(e.currentTarget, async () => {
  await api('/api/konto/abmelden', { methode: 'POST' });
  location.href = '/';
}));

$('#konto-loeschen').addEventListener('click', () => {
  const { inhalt } = blatt(t('konto.kontoLoeschenTitel'));
  const statusEl = h('div', { class: 'status', role: 'status', hidden: true });
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec' }, t('konto.kontoLoeschenText')),
    h('input', { type: 'text', name: 'benutzer', autocomplete: 'username', hidden: true, value: ich?.email || '' }),
    h('label', { class: 'feld' }, h('span', {}, t('konto.passwortBestaetigen')), h('input', { name: 'passwort', type: 'password', autocomplete: 'current-password', required: true })),
    statusEl,
    h('button', { type: 'submit', class: 'btn gefahr voll' }, t('konto.endgueltigLoeschen')));
  inhalt.append(f);
  f.passwort.focus();
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!f.reportValidity()) return;
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      await api('/api/konto', { methode: 'DELETE', json: { passwort: f.passwort.value } });
      location.href = '/';
    }, statusEl);
  });
});

$('#neues-meeting').addEventListener('click', (e) => beschaeftigt(e.currentTarget, async () => {
  const { raum } = await api('/api/raeume', { methode: 'POST', json: { titel: t('start.sofortTitel', { name: ich?.name || '' }) } });
  location.href = `/r/${raum.code}`;
}));
$('#planen-knopf').addEventListener('click', () => planenDialog());

// ---------------------------------------------------------------- Start
(async () => {
  ich = await ichLaden();
  if (!ich) { location.replace(`/?weiter=${encodeURIComponent('/konto' + location.hash)}`); return; }
  kopfZeigen();
  kalenderAboEinrichten();
  vorlagenEinrichten({ planen: (vorlage) => planenDialog(null, { vorlage }) });
  erinnerungEinrichten();
  verlaufEinrichten({ zeitzone: zone });
  const ziel = location.hash.slice(1);
  reiter(ziel === 'planen' ? 'meetings' : ziel);
  try { await meetingsLaden(); } catch (e) { toast(e.message, 'err'); }
  if (ziel === 'planen') planenDialog();
})();
