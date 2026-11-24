// Konto: Ende-zu-Ende-Verschlüsselung je Raum ein- und ausschalten. Das Geheimnis entsteht hier im Browser und bleibt
// im Gerätespeicher (localStorage) des Hosts; der Server bekommt nur den Prüfwert. „Link kopieren“ hängt #k=… an.
import { h, api } from './common.js';
import { t } from './i18n.js';
import { linkMitSchluessel, pruefwert, schluesselGemerkt, schluesselMerken, schluesselNeu } from './e2ee-schluessel.js';

/** Link zum Weitergeben: bei E2EE mit dem Schlüssel dieses Geräts (sonst ohne – die Lobby fragt dann danach). */
export const e2eeLink = (raum, link) => (raum?.e2ee ? linkMitSchluessel(link, schluesselGemerkt(raum.code)) : link);

export const e2eeMarker = (raum) => (raum?.e2ee ? h('span', { class: 'marker ok' }, t('e2ee.kurz')) : null);

export function e2eeSchalter(raum) {
  return h('label', { class: 'schalter' },
    h('span', { class: 'txt' }, h('span', {}, t('e2ee.schalter')), h('small', {}, t('e2ee.schalterHinweis'))),
    h('input', { type: 'checkbox', role: 'switch', name: 'e2ee', checked: !!raum?.e2ee }));
}

/** Nach dem Speichern: gewünschten E2EE-Zustand setzen (eigener PATCH, damit der Server nie das Geheimnis sieht). */
export async function e2eeNachSpeichern(raum, an) {
  if (!!raum.e2ee === an) return raum;
  const s = an ? schluesselNeu() : '';
  const body = an ? { e2ee: true, e2ee_pruef: await pruefwert(s) } : { e2ee: false };
  const r = await api(`/api/raeume/${raum.code}`, { methode: 'PATCH', json: body });
  schluesselMerken(raum.code, s);
  return r.raum;
}

/** Hinweis im Einladen-Dialog: Mails enthalten den Schlüssel nicht. */
export const e2eeEinladenHinweis = (raum) => (raum?.e2ee
  ? h('p', { class: 'status warn' }, schluesselGemerkt(raum.code) ? t('e2ee.einladenHinweis') : t('e2ee.einladenOhneSchluessel')) : null);
