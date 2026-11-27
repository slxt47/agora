// Startseite: Beitreten per Code/Link, Anmelden / Registrieren / Passwort vergessen / Bestätigung erneut,
// Sofort-Meeting für Angemeldete, Rückmeldung nach dem Bestätigungslink (?bestaetigt=1|0).
import { $, api, ApiFehler, ichLaden, status, toast, beschaeftigt } from './common.js';
import { t } from './i18n.js';
import { codeAusText, initialen } from './helfer.js';
import { schluesselAusFragment } from './e2ee-schluessel.js';
import './sicherheit.css';
import { anmeldungEinrichten } from './anmeldung-start.js';     // Anmelden mit Google/Microsoft/GitHub (nur wenn eingerichtet)

const params = new URLSearchParams(location.search);
// Nur eigene Pfade als Ziel nach der Anmeldung (kein //fremd.de)
const weiter = (() => { const w = params.get('weiter') || ''; return /^\/(?!\/)[\w\-/#?=&.]*$/.test(w) ? w : ''; })();
const FORMULARE = ['f-anmelden', 'f-registrieren', 'f-vergessen', 'f-erneut', 'f-code', 'f-verknuepfen'];
let ich = null;
let schritt = null;          // Zwei-Faktor: Passwort war richtig, Code fehlt noch

function zeigen(id) {
  for (const f of FORMULARE) $('#' + f).hidden = f !== id;
  $('#status').hidden = true;
  const reg = id === 'f-registrieren';
  $('#tab-anmelden').setAttribute('aria-selected', String(!reg));
  $('#tab-registrieren').setAttribute('aria-selected', String(reg));
  $('#' + id).querySelector('input')?.focus({ preventScroll: true });
}
const meldung = (text, art = 'err') => status($('#status'), text, art);

$('#tab-anmelden').addEventListener('click', () => zeigen('f-anmelden'));
$('#tab-registrieren').addEventListener('click', () => zeigen('f-registrieren'));
// Pfeiltasten zwischen den Reitern (WAI-ARIA Tabs)
$('.umschalter[role="tablist"]').addEventListener('keydown', (e) => {
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  const neu = document.activeElement.id === 'tab-anmelden' ? 'tab-registrieren' : 'tab-anmelden';
  $('#' + neu).focus(); $('#' + neu).click();
});
for (const b of document.querySelectorAll('[data-zeige]')) b.addEventListener('click', () => zeigen(b.dataset.zeige));

function senden(formId, fn) {
  const f = $('#' + formId);
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!f.reportValidity()) return;
    beschaeftigt(f.querySelector('button[type="submit"]'), () => fn(f), $('#status'));
  });
}

senden('f-anmelden', async (f) => {
  try {
    const r = await api('/api/konto/anmelden', { methode: 'POST', json: { email: f.email.value.trim(), passwort: f.passwort.value } });
    f.passwort.value = '';
    if (r.zwei_faktor) { codeSchritt(r.schritt); return; }
    fertigAngemeldet(r);
  } catch (e) {
    if (e instanceof ApiFehler && e.status === 403 && /best[äa]tig/i.test(e.message)) {
      meldung(t('start.nichtBestaetigt'), 'warn');
      $('#f-erneut').email.value = f.email.value.trim();
      return;
    }
    throw e;
  }
});

function fertigAngemeldet(r) {
  if (weiter) { location.href = weiter; return; }
  angemeldet(r.ich);
  toast(t('start.willkommen', { name: r.ich?.name || '' }), 'ok');
}

// ---------------------------------------------------------------- Zwei-Faktor: zweiter Schritt (Code)
function codeSchritt(neu) {
  schritt = neu;
  const f = $('#f-code');
  f.code.value = '';
  f.code.inputMode = 'numeric';
  $('#code-text').textContent = t('zweifaktor.anmeldenText');
  zeigen('f-code');
}
$('#code-wiederherstellung').addEventListener('click', () => {
  const feld = $('#f-code').code;
  feld.inputMode = 'text';
  $('#code-text').textContent = t('zweifaktor.anmeldenWiederherstellung');
  feld.focus();
});
senden('f-code', async (f) => {
  try {
    const r = await api('/api/konto/anmelden/code', { methode: 'POST', json: { schritt, code: f.code.value.replace(/\s/g, '') } });
    schritt = null;
    f.code.value = '';
    fertigAngemeldet(r);
    if (typeof r.codes_uebrig === 'number' && r.codes_uebrig <= 2) toast(t('zweifaktor.wenigeCodes', { n: r.codes_uebrig }), 'warn', { dauer: 9000 });
  } catch (e) {
    if (e instanceof ApiFehler && e.status === 400 && !/stimmt nicht/.test(e.message)) {   // abgelaufen / zu viele → neu anmelden
      schritt = null;
      zeigen('f-anmelden');
      meldung(e.message, 'warn');
      return;
    }
    f.code.select();
    throw e;
  }
});

