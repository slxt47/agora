// Gemeinsam für alle Seiten:
//  • Hell/Dunkel kommt aus /modus.js (slxt-Standard: dunkel, Wahl in localStorage „modus“). Wir ersetzen nur die
//    Animation des Schalters durch die Kreisblende vom Schalter aus (View Transitions, 0,6 s); ohne Unterstützung oder
//    bei „Bewegung reduzieren“ wechselt modus.js sofort.
//  • Himmel wie auf slxt.org: Sterne (dunkel) + seltene Sternschnuppen. Im Meeting ruht der Himmel (ruhe(true)).
//  • API-Helfer (X-Agora gegen CSRF, deutsche Fehlermeldungen), Upload mit Fortschritt, Toasts, Dialoge, DOM-Helfer.
import './app.css';
import { t, seiteUebersetzen } from './i18n.js';
import { icon } from './icons.js';

const d = document.documentElement;
const ruhig = matchMedia('(prefers-reduced-motion: reduce)');

// ---------------------------------------------------------------- Hell/Dunkel mit Kreisblende
// Im Capture-Schritt am window abfangen, damit der Klick-Handler von modus.js (Überblendung) nicht zusätzlich läuft.
window.addEventListener('click', (e) => {
  const knopf = e.target.closest?.('[data-modus-schalter]');
  const M = window.slxtModus;
  if (!knopf || !M || !document.startViewTransition || ruhig.matches) return;
  e.preventDefault();
  e.stopPropagation();
  const r = knopf.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  d.classList.add('kreis-wechsel', 'modus-wechsel');
  const vt = document.startViewTransition(() => M.wechseln());
  vt.ready.then(() => {
    d.animate({ clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
      { duration: 600, easing: 'cubic-bezier(.2, .8, .2, 1)', pseudoElement: '::view-transition-new(root)' });
  }).catch(() => {});
  vt.finished.catch(() => {}).finally(() => d.classList.remove('kreis-wechsel', 'modus-wechsel'));
}, true);

// ---------------------------------------------------------------- Himmel
const himmel = document.querySelector('.sky');
const sternCanvas = himmel?.querySelector('canvas');
const meteorBox = himmel?.querySelector('.meteors');
let stille = false;

function sterne() {
  if (!sternCanvas) return;
  const dpr = Math.min(2, devicePixelRatio || 1), w = innerWidth, h = innerHeight;
  sternCanvas.width = w * dpr; sternCanvas.height = h * dpr;
  const c = sternCanvas.getContext('2d');
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, w, h);
  const n = Math.round((w * h) / 5200);
  for (let i = 0; i < n; i++) {
    const rad = Math.random() < 0.08 ? 1.3 : Math.random() * 0.8 + 0.3;
    c.globalAlpha = 0.25 + Math.random() * 0.65;
    c.fillStyle = Math.random() < 0.15 ? '#cdd8ff' : '#ffffff';
    c.beginPath(); c.arc(Math.random() * w, Math.random() * h * 0.92, rad, 0, Math.PI * 2); c.fill();
  }
  sternCanvas.dataset.gemalt = '1';
}
const istDunkel = () => (window.slxtModus?.get?.() ?? 'dunkel') === 'dunkel';
if (istDunkel()) sterne();
addEventListener('modus', () => { if (istDunkel() && !sternCanvas?.dataset.gemalt) sterne(); });
let rt = 0;
addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { if (sternCanvas?.dataset.gemalt) sterne(); }, 250); });

/** Eine Sternschnuppe (wie slxt.org): nur transform/opacity, läuft auf der GPU. */
function sternschnuppe(x0, y0) {
  if (!meteorBox || ruhig.matches || stille || document.hidden) return;
  const W = innerWidth, H = innerHeight;
  const len = 80 + Math.random() * 150;
  const dir = x0 === undefined ? (Math.random() < 0.5 ? 1 : -1) : (x0 < W / 2 ? 1 : -1);
  const ang = ((14 + Math.random() * 26) * Math.PI) / 180;
  const dist = Math.max(W, H) * (0.22 + Math.random() * 0.25);
  if (x0 === undefined) { x0 = dir > 0 ? Math.random() * W * 0.65 : W * 0.35 + Math.random() * W * 0.65; y0 = Math.random() * H * 0.42; }
  const x1 = x0 + dir * Math.cos(ang) * dist, y1 = y0 + Math.sin(ang) * dist;
  const deg = (Math.atan2(y1 - y0, x1 - x0) * 180) / Math.PI;
  const el = document.createElement('i');
  el.className = 'meteor';
  el.style.width = len + 'px';
  meteorBox.appendChild(el);
  const at = (x, y, s) => `translate(${x - len}px, ${y - 1}px) rotate(${deg}deg) scaleX(${s})`;
  const a = el.animate([
    { transform: at(x0, y0, 0.3), opacity: 0 }, { opacity: 1, offset: 0.12 }, { opacity: 0.9, offset: 0.65 },
    { transform: at(x1, y1, 1), opacity: 0 },
  ], { duration: 650 + Math.random() * 700, easing: 'cubic-bezier(.3, .55, .55, 1)' });
  a.onfinish = a.oncancel = () => el.remove();
}
if (meteorBox) {
  (function planen(erst) {
    setTimeout(() => {
      sternschnuppe();
      if (Math.random() < 0.18) setTimeout(sternschnuppe, 200 + Math.random() * 700);
      planen(false);
    }, erst ? 1500 + Math.random() * 2500 : 3500 + Math.random() * 9000);
  })(true);
  // In den freien Himmel tippen = eigene Sternschnuppe (wie slxt.org)
  addEventListener('click', (e) => {
    if (stille || e.target.closest('.card, a, button, input, label, select, textarea, footer, .bar, dialog, .meeting, .lobby')) return;
    sternschnuppe(e.clientX, e.clientY);
  });
}

