// Öffentliche Ansicht einer freigegebenen Aufzeichnung: /a/<token> (optional mit Passwort).
import { $, api, ApiFehler, status, beschaeftigt } from './common.js';
import { t } from './i18n.js';
import { groesseText, laufzeitText } from './helfer.js';

const token = decodeURIComponent(location.pathname.split('/').filter(Boolean)[1] || new URLSearchParams(location.search).get('t') || '');
const quelle = (passwort) => `/api/freigaben/${encodeURIComponent(token)}/datei${passwort ? `?p=${encodeURIComponent(passwort)}` : ''}`;

function abspielen(passwort) {
  const v = $('#video');
  v.src = quelle(passwort);
  $('#herunterladen').href = quelle(passwort);
  $('#player').hidden = false;
  $('#f-passwort').hidden = true;
  v.addEventListener('error', () => status($('#status'), passwort ? t('freigabe.passwortFalsch') : t('freigabe.ladefehler'), 'err'), { once: true });
}

(async () => {
  if (!token) { $('#titel').textContent = t('freigabe.nichtGefunden'); return; }
  try {
    const info = await api(`/api/freigaben/${encodeURIComponent(token)}`);
    $('#titel').textContent = info.titel || t('freigabe.ohneTitel');
    document.title = `${info.titel || t('freigabe.ohneTitel')} – Agora`;
    const teile = [];
    if (info.dauer) teile.push(laufzeitText(info.dauer));
    if (info.groesse) teile.push(groesseText(info.groesse));
    $('#info').textContent = teile.join(' · ');
    if (info.passwort_noetig) {
      $('#f-passwort').hidden = false;
      $('#f-passwort').passwort.focus();
    } else abspielen('');
  } catch (e) {
    $('#titel').textContent = e instanceof ApiFehler && (e.status === 404 || e.status === 410) ? t('freigabe.nichtGefunden') : t('freigabe.ladefehler');
    if (e instanceof ApiFehler && e.status !== 404 && e.status !== 410) status($('#status'), e.message, 'err');
    else $('#info').textContent = t('freigabe.abgelaufenHinweis');
  }
})();

$('#f-passwort').addEventListener('submit', (e) => {
  e.preventDefault();
  const f = e.currentTarget;
  if (!f.reportValidity()) return;
  beschaeftigt(f.querySelector('button[type="submit"]'), async () => {
    // Passwort vorab prüfen (Range 0-0 → kleine Antwort), damit eine verständliche Meldung kommt statt eines leeren Players
    const r = await fetch(quelle(f.passwort.value), { headers: { Range: 'bytes=0-0' }, credentials: 'same-origin' }).catch(() => null);
    if (!r) throw new ApiFehler(t('fehler.verbindung'), 0);
    if (r.status === 403 || r.status === 401) { status($('#status'), t('freigabe.passwortFalsch'), 'err'); return; }
    if (!r.ok) { status($('#status'), t('freigabe.ladefehler'), 'err'); return; }
    $('#status').hidden = true;
    abspielen(f.passwort.value);
  }, $('#status'));
});
