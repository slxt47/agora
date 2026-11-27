// Startseite: „Anmelden mit Google/Microsoft/GitHub“ (nur Anbieter, die der Server nennt – /api/einstellungen),
// Rückkehr vom Anbieter: Zwei-Faktor-Schritt (#zf=…), bestehendes Konto mit Passwort verknüpfen (#verknuepfen=…),
// Fehler (?anmeldung=fehler&grund=…, nur feste Codes – der Text kommt aus dem Wörterbuch, nie aus der Adresse).
import './anmeldung.css';
import { $, h, api, beschaeftigt, status } from './common.js';
import { t } from './i18n.js';

const GRUENDE = ['abgebrochen', 'ungueltig', 'anbieter', 'token', 'email_unbestaetigt', 'kein_passwort', 'gesperrt',
  'schon_verknuepft', 'schon_da', 'firma_pflicht', 'zu_viele', 'domain'];

/** Text zu einem Fehler-Code aus der Umleitung (unbekannte Codes → allgemein). */
export function fehlerText(grund, anbieter = '') {
  return t(`anmeldung.grund.${GRUENDE.includes(grund) ? grund : 'ungueltig'}`, { anbieter: anbieter || t('anmeldung.derAnbieter') });
}

/** Anmelde-Knopf: per POST starten (X-Agora, Cookie für die Bindung), dann zum Anbieter. */
export async function starten(anbieter, json = {}) {
  const r = await api(`/api/anmeldung/${encodeURIComponent(anbieter)}/start`, { methode: 'POST', json });
  location.href = r.url;
  await new Promise(() => {});        // Seite wechselt
}

export async function anmeldungEinrichten({ angemeldet, weiter, zeigen, meldung, codeSchritt, fertigAngemeldet }) {
  const params = new URLSearchParams(location.search);
  const frag = new URLSearchParams(location.hash.slice(1));
  // Fehler aus der Umleitung
  if (params.get('anmeldung') === 'fehler') {
    const namen = { google: 'Google', microsoft: 'Microsoft', github: 'GitHub' };
    const text = fehlerText(params.get('grund'), namen[params.get('anbieter')] || '');
    for (const k of ['anmeldung', 'grund', 'anbieter']) params.delete(k);
    history.replaceState(null, '', location.pathname + (params.toString() ? `?${params}` : ''));
    meldung(text, 'err');
  }
  if (angemeldet) return;
  // Fragment sofort aus der Adresse entfernen (Verlauf, Lesezeichen)
  if (frag.has('zf') || frag.has('verknuepfen')) history.replaceState(null, '', location.pathname + location.search);
  if (frag.get('zf')) codeSchritt(frag.get('zf'));
  else if (frag.get('verknuepfen')) await verknuepfenZeigen(frag.get('verknuepfen'), { zeigen, meldung, fertigAngemeldet });

  let einst = {};
  try { einst = await api('/api/einstellungen'); } catch { return; }
  const anbieter = Array.isArray(einst.anmeldung) ? einst.anmeldung : [];
  const box = $('#extern-knoepfe');
  if (!box || !anbieter.length) return;
  box.replaceChildren(...anbieter.map((a) => h('button', { type: 'button', class: 'btn extern', 'data-anbieter': a.id,
    onclick: (e) => beschaeftigt(e.currentTarget, () => starten(a.id, { weiter }), $('#status')) },
  h('span', { class: `extern-marke extern-${a.id}`, 'aria-hidden': 'true' }, a.name.slice(0, 1)), t('anmeldung.mit', { name: a.name }))));
  $('#extern-anmelden').hidden = false;
}

async function verknuepfenZeigen(token, { zeigen, meldung, fertigAngemeldet }) {
  let info;
  try { info = await api('/api/anmeldung/offen', { methode: 'POST', json: { token } }); } catch (e) { meldung(e.message, 'err'); return; }
  const f = $('#f-verknuepfen');
  $('#verknuepfen-text').textContent = t('anmeldung.verknuepfenText', { anbieter: info.name, email: info.email });
  f.benutzer.value = info.email;
  const codeFeld = $('#verknuepfen-code');
  codeFeld.hidden = !info.zwei_faktor;
  f.code.required = !!info.zwei_faktor;
  zeigen('f-verknuepfen');
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!f.reportValidity()) return;
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const json = { token, passwort: f.passwort.value };
      if (!codeFeld.hidden && f.code.value.trim()) json.code = f.code.value.replace(/\s/g, '');
      const r = await api('/api/anmeldung/verknuepfen', { methode: 'POST', json });
      if (r.zwei_faktor) { codeFeld.hidden = false; f.code.required = true; f.code.focus(); status($('#status'), t('anmeldung.codeNoetig'), 'warn'); return; }
      f.reset();
      fertigAngemeldet(r);
    }, $('#status'));
  });
}
