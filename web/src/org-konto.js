// Konto → Teams: Organisationen anlegen, Einladungen annehmen/ablehnen (auch über den Link ?einladung=… aus der Mail),
// mit passender E-Mail-Domain selbst beitreten. Je Organisation: Meetings (alle sehen sie, owner/admin verwalten),
// gemeinsame Vorlagen, Aufnahmen-Bibliothek, Mitglieder (einladen, Rollen, entfernen), Einstellungen, verlassen, löschen.
// Eigenes Modul (eigenes <script> in konto.html); den Reiter schaltet konto.js um. Planen und Einladen zu einem Meeting
// übernimmt der Dialog aus konto.js (Ereignisse agora:org-planen / agora:org-einladen).
import './org-konto.css';
import { $, h, api, blatt, bestaetigen, beschaeftigt, toast, knopf, status, kopieren } from './common.js';
import { t } from './i18n.js';
import { icon } from './icons.js';
import { zeitText, groesseText, emailsAusText } from './helfer.js';
import { orgKuerzel, rolleRang, FARBEN_ORG } from './org-format.js';
import { vorlageDialog, vorlageMerkmale, passcodeZeigen } from './vorlagen.js';
import { ssoKarte } from './org-sso.js';     // Firmen-Login (OIDC) – nur Owner, nur wenn der Server es kann

const ZONE = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Vienna'; } catch { return 'Europe/Vienna'; } })();
const zeit = (iso) => zeitText(iso, ZONE);
const statusFeld = () => h('div', { class: 'status', role: 'status', hidden: true });
const RECHTE = [['chat', 'recht.chat'], ['bildschirm', 'recht.bildschirm'], ['selbst_unmute', 'recht.selbstUnmute'], ['umbenennen', 'recht.umbenennen']];
const rolleText = (r) => t(`org.rolle.${r}`);
let offen = null;          // Kurzname der geöffneten Organisation

function logo(o, gross = false) {
  const el = h('span', { class: `org-logo${gross ? ' gross' : ''}`, 'aria-hidden': 'true' }, orgKuerzel(o.name));
  el.style.background = o.farbe;
  return el;
}

function schalter(name, text, hinweis, an) {
  return h('label', { class: 'schalter' },
    h('span', { class: 'txt' }, h('span', {}, text), hinweis ? h('small', {}, hinweis) : null),
    h('input', { type: 'checkbox', role: 'switch', name, checked: an }));
}

// ---------------------------------------------------------------- Übersicht
async function uebersichtLaden() {
  const ul = $('#liste-orgs');
  if (!ul) return;
  let d;
  try { d = await api('/api/orgs'); } catch (e) { ul.replaceChildren(h('li', { class: 'leer' }, e.message)); return; }
  einladungenZeigen(d.einladungen || []);
  beitretbarZeigen(d.beitretbar || []);
  ul.replaceChildren();
  if (!d.orgs.length) ul.append(h('li', { class: 'leer' }, t('org.keine')));
  for (const o of d.orgs) {
    ul.append(h('li', { 'data-org': o.slug },
      h('div', { class: 'kopf' }, logo(o),
        h('div', { class: 'text' }, h('span', { class: 'name' }, o.name),
          h('span', { class: 'info' }, `${rolleText(o.rolle)} · ${t('org.mitgliederN', { n: o.mitglieder })}`),
          h('span', { class: 'info mono' }, o.slug))),
      h('div', { class: 'aktionen' }, knopf(t('org.oeffnen'), { klasse: 'btn primary klein', symbol: 'pfeilRechts', 'data-org-oeffnen': o.slug, onclick: () => oeffnen(o.slug) }))));
  }
  if (offen && !d.orgs.some((o) => o.slug === offen)) schliessenDetail();
}

