// Meeting-Vorlagen im Konto: Liste (Sofort starten, Planen, Bearbeiten, Löschen), Vorlage anlegen/bearbeiten und
// „Als Vorlage speichern“ aus einem bestehenden Meeting. Planen nutzt den Planen-Dialog aus konto.js (Rückruf `planen`).
import { $, h, api, blatt, bestaetigen, beschaeftigt, toast, knopf, status, kopieren } from './common.js';
import { t } from './i18n.js';
import { icon } from './icons.js';
import { minutenText } from './helfer.js';
import './planung.css';

const RECHTE = [['chat', 'recht.chat'], ['bildschirm', 'recht.bildschirm'], ['selbst_unmute', 'recht.selbstUnmute'], ['umbenennen', 'recht.umbenennen']];
let planenRueckruf = () => {};
let vorlagen = [];

export const alleVorlagen = () => vorlagen;

/** Kurzbeschreibung einer Vorlage als Liste von Merkmalen (für Marker). */
export function vorlageMerkmale(e) {
  const m = [minutenText(e.dauer_min || 60)];
  if (e.passcode) m.push(t('konto.passcode'));
  if (e.warteraum) m.push(t('konto.warteraum'));
  if (e.chat_speichern) m.push(t('konto.chatGespeichert'));
  if (e.stumm_beitritt) m.push(t('vorlage.stummKurz'));
  for (const [k, text] of RECHTE) if (e.rechte && e.rechte[k] === false) m.push(t('vorlage.ohne', { recht: t(text) }));
  return m;
}

export async function vorlagenEinrichten({ planen }) {
  planenRueckruf = planen;
  $('#vorlage-neu')?.addEventListener('click', () => vorlageDialog());
  await vorlagenLaden();
}

export async function vorlagenLaden() {
  const ul = $('#liste-vorlagen');
  if (!ul) return;
  try {
    vorlagen = (await api('/api/vorlagen')).vorlagen || [];
  } catch (e) { ul.replaceChildren(h('li', { class: 'leer' }, e.message)); return; }
  ul.replaceChildren();
  if (!vorlagen.length) { ul.append(h('li', { class: 'leer' }, t('vorlage.keine'))); return; }
  for (const v of vorlagen) ul.append(eintrag(v));
}

