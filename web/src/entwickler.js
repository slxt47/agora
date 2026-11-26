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
      h('div', { class: 'zeile' }, k.org ? h('span', { class: 'marker ok' }, t('dev.orgMarker', { name: k.org.name })) : null,
        k.rechte.map((r) => h('span', { class: 'marker' }, rechtText(r)))),
      h('div', { class: 'aktionen' }, knopf(t('dev.widerrufen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: async (e) => {
        const el = e.currentTarget;
        if (!await bestaetigen(t('dev.widerrufenTitel'), t('dev.widerrufenText', { name: k.name }), { ja: t('dev.widerrufen'), gefahr: true })) return;
        beschaeftigt(el, async () => { await api(`/api/konto/api-schluessel/${k.id}`, { methode: 'DELETE' }); toast(t('dev.widerrufenOk'), 'ok'); schluesselLaden(); });
      } }))));
  }
}

async function neuDialog() {
  // Organisationen, in denen ich owner/admin bin: Schlüssel kann auch nur für eine davon gelten (ohne Webhooks)
  const orgs = await api('/api/orgs').then((d) => d.orgs.filter((o) => o.rolle !== 'mitglied')).catch(() => []);
  const { inhalt, schliessen } = blatt(t('dev.apiNeuTitel'), { onclose: schluesselLaden });
  const st = statusFeld();
  const f = h('form', { class: 'formular', novalidate: true },
    h('label', { class: 'feld' }, h('span', {}, t('dev.feldName')),
      h('input', { name: 'name', required: true, maxlength: '60', placeholder: t('dev.nameBeispiel'), autocomplete: 'off' })),
    h('fieldset', { class: 'feld ohne-rahmen dev-rechte' }, h('legend', {}, h('b', {}, t('dev.feldRechte'))),
      API_RECHTE.map((r) => h('label', { class: 'dev-recht' },
        h('input', { type: 'checkbox', name: 'recht', value: r, checked: r === 'meetings:lesen' }),
        h('span', {}, h('code', {}, r), h('small', {}, rechtText(r)))))),
    orgs.length ? h('label', { class: 'feld' }, h('span', {}, t('dev.feldBereich')),
      h('select', { name: 'org' }, [['', t('dev.bereichKonto')], ...orgs.map((o) => [o.slug, o.name])].map(([w, text]) => h('option', { value: w }, text))),
      h('small', {}, t('dev.bereichHinweis'))) : null,
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
        json: { name: f.name.value.trim(), rechte, gueltig_tage: f.gueltig.value ? Number(f.gueltig.value) : null, ...(f.org?.value ? { org: f.org.value } : {}) } });
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

// ---------------------------------------------------------------- Webhooks
export const EREIGNISSE = ['meeting.gestartet', 'meeting.beendet', 'teilnehmer.beigetreten', 'teilnehmer.gegangen', 'aufnahme.fertig', 'webinar.anmeldung'];

async function webhooksLaden() {
  const ul = $('#liste-webhooks');
  if (!ul) return;
  let d;
  try { d = await api('/api/konto/webhooks'); } catch (e) { ul.replaceChildren(h('li', { class: 'leer' }, e.message)); return; }
  ul.replaceChildren();
  $('#webhook-neu').disabled = d.webhooks.length >= d.maximal;
  if (!d.webhooks.length) { ul.append(h('li', { class: 'leer' }, t('wh.leer'))); return; }
  for (const w of d.webhooks) ul.append(webhookEintrag(w));
}