function einladungenZeigen(liste) {
  const box = $('#org-einladungen');
  box.hidden = !liste.length;
  const ul = $('#liste-org-einladungen');
  ul.replaceChildren(...liste.map((e) => h('li', { 'data-einladung': e.id },
    h('div', { class: 'kopf' }, logo(e.org),
      h('div', { class: 'text' }, h('span', { class: 'name' }, e.org.name),
        h('span', { class: 'info' }, t('org.einladungVon', { von: e.von_name || '–', rolle: rolleText(e.rolle) })),
        h('span', { class: 'info' }, t('org.gueltigBis', { zeit: zeit(e.gueltig_bis) })))),
    h('div', { class: 'aktionen' },
      knopf(t('org.annehmen'), { klasse: 'btn primary klein', symbol: 'haken', 'data-annehmen': e.id, onclick: (ev) => annehmen(ev.currentTarget, e) }),
      knopf(t('org.ablehnen'), { klasse: 'btn klein', symbol: 'schliessen', onclick: (ev) => ablehnen(ev.currentTarget, e) })))));
}

function beitretbarZeigen(liste) {
  const box = $('#org-beitretbar');
  box.hidden = !liste.length;
  $('#liste-org-beitretbar').replaceChildren(...liste.map((o) => h('li', {},
    h('div', { class: 'kopf' }, logo(o), h('div', { class: 'text' }, h('span', { class: 'name' }, o.name), h('span', { class: 'info' }, t('org.domainPasst')))),
    h('div', { class: 'aktionen' }, knopf(t('org.beitreten'), { klasse: 'btn primary klein', onclick: (ev) => beschaeftigt(ev.currentTarget, async () => {
      await api(`/api/orgs/${o.slug}/beitreten`, { methode: 'POST' });
      toast(t('org.beigetreten', { name: o.name }), 'ok');
      await uebersichtLaden(); oeffnen(o.slug);
    }) })))));
}

function annehmen(el, e) {
  return beschaeftigt(el, async () => {
    await api(`/api/org-einladungen/${e.id}/annehmen`, { methode: 'POST' });
    toast(t('org.beigetreten', { name: e.org.name }), 'ok');
    await uebersichtLaden();
    oeffnen(e.org.slug);
  });
}

function ablehnen(el, e) {
  return beschaeftigt(el, async () => {
    await api(`/api/org-einladungen/${e.id}/ablehnen`, { methode: 'POST' });
    toast(t('org.abgelehnt'), 'ok');
    await uebersichtLaden();
  });
}

/** Link aus der Einladungs-Mail: /konto?einladung=<token>#teams → Dialog mit Annehmen/Ablehnen. */
async function linkPruefen() {
  const u = new URL(location.href);
  const token = u.searchParams.get('einladung');
  if (!token) return;
  u.searchParams.delete('einladung');
  history.replaceState(null, '', u.pathname + u.search + u.hash);
  let e;
  try { e = (await api('/api/org-einladungen/link', { methode: 'POST', json: { token } })).einladung; } catch (err) { toast(err.message, 'err', { dauer: 0 }); return; }
  const { inhalt, schliessen } = blatt(t('org.einladungTitel'));
  inhalt.append(
    h('div', { class: 'kopf org-kopf' }, logo(e.org, true), h('div', { class: 'text' }, h('b', {}, e.org.name), h('span', { class: 'sec klein' }, rolleText(e.rolle)))),
    h('p', { class: 'sec' }, t('org.einladungText', { von: e.von_name || '–', name: e.org.name })),
    h('div', { class: 'knoepfe' },
      knopf(t('org.ablehnen'), { onclick: async (ev) => { await ablehnen(ev.currentTarget, e); schliessen(); } }),
      knopf(t('org.annehmen'), { klasse: 'btn primary', symbol: 'haken', 'data-link-annehmen': '', onclick: async (ev) => { await annehmen(ev.currentTarget, e); schliessen(); } })));
}

