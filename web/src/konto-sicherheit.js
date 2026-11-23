// Konto → Profil, Sicherheit: Zwei-Faktor-Anmeldung (TOTP) einschalten/ausschalten, Wiederherstellungscodes.
// Eigenes Modul (eigenes <script> in konto.html), damit konto.js unberührt bleibt.
import './sicherheit.css';
import { $, h, api, ichLaden, toast, blatt, beschaeftigt, kopieren, knopf } from './common.js';
import { t } from './i18n.js';
import { qrSvg } from './qr.js';
import { datumText, zeitText } from './helfer.js';
import { icon } from './icons.js';
import { sicherheitArt } from './sicherheit-arten.js';

let ich = null;

// ---------------------------------------------------------------- Zwei-Faktor
async function zweiFaktorZeigen() {
  const box = $('#zwei-faktor');
  if (!box) return;
  let z;
  try { z = await api('/api/konto/2fa'); } catch (e) { box.replaceChildren(h('p', { class: 'leer' }, e.message)); return; }
  box.replaceChildren();
  if (!z.an) {
    box.append(
      h('div', { class: 'zeile' }, h('span', { class: 'marker' }, t('zweifaktor.aus'))),
      h('p', { class: 'sec klein' }, t('zweifaktor.erklaerung')),
      h('div', { class: 'knoepfe' }, knopf(t('zweifaktor.einschalten'), { klasse: 'btn primary', symbol: 'schloss', id: 'zf-einschalten', onclick: einschaltenDialog })));
    return;
  }
  box.append(
    h('div', { class: 'zeile' }, h('span', { class: 'marker ok' }, t('zweifaktor.an')),
      h('span', { class: `sec klein${z.codes_uebrig <= 2 ? ' zf-warnung' : ''}` }, t('zweifaktor.codesUebrig', { n: z.codes_uebrig }))),
    h('p', { class: 'sec klein' }, t('zweifaktor.anErklaerung')),
    h('div', { class: 'knoepfe' },
      knopf(t('zweifaktor.neueCodes'), { klasse: 'btn', symbol: 'kopie', onclick: () => passwortUndCode('codes') }),
      knopf(t('zweifaktor.ausschalten'), { klasse: 'btn gefahr', symbol: 'schlossOffen', id: 'zf-ausschalten', onclick: () => passwortUndCode('aus') })));
}

const statusFeld = () => h('div', { class: 'status', role: 'status', hidden: true });

function einschaltenDialog() {
  const { inhalt, schliessen } = blatt(t('zweifaktor.einrichtenTitel'), { onclose: () => { zweiFaktorZeigen(); ereignisseZeigen(); } });
  const st = statusFeld();
  // Schritt 1: Passwort bestätigen
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec' }, t('zweifaktor.schritt1')),
    h('input', { type: 'text', name: 'benutzer', autocomplete: 'username', hidden: true, value: ich?.email || '' }),
    h('label', { class: 'feld' }, h('span', {}, t('konto.passwortBestaetigen')),
      h('input', { name: 'passwort', type: 'password', autocomplete: 'current-password', required: true })),
    st,
    h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn primary' }, t('zweifaktor.weiter'))));
  inhalt.append(f);
  f.passwort.focus();
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!f.reportValidity()) return;
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const r = await api('/api/konto/2fa/einrichten', { methode: 'POST', json: { passwort: f.passwort.value } });
      scannenZeigen(inhalt, r, schliessen);
    }, st);
  });
}

function scannenZeigen(inhalt, { geheim, uri }, schliessen) {
  const st = statusFeld();
  const gruppen = geheim.match(/.{1,4}/g).join(' ');
  const code = h('input', { name: 'code', inputmode: 'numeric', autocomplete: 'one-time-code', pattern: '[0-9 ]{6,7}', maxlength: '7', required: true,
    class: 'zf-code', placeholder: '123456', 'aria-describedby': 'zf-code-hinweis' });
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec' }, t('zweifaktor.schritt2')),
    h('div', { class: 'zf-qr' }, qrSvg(uri, { label: t('zweifaktor.qrLabel') })),
    h('details', { class: 'zf-geheim' }, h('summary', {}, t('zweifaktor.ohneKamera')),
      h('p', { class: 'sec klein' }, t('zweifaktor.ohneKameraText')),
      h('div', { class: 'link-feld mono', id: 'zf-geheim' }, gruppen),
      knopf(t('zweifaktor.schluesselKopieren'), { klasse: 'btn klein', symbol: 'kopie', onclick: () => kopieren(geheim, t('allg.kopiert')) })),
    h('label', { class: 'feld' }, h('span', {}, t('zweifaktor.codeFeld')), code,
      h('small', { id: 'zf-code-hinweis' }, t('zweifaktor.codeHinweis'))),
    st,
    h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn primary' }, t('zweifaktor.einschalten'))));
  inhalt.replaceChildren(f);
  code.focus();
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!f.reportValidity()) return;
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const r = await api('/api/konto/2fa/bestaetigen', { methode: 'POST', json: { code: code.value.replace(/\s/g, '') } });
      codesZeigen(inhalt, r.wiederherstellungscodes, schliessen, t('zweifaktor.eingeschaltet'));
    }, st);
  });
}

