// Konto → Entwickler: persönliche API-Schlüssel (öffentliche API /api/v1). Eigenes Modul (eigenes <script> in
// konto.html); den Reiter schaltet konto.js um. Ein neuer Schlüssel ist nur direkt nach dem Anlegen sichtbar.
import './entwickler.css';
import { $, h, api, blatt, bestaetigen, kopieren, beschaeftigt, toast, knopf, status } from './common.js';
import { t } from './i18n.js';
import { icon } from './icons.js';
import { zeitText } from './helfer.js';

const ZONE = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Vienna'; } catch { return 'Europe/Vienna'; } })();
export const zeit = (iso) => zeitText(iso, ZONE);
const statusFeld = () => h('div', { class: 'status', role: 'status', hidden: true });

// ---------------------------------------------------------------- API-Schlüssel
export const API_RECHTE = ['meetings:lesen', 'meetings:schreiben', 'aufnahmen:lesen', 'webhooks'];
const rechtText = (r) => t(`dev.recht.${r}`);

async function schluesselLaden() {
  const ul = $('#liste-api');
  if (!ul) return;
  let d;
  try { d = await api('/api/konto/api-schluessel'); } catch (e) { ul.replaceChildren(h('li', { class: 'leer' }, e.message)); return; }
  ul.replaceChildren();
  if (!d.schluessel.length) { ul.append(h('li', { class: 'leer' }, t('dev.apiLeer'))); return; }
  for (const k of d.schluessel) {
    const zeilen = [t('dev.erstellt', { zeit: zeit(k.erstellt) }),
      k.benutzt ? t('dev.benutzt', { zeit: zeit(k.benutzt) }) : t('dev.nieBenutzt')];
    const abgelaufen = k.gueltig_bis && new Date(k.gueltig_bis).getTime() < Date.now();
    if (k.gueltig_bis) zeilen.push(t(abgelaufen ? 'dev.abgelaufen' : 'dev.gueltigBis', { zeit: zeit(k.gueltig_bis) }));
    ul.append(h('li', { 'data-schluessel': k.id },
      h('div', { class: 'kopf' },
        h('div', { class: 'symbol', html: icon('schluessel') }),
        h('div', { class: 'text' },
          h('span', { class: 'name' }, k.name),
          h('span', { class: 'info mono' }, `${k.anfang}…`),
          h('span', { class: 'info' }, zeilen.join(' · ')))),
      h('div', { class: 'zeile' }, k.rechte.map((r) => h('span', { class: 'marker' }, rechtText(r)))),
      h('div', { class: 'aktionen' }, knopf(t('dev.widerrufen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: async (e) => {
        const el = e.currentTarget;
        if (!await bestaetigen(t('dev.widerrufenTitel'), t('dev.widerrufenText', { name: k.name }), { ja: t('dev.widerrufen'), gefahr: true })) return;
        beschaeftigt(el, async () => { await api(`/api/konto/api-schluessel/${k.id}`, { methode: 'DELETE' }); toast(t('dev.widerrufenOk'), 'ok'); schluesselLaden(); });
      } }))));
  }
}

function neuDialog() {
  const { inhalt, schliessen } = blatt(t('dev.apiNeuTitel'), { onclose: schluesselLaden });
  const st = statusFeld();
  const f = h('form', { class: 'formular', novalidate: true },
    h('label', { class: 'feld' }, h('span', {}, t('dev.feldName')),
      h('input', { name: 'name', required: true, maxlength: '60', placeholder: t('dev.nameBeispiel'), autocomplete: 'off' })),
    h('fieldset', { class: 'feld ohne-rahmen dev-rechte' }, h('legend', {}, h('b', {}, t('dev.feldRechte'))),
      API_RECHTE.map((r) => h('label', { class: 'dev-recht' },
        h('input', { type: 'checkbox', name: 'recht', value: r, checked: r === 'meetings:lesen' }),
        h('span', {}, h('code', {}, r), h('small', {}, rechtText(r)))))),
    h('label', { class: 'feld' }, h('span', {}, t('dev.feldGueltig')),
      h('select', { name: 'gueltig' }, [['', t('dev.ohneAblauf')], ['30', t('dev.tage', { n: 30 })], ['90', t('dev.tage', { n: 90 })], ['365', t('dev.tage', { n: 365 })]]
        .map(([w, text]) => h('option', { value: w, selected: w === '90' }, text)))),
    st,
    h('div', { class: 'knoepfe' },
      h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn primary' }, t('dev.erstellen'))));
  inhalt.append(f);
  f.name.focus();
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    const rechte = [...f.querySelectorAll('[name=recht]:checked')].map((x) => x.value);
    if (!f.name.value.trim()) { status(st, t('dev.nameFehlt'), 'err'); f.name.focus(); return; }
    if (!rechte.length) { status(st, t('dev.rechteFehlen'), 'err'); return; }
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const r = await api('/api/konto/api-schluessel', { methode: 'POST',
        json: { name: f.name.value.trim(), rechte, gueltig_tage: f.gueltig.value ? Number(f.gueltig.value) : null } });
      geheimZeigen(inhalt, schliessen, r.token, t('dev.apiEinmal'),
        `curl -H "Authorization: Bearer ${r.token}" ${location.origin}/api/v1/meetings`);
    }, st);
  });
}

/** Geheimnis genau einmal zeigen (Schlüssel oder Webhook-Geheimnis) – mit Kopieren und „Fertig“. */
export function geheimZeigen(inhalt, schliessen, geheim, hinweis, beispiel) {
  inhalt.replaceChildren(h('div', { class: 'formular' },
    h('div', { class: 'status warn' }, hinweis),
    h('div', { class: 'link-feld mono dev-geheim', id: 'dev-geheim' }, geheim),
    h('div', { class: 'knoepfe' }, knopf(t('dev.kopieren'), { klasse: 'btn primary', symbol: 'kopie', onclick: () => kopieren(geheim, t('allg.kopiert')) })),
    beispiel ? h('details', { class: 'dev-beispiel' }, h('summary', {}, t('dev.beispiel')), h('pre', { class: 'mono' }, beispiel)) : null,
    h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.fertig')))));
}

$('#api-neu')?.addEventListener('click', neuDialog);
schluesselLaden();