function neuDialog() {
  const { inhalt, schliessen } = blatt(t('org.neuTitel'));
  const st = statusFeld();
  const f = h('form', { class: 'formular', novalidate: true },
    h('p', { class: 'sec klein' }, t('org.neuText')),
    h('label', { class: 'feld' }, h('span', {}, t('org.feldName')), h('input', { name: 'name', required: true, maxlength: '80', placeholder: t('org.nameBeispiel'), autocomplete: 'organization' })),
    h('label', { class: 'feld' }, h('span', {}, t('org.feldSlug')), h('input', { name: 'slug', maxlength: '40', pattern: '[a-z0-9][a-z0-9\\-]{1,38}[a-z0-9]', autocapitalize: 'none', spellcheck: 'false', placeholder: t('org.slugAuto') }),
      h('small', {}, t('org.slugHinweis'))),
    farbwahl(FARBEN_ORG[0]),
    st,
    h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn primary' }, t('org.anlegen'))));
  inhalt.append(f);
  f.name.focus();
  f.addEventListener('submit', (ev) => {
    ev.preventDefault();
    if (f.name.value.trim().length < 2) { status(st, t('org.nameFehlt'), 'err'); f.name.focus(); return; }
    const json = { name: f.name.value.trim(), farbe: new FormData(f).get('farbe') };
    if (f.slug.value.trim()) json.slug = f.slug.value.trim().toLowerCase();
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const { org } = await api('/api/orgs', { methode: 'POST', json });
      schliessen(); toast(t('org.angelegt'), 'ok');
      await uebersichtLaden(); oeffnen(org.slug);
    }, st);
  });
}

function farbwahl(wert) {
  return h('fieldset', { class: 'feld ohne-rahmen' }, h('legend', {}, h('b', {}, t('org.feldFarbe'))),
    h('div', { class: 'farben' }, FARBEN_ORG.map((farbe) => {
      const l = h('label', { title: farbe }, h('input', { type: 'radio', name: 'farbe', value: farbe, checked: farbe === wert, 'aria-label': farbe }));
      l.style.background = farbe;
      return l;
    })));
}

// ---------------------------------------------------------------- Eine Organisation
function schliessenDetail() {
  offen = null;
  $('#org-detail').hidden = true;
  $('#org-uebersicht').hidden = false;
}

async function oeffnen(slug) {
  offen = slug;
  const box = $('#org-detail');
  let o;
  try { o = (await api(`/api/orgs/${slug}`)).org; } catch (e) { toast(e.message, 'err'); schliessenDetail(); return; }
  const admin = rolleRang(o.rolle) >= rolleRang('admin');
  $('#org-uebersicht').hidden = true;
  box.hidden = false;
  box.replaceChildren(
    h('section', { class: 'card', 'aria-labelledby': 'org-titel' },
      h('div', { class: 'kopf org-kopf' }, logo(o, true),
        h('div', { class: 'text' }, h('h2', { class: 'flach', id: 'org-titel' }, o.name),
          h('span', { class: 'sec klein' }, `${rolleText(o.rolle)} · `, h('span', { class: 'mono' }, o.slug)))),
      h('div', { class: 'aktionen oben' },
        knopf(t('org.zurueck'), { klasse: 'btn klein', symbol: 'pfeilLinks', onclick: () => { schliessenDetail(); uebersichtLaden(); } }),
        knopf(t('org.verlassen'), { klasse: 'btn klein gefahr', symbol: 'tuer', onclick: () => verlassen(o) }))),
    abschnitt('org-h-meetings', t('org.meetings'), 'liste-org-raeume',
      admin ? [knopf(t('html.neuesMeeting'), { klasse: 'btn primary klein', symbol: 'video', 'data-org-sofort': '', onclick: (ev) => sofort(ev.currentTarget, o) }),
        knopf(t('html.meetingPlanen'), { klasse: 'btn klein', symbol: 'kalender', 'data-org-planen': '',
          onclick: () => dispatchEvent(new CustomEvent('agora:org-planen', { detail: { ziel: `/api/orgs/${o.slug}/raeume`, danach: () => raeumeLaden(o) } })) })] : [],
      admin ? t('org.meetingsTextAdmin') : t('org.meetingsText')),
    abschnitt('org-h-vorlagen', t('html.vorlagen'), 'liste-org-vorlagen',
      admin ? [knopf(t('html.neueVorlage'), { klasse: 'btn klein', symbol: 'plus', onclick: () => vorlageDialog(null, { basis: `/api/orgs/${o.slug}/vorlagen`, danach: () => vorlagenLaden(o) }) })] : [],
      t('org.vorlagenText')),
    abschnitt('org-h-aufnahmen', t('html.aufnahmen'), 'liste-org-aufnahmen', [], null,
      h('div', { class: 'speicher' }, h('div', { class: 'zeile' }, h('span', { id: 'org-speicher-text' }, '–'), h('span', { class: 'sec', id: 'org-speicher-prozent' })),
        h('div', { class: 'fortschritt', role: 'progressbar', 'aria-labelledby': 'org-h-aufnahmen', 'aria-valuemin': '0', 'aria-valuemax': '100', id: 'org-speicher-balken' }, h('i', {})),
        h('p', { class: 'sec klein' }, t('org.aufnahmenText')))),
    abschnitt('org-h-mitglieder', t('org.mitglieder'), 'liste-org-mitglieder',
      admin ? [knopf(t('org.einladen'), { klasse: 'btn klein', symbol: 'mail', 'data-org-einladen': '', onclick: () => einladenDialog(o) })] : []),
    admin ? abschnitt('org-h-einladungen', t('org.offeneEinladungen'), 'liste-org-offen', []) : null,
    admin ? einstellungenKarte(o) : null);
  mitgliederZeigen(o);
  ssoKarte(o, () => oeffnen(o.slug)).then((karte) => { if (karte && offen === o.slug) box.append(karte); });
  await Promise.all([raeumeLaden(o), vorlagenLaden(o), aufnahmenLaden(o), admin ? offeneLaden(o) : null]);
}

