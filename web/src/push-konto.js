// Push-Benachrichtigungen im Konto (Profil): je Gerät ausdrücklich einschalten (Browser fragt nach Erlaubnis), Ereignisse
// wählen, Test senden, Geräte entfernen. Der Browser abonniert beim Push-Dienst seines Herstellers; Agora speichert nur
// dessen Adresse und die Schlüssel, der Inhalt ist für das Gerät verschlüsselt. iPhone/iPad: nur in der installierten App
// (Zum Home-Bildschirm, ab iOS 16.4) – im Safari-Tab gibt es kein Push.
import { $, h, api, beschaeftigt, toast, knopf, bestaetigen } from './common.js';
import { t } from './i18n.js';
import { zeitText } from './helfer.js';
import { pushMoeglich, schluesselBytes, geraetName } from './push-hilfe.js';

const EREIGNIS_TEXT = { erinnerung: 'push.erinnerung', warteraum: 'push.warteraum', einladung: 'push.einladung', fertig: 'push.fertig' };

async function kennungVon(endpunkt) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(endpunkt));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 16);
}

export async function pushEinrichten(box = $('#push')) {
  if (!box) return;
  const moeglich = pushMoeglich();
  let zustand;
  try { zustand = await api('/api/push'); } catch (e) { box.replaceChildren(h('p', { class: 'leer' }, e.message)); return; }
  // Mit Frist: Ohne Push-Dienst (z. B. headless) können diese Aufrufe hängen – die Einstellungen sollen trotzdem erscheinen.
  const frist = (p) => Promise.race([p.catch(() => null), new Promise((r) => { setTimeout(() => r(null), 3000); })]);
  const reg = moeglich.ok ? await frist(navigator.serviceWorker.getRegistration('/')) : null;
  const abo = reg ? await frist(reg.pushManager.getSubscription()) : null;
  const eigene = abo ? await kennungVon(abo.endpoint) : null;
  const hier = zustand.geraete.find((g) => g.kennung === eigene);
  const neu = () => pushEinrichten(box);

  const teile = [h('p', { class: 'sec klein' }, t('push.text'))];
  if (!moeglich.ok) {
    teile.push(h('div', { class: 'status warn', role: 'status' }, t(moeglich.grund === 'ios' ? 'push.iosHinweis' : 'push.nichtMoeglich')));
  } else if (Notification.permission === 'denied') {
    teile.push(h('div', { class: 'status warn', role: 'status' }, t('push.blockiert')));
  } else {
    teile.push(h('div', { class: 'knoepfe oben' }, hier
      ? knopf(t('push.hierAus'), { klasse: 'btn', symbol: 'glocke', id: 'push-hier', onclick: (e) => beschaeftigt(e.currentTarget, async () => {
        await api(`/api/push/abo/${hier.id}`, { methode: 'DELETE' });
        await abo?.unsubscribe().catch(() => {});
        toast(t('push.ausOk'), 'ok');
        neu();
      }) })
      : knopf(t('push.hierAn'), { klasse: 'btn primary', symbol: 'glocke', id: 'push-hier', onclick: (e) => beschaeftigt(e.currentTarget, async () => {
        if (await Notification.requestPermission() !== 'granted') { toast(t('push.blockiert'), 'warn', { dauer: 7000 }); return; }
        const r = reg || await navigator.serviceWorker.ready;
        let sub = await r.pushManager.getSubscription();
        if (sub && sub.options?.applicationServerKey) {       // anderer Server-Schlüssel (neu erzeugt) → neu abonnieren
          const alt = new Uint8Array(sub.options.applicationServerKey);
          const soll = schluesselBytes(zustand.schluessel);
          if (alt.length !== soll.length || alt.some((b, i) => b !== soll[i])) { await sub.unsubscribe(); sub = null; }
        }
        sub = sub || await r.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: schluesselBytes(zustand.schluessel) });
        await api('/api/push/abo', { methode: 'POST', json: { ...sub.toJSON(), geraet: geraetName() } });
        toast(t('push.anOk'), 'ok');
        neu();
      }) })));
  }
  if (zustand.geraete.length) {
    const haken = zustand.alle_ereignisse.map((e) => {
      const i = h('input', { type: 'checkbox', role: 'switch', name: `push-${e}`, checked: zustand.ereignisse.includes(e) });
      i.addEventListener('change', () => beschaeftigt(null, async () => {
        const wahl = haken.filter((x) => x.checked).map((x) => x.name.slice(5));
        try { await api('/api/push/ereignisse', { methode: 'PUT', json: { ereignisse: wahl } }); } catch (err) { i.checked = !i.checked; throw err; }
      }));
      return i;
    });
    teile.push(
      h('h3', {}, t('push.ereignisse')),
      ...zustand.alle_ereignisse.map((e, n) => h('label', { class: 'schalter' },
        h('span', { class: 'txt' }, h('span', {}, t(EREIGNIS_TEXT[e])), h('small', {}, t(`${EREIGNIS_TEXT[e]}Hinweis`))), haken[n])),
      h('h3', {}, t('push.geraete')),
      h('ul', { class: 'liste', id: 'push-geraete' }, zustand.geraete.map((g) => h('li', { dataset: { geraet: g.id } },
        h('div', { class: 'text' }, h('b', {}, g.geraet || t('push.unbenannt'), g.kennung === eigene ? ` (${t('push.diesesGeraet')})` : ''),
          h('div', { class: 'sec klein' }, g.benutzt ? t('push.zuletzt', { zeit: zeitText(g.benutzt) }) : t('push.seit', { zeit: zeitText(g.erstellt) }))),
        h('div', { class: 'aktionen' }, knopf(t('konto.loeschen'), { klasse: 'icon-btn', symbol: 'muell', 'aria-label': t('push.entfernen'), onclick: async () => {
          if (!await bestaetigen(t('push.entfernen'), null, { ja: t('konto.loeschen'), gefahr: true })) return;
          beschaeftigt(null, async () => { await api(`/api/push/abo/${g.id}`, { methode: 'DELETE' }); neu(); });
        } }))))),
      h('div', { class: 'knoepfe oben' }, knopf(t('push.test'), { symbol: 'glocke', id: 'push-test', onclick: (e) => beschaeftigt(e.currentTarget, async () => {
        const r = await api('/api/push/test', { methode: 'POST' });
        toast(t('push.testOk', { n: r.geraete }), 'ok');
      }) })));
  }
  teile.push(h('p', { class: 'sec klein' }, t('push.datenschutz')));
  box.replaceChildren(...teile);
}
