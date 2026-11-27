// Konto → Teams → Organisation → „Firmen-Login (OIDC)“: nur für Owner und nur, wenn der Server es kann
// (AGORA_FIRMEN_LOGIN=1 – sonst 404 und die Karte bleibt weg). Geheimnis wird nie angezeigt, nur „gesetzt“.
import { h, api, beschaeftigt, toast, knopf, kopieren, bestaetigen } from './common.js';
import { t } from './i18n.js';

const statusFeld = () => h('div', { class: 'status', role: 'status', hidden: true });

function schalter(name, text, hinweis, an) {
  return h('label', { class: 'schalter' },
    h('span', { class: 'txt' }, h('span', {}, text), hinweis ? h('small', {}, hinweis) : null),
    h('input', { type: 'checkbox', role: 'switch', name, checked: an }));
}

/** Karte für die Organisation (Element) oder null. `neu` lädt die Organisation neu. */
export async function ssoKarte(o, neu) {
  if (o.rolle !== 'owner') return null;
  let d;
  try {
    if (!(await api('/api/einstellungen')).firmen_login_an) return null;      // aus → keine Karte (Routen wären 404)
    d = await api(`/api/orgs/${o.slug}/sso`);
  } catch { return null; }
  const s = d.sso || {};
  const st = statusFeld();
  const f = h('form', { class: 'formular', id: 'f-org-sso', novalidate: true },
    h('label', { class: 'feld' }, h('span', {}, t('sso.issuer')),
      h('input', { name: 'issuer', type: 'url', maxlength: '300', value: s.issuer || '', placeholder: 'https://login.firma.de/realms/firma', autocapitalize: 'none', spellcheck: 'false', required: true }),
      h('small', {}, t('sso.issuerHinweis'))),
    h('div', { class: 'zwei' },
      h('label', { class: 'feld' }, h('span', {}, t('sso.clientId')),
        h('input', { name: 'client_id', maxlength: '200', value: s.client_id || '', autocapitalize: 'none', spellcheck: 'false', required: true })),
      h('label', { class: 'feld' }, h('span', {}, t('sso.clientSecret')),
        h('input', { name: 'client_secret', type: 'password', maxlength: '500', autocomplete: 'new-password', placeholder: s.geheimnis_gesetzt ? t('sso.geheimnisGesetzt') : '' }))),
    h('label', { class: 'feld' }, h('span', {}, t('sso.domain')),
      h('input', { name: 'domain', maxlength: '190', value: s.domain || d.domain_vorschlag || '', autocapitalize: 'none', spellcheck: 'false', required: true }),
      h('small', {}, t('sso.domainHinweis'))),
    schalter('auto_beitritt', t('sso.autoBeitritt'), t('sso.autoBeitrittHinweis'), s.auto_beitritt ?? true),
    schalter('erzwingen', t('sso.erzwingen'), t('sso.erzwingenHinweis'), !!s.erzwingen),
    schalter('aktiv', t('sso.aktiv'), '', s.aktiv ?? true),
    st,
    h('div', { class: 'knoepfe' },
      h('button', { type: 'submit', class: 'btn primary' }, t('allg.speichern')),
      d.sso ? knopf(t('sso.entfernen'), { klasse: 'btn gefahr', symbol: 'muell', onclick: () => entfernen(o, neu) }) : null));
  f.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const json = { issuer: f.issuer.value.trim(), client_id: f.client_id.value.trim(), domain: f.domain.value.trim(),
      auto_beitritt: f.auto_beitritt.checked, erzwingen: f.erzwingen.checked, aktiv: f.aktiv.checked };
    if (f.client_secret.value) json.client_secret = f.client_secret.value;
    beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
      await api(`/api/orgs/${o.slug}/sso`, { methode: 'PUT', json });
      toast(t('sso.gespeichert'), 'ok');
      neu();
    }, st);
  });
  return h('section', { class: 'card', 'aria-labelledby': 'org-h-sso', id: 'org-sso' },
    h('h2', { id: 'org-h-sso' }, t('sso.titel')),
    h('p', { class: 'sec klein' }, t('sso.text')),
    h('div', { class: 'zeile' }, h('span', { class: 'sec klein' }, t('sso.redirect')), h('code', { class: 'mono', id: 'sso-redirect' }, d.redirect_uri),
      knopf(t('konto.linkKopieren'), { klasse: 'btn klein', symbol: 'kopie', onclick: () => kopieren(d.redirect_uri) })),
    d.sso ? h('p', { class: 'sec klein' }, t('sso.stand', { n: d.sso.verknuepft })) : null,
    f);
}

async function entfernen(o, neu) {
  if (!await bestaetigen(t('sso.entfernenTitel'), t('sso.entfernenText'), { ja: t('sso.entfernen'), gefahr: true })) return;
  await beschaeftigt(null, async () => {
    await api(`/api/orgs/${o.slug}/sso`, { methode: 'DELETE' });
    toast(t('sso.entfernt'), 'ok');
    neu();
  });
}
