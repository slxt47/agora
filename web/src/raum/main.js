// /r/{code}: Pre-Join-Lobby → (Passcode) → Warteraum (alle 2 s nachfragen) → Meeting → Ende-Bildschirm.
import { $, api, ApiFehler, ichLaden, status, h } from '../common.js';
import './raum.css';
import { t } from '../i18n.js';
import { CODE_MUSTER, codeAusText, laufzeitText } from '../helfer.js';
import { Lobby } from './lobby.js';
import { feedbackZeigen } from './feedback.js';

const ANSICHTEN = ['laden', 'fehler', 'lobby', 'warten', 'ende'];
function zeigen(name) { for (const a of ANSICHTEN) $(`#v-${a}`).hidden = a !== name; }
function fehler(titel, text) { $('#fehler-titel').textContent = titel; $('#fehler-text').textContent = text || ''; zeigen('fehler'); }

const roh = decodeURIComponent(location.pathname.split('/').filter(Boolean)[1] || new URLSearchParams(location.search).get('code') || '');
const code = CODE_MUSTER.test(roh) ? roh : codeAusText(roh);
const params = new URLSearchParams(location.search);
let raum = null, ich = null, lobby = null, warten = null;

async function start() {
  if (!code) { fehler(t('raum.ungueltigTitel'), t('raum.ungueltigText')); return; }
  if (code !== roh) history.replaceState(null, '', `/r/${code}${location.search}`);
  try {
    [{ raum }, ich] = await Promise.all([api(`/api/raeume/${code}`), ichLaden()]);
  } catch (e) {
    if (e instanceof ApiFehler && e.status === 404) fehler(t('raum.nichtGefunden'), t('raum.nichtGefundenText'));
    else fehler(t('raum.ladefehler'), e.message);
    return;
  }
  document.title = `${raum.titel || code} – Agora`;
  $('#raum-titel').textContent = raum.titel || t('meeting.ohneTitel');
  $('#raum-info').textContent = [raum.besitzer_name ? t('meeting.gastgeber', { name: raum.besitzer_name }) : '', code].filter(Boolean).join(' · ');
  const merkmale = [];
  if (raum.aktiv) merkmale.push(['ok', t('raum.laeuft')]);
  if (raum.passcode_noetig) merkmale.push(['', t('konto.passcode')]);
  if (raum.warteraum && !raum.ist_host) merkmale.push(['', t('konto.warteraum')]);
  if (raum.gesperrt) merkmale.push(['warn', t('meeting.gesperrtKurz')]);
  if (raum.webinar) merkmale.push(['', t('webinar.marke')]);
  $('#raum-merkmale').replaceChildren(...merkmale.map(([k, text]) => h('span', { class: `marker ${k}` }, text)));
  if (raum.beendet && !raum.ist_host) status($('#beitritt-status'), t('raum.beendetHinweis'), 'warn');
  else if (raum.gesperrt && !raum.ist_host) status($('#beitritt-status'), t('raum.gesperrtHinweis'), 'warn');

  const f = $('#f-beitritt');
  let gespeichert = '';
  try { gespeichert = localStorage.getItem('agora-name') || ''; } catch { /* privat */ }
  f.name.value = ich?.name || gespeichert;
  if (ich) $('#name-hinweis').textContent = t('raum.angemeldetAls', { email: ich.email });
  const pc = params.get('p') || params.get('pwd');
  if (raum.passcode_noetig && !raum.ist_host) {
    $('#feld-passcode').hidden = false;
    f.passcode.required = true;
    if (pc) f.passcode.value = pc;
  }
  if (pc) { params.delete('p'); params.delete('pwd'); history.replaceState(null, '', `/r/${code}${params.toString() ? `?${params}` : ''}`); }
  if (raum.ist_host) $('#beitreten-knopf').textContent = t('raum.starten');
  // Webinar: Panel-Link (?panel=) oder persönlicher Anmeldelink (?anmeldung=) – Zuschauer senden nichts
  if (raum.webinar && !raum.ist_host && !params.get('panel')) {
    status($('#beitritt-status'), t('webinar.lobbyZuschauer'), '');
    if (raum.webinar.registrierung && !params.get('anmeldung')) $('#beitritt-status').append(' ', h('a', { href: raum.webinar.anmelde_link || `/w/${code}` }, t('webinar.zurAnmeldung')));
  }

  zeigen('lobby');
  lobby = new Lobby();
  f.name.addEventListener('input', () => lobby.avatar(f.name.value));
  f.addEventListener('submit', (e) => { e.preventDefault(); beitreten(); });
  await lobby.start(f.name.value);
  (f.name.value ? (f.passcode.required ? f.passcode : $('#beitreten-knopf')) : f.name).focus({ preventScroll: true });
}