function abschnitt(id, titel, listeId, knoepfe = [], text = null, vorspann = null) {
  return h('section', { class: 'card', 'aria-labelledby': id },
    h('div', { class: 'zeile zwischen' }, h('h2', { id }, titel), knoepfe.length ? h('div', { class: 'aktionen' }, knoepfe) : null),
    text ? h('p', { class: 'sec klein' }, text) : null,
    vorspann,
    h('ul', { class: 'liste', id: listeId }));
}

function sofort(el, o) {
  beschaeftigt(el, async () => {
    const { raum } = await api(`/api/orgs/${o.slug}/raeume`, { methode: 'POST', json: { titel: t('org.sofortTitel', { name: o.name }) } });
    location.href = `/r/${raum.code}`;
  });
}

async function raeumeLaden(o) {
  const ul = $('#liste-org-raeume');
  if (!ul) return;
  try {
    const r = await api(`/api/orgs/${o.slug}/raeume`);
    const alle = [...r.geplant, ...r.vergangen];
    ul.replaceChildren(...(alle.length ? alle.map((raum) => raumEintrag(o, raum, r.vergangen.includes(raum))) : [h('li', { class: 'leer' }, t('org.keineMeetings'))]));
  } catch (e) { ul.replaceChildren(h('li', { class: 'leer' }, e.message)); }
}

function raumEintrag(o, raum, vergangen) {
  const termin = raum.naechster_termin || raum.geplant?.start;
  const info = [termin ? zeit(termin) : (raum.aktiv ? t('konto.laeuft') : t('org.sofortMeeting'))];
  const marker = [];
  if (raum.aktiv) marker.push(h('span', { class: 'marker ok' }, t('konto.laeuft')));
  if (raum.passcode_noetig) marker.push(h('span', { class: 'marker' }, t('konto.passcode')));
  if (raum.warteraum) marker.push(h('span', { class: 'marker' }, t('konto.warteraum')));
  if (vergangen) marker.push(h('span', { class: 'marker' }, t('org.vorbei')));
  const aktionen = h('div', { class: 'aktionen' },
    h('a', { class: 'btn primary klein', href: `/r/${raum.code}` }, raum.ist_host ? t('konto.starten') : t('org.teilnehmen')),
    knopf(t('konto.linkKopieren'), { klasse: 'btn klein', symbol: 'kopie', onclick: () => kopieren(raum.link, t('konto.linkKopiert')) }));
  if (raum.ist_host) {
    aktionen.append(
      knopf(t('konto.einladen'), { klasse: 'btn klein', symbol: 'mail', onclick: () => dispatchEvent(new CustomEvent('agora:org-einladen', { detail: { raum } })) }),
      raum.geplant ? knopf(t('konto.bearbeiten'), { klasse: 'btn klein', symbol: 'stift',
        onclick: () => dispatchEvent(new CustomEvent('agora:org-planen', { detail: { raum, danach: () => raeumeLaden(o) } })) }) : null,
      knopf(t('konto.loeschen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: async (ev) => {
        const el = ev.currentTarget;
        if (!await bestaetigen(t('konto.loeschenTitel'), t('konto.loeschenText', { titel: raum.titel || raum.code }), { ja: t('konto.loeschen'), gefahr: true })) return;
        beschaeftigt(el, async () => { await api(`/api/raeume/${raum.code}`, { methode: 'DELETE' }); toast(t('konto.geloescht'), 'ok'); raeumeLaden(o); });
      } }));
  }
  return h('li', { 'data-org-raum': raum.code },
    h('div', { class: 'kopf' }, h('div', { class: 'symbol', html: icon(raum.geplant ? 'kalender' : 'video') }),
      h('div', { class: 'text' }, h('span', { class: 'name' }, raum.titel || t('konto.ohneTitel')), h('span', { class: 'info' }, info.join(' · ')),
        h('span', { class: 'info mono' }, raum.code))),
    marker.length ? h('div', { class: 'zeile' }, marker) : null,
    aktionen);
}