/** Ruhe (im Meeting): keine Schnuppen, keine Unschärfe – spart Akku/CPU neben dem Video. */
export function ruhe(an) {
  stille = an;
  d.classList.toggle('ruhe', an);
  if (an) meteorBox?.replaceChildren();
}

// ---------------------------------------------------------------- API

export class ApiFehler extends Error {
  constructor(text, status, daten = {}) { super(text); this.status = status; this.daten = daten; }
}

/** fetch auf die eigene Herkunft: X-Agora gegen CSRF, JSON rein/raus, Fehler als ApiFehler mit deutschem Text. */
export async function api(pfad, { methode = 'GET', json, body, header = {}, signal } = {}) {
  let r;
  try {
    r = await fetch(pfad, {
      method: methode, credentials: 'same-origin', signal, cache: 'no-store',
      headers: { ...(methode !== 'GET' ? { 'X-Agora': '1' } : {}), ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}), ...header },
      body: json !== undefined ? JSON.stringify(json) : body,
    });
  } catch (e) {
    if (e?.name === 'AbortError') throw e;
    throw new ApiFehler(t('fehler.verbindung'), 0);
  }
  const daten = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiFehler(daten.fehler || (r.status === 429 ? t('fehler.zuViele') : t('fehler.status', { status: r.status })), r.status, daten);
  return daten;
}

/** Hochladen mit Fortschritt (XMLHttpRequest – fetch kennt keinen Upload-Fortschritt). Gibt {promise, abbrechen} zurück. */
export function hochladen(pfad, daten, header = {}, fortschritt) {
  const x = new XMLHttpRequest();
  const promise = new Promise((ok, nein) => {
    x.open('POST', pfad);
    x.withCredentials = true;
    x.setRequestHeader('X-Agora', '1');
    for (const [k, v] of Object.entries(header)) x.setRequestHeader(k, v);
    x.upload.onprogress = (e) => e.lengthComputable && fortschritt?.(e.loaded / e.total, e.loaded, e.total);
    x.onload = () => {
      let d = {};
      try { d = JSON.parse(x.responseText); } catch { /* leer */ }
      if (x.status >= 200 && x.status < 300) ok(d); else nein(new ApiFehler(d.fehler || t('fehler.status', { status: x.status }), x.status, d));
    };
    x.onerror = () => nein(new ApiFehler(t('fehler.verbindung'), 0));
    x.onabort = () => nein(new ApiFehler(t('fehler.abgebrochen'), 0));
    x.send(daten);
  });
  return { promise, abbrechen: () => x.abort() };
}

/** Angemeldet? → ich oder null (Fehler beim Server zählen als „nicht angemeldet“). */
export async function ichLaden() {
  try { const r = await api('/api/konto/ich'); return r.angemeldet ? r.ich : null; } catch { return null; }
}

// ---------------------------------------------------------------- DOM

export const $ = (sel, wurzel = document) => wurzel.querySelector(sel);
export const $$ = (sel, wurzel = document) => [...wurzel.querySelectorAll(sel)];

/**
 * Element bauen: h('button', {class: 'btn', onclick, 'aria-label': '…'}, 'Text', kind, …).
 * Texte werden als Textknoten eingefügt (nie als HTML). Nur `html:` setzt innerHTML – ausschließlich für Symbole.
 */
export function h(tag, attrs = {}, ...kinder) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'html') el.innerHTML = v;
    else if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const k of kinder.flat()) if (k !== null && k !== undefined && k !== false) el.append(k instanceof Node ? k : String(k));
  return el;
}

/** Knopf mit Symbol + Text (oder nur Symbol mit aria-label). */
export function knopf(text, { symbol, klasse = 'btn', titel, ...rest } = {}) {
  const b = h('button', { type: 'button', class: klasse, ...rest });
  if (symbol) b.insertAdjacentHTML('beforeend', icon(symbol));
  if (text && !rest['aria-label']) b.append(h('span', {}, text));
  else if (text) b.append(h('span', { class: 'nur-sr' }, text));
  if (titel) b.title = titel;
  return b;
}