async function beitreten() {
  const f = $('#f-beitritt');
  const name = f.name.value.trim();
  if (!name) { f.name.setAttribute('aria-invalid', 'true'); status($('#beitritt-status'), t('raum.nameFehlt'), 'err'); f.name.focus(); return; }
  if (f.passcode.required && !f.passcode.value.trim()) { f.passcode.setAttribute('aria-invalid', 'true'); status($('#beitritt-status'), t('raum.passcodeFehlt'), 'err'); f.passcode.focus(); return; }
  f.name.removeAttribute('aria-invalid'); f.passcode.removeAttribute('aria-invalid');
  try { if (!ich) localStorage.setItem('agora-name', name); } catch { /* privat */ }
  const knopf = $('#beitreten-knopf');
  const text = knopf.textContent;
  knopf.disabled = true; knopf.textContent = t('raum.verbinde');
  try {
    const body = { name };
    if (f.passcode.value.trim()) body.passcode = f.passcode.value.trim();
    for (const k of ['panel', 'anmeldung']) if (params.get(k)) body[k] = params.get(k);
    const r = await api(`/api/raeume/${code}/beitreten`, { methode: 'POST', json: body });
    $('#beitritt-status').hidden = true;
    if (r.status === 'wartet') wartenStarten(r.anfrage, r.anfrage_geheim);
    else await meetingStarten(r.zugang);
  } catch (e) {
    const msg = e instanceof ApiFehler ? e.message : t('fehler.allgemein', { text: e.message });
    if (e instanceof ApiFehler && e.status === 403 && /passcode/i.test(msg)) {
      $('#feld-passcode').hidden = false; f.passcode.required = true; f.passcode.setAttribute('aria-invalid', 'true'); f.passcode.select();
    }
    status($('#beitritt-status'), msg, 'err');
    zeigen('lobby');
  } finally { knopf.disabled = false; knopf.textContent = text; }
}

function wartenStarten(anfrage, geheim) {
  zeigen('warten');
  const seit = Date.now();
  let aktiv = true, fehlerZahl = 0;
  const uhr = setInterval(() => { $('#warte-zeit').textContent = t('raum.wartetSeit', { zeit: laufzeitText((Date.now() - seit) / 1000) }); }, 1000);
  const stopp = () => { aktiv = false; clearInterval(uhr); clearTimeout(warten?.timer); warten = null; };
  warten = { stopp };
  $('#warten-abbrechen').onclick = () => { stopp(); zeigen('lobby'); $('#beitreten-knopf').focus(); };
  const fragen = async () => {
    if (!aktiv) return;
    try {
      const r = await api(`/api/raeume/${code}/warten/${encodeURIComponent(anfrage)}?g=${encodeURIComponent(geheim)}`);
      fehlerZahl = 0;
      if (!aktiv) return;
      if (r.status === 'zugelassen' && r.zugang) { stopp(); await meetingStarten(r.zugang); return; }
      if (r.status === 'abgelehnt') { stopp(); ende(r.grund === 'beendet' ? 'beendet' : 'abgelehnt'); return; }
    } catch (e) {
      if (e instanceof ApiFehler && (e.status === 404 || e.status === 403 || e.status === 410)) { stopp(); ende('abgelehnt'); return; }
      fehlerZahl++;
      $('#warte-text').textContent = fehlerZahl > 2 ? t('raum.warteVerbindung') : t('raum.warteText');
    }
    if (aktiv) warten.timer = setTimeout(fragen, 2000);
  };
  warten.timer = setTimeout(fragen, 2000);
}

