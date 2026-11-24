// Konto: „Einbetten“ je Raum – erlauben, Herkünfte (Origins) festlegen, Code-Schnipsel kopieren. Der Server setzt
// für /e/<code> frame-ancestors genau auf diese Herkünfte (app/einbetten.py, docs/EINBETTEN.md).
import { h, api, blatt, kopieren, beschaeftigt, toast, knopf, status } from './common.js';
import { t } from './i18n.js';
import './entwickler.css';

export function einbettenMarke(raum) {
  return raum.einbetten?.erlaubt ? h('span', { class: 'marker' }, t('emb.marke')) : null;
}

export function einbettenKnopf(raum, klasse = 'btn klein', nachher = () => {}) {
  if (!raum.einbetten) return null;
  return knopf(t('emb.knopf'), { klasse, symbol: 'code', 'data-einbetten': raum.code, onclick: () => einbettenDialog(raum, nachher) });
}

export function schnipsel(origin, code) {
  return `<div id="agora-meeting"></div>\n<script src="${origin}/embed.js"></script>\n<script>\n`
    + `  Agora.einbetten('#agora-meeting', { code: '${code}', hoehe: '640px' })\n`
    + `    .on('beigetreten', (e) => console.log('beigetreten', e.identity))\n`
    + `    .on('verlassen', (e) => console.log('verlassen', e.grund));\n</script>`;
}

function einbettenDialog(raum, nachher) {
  const e = raum.einbetten;
  const { inhalt, schliessen } = blatt(t('emb.titel'));
  const st = h('div', { class: 'status', role: 'status', hidden: true });
  const code = schnipsel(location.origin, raum.code);
  const f = h('form', { class: 'formular', novalidate: true },
    h('p', { class: 'sec klein' }, t('emb.text')),
    h('label', { class: 'schalter' }, h('span', { class: 'txt' }, h('span', {}, t('emb.erlauben')), h('small', {}, t('emb.erlaubenHinweis'))),
      h('input', { type: 'checkbox', role: 'switch', name: 'erlaubt', checked: !!e.erlaubt })),
    h('label', { class: 'feld' }, h('span', {}, t('emb.herkuenfte')),
      h('textarea', { name: 'herkuenfte', rows: '3', spellcheck: 'false', autocapitalize: 'none', placeholder: 'https://beispiel.de' }, (e.herkuenfte || []).join('\n')),
      h('small', {}, t('emb.herkuenfteHinweis'))),
    h('details', { class: 'dev-beispiel' }, h('summary', {}, t('emb.schnipsel')),
      h('pre', { class: 'mono' }, code),
      h('p', { class: 'sec klein' }, t('emb.allowHinweis')),
      knopf(t('dev.kopieren'), { klasse: 'btn klein', symbol: 'kopie', onclick: () => kopieren(code, t('allg.kopiert')) })),
    st,
    h('div', { class: 'knoepfe' },
      h('button', { type: 'button', class: 'btn', onclick: schliessen }, t('allg.abbrechen')),
      h('button', { type: 'submit', class: 'btn primary' }, t('allg.speichern'))));
  inhalt.append(f);
  f.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const herkuenfte = f.herkuenfte.value.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
    if (f.erlaubt.checked && !herkuenfte.length) { status(st, t('emb.herkuenfteFehlen'), 'err'); f.herkuenfte.focus(); return; }
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      const r = await api(`/api/raeume/${raum.code}/einbetten`, { methode: 'PUT', json: { erlaubt: f.erlaubt.checked, herkuenfte } });
      raum.einbetten = r.einbetten;
      schliessen();
      toast(t('konto.gespeichert'), 'ok');
      nachher();
    }, st);
  });
}
