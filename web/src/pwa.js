// PWA: Service Worker anmelden (/sw.js, Bereich „/“), „Neue Version – neu laden“ (der neue Service Worker übernimmt erst
// nach dem Klick, nie mitten im Meeting) und Installieren auf der Startseite (#app-installieren): Chrome/Edge/Android
// über beforeinstallprompt, iOS/iPadOS-Safari mit Anleitung „Zum Home-Bildschirm“.
import { h, toast, blatt } from './common.js';
import { t } from './i18n.js';

const IOS_GEMERKT = 'agora-ios-hinweis';
let neuLaden = false;

export function pwaStarten() {
  if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
  if (document.documentElement.dataset.seite === 'offline') return;     // ohne Netz nichts anmelden
  const anmelden = async () => {
    let reg;
    try { reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' }); } catch { return; }
    if (reg.waiting && navigator.serviceWorker.controller) neueVersion(reg.waiting);
    reg.addEventListener('updatefound', () => {
      const w = reg.installing;
      w?.addEventListener('statechange', () => { if (w.state === 'installed' && navigator.serviceWorker.controller) neueVersion(w); });
    });
    // Lange offene Tabs (Meeting, Konto): beim Zurückkommen nach einer neuen Version fragen
    document.addEventListener('visibilitychange', () => { if (!document.hidden && navigator.onLine) reg.update().catch(() => {}); });
  };
  if (document.readyState === 'complete') anmelden(); else addEventListener('load', anmelden, { once: true });
  navigator.serviceWorker.addEventListener('controllerchange', () => { if (neuLaden) location.reload(); });
  installierenAnbieten();
}

function neueVersion(w) {
  const zeigen = () => toast(t('pwa.neueVersion'), '', { dauer: 0, aktion: { text: t('pwa.neuLaden'), fn: () => { neuLaden = true; w.postMessage({ typ: 'aktivieren' }); } } });
  if (!document.body.classList.contains('im-meeting')) { zeigen(); return; }
  // Im Meeting nicht stören – erst danach anbieten
  const mo = new MutationObserver(() => { if (!document.body.classList.contains('im-meeting')) { mo.disconnect(); zeigen(); } });
  mo.observe(document.body, { attributes: true, attributeFilter: ['class'] });
}

function installierenAnbieten() {
  const box = document.getElementById('app-installieren');
  if (!box) return;
  const text = box.querySelector('.app-text'), knopf = box.querySelector('button');
  const eigenesFenster = matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  if (eigenesFenster) return;
  let aufschub = null;
  addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    aufschub = e;
    text.textContent = t('pwa.installierenText');
    knopf.textContent = t('pwa.installieren');
    box.hidden = false;
  });
  addEventListener('appinstalled', () => { box.hidden = true; aufschub = null; });
  knopf.addEventListener('click', async () => {
    if (!aufschub) { iosAnleitung(box); return; }
    aufschub.prompt();
    const { outcome } = await aufschub.userChoice.catch(() => ({}));
    aufschub = null;
    if (outcome === 'accepted') box.hidden = true;
  });
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  let gemerkt = false;
  try { gemerkt = localStorage.getItem(IOS_GEMERKT) === '1'; } catch { /* privat */ }
  if (ios && !gemerkt) {
    text.textContent = t('pwa.iosText');
    knopf.textContent = t('pwa.iosKnopf');
    box.hidden = false;
  }
}

function iosAnleitung(box) {
  const { inhalt, schliessen } = blatt(t('pwa.iosTitel'));
  inhalt.append(
    h('ol', { class: 'anleitung' }, [t('pwa.iosSchritt1'), t('pwa.iosSchritt2'), t('pwa.iosSchritt3')].map((s) => h('li', {}, s))),
    h('div', { class: 'knoepfe' },
      h('button', { type: 'button', class: 'btn leise', onclick: () => { try { localStorage.setItem(IOS_GEMERKT, '1'); } catch { /* egal */ } box.hidden = true; schliessen(); } }, t('pwa.nichtMehr')),
      h('button', { type: 'button', class: 'btn primary', onclick: schliessen }, t('allg.fertig'))));
}
