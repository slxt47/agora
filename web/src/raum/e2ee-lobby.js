// Lobby-Teil der Ende-zu-Ende-Verschlüsselung:
// - Schlüssel aus dem Link-Fragment (#k=…), beim Host auch aus dem Gerätespeicher; fehlt er, fragt die Lobby danach
//   und prüft ihn gegen den Prüfwert des Servers (falscher Schlüssel → klare Meldung, kein Beitritt).
// - Host: Schalter „Ende-zu-Ende-verschlüsselt“ (erzeugt das Geheimnis im Browser) und „Neuer Schlüssel“.
// - Browser ohne Encoded Transforms / RTCRtpScriptTransform: Beitritt gesperrt mit Hinweis.
import { isE2EESupported } from 'livekit-client';
import { $, h, api, status } from '../common.js';
import { t } from '../i18n.js';
import {
  linkMitSchluessel, pruefwert, schluesselAusFragment, schluesselAusText, schluesselGemerkt, schluesselMerken, schluesselNeu,
} from '../e2ee-schluessel.js';

const moeglich = () => { try { return isE2EESupported(); } catch { return false; } };

export class E2eeLobby {
  constructor(code, raum, aendern) {
    this.code = code;
    this.raum = raum;
    this.aendern = aendern;           // neuer Raum-Zustand nach PATCH
    this.schluessel = '';
    this.statusEl = $('#beitritt-status');
    this.feld = $('#feld-schluessel');
  }

  einrichten() {
    const vomLink = schluesselAusFragment(location.hash);
    this.schluessel = vomLink || (this.raum.ist_host ? schluesselGemerkt(this.code) : '');
    if (vomLink && this.raum.ist_host && this.raum.e2ee) schluesselMerken(this.code, vomLink);
    this.anzeigen();
  }

  anzeigen() {
    const r = this.raum;
    $('#merkmal-e2ee')?.remove();
    if (r.e2ee) $('#raum-merkmale').append(h('span', { class: 'marker ok e2ee', id: 'merkmal-e2ee' }, t('e2ee.kurz')));
    this.feld.hidden = !r.e2ee || !!this.schluessel;
    $('#f-beitritt').schluessel.required = !this.feld.hidden;
    if (r.e2ee && !moeglich()) {
      status(this.statusEl, t('e2ee.nichtUnterstuetzt'), 'err');
      $('#beitreten-knopf').disabled = true;
    }
    if (r.ist_host) this.hostSchalter();
  }

  hostSchalter() {
    const box = $('#feld-e2ee');
    box.hidden = false;
    const r = this.raum;
    const schalter = h('input', { type: 'checkbox', role: 'switch', name: 'e2ee', checked: !!r.e2ee, disabled: !moeglich(),
      onchange: (e) => this.umschalten(e.target.checked, e.target) });
    box.replaceChildren(...[
      h('label', { class: 'schalter' }, h('span', { class: 'txt' }, h('span', {}, t('e2ee.schalter')), h('small', {}, t('e2ee.schalterHinweis'))), schalter),
      r.e2ee && !this.schluessel ? h('div', { class: 'e2ee-aktionen' },
        h('span', { class: 'e2ee-hinweis' }, t('e2ee.hostOhneSchluessel')),
        h('button', { type: 'button', class: 'btn klein', onclick: (e) => this.umschalten(true, e.currentTarget, true) }, t('e2ee.neuerSchluessel'))) : null,
    ].filter(Boolean));
  }

  /** an/aus (oder neuer Schlüssel) – nur solange niemand im Meeting ist (sonst 409 vom Server). */
  async umschalten(an, el, neu = false) {
    el.disabled = true;
    try {
      let body = { e2ee: false }, s = '';
      if (an) { s = schluesselNeu(); body = { e2ee: true, e2ee_pruef: await pruefwert(s) }; }
      const { raum } = await api(`/api/raeume/${this.code}`, { methode: 'PATCH', json: body });
      this.raum = raum;
      this.aendern(raum);
      this.schluessel = s;
      schluesselMerken(this.code, s);
      history.replaceState(null, '', s ? linkMitSchluessel(location.href, s) : location.href.split('#')[0]);
      this.statusEl.hidden = true;
      if (an) status(this.statusEl, neu ? t('e2ee.neuerSchluesselOk') : t('e2ee.angeschaltet'), 'ok');
    } catch (e) {
      status(this.statusEl, e.message, 'err');
    }
    this.anzeigen();
  }

  /** Vor dem Beitritt: gültiger Schlüssel (oder '' ohne E2EE). Wirft mit verständlicher Meldung. */
  async pruefen() {
    if (!this.raum.e2ee) return '';
    if (!moeglich()) throw new Error(t('e2ee.nichtUnterstuetzt'));
    const eingabe = $('#f-beitritt').schluessel;
    const s = this.schluessel || schluesselAusText(eingabe.value);
    const falsch = (text) => {
      this.feld.hidden = false; eingabe.required = true;
      eingabe.setAttribute('aria-invalid', 'true'); eingabe.focus();
      throw new Error(text);
    };
    if (!s) falsch(eingabe.value.trim() ? t('e2ee.schluesselFormat') : t('e2ee.schluesselFehlt'));
    if (await pruefwert(s) !== this.raum.e2ee_pruef) {
      this.schluessel = '';
      falsch(t('e2ee.falscherSchluessel'));
    }
    eingabe.removeAttribute('aria-invalid');
    this.schluessel = s;
    if (this.raum.ist_host) schluesselMerken(this.code, s);
    if (schluesselAusFragment(location.hash) !== s) history.replaceState(null, '', linkMitSchluessel(location.href, s));
    return s;
  }
}