async function vorlagenLaden(o) {
  const ul = $('#liste-org-vorlagen');
  if (!ul) return;
  const admin = rolleRang(o.rolle) >= rolleRang('admin');
  try {
    const { vorlagen } = await api(`/api/orgs/${o.slug}/vorlagen`);
    ul.replaceChildren(...(vorlagen.length ? vorlagen.map((v) => h('li', { 'data-org-vorlage': v.id },
      h('div', { class: 'kopf' }, h('div', { class: 'symbol', html: icon('stern') }),
        h('div', { class: 'text' }, h('span', { class: 'name' }, v.name), v.einstellungen.titel ? h('span', { class: 'info' }, v.einstellungen.titel) : null)),
      h('div', { class: 'zeile' }, vorlageMerkmale(v.einstellungen).map((x) => h('span', { class: 'marker' }, x))),
      h('div', { class: 'aktionen' },
        admin ? knopf(t('org.vorlageOrg'), { klasse: 'btn primary klein', symbol: 'video', onclick: (ev) => ausVorlage(ev.currentTarget, o, v, 'org') }) : null,
        knopf(t('org.vorlageEigen'), { klasse: admin ? 'btn klein' : 'btn primary klein', symbol: 'person', onclick: (ev) => ausVorlage(ev.currentTarget, o, v, 'konto') }),
        admin ? knopf(t('konto.bearbeiten'), { klasse: 'btn klein', symbol: 'stift', onclick: () => vorlageDialog(v, { basis: `/api/orgs/${o.slug}/vorlagen`, danach: () => vorlagenLaden(o) }) }) : null,
        admin ? knopf(t('konto.loeschen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: async (ev) => {
          const el = ev.currentTarget;
          if (!await bestaetigen(t('vorlage.loeschenTitel'), t('vorlage.loeschenText', { name: v.name }), { ja: t('konto.loeschen'), gefahr: true })) return;
          beschaeftigt(el, async () => { await api(`/api/orgs/${o.slug}/vorlagen/${v.id}`, { methode: 'DELETE' }); vorlagenLaden(o); });
        } }) : null)))
      : [h('li', { class: 'leer' }, t('vorlage.keine'))]));
  } catch (e) { ul.replaceChildren(h('li', { class: 'leer' }, e.message)); }
}

function ausVorlage(el, o, v, ziel) {
  beschaeftigt(el, async () => {
    const r = await api(`/api/orgs/${o.slug}/vorlagen/${v.id}/raum`, { methode: 'POST', json: { ziel } });
    if (r.passcode) passcodeZeigen(r.raum, r.passcode, true);
    else location.href = `/r/${r.raum.code}`;
  });
}