function eintrag(v) {
  const e = v.einstellungen;
  return h('li', { 'data-vorlage': v.id },
    h('div', { class: 'kopf' },
      h('div', { class: 'symbol', html: icon('stern') }),
      h('div', { class: 'text' }, h('span', { class: 'name' }, v.name), e.titel ? h('span', { class: 'info' }, e.titel) : null)),
    h('div', { class: 'zeile' }, vorlageMerkmale(e).map((x) => h('span', { class: 'marker' }, x))),
    h('div', { class: 'aktionen' },
      knopf(t('vorlage.sofort'), { klasse: 'btn primary klein', symbol: 'video', onclick: (ev) => sofort(ev.currentTarget, v) }),
      knopf(t('konto.planen'), { klasse: 'btn klein', symbol: 'kalender', onclick: () => planenRueckruf(v) }),
      knopf(t('konto.bearbeiten'), { klasse: 'btn klein', symbol: 'stift', onclick: () => vorlageDialog(v) }),
      knopf(t('konto.loeschen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: () => loeschen(v) })));
}

function sofort(el, v) {
  beschaeftigt(el, async () => {
    const r = await api(`/api/vorlagen/${v.id}/raum`, { methode: 'POST', json: {} });
    if (r.passcode) passcodeZeigen(r.raum, r.passcode, true);
    else location.href = `/r/${r.raum.code}`;
  });
}

/** Ein vom Server erzeugter Passcode wird nur einmal gezeigt (gespeichert ist nur sein Hash). */
export function passcodeZeigen(raum, passcode, starten = false) {
  const { inhalt } = blatt(t('vorlage.passcodeTitel'));
  const link = `${raum.link}?p=${encodeURIComponent(passcode)}`;
  inhalt.append(
    h('p', { class: 'sec klein' }, t('vorlage.passcodeText')),
    h('div', { class: 'link-feld', id: 'vorlage-passcode' }, passcode),
    h('div', { class: 'link-feld' }, link),
    h('div', { class: 'knoepfe' },
      knopf(t('konto.linkKopieren'), { symbol: 'kopie', onclick: () => kopieren(link, t('konto.linkKopiert')) }),
      starten ? h('a', { class: 'btn primary', href: `/r/${raum.code}` }, t('konto.starten')) : null));
}

async function loeschen(v) {
  if (!await bestaetigen(t('vorlage.loeschenTitel'), t('vorlage.loeschenText', { name: v.name }), { ja: t('konto.loeschen'), gefahr: true })) return;
  await beschaeftigt(null, async () => { await api(`/api/vorlagen/${v.id}`, { methode: 'DELETE' }); toast(t('konto.geloescht'), 'ok'); await vorlagenLaden(); });
}

/** Aus einem bestehenden Meeting eine Vorlage machen (Name abfragen). */
export function alsVorlage(raum) {
  const { inhalt, schliessen } = blatt(t('vorlage.ausMeetingTitel'));
  const statusEl = h('div', { class: 'status', role: 'status', hidden: true });
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec klein' }, t('vorlage.ausMeetingText')),
    h('label', { class: 'feld' }, h('span', {}, t('vorlage.name')), h('input', { name: 'name', required: true, maxlength: '80', value: raum.titel || '' })),
    statusEl,
    h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: () => schliessen() }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn primary' }, t('allg.speichern'))));
  inhalt.append(f);
  f.name.select();
  f.addEventListener('submit', (ev) => {
    ev.preventDefault();
    if (!f.reportValidity()) return;
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      await api('/api/vorlagen', { methode: 'POST', json: { name: f.name.value.trim(), aus_raum: raum.code } });
      schliessen(); toast(t('vorlage.gespeichert'), 'ok'); await vorlagenLaden();
    }, statusEl);
  });
}

function schalter(name, text, hinweis, an) {
  return h('label', { class: 'schalter' },
    h('span', { class: 'txt' }, h('span', {}, text), hinweis ? h('small', {}, hinweis) : null),
    h('input', { type: 'checkbox', role: 'switch', name, checked: an }));
}

/** Vorlage anlegen oder bearbeiten. */
export function vorlageDialog(v = null) {
  const e = v?.einstellungen || { dauer_min: 60, rechte: {} };
  const { inhalt, schliessen } = blatt(v ? t('vorlage.bearbeitenTitel') : t('vorlage.neuTitel'));
  const statusEl = h('div', { class: 'status', role: 'status', hidden: true });
  const dauerWerte = [15, 30, 45, 60, 90, 120, 180, 240, 480];
  if (!dauerWerte.includes(e.dauer_min)) dauerWerte.push(e.dauer_min);
  const f = h('form', { class: 'formular', novalidate: true },
    h('label', { class: 'feld' }, h('span', {}, t('vorlage.name')),
      h('input', { name: 'name', required: true, maxlength: '80', value: v?.name || '', placeholder: t('vorlage.nameBeispiel') })),
    h('div', { class: 'zwei' },
      h('label', { class: 'feld' }, h('span', {}, t('konto.feldTitel')), h('input', { name: 'titel', maxlength: '120', value: e.titel || '' })),
      h('label', { class: 'feld' }, h('span', {}, t('konto.feldDauer')),
        h('select', { name: 'dauer' }, dauerWerte.sort((a, b) => a - b).map((m) => h('option', { value: m, selected: m === e.dauer_min }, minutenText(m)))))),
    h('label', { class: 'feld' }, h('span', {}, t('konto.feldBeschreibung')), h('textarea', { name: 'beschreibung', maxlength: '2000', rows: '3' }, e.beschreibung || '')),
    schalter('passcode', t('vorlage.passcode'), t('vorlage.passcodeHinweis'), !!e.passcode),
    schalter('warteraum', t('konto.feldWarteraum'), t('konto.warteraumHinweis'), !!e.warteraum),
    schalter('chat_speichern', t('konto.feldChatSpeichern'), t('konto.chatSpeichernHinweis'), !!e.chat_speichern),
    schalter('stumm_beitritt', t('vorlage.stumm'), t('vorlage.stummHinweis'), !!e.stumm_beitritt),
    h('fieldset', { class: 'feld ohne-rahmen' }, h('legend', {}, h('b', {}, t('vorlage.rechte'))),
      ...RECHTE.map(([k, text]) => schalter(`recht_${k}`, t(text), '', e.rechte?.[k] !== false))),
    statusEl,
    h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: () => schliessen() }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn primary' }, t('allg.speichern'))));
  inhalt.append(f);
  f.name.focus();
  f.addEventListener('submit', (ev) => {
    ev.preventDefault();
    if (!f.name.value.trim()) { status(statusEl, t('vorlage.nameFehlt'), 'err'); f.name.focus(); return; }
    const einstellungen = {
      titel: f.titel.value.trim(), dauer_min: Number(f.dauer.value), beschreibung: f.beschreibung.value.trim(),
      passcode: f.passcode.checked, warteraum: f.warteraum.checked, chat_speichern: f.chat_speichern.checked, stumm_beitritt: f.stumm_beitritt.checked,
      rechte: Object.fromEntries(RECHTE.map(([k]) => [k, f[`recht_${k}`].checked])),
    };
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const body = { name: f.name.value.trim(), einstellungen };
      if (v) await api(`/api/vorlagen/${v.id}`, { methode: 'PUT', json: body });
      else await api('/api/vorlagen', { methode: 'POST', json: body });
      schliessen(); toast(t('vorlage.gespeichert'), 'ok'); await vorlagenLaden();
    }, statusEl);
  });
}