senden('f-registrieren', async (f) => {
  await api('/api/konto/registrieren', { methode: 'POST', json: { name: f.name.value.trim(), email: f.email.value.trim(), passwort: f.passwort.value } });
  const email = f.email.value.trim();
  f.reset();
  zeigen('f-anmelden');
  $('#f-anmelden').email.value = email;
  meldung(t('start.registriert', { email }), 'ok');
});

senden('f-vergessen', async (f) => {
  await api('/api/konto/passwort-vergessen', { methode: 'POST', json: { email: f.email.value.trim() } });
  meldung(t('start.vergessenGesendet'), 'ok');
});

senden('f-erneut', async (f) => {
  await api('/api/konto/bestaetigung-erneut', { methode: 'POST', json: { email: f.email.value.trim() } });
  meldung(t('start.erneutGesendet'), 'ok');
});

// ---------------------------------------------------------------- Beitreten per Code oder Link
const codeFeld = $('#code-eingabe');
let linkSchluessel = '';      // E2EE: #k=… aus einem eingefügten Link mitnehmen (bleibt im Fragment)
const schluesselAus = (text) => schluesselAusFragment(String(text).slice(String(text).indexOf('#')));
$('#f-beitreten').addEventListener('submit', (e) => {
  e.preventDefault();
  const code = codeAusText(codeFeld.value);
  if (!code) {
    codeFeld.setAttribute('aria-invalid', 'true');
    status($('#beitreten-status'), codeFeld.value.trim() ? t('start.codeUngueltig') : t('start.codeLeer'), 'err');
    codeFeld.focus();
    return;
  }
  const k = schluesselAus(codeFeld.value) || linkSchluessel;
  location.href = `/r/${code}${k ? `#k=${k}` : ''}`;
});
codeFeld.addEventListener('input', () => { codeFeld.removeAttribute('aria-invalid'); $('#beitreten-status').hidden = true; });
// Eingefügter Link → sofort normalisieren, damit man sieht, dass er erkannt wurde
codeFeld.addEventListener('paste', () => setTimeout(() => {
  if (codeFeld.value.includes('#')) linkSchluessel = schluesselAus(codeFeld.value);
  const c = codeAusText(codeFeld.value); if (c) codeFeld.value = c;
}));

// ---------------------------------------------------------------- Angemeldet
function angemeldet(daten) {
  ich = daten;
  $('#angemeldet').hidden = !ich;
  $('#abgemeldet').hidden = !!ich;
  const kopf = $('#kopf-konto');
  kopf.hidden = !ich;
  if (!ich) { zeigen('f-anmelden'); return; }
  $('#status').hidden = true;
  $('#ich-name').textContent = ich.name;
  const av = $('#ich-avatar');
  av.textContent = initialen(ich.name);
  av.style.background = ich.farbe || '';
  kopf.textContent = t('start.konto');
  if (ich.persoenlicher_raum) {
    $('#persoenlich-link').href = `/r/${ich.persoenlicher_raum}`;
    $('#persoenlich-code').textContent = ich.persoenlicher_raum;
    $('#persoenlich-code').classList.add('mono');
  }
}

$('#neues-meeting').addEventListener('click', (e) => beschaeftigt(e.currentTarget, async () => {
  const { raum } = await api('/api/raeume', { methode: 'POST', json: { titel: t('start.sofortTitel', { name: ich?.name || '' }) } });
  location.href = `/r/${raum.code}`;
}));

$('#abmelden').addEventListener('click', (e) => beschaeftigt(e.currentTarget, async () => {
  await api('/api/konto/abmelden', { methode: 'POST' });
  angemeldet(null);
  toast(t('start.abgemeldet'), 'ok');
}));

// ---------------------------------------------------------------- Start
(async () => {
  const b = params.get('bestaetigt');
  angemeldet(await ichLaden());
  if (b === '1') meldung(t('start.bestaetigtJa'), 'ok');
  else if (b === '0') {
    if (!ich) zeigen('f-erneut');   // zeigen() blendet den Status aus – Meldung danach setzen
    meldung(t('start.bestaetigtNein'), 'err');
  }
  if (params.get('abgelaufen') === '1') meldung(t('start.sitzungAbgelaufen'), 'warn');
  if (b !== null || params.has('abgelaufen')) {
    params.delete('bestaetigt'); params.delete('abgelaufen');
    history.replaceState(null, '', location.pathname + (params.toString() ? `?${params}` : ''));
  }
  if (weiter && ich) location.replace(weiter);
  anmeldungEinrichten({ angemeldet: !!ich, weiter, zeigen, meldung, codeSchritt, fertigAngemeldet });
  const vorgabe = params.get('code');
  if (vorgabe) codeFeld.value = codeAusText(vorgabe) || '';
  // App-Verknüpfungen aus dem Manifest: /?aktion=neu | /?aktion=beitreten
  const aktion = params.get('aktion');
  if (aktion) {
    params.delete('aktion');
    history.replaceState(null, '', location.pathname + (params.toString() ? `?${params}` : ''));
    if (aktion === 'neu' && ich) $('#neues-meeting').click();
    else if (aktion === 'neu') meldung(t('pwa.zuerstAnmelden'), 'warn');
    else if (aktion === 'beitreten') codeFeld.focus();
  }
})();