async function aufnahmenLaden(o) {
  const ul = $('#liste-org-aufnahmen');
  if (!ul) return;
  const admin = rolleRang(o.rolle) >= rolleRang('admin');
  try {
    const r = await api(`/api/orgs/${o.slug}/aufnahmen`);
    const p = Math.min(100, Math.round(((r.belegt || 0) / (r.kontingent || 1)) * 100));
    $('#org-speicher-text').textContent = t('konto.speicherText', { belegt: groesseText(r.belegt || 0), gesamt: groesseText(r.kontingent || 0) });
    $('#org-speicher-prozent').textContent = `${p} %`;
    const balken = $('#org-speicher-balken');
    balken.setAttribute('aria-valuenow', String(p));
    balken.classList.toggle('voll', p >= 85);
    balken.firstElementChild.style.width = `${p}%`;
    ul.replaceChildren(...(r.aufnahmen.length ? r.aufnahmen.map((a) => {
      const datei = `/api/orgs/${o.slug}/aufnahmen/${a.id}/datei`;
      return h('li', {},
        h('div', { class: 'kopf' }, h('div', { class: 'symbol', html: icon('aufnahme') }),
          h('div', { class: 'text' }, h('span', { class: 'name' }, a.titel || t('konto.ohneTitel')),
            h('span', { class: 'info' }, [zeit(a.erstellt), groesseText(a.groesse), t('konto.loeschtAm', { datum: zeit(a.ablauf) })].join(' · ')))),
        h('div', { class: 'aktionen' },
          knopf(t('konto.abspielen'), { klasse: 'btn primary klein', symbol: 'abspielen', onclick: () => {
            const { inhalt } = blatt(a.titel || t('konto.ohneTitel'), { breit: true });
            inhalt.append(h('video', { src: datei, controls: true, playsinline: true, autoplay: true, preload: 'metadata' }));
          } }),
          h('a', { class: 'btn klein', href: `${datei}?download=1`, html: icon('herunter') }, h('span', {}, t('konto.herunterladen'))),
          admin ? knopf(t('konto.loeschen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: async (ev) => {
            const el = ev.currentTarget;
            if (!await bestaetigen(t('konto.aufnahmeLoeschenTitel'), t('konto.aufnahmeLoeschenText', { titel: a.titel || '' }), { ja: t('konto.loeschen'), gefahr: true })) return;
            beschaeftigt(el, async () => { await api(`/api/orgs/${o.slug}/aufnahmen/${a.id}`, { methode: 'DELETE' }); aufnahmenLaden(o); });
          } }) : null));
    }) : [h('li', { class: 'leer' }, t('konto.keineAufnahmen'))]));
  } catch (e) { ul.replaceChildren(h('li', { class: 'leer' }, e.message)); }
}

function mitgliederZeigen(o) {
  const ul = $('#liste-org-mitglieder');
  const owner = o.rolle === 'owner', admin = rolleRang(o.rolle) >= rolleRang('admin');
  ul.replaceChildren(...o.mitglieder.map((m) => {
    const aktionen = [];
    if (owner) {
      aktionen.push(h('label', { class: 'nur-sr', for: `org-rolle-${m.id}` }, t('org.rolleFuer', { name: m.name })),
        h('select', { id: `org-rolle-${m.id}`, class: 'org-rolle', 'data-mitglied-rolle': m.id, onchange: (ev) => rolleSetzen(o, m, ev.currentTarget) },
          ['owner', 'admin', 'mitglied'].map((r) => h('option', { value: r, selected: r === m.rolle }, rolleText(r)))));
    }
    const darf = admin && (owner || m.rolle === 'mitglied');
    if (darf && !m.ich) {
      aktionen.push(knopf(t('org.entfernen'), { klasse: 'btn klein gefahr', symbol: 'muell', 'data-entfernen': m.id, onclick: async (ev) => {
        const el = ev.currentTarget;
        if (!await bestaetigen(t('org.entfernenTitel', { name: m.name }), t('org.entfernenText'), { ja: t('org.entfernen'), gefahr: true })) return;
        beschaeftigt(el, async () => { await api(`/api/orgs/${o.slug}/mitglieder/${m.id}`, { methode: 'DELETE' }); toast(t('org.entfernt'), 'ok'); oeffnen(o.slug); });
      } }));
    }
    const av = h('div', { class: 'avatar klein', 'aria-hidden': 'true' }, (m.name || '?').slice(0, 1).toUpperCase());
    av.style.background = m.farbe || '';
    return h('li', { 'data-mitglied': m.id },
      h('div', { class: 'kopf' }, av, h('div', { class: 'text' }, h('span', { class: 'name' }, m.name),
        h('span', { class: 'info' }, [rolleText(m.rolle), m.email, t('org.seit', { zeit: zeit(m.seit) })].filter(Boolean).join(' · ')))),
      aktionen.length ? h('div', { class: 'aktionen' }, aktionen) : null);
  }));
}

