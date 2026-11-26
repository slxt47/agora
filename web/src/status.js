// Öffentliche Status-Seite (/status, ohne Anmeldung): Gesamtzustand, Meldungen (Störung/Wartung/Hinweis), je Dienst
// Zustand, Verfügbarkeit 24 h / 7 Tage / 90 Tage, Balken und Antwortzeit. Daten aus /api/status, jede Minute neu.
import './status.css';
import { $, $$, h, api } from './common.js';
import { t, gebiet } from './i18n.js';
import { prozentText, balkenStufe, msText } from './status-format.js';

const ZONE = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } })();
let daten = null;
let zeitraum = (() => { try { return localStorage.getItem('status-zeitraum') || '90d'; } catch { return '90d'; } })();

const zeit = (iso, mitTag = true) => (iso ? new Intl.DateTimeFormat(gebiet(), {
  ...(mitTag ? { dateStyle: 'medium' } : {}), timeStyle: 'short', timeZone: ZONE }).format(new Date(iso)) : '–');
const tag = (iso) => new Intl.DateTimeFormat(gebiet(), { dateStyle: 'medium', timeZone: ZONE }).format(new Date(iso));

function balkenTitel(b) {
  const wann = zeitraum === '90d' ? tag(b.start) : zeit(b.start);
  if (b.uptime === null) return `${wann}: ${t('status.keineDaten')}`;
  const teile = [`${wann}: ${prozentText(b.uptime, gebiet())}`];
  if (b.ms !== null) teile.push(msText(b.ms, gebiet()));
  if (b.ausfaelle) teile.push(t('status.fehlschlaege', { n: b.ausfaelle }));
  return teile.join(' · ');
}

function dienstKarte(d) {
  const liste = d.balken[zeitraum] || [];
  const quote = d.uptime[zeitraum];
  return h('article', { class: 'status-dienst', 'data-dienst': d.id, 'data-zustand': d.zustand },
    h('div', { class: 'zeile zwischen' },
      h('h3', {}, h('span', { class: 'status-punkt', 'aria-hidden': 'true' }), t(`status.dienst.${d.id}`)),
      h('span', { class: 'status-zustand' }, t(`status.zustand.${d.zustand}`))),
    h('div', { class: 'status-balken', role: 'img', 'aria-label': t('status.balkenAria', { dienst: t(`status.dienst.${d.id}`), quote: prozentText(quote, gebiet()) }) },
      liste.map((b) => h('i', { 'data-stufe': balkenStufe(b.uptime), title: balkenTitel(b) }))),
    h('div', { class: 'zeile zwischen sec klein status-achse' },
      h('span', {}, t(`status.achse.${zeitraum}`)), h('span', {}, t('status.jetzt'))),
    h('dl', { class: 'status-werte' },
      h('div', {}, h('dt', {}, t('status.z24h')), h('dd', {}, prozentText(d.uptime['24h'], gebiet()))),
      h('div', {}, h('dt', {}, t('status.z7d')), h('dd', {}, prozentText(d.uptime['7d'], gebiet()))),
      h('div', {}, h('dt', {}, t('status.z90d')), h('dd', {}, prozentText(d.uptime['90d'], gebiet()))),
      h('div', {}, h('dt', {}, t('status.antwortzeit')), h('dd', {}, d.ms === null ? '–' : msText(d.ms, gebiet())))));
}

function meldungEintrag(m) {
  const zeilen = [t('status.seit', { zeit: zeit(m.beginn) })];
  if (m.ende) zeilen[0] = t('status.vonBis', { von: zeit(m.beginn), bis: zeit(m.ende) });
  if (m.dienste.length) zeilen.push(m.dienste.map((x) => t(`status.dienst.${x}`)).join(', '));
  return h('li', { class: 'status-meldung', 'data-art': m.art, 'data-meldung': m.id },
    h('div', { class: 'zeile' }, h('span', { class: `marker ${m.art === 'stoerung' && !['behoben'].includes(m.zustand) ? 'warn' : ''}` }, t(`status.art.${m.art}`)),
      h('span', { class: 'marker' }, t(`status.mz.${m.zustand}`))),
    h('b', { class: 'status-meldung-titel' }, m.titel),
    m.text ? h('p', { class: 'sec status-meldung-text' }, m.text) : null,
    h('span', { class: 'sec klein' }, `${zeilen.join(' · ')} · ${t('status.aktualisiert', { zeit: zeit(m.aktualisiert) })}`));
}

function zeigen() {
  if (!daten) return;
  const g = $('#gesamt');
  g.dataset.zustand = daten.gesamt;
  $('#gesamt-text').textContent = t(`status.gesamt.${daten.gesamt}`);
  $('#stand').textContent = t('status.stand', { zeit: zeit(daten.stand) });
  $('#dienste').replaceChildren(...daten.dienste.map(dienstKarte));
  $('#meldungen-box').hidden = !daten.meldungen.length;
  $('#meldungen').replaceChildren(...daten.meldungen.map(meldungEintrag));
  $('#vergangen-box').hidden = !daten.vergangen.length;
  $('#vergangen').replaceChildren(...daten.vergangen.map(meldungEintrag));
}

async function laden() {
  try {
    daten = await api('/api/status');
    zeigen();
  } catch (e) {
    $('#gesamt').dataset.zustand = 'unbekannt';
    $('#gesamt-text').textContent = t('status.nichtErreichbar');
    $('#stand').textContent = e.message;
  }
}

for (const r of $$('input[name="zeitraum"]')) {
  r.checked = r.value === zeitraum;
  r.addEventListener('change', () => {
    zeitraum = r.value;
    try { localStorage.setItem('status-zeitraum', zeitraum); } catch { /* egal */ }
    zeigen();
  });
}
laden();
setInterval(() => { if (!document.hidden) laden(); }, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) laden(); });