function webhookEintrag(w) {
  const zeilen = [t('dev.erstellt', { zeit: zeit(w.erstellt) }), w.letzter_erfolg ? t('wh.erfolg', { zeit: zeit(w.letzter_erfolg) }) : t('wh.nochKeinErfolg')];
  const ergebnis = h('div', { class: 'status', role: 'status', hidden: true });
  return h('li', { 'data-webhook': w.id },
    h('div', { class: 'kopf' },
      h('div', { class: 'symbol', html: icon('link') }),
      h('div', { class: 'text' },
        h('span', { class: 'name mono dev-url' }, w.url),
        h('span', { class: 'info' }, zeilen.join(' · ')))),
    h('div', { class: 'zeile' },
      h('span', { class: `marker ${w.aktiv ? 'ok' : 'warn'}` }, w.aktiv ? t('wh.aktiv') : t('wh.aus')),
      w.ereignisse.map((e) => h('span', { class: 'marker' }, e))),
    w.deaktiviert_grund ? h('p', { class: 'sec klein' }, w.deaktiviert_grund) : null,
    ergebnis,
    h('div', { class: 'aktionen' },
      knopf(t('wh.testen'), { klasse: 'btn klein', symbol: 'senden', 'data-wh-test': '', onclick: (e) => beschaeftigt(e.currentTarget, async () => {
        const r = await api(`/api/konto/webhooks/${w.id}/test`, { methode: 'POST' });
        status(ergebnis, r.ok ? t('wh.testOk', { status: r.status, ms: r.dauer_ms }) : t('wh.testFehler', { fehler: r.fehler }), r.ok ? 'ok' : 'err');
      }) }),
      knopf(t('wh.protokoll'), { klasse: 'btn klein', symbol: 'uhr', 'data-wh-protokoll': '', onclick: () => protokollDialog(w) }),
      knopf(t('konto.bearbeiten'), { klasse: 'btn klein', symbol: 'stift', onclick: () => webhookDialog(w) }),
      knopf(w.aktiv ? t('wh.ausschalten') : t('wh.einschalten'), { klasse: 'btn klein', onclick: (e) => beschaeftigt(e.currentTarget, async () => {
        await api(`/api/konto/webhooks/${w.id}`, { methode: 'PATCH', json: { aktiv: !w.aktiv } }); webhooksLaden();
      }) }),
      knopf(t('wh.neuesGeheimnis'), { klasse: 'btn klein', symbol: 'schluessel', onclick: async (e) => {
        const el = e.currentTarget;
        if (!await bestaetigen(t('wh.neuesGeheimnis'), t('wh.neuesGeheimnisText'), { ja: t('wh.neuesGeheimnis') })) return;
        beschaeftigt(el, async () => {
          const r = await api(`/api/konto/webhooks/${w.id}/geheimnis`, { methode: 'POST' });
          const { inhalt, schliessen } = blatt(t('wh.geheimnisTitel'));
          geheimZeigen(inhalt, schliessen, r.geheimnis, t('wh.geheimnisEinmal'));
        });
      } }),
      knopf(t('konto.loeschen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: async (e) => {
        const el = e.currentTarget;
        if (!await bestaetigen(t('wh.loeschenTitel'), t('wh.loeschenText'), { ja: t('konto.loeschen'), gefahr: true })) return;
        beschaeftigt(el, async () => { await api(`/api/konto/webhooks/${w.id}`, { methode: 'DELETE' }); webhooksLaden(); });
      } })));
}

function webhookDialog(w = null) {
  const { inhalt, schliessen } = blatt(w ? t('wh.bearbeitenTitel') : t('wh.neuTitel'), { onclose: webhooksLaden });
  const st = statusFeld();
  const f = h('form', { class: 'formular', novalidate: true },
    h('label', { class: 'feld' }, h('span', {}, t('wh.feldUrl')),
      h('input', { name: 'url', type: 'url', required: true, maxlength: '500', value: w?.url || '', placeholder: 'https://beispiel.de/agora-hook', autocomplete: 'off', spellcheck: 'false' }),
      h('small', {}, t('wh.urlHinweis'))),
    h('fieldset', { class: 'feld ohne-rahmen dev-rechte' }, h('legend', {}, h('b', {}, t('wh.feldEreignisse'))),
      EREIGNISSE.map((e) => h('label', { class: 'dev-recht' },
        h('input', { type: 'checkbox', name: 'ereignis', value: e, checked: w ? w.ereignisse.includes(e) : e.startsWith('meeting.') }),
        h('span', {}, h('code', {}, e), h('small', {}, t(`wh.e.${e}`)))))),
    st,
    h('div', { class: 'knoepfe' },
      h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn primary' }, w ? t('allg.speichern') : t('wh.anlegen'))));
  inhalt.append(f);
  f.url.focus();
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    const ereignisse = [...f.querySelectorAll('[name=ereignis]:checked')].map((x) => x.value);
    if (!f.url.value.trim()) { status(st, t('wh.nurHttps'), 'err'); f.url.focus(); return; }
    if (!ereignisse.length) { status(st, t('wh.ereignisseFehlen'), 'err'); return; }
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const json = { url: f.url.value.trim(), ereignisse };
      if (w) { await api(`/api/konto/webhooks/${w.id}`, { methode: 'PATCH', json }); schliessen(); toast(t('konto.gespeichert'), 'ok'); return; }
      const r = await api('/api/konto/webhooks', { methode: 'POST', json });
      geheimZeigen(inhalt, schliessen, r.geheimnis, t('wh.geheimnisEinmal'));
    }, st);
  });
}

async function protokollDialog(w) {
  const { inhalt } = blatt(t('wh.protokollTitel'), { breit: true });
  inhalt.append(h('p', { class: 'sec' }, t('allg.laden')));
  try {
    const { zustellungen } = await api(`/api/konto/webhooks/${w.id}/zustellungen`);
    inhalt.replaceChildren(h('p', { class: 'sec klein' }, t('wh.protokollText')));
    if (!zustellungen.length) { inhalt.append(h('p', { class: 'leer' }, t('wh.protokollLeer'))); return; }
    inhalt.append(h('ul', { class: 'liste dev-protokoll' }, zustellungen.map((z) => h('li', { 'data-ok': String(z.ok) },
      h('span', { class: `marker ${z.ok ? 'ok' : 'warn'}` }, z.status ?? '—'),
      h('span', { class: 'mono' }, z.ereignis),
      h('span', { class: 'sec klein' }, [zeit(z.zeit), t('wh.versuch', { n: z.versuch }), `${z.dauer_ms} ms`, z.fehler].filter(Boolean).join(' · '))))));
  } catch (e) { inhalt.replaceChildren(h('p', { class: 'leer' }, e.message)); }
}

$('#webhook-neu')?.addEventListener('click', () => webhookDialog());
webhooksLaden();