function rolleSetzen(o, m, sel) {
  beschaeftigt(sel, async () => {
    try {
      await api(`/api/orgs/${o.slug}/mitglieder/${m.id}`, { methode: 'PATCH', json: { rolle: sel.value } });
      toast(t('konto.gespeichert'), 'ok');
    } finally { oeffnen(o.slug); }
  });
}

async function offeneLaden(o) {
  const ul = $('#liste-org-offen');
  if (!ul) return;
  try {
    const { einladungen } = await api(`/api/orgs/${o.slug}/einladungen`);
    ul.replaceChildren(...(einladungen.length ? einladungen.map((e) => h('li', {},
      h('div', { class: 'kopf' }, h('div', { class: 'symbol', html: icon('mail') }),
        h('div', { class: 'text' }, h('span', { class: 'name mono' }, e.email), h('span', { class: 'info' }, `${rolleText(e.rolle)} · ${t('org.gueltigBis', { zeit: zeit(e.gueltig_bis) })}`))),
      h('div', { class: 'aktionen' }, knopf(t('org.zurueckziehen'), { klasse: 'btn klein', symbol: 'schliessen', onclick: (ev) => beschaeftigt(ev.currentTarget, async () => {
        await api(`/api/orgs/${o.slug}/einladungen/${e.id}`, { methode: 'DELETE' }); offeneLaden(o);
      }) }))))
      : [h('li', { class: 'leer' }, t('org.keineOffenen'))]));
  } catch (e) { ul.replaceChildren(h('li', { class: 'leer' }, e.message)); }
}

function einladenDialog(o) {
  const { inhalt, schliessen } = blatt(t('org.einladenTitel', { name: o.name }));
  const st = statusFeld();
  const feld = h('textarea', { name: 'emails', rows: '4', placeholder: 'anna@beispiel.at, ben@beispiel.de', autocapitalize: 'none', spellcheck: 'false' });
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec klein' }, t('org.einladenText')),
    h('label', { class: 'feld' }, h('span', {}, t('konto.feldEmails')), feld, h('small', {}, t('konto.emailsHinweis'))),
    o.rolle === 'owner' ? h('label', { class: 'feld' }, h('span', {}, t('org.feldRolle')),
      h('select', { name: 'rolle' }, ['mitglied', 'admin'].map((r) => h('option', { value: r }, rolleText(r))))) : null,
    st,
    h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn primary' }, t('konto.einladungSenden'))));
  inhalt.append(f);
  feld.focus();
  f.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const { gueltig, ungueltig, zuViele } = emailsAusText(feld.value);
    if (ungueltig.length) { status(st, t('konto.emailsUngueltig', { liste: ungueltig.slice(0, 3).join(', ') }), 'err'); return; }
    if (!gueltig.length) { status(st, t('konto.emailsLeer'), 'err'); return; }
    if (zuViele) { status(st, t('konto.emailsZuViele'), 'err'); return; }
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const r = await api(`/api/orgs/${o.slug}/einladungen`, { methode: 'POST', json: { emails: gueltig, rolle: f.rolle?.value || 'mitglied' } });
      schliessen();
      toast(t('org.eingeladen', { n: r.gesendet, schon: r.schon_mitglied }), 'ok');
      offeneLaden(o);
    }, st);
  });
}