/** Wiederherstellungscodes – nur jetzt sichtbar: kopieren, herunterladen, „Fertig“. */
function codesZeigen(inhalt, codes, schliessen, titel) {
  const text = `${t('zweifaktor.dateiKopf', { email: ich?.email || '' })}\n\n${codes.join('\n')}\n`;
  const datei = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  inhalt.replaceChildren(
    h('div', { class: 'status ok' }, titel),
    h('p', { class: 'sec' }, t('zweifaktor.codesText')),
    h('ol', { class: 'zf-codes mono', 'aria-label': t('zweifaktor.codesTitel') }, codes.map((c) => h('li', {}, c))),
    h('div', { class: 'knoepfe' },
      knopf(t('zweifaktor.kopieren'), { klasse: 'btn', symbol: 'kopie', onclick: () => kopieren(codes.join('\n'), t('allg.kopiert')) }),
      h('a', { class: 'btn', href: datei, download: 'agora-wiederherstellungscodes.txt' }, t('zweifaktor.herunterladen')),
      h('button', { type: 'button', class: 'btn primary', id: 'zf-fertig', onclick: () => { URL.revokeObjectURL(datei); schliessen(); } }, t('allg.fertig'))));
}

/** Ausschalten oder neue Codes: Passwort + aktueller Code (oder ein Wiederherstellungscode). */
function passwortUndCode(art) {
  const { inhalt, schliessen } = blatt(art === 'aus' ? t('zweifaktor.ausschaltenTitel') : t('zweifaktor.neueCodesTitel'), { onclose: () => { zweiFaktorZeigen(); ereignisseZeigen(); } });
  const st = statusFeld();
  const f = h('form', { class: 'formular' },
    h('p', { class: 'sec' }, art === 'aus' ? t('zweifaktor.ausschaltenText') : t('zweifaktor.neueCodesText')),
    h('input', { type: 'text', name: 'benutzer', autocomplete: 'username', hidden: true, value: ich?.email || '' }),
    h('label', { class: 'feld' }, h('span', {}, t('konto.passwortBestaetigen')),
      h('input', { name: 'passwort', type: 'password', autocomplete: 'current-password', required: true })),
    h('label', { class: 'feld' }, h('span', {}, t('zweifaktor.codeOderWiederherstellung')),
      h('input', { name: 'code', autocomplete: 'one-time-code', required: true, maxlength: '20', autocapitalize: 'none', spellcheck: 'false' })),
    st,
    h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: art === 'aus' ? 'btn gefahr voll' : 'btn primary' },
        art === 'aus' ? t('zweifaktor.ausschalten') : t('zweifaktor.neueCodes'))));
  inhalt.append(f);
  f.passwort.focus();
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!f.reportValidity()) return;
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const r = await api(art === 'aus' ? '/api/konto/2fa/ausschalten' : '/api/konto/2fa/codes',
        { methode: 'POST', json: { passwort: f.passwort.value, code: f.code.value.trim() } });
      if (art === 'aus') { schliessen(); toast(t('zweifaktor.ausgeschaltet'), 'ok'); } else codesZeigen(inhalt, r.wiederherstellungscodes, schliessen, t('zweifaktor.neueCodesFertig'));
    }, st);
  });
}

// ---------------------------------------------------------------- Sicherheitsereignisse (eigenes Protokoll)
async function ereignisseZeigen() {
  const ul = $('#liste-sicherheit');
  if (!ul) return;
  try {
    const { ereignisse } = await api('/api/konto/sicherheit');
    ul.replaceChildren();
    if (!ereignisse.length) { ul.append(h('li', { class: 'leer' }, t('sicherheit.leer'))); return; }
    for (const e of ereignisse) {
      const a = sicherheitArt(e.art);
      ul.append(h('li', { class: `sicherheit-${a.ton}`, dataset: { art: e.art } }, h('div', { class: 'kopf' },
        h('div', { class: 'symbol', html: icon(a.symbol) }),
        h('div', { class: 'text' }, h('span', { class: 'name' }, t(`sicherheit.art.${e.art}`)),
          h('span', { class: 'info' }, zeitText(e.zeit, ich?.zeitzone))))));
    }
  } catch (e) { ul.replaceChildren(h('li', { class: 'leer' }, e.message)); }
}
addEventListener('agora:sicherheit-neu', ereignisseZeigen);

// ---------------------------------------------------------------- Blockierte Konten
async function blockiertZeigen() {
  const ul = $('#liste-blockiert');
  if (!ul) return;
  try {
    const { blockiert } = await api('/api/konto/blockiert');
    ul.replaceChildren();
    if (!blockiert.length) { ul.append(h('li', { class: 'leer' }, t('blockiert.leer'))); return; }
    for (const b of blockiert) {
      ul.append(h('li', {}, h('div', { class: 'kopf' },
        h('div', { class: 'text' }, h('span', { class: 'name' }, b.name),
          h('span', { class: 'info' }, t('blockiert.seit', { datum: datumText(b.seit, ich?.zeitzone) })))),
      h('div', { class: 'aktionen' }, knopf(t('blockiert.aufheben'), { klasse: 'btn klein', onclick: (e) => beschaeftigt(e.currentTarget, async () => {
        await api(`/api/konto/blockiert/${b.id}`, { methode: 'DELETE' });
        toast(t('blockiert.aufgehoben'), 'ok');
        blockiertZeigen();
      }) }))));
    }
  } catch (e) { ul.replaceChildren(h('li', { class: 'leer' }, e.message)); }
}

// ---------------------------------------------------------------- Start
(async () => {
  ich = await ichLaden();
  if (!ich) return;               // konto.js leitet zur Anmeldung weiter
  zweiFaktorZeigen();
  blockiertZeigen();
  ereignisseZeigen();
  if (ich.admin) $('.bar .rechts')?.prepend(h('a', { class: 'pille-link', href: '/admin', id: 'admin-link' }, t('admin.link')));
})();
