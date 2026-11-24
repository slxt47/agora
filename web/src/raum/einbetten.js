// Meeting-Seite im iframe einer fremden Seite (/e/<code>, siehe docs/EINBETTEN.md): schlanke Ansicht und
// postMessage-Ereignisse an die einbettende Seite – nur an Herkünfte, die der Besitzer erlaubt hat.
import { api } from '../common.js';
import './einbetten.css';
import { ziele } from './einbetten-ziele.js';

export const EINGEBETTET = location.pathname.startsWith('/e/');

if (EINGEBETTET && window.parent !== window) {
  document.documentElement.classList.add('eingebettet');
  const code = location.pathname.split('/').filter(Boolean)[1] || '';
  const wunsch = new URLSearchParams(location.search).get('herkunft');
  let warteschlange = [], an = null;
  const senden = (typ, daten = {}) => {
    const nachricht = { quelle: 'agora', typ, code, ...daten };
    if (!an) { warteschlange.push(nachricht); return; }
    for (const z of an) { try { window.parent.postMessage(nachricht, z); } catch { /* falsche Herkunft → nichts */ } }
  };
  api(`/api/einbetten/${encodeURIComponent(code)}`).then((r) => {
    an = ziele(r.herkuenfte, location.ancestorOrigins, wunsch);
    const alt = warteschlange; warteschlange = [];
    for (const n of alt) senden(n.typ, n);
  }).catch(() => { an = []; });
  senden('bereit');
  document.addEventListener('agora:beigetreten', (e) => senden('beigetreten', { identity: e.detail?.identity, rolle: e.detail?.rolle }));
  document.addEventListener('agora:verlassen', (e) => senden('verlassen', { grund: e.detail?.grund }));
  // Links auf andere Agora-Seiten (Start, Konto) dürfen nicht im iframe laden (dort frame-ancestors 'none') → neuer Tab
  document.addEventListener('click', (e) => {
    const a = e.target.closest?.('a[href]');
    if (!a || a.target) return;
    const u = new URL(a.href, location.href);
    if (u.origin === location.origin && u.pathname.startsWith('/e/')) return;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
  }, true);
}