export function status(el, text, art = '') {
  el.hidden = false;
  el.className = 'status' + (art ? ' ' + art : '');
  el.textContent = text;
}

// ---------------------------------------------------------------- Toasts

let toastBox = null;
/** Kurze Mitteilung oben (aria-live). aktion = {text, fn} für einen Knopf. dauer 0 = bleibt bis zum Klick. */
export function toast(text, art = '', { dauer = 4500, aktion } = {}) {
  if (!toastBox) {
    toastBox = h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' });
    document.body.append(toastBox);
  }
  const el = h('div', { class: 'toast' + (art ? ' ' + art : '') }, h('span', {}, text));
  const weg = () => { el.classList.add('weg'); setTimeout(() => el.remove(), 260); };
  if (aktion) el.append(h('button', { type: 'button', class: 'btn', onclick: () => { weg(); aktion.fn(); } }, aktion.text));
  el.append(h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('allg.schliessen'), html: icon('schliessen'), onclick: weg }));
  toastBox.append(el);
  while (toastBox.children.length > 4) toastBox.firstElementChild.remove();
  if (dauer) setTimeout(weg, dauer);
  return weg;
}

// ---------------------------------------------------------------- Dialoge

/** Leeren Blatt-Dialog bauen: {dialog, inhalt, schliessen}. Wird beim Schließen entfernt. */
export function blatt(titel, { breit = false, onclose } = {}) {
  const inhalt = h('div', { class: 'formular' });
  const titelId = 'dlg-' + Math.random().toString(36).slice(2, 8);
  const dialog = h('dialog', { class: 'blatt' + (breit ? ' breit' : ''), 'aria-labelledby': titelId },
    h('div', { class: 'blatt-kopf' }, h('h3', { id: titelId }, titel),
      h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('allg.schliessen'), html: icon('schliessen'), onclick: () => dialog.close() })),
    inhalt);
  dialog.addEventListener('close', () => { onclose?.(); dialog.remove(); });
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });   // Klick auf den Hintergrund
  document.body.append(dialog);
  dialog.showModal();
  return { dialog, inhalt, schliessen: () => dialog.close() };
}

/** Bestätigen-Dialog → Promise<boolean>. */
export function bestaetigen(titel, text, { ja = t('allg.ok'), nein = t('allg.abbrechen'), gefahr = false } = {}) {
  return new Promise((fertig) => {
    let ergebnis = false;
    const { inhalt, schliessen } = blatt(titel, { onclose: () => fertig(ergebnis) });
    if (text) inhalt.append(h('p', { class: 'sec' }, text));
    const jaKnopf = h('button', { type: 'button', class: 'btn ' + (gefahr ? 'gefahr voll' : 'primary'), onclick: () => { ergebnis = true; schliessen(); } }, ja);
    inhalt.append(h('div', { class: 'knoepfe' }, h('button', { type: 'button', class: 'btn', onclick: schliessen }, nein), jaKnopf));
    jaKnopf.focus();
  });
}

/** In die Zwischenablage (mit Rückmeldung). */
export async function kopieren(text, meldung = t('allg.kopiert')) {
  try { await navigator.clipboard.writeText(text); toast(meldung, 'ok', { dauer: 2500 }); return true; } catch {
    const feld = h('textarea', { style: { position: 'fixed', opacity: '0' } }, text);
    document.body.append(feld); feld.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { /* nichts */ }
    feld.remove();
    toast(ok ? meldung : t('allg.kopierenFehler'), ok ? 'ok' : 'err', { dauer: 3000 });
    return ok;
  }
}

/** Während einer Aktion Knopf sperren und Text ändern; Fehler landen als Toast/Status. */
export async function beschaeftigt(knopfEl, fn, statusEl) {
  const alt = knopfEl?.innerHTML;
  if (knopfEl) { knopfEl.disabled = true; knopfEl.setAttribute('aria-busy', 'true'); }
  try { return await fn(); } catch (e) {
    const text = e instanceof ApiFehler ? e.message : t('fehler.allgemein', { text: e?.message || '' });
    if (statusEl) status(statusEl, text, 'err'); else toast(text, 'err');
    return undefined;
  } finally {
    if (knopfEl) { knopfEl.disabled = false; knopfEl.removeAttribute('aria-busy'); if (alt !== undefined) knopfEl.innerHTML = alt; }
  }
}

/** Bildschirm wach halten (Screen Wake Lock); gibt das Lock oder null zurück. */
export async function wachBleiben() {
  try { return (await navigator.wakeLock?.request('screen')) || null; } catch { return null; }
}

seiteUebersetzen();