function einstellungenKarte(o) {
  const e = o.einstellungen;
  const st = statusFeld();
  const f = h('form', { class: 'formular', id: 'f-org-einstellungen', novalidate: true },
    h('label', { class: 'feld' }, h('span', {}, t('org.feldName')), h('input', { name: 'name', required: true, maxlength: '80', value: o.name })),
    farbwahl(o.farbe),
    h('fieldset', { class: 'feld ohne-rahmen' }, h('legend', {}, h('b', {}, t('org.standardRechte'))),
      h('small', { class: 'sec' }, t('org.standardRechteHinweis')),
      ...RECHTE.map(([k, text]) => schalter(`recht_${k}`, t(text), '', e.rechte?.[k] !== false))),
    schalter('warteraum_extern', t('org.warteraumExtern'), t('org.warteraumExternHinweis'), !!e.warteraum_extern),
    h('label', { class: 'feld' }, h('span', {}, t('org.feldDomain')),
      h('input', { name: 'domain', maxlength: '190', value: e.domain || '', placeholder: 'firma.de', autocapitalize: 'none', spellcheck: 'false' }),
      h('small', {}, t('org.domainHinweis'))),
    st,
    h('div', { class: 'knoepfe' }, h('button', { type: 'submit', class: 'btn primary' }, t('allg.speichern'))));
  f.addEventListener('submit', (ev) => {
    ev.preventDefault();
    if (f.name.value.trim().length < 2) { status(st, t('org.nameFehlt'), 'err'); return; }
    const json = { name: f.name.value.trim(), farbe: new FormData(f).get('farbe') || o.farbe, einstellungen: {
      rechte: Object.fromEntries(RECHTE.map(([k]) => [k, f[`recht_${k}`].checked])), warteraum_extern: f.warteraum_extern.checked,
      domain: f.domain.value.trim() || null } };
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      await api(`/api/orgs/${o.slug}`, { methode: 'PATCH', json });
      toast(t('konto.gespeichert'), 'ok');
      oeffnen(o.slug);
    }, st);
  });
  return h('section', { class: 'card', 'aria-labelledby': 'org-h-einstellungen' },
    h('h2', { id: 'org-h-einstellungen' }, t('konto.einstellungen')), f,
    o.rolle === 'owner' ? h('div', { class: 'org-gefahr oben' }, h('h3', {}, t('org.loeschenTitel')), h('p', { class: 'sec klein' }, t('org.loeschenText')),
      knopf(t('org.loeschen'), { klasse: 'btn gefahr', symbol: 'muell', onclick: () => loeschenDialog(o) })) : null);
}

function loeschenDialog(o) {
  const { inhalt, schliessen } = blatt(t('org.loeschenTitel'));
  const st = statusFeld();
  const feld = h('input', { name: 'slug', autocomplete: 'off', spellcheck: 'false', autocapitalize: 'none', required: true });
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec' }, t('org.loeschenText')),
    h('label', { class: 'feld' }, h('span', {}, t('org.loeschenEingabe', { slug: o.slug })), feld),
    st,
    h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn gefahr voll' }, t('org.endgueltigLoeschen'))));
  inhalt.append(f);
  feld.focus();
  f.addEventListener('submit', (ev) => {
    ev.preventDefault();
    if (feld.value.trim() !== o.slug) { status(st, t('org.loeschenFalsch'), 'err'); return; }
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      await api(`/api/orgs/${o.slug}`, { methode: 'DELETE', json: { bestaetigung: o.slug } });
      schliessen(); toast(t('org.geloescht'), 'ok'); schliessenDetail(); uebersichtLaden();
    }, st);
  });
}

async function verlassen(o) {
  if (!await bestaetigen(t('org.verlassenTitel', { name: o.name }), t('org.verlassenText'), { ja: t('org.verlassen'), gefahr: true })) return;
  await beschaeftigt(null, async () => {
    await api(`/api/orgs/${o.slug}/verlassen`, { methode: 'POST' });
    toast(t('org.verlassenOk'), 'ok'); schliessenDetail(); uebersichtLaden();
  });
}

// ---------------------------------------------------------------- Start
$('#org-neu')?.addEventListener('click', neuDialog);
addEventListener('agora:teams-zeigen', () => (offen ? oeffnen(offen) : uebersichtLaden()));
uebersichtLaden().then(linkPruefen);
