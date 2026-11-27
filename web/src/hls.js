// Zuschauerseite der öffentlichen Übertragung (HLS) eines Webinars: /h/<code>, ohne WebRTC und ohne Konto.
// Safari spielt HLS selbst; sonst lädt die Seite hls.js (light, selbst gehostet, Version fest in package.json) nach.
// Nur Angemeldete: Token aus ?anmeldung= oder dem eingefügten Link aus der Bestätigungsmail → Quelle vom Server.
// Alle 30 s meldet sich die Seite (zufällige Kennung) – daraus zählt der Server ungefähr die Zuschauer.
import { $, api, ApiFehler, status } from './common.js';
import './hls.css';
import { t } from './i18n.js';
import { CODE_MUSTER } from './helfer.js';
import { abspielweg, quelleGueltig, tokenAusLink, zuschauerId } from './hls-logik.js';

const code = decodeURIComponent(location.pathname.split('/').filter(Boolean)[1] || '');
const params = new URLSearchParams(location.search);
const video = $('#video');
let spieler = null, quelle = '', meldeTimer = 0, pruefTimer = 0, token = '';
const speicher = (() => { try { return sessionStorage; } catch { return null; } })();

token = tokenAusLink(params.get('anmeldung') || speicher?.getItem(`agora-hls-${code}`) || '');
if (params.has('anmeldung')) history.replaceState(null, '', location.pathname);       // Token nicht in der Adresszeile lassen

function anzeigen(text, art = '') { status($('#status'), text, art); }

async function abspielen(neu) {
  if (!quelleGueltig(neu, code) || neu === quelle) return;
  quelle = neu;
  $('#f-zugang').hidden = true;
  $('#buehne').hidden = false;
  $('#verzoegerung').hidden = false;
  $('#status').hidden = true;
  const weg = abspielweg(video, typeof window.MediaSource !== 'undefined' || typeof window.ManagedMediaSource !== 'undefined');
  document.body.dataset.abspielweg = weg;
  if (weg === 'nativ') {
    video.src = quelle;
  } else if (weg === 'hlsjs') {
    const { default: Hls } = await import('hls.js/light');
    if (!Hls.isSupported()) { anzeigen(t('hls.nichtUnterstuetzt'), 'err'); return; }
    spieler?.destroy();
    spieler = new Hls({ liveSyncDurationCount: 3, liveMaxLatencyDurationCount: 6, enableWorker: true, backBufferLength: 30 });
    spieler.on(Hls.Events.ERROR, (_e, d) => {
      if (!d.fatal) return;
      if (d.type === Hls.ErrorTypes.MEDIA_ERROR) { spieler.recoverMediaError(); return; }
      pruefen();                       // Netzwerkfehler: läuft die Übertragung noch?
    });
    spieler.loadSource(quelle);
    spieler.attachMedia(video);
  } else {
    anzeigen(t('hls.nichtUnterstuetzt'), 'err');
    return;
  }
  video.play().catch(() => {});
  $('#ton-box').hidden = !video.muted;
  clearInterval(meldeTimer);
  const melden = () => { if (!document.hidden) api(`/api/hls/${code}/da`, { methode: 'POST', json: { id: zuschauerId(speicher) } }).catch(() => {}); };
  melden();
  meldeTimer = setInterval(melden, 30000);
}

function anhalten() {
  clearInterval(meldeTimer);
  spieler?.destroy(); spieler = null;
  if (quelle) { video.removeAttribute('src'); video.load(); }
  quelle = '';
  $('#buehne').hidden = true;
  $('#ton-box').hidden = true;
  $('#verzoegerung').hidden = true;
}

async function zugangHolen() {
  try {
    const z = await api(`/api/hls/${code}/zugang`, { methode: 'POST', json: { anmeldung: token } });
    try { speicher?.setItem(`agora-hls-${code}`, token); } catch { /* voll */ }
    await abspielen(z.quelle);
  } catch (e) {
    token = '';
    try { speicher?.removeItem(`agora-hls-${code}`); } catch { /* privat */ }
    anzeigen(e.message, 'err');
    $('#f-zugang').hidden = false;
  }
}

async function pruefen() {
  clearTimeout(pruefTimer);
  let i;
  try { i = await api(`/api/hls/${code}`); } catch (e) {
    if (e instanceof ApiFehler && e.status === 404) { $('#titel').textContent = t('hls.nichtGefunden'); anhalten(); return; }
    pruefTimer = setTimeout(pruefen, 15000);
    return;
  }
  $('#titel').textContent = i.titel || t('meeting.ohneTitel');
  document.title = `${i.titel || code} – ${t('hls.titel')}`;
  if (!i.laeuft) {
    anhalten();
    $('#info').textContent = '';
    anzeigen(i.beendet ? t('hls.beendet') : t('hls.nochNicht'), i.beendet ? '' : 'warn');
    $('#f-zugang').hidden = true;
    pruefTimer = setTimeout(pruefen, i.beendet ? 60000 : 15000);
    return;
  }
  $('#info').textContent = i.zugang === 'anmeldung' ? t('hls.nurAngemeldete') : t('hls.oeffentlich');
  if (i.quelle) await abspielen(i.quelle);
  else if (!quelle && token) await zugangHolen();
  else if (!quelle) {
    $('#f-zugang').hidden = false;
    anzeigen(t('hls.anmeldungNoetig'), 'warn');
    if (i.anmelde_link) $('#status').append(' ', Object.assign(document.createElement('a'), { href: i.anmelde_link, textContent: t('webinar.zurAnmeldung') }));
  }
  pruefTimer = setTimeout(pruefen, 30000);
}

$('#ton').addEventListener('click', () => { video.muted = false; video.play().catch(() => {}); $('#ton-box').hidden = true; });
video.addEventListener('volumechange', () => { if (!video.muted) $('#ton-box').hidden = true; });
$('#f-zugang').addEventListener('submit', (e) => {
  e.preventDefault();
  const f = e.currentTarget;
  token = tokenAusLink(f.link.value);
  if (!token) { f.link.setAttribute('aria-invalid', 'true'); anzeigen(t('hls.linkFalsch'), 'err'); f.link.focus(); return; }
  f.link.removeAttribute('aria-invalid');
  zugangHolen();
});

if (!CODE_MUSTER.test(code)) $('#titel').textContent = t('hls.nichtGefunden');
else pruefen();