async function meetingStarten(zugang) {
  zeigen('laden');
  const medien = lobby.beenden(false);
  try {
    const { Meeting } = await import('./meeting.js');
    const m = new Meeting({ code, raum, ich, zugang, medien, onEnde: ende });
    await m.start();
    $('#v-laden').hidden = true;
  } catch (e) {
    medien.videoTrack?.stop(); medien.audioTrack?.stop();
    document.body.classList.remove('im-meeting');
    $('#v-meeting').hidden = true;
    fehler(t('raum.verbindenFehler'), t('raum.verbindenFehlerText', { text: e?.message || '' }));
    $('#fehler-text').after(h('div', { class: 'knoepfe oben' }, h('a', { class: 'btn', href: location.pathname }, t('raum.nochmal'))));
  }
}

/** Ende-Bildschirm. art: verlassen | beendet | beendetSelbst | entfernt | abgelehnt | doppelt | verbindung */
function ende(art, aufnahmeSichern, laufenderUpload, extra = {}) {
  lobby?.beenden(true);
  const texte = {
    verlassen: ['ende.verlassenTitel', 'ende.verlassenText'],
    beendet: ['ende.beendetTitel', 'ende.beendetText'],
    beendetSelbst: ['ende.beendetSelbstTitel', 'ende.beendetSelbstText'],
    entfernt: ['ende.entferntTitel', 'ende.entferntText'],
    abgelehnt: ['ende.abgelehntTitel', 'ende.abgelehntText'],
    doppelt: ['ende.doppeltTitel', 'ende.doppeltText'],
    verbindung: ['ende.verbindungTitel', 'ende.verbindungText'],
  }[art] || ['ende.verlassenTitel', ''];
  $('#ende-titel').textContent = t(texte[0]);
  $('#ende-text').textContent = texte[1] ? t(texte[1]) : '';
  const erneut = $('#ende-erneut');
  erneut.href = `/r/${code}`;
  erneut.hidden = art === 'entfernt' || (art === 'beendet' && !raum?.ist_host) || art === 'beendetSelbst';
  erneut.textContent = art === 'verbindung' ? t('ende.neuVerbinden') : t('ende.erneut');
  if (ich) $('#ende-start').replaceWith(h('a', { class: 'btn', href: '/konto', id: 'ende-start' }, t('ende.zumKonto')));
  const upload = $('#ende-upload');
  if (laufenderUpload) { upload.hidden = false; upload.append(laufenderUpload); }
  if (aufnahmeSichern) { upload.hidden = false; aufnahmeSichern(upload); }
  if (art !== 'abgelehnt') feedbackZeigen(code, extra.feedback, upload);     // „Wie war die Qualität?“
  if (raum?.ist_host && ich) transkriptAmEnde(upload);   // Untertitel-Transkript für den Besitzer
  zeigen('ende');
  $('#ende-titel').setAttribute('tabindex', '-1');
  $('#ende-titel').focus();
  document.title = `${t(texte[0])} – Agora`;
}

/** Ende-Bildschirm (Besitzer): Transkript dieses Meetings als .txt/.vtt, falls etwas gespeichert wurde. */
async function transkriptAmEnde(box) {
  try {
    const r = await api(`/api/raeume/${code}/transkripte`);
    const x = r.transkripte?.[0];
    if (!x) return;
    box.hidden = false;
    box.append(h('div', { class: 'knoepfe oben ende-transkript' }, h('span', { class: 'sec' }, t('tk.amEnde', { n: x.abschnitte })),
      h('a', { class: 'btn klein', href: `/api/raeume/${code}/transkript?sitzung=${x.sitzung}&format=txt`, download: '' }, '.txt'),
      h('a', { class: 'btn klein', href: `/api/raeume/${code}/transkript?sitzung=${x.sitzung}&format=vtt`, download: '' }, '.vtt')));
  } catch { /* kein Transkript */ }
}

start();
