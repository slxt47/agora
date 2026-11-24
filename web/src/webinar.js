// Öffentliche Anmeldeseite eines Webinars: /w/<code> (Name, E-Mail, Zusatzfragen). Abmelden: /w/<code>?abmelden=<token>.
import { $, h, api, ApiFehler, status, beschaeftigt } from './common.js';
import './webinar-konto.css';
import { t } from './i18n.js';
import { CODE_MUSTER, zeitText, minutenText } from './helfer.js';

const code = decodeURIComponent(location.pathname.split('/').filter(Boolean)[1] || '');
const params = new URLSearchParams(location.search);
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Berlin';

async function abmelden(token) {
  $('#f-anmelden').hidden = true;
  $('#abmelden-box').hidden = false;
  $('#abmelden').addEventListener('click', () => beschaeftigt($('#abmelden'), async () => {
    try {
      await api(`/api/webinare/${code}/abmelden`, { methode: 'POST', json: { token } });
      $('#abmelden-box').hidden = true;
      status($('#status'), t('webinar.abgemeldet'), 'ok');
    } catch (e) { status($('#status'), e.message, 'err'); }
  }));
}

(async () => {
  if (!CODE_MUSTER.test(code)) { $('#titel').textContent = t('webinar.nichtGefunden'); return; }
  let w;
  try { w = await api(`/api/webinare/${code}`); } catch (e) {
    $('#titel').textContent = e instanceof ApiFehler && e.status === 404 ? t('webinar.nichtGefunden') : t('webinar.ladefehler');
    return;
  }
  $('#titel').textContent = w.titel;
  document.title = `${w.titel} – Agora`;
  const info = [];
  if (w.besitzer_name) info.push(t('meeting.gastgeber', { name: w.besitzer_name }));
  if (w.naechster_termin) info.push(zeitText(w.naechster_termin, zone));
  if (w.geplant?.dauer_min) info.push(minutenText(w.geplant.dauer_min));
  $('#info').textContent = info.join(' · ');
  $('#beschreibung').textContent = w.geplant?.beschreibung || '';
  const tok = params.get('abmelden');
  if (tok) { history.replaceState(null, '', location.pathname); abmelden(tok); return; }
  if (!w.offen) { status($('#status'), t('webinar.ausgebucht'), 'warn'); return; }
  if (w.frei != null) $('#info').textContent += ` · ${t('webinar.freiePlaetze', { n: w.frei })}`;
  const f = $('#f-anmelden');
  $('#fragen').replaceChildren(...(w.fragen || []).map((q, i) => h('label', { class: 'feld' }, h('span', {}, q),
    h('input', { name: `frage${i}`, maxlength: '1000' }), h('small', {}, t('webinar.optional')))));
  f.hidden = false;
  f.name.focus();
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!f.name.value.trim()) { f.name.setAttribute('aria-invalid', 'true'); status($('#status'), t('raum.nameFehlt'), 'err'); f.name.focus(); return; }
    if (!f.email.checkValidity() || !f.email.value.trim()) { f.email.setAttribute('aria-invalid', 'true'); status($('#status'), t('webinar.emailFehlt'), 'err'); f.email.focus(); return; }
    f.name.removeAttribute('aria-invalid'); f.email.removeAttribute('aria-invalid');
    beschaeftigt(f.querySelector('button[type=submit]'), async () => {
      try {
        await api(`/api/webinare/${code}/anmelden`, { methode: 'POST', json: {
          name: f.name.value.trim(), email: f.email.value.trim(), antworten: (w.fragen || []).map((_q, i) => f[`frage${i}`].value.trim()),
        } });
        f.hidden = true;
        status($('#status'), t('webinar.angemeldet', { email: f.email.value.trim() }), 'ok');
        $('#status').setAttribute('tabindex', '-1'); $('#status').focus();
      } catch (err) { status($('#status'), err.message, 'err'); }
    });
  });
})();
