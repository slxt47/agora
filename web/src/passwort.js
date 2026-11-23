// Neues Passwort setzen (Link aus der Mail: /passwort?t=<token>).
import { $, api, status, beschaeftigt } from './common.js';
import { t } from './i18n.js';

const token = new URLSearchParams(location.search).get('t') || '';
const f = $('#f-passwort');
if (!token) {
  f.hidden = true;
  status($('#status'), t('passwort.ohneToken'), 'err');
  $('#weiter').hidden = false;
}
f.addEventListener('submit', (e) => {
  e.preventDefault();
  if (!f.reportValidity()) return;
  if (f.passwort.value !== f.wiederholung.value) {
    f.wiederholung.setAttribute('aria-invalid', 'true');
    status($('#status'), t('passwort.ungleich'), 'err');
    f.wiederholung.focus();
    return;
  }
  f.wiederholung.removeAttribute('aria-invalid');
  beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
    await api('/api/konto/passwort-setzen', { methode: 'POST', json: { token, passwort: f.passwort.value } });
    f.reset();
    f.hidden = true;
    status($('#status'), t('passwort.gesetzt'), 'ok');
    $('#weiter').hidden = false;
    history.replaceState(null, '', '/passwort');   // Token nicht in der Adresse lassen
  }, $('#status'));
});
