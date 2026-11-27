// Push: reine Hilfen ohne DOM (auch in den Node-Tests): Kann der Browser Push? Schlüssel umwandeln, Gerätename.
/** Kann dieser Browser Push? ({ok} | {grund: 'ios' | 'nein'}) – reine Prüfung, auch für Tests. */
export function pushMoeglich(g = globalThis) {
  const nav = g.navigator || {};
  const ios = /iphone|ipad|ipod/i.test(nav.userAgent || '') || (nav.platform === 'MacIntel' && nav.maxTouchPoints > 1);
  const installiert = !!(g.matchMedia?.('(display-mode: standalone)').matches || nav.standalone === true);
  const api_ = 'serviceWorker' in nav && 'PushManager' in g && 'Notification' in g && g.isSecureContext !== false;
  if (ios && !installiert) return { grund: 'ios' };
  return api_ ? { ok: true } : { grund: 'nein' };
}

/** Base64url → Uint8Array (applicationServerKey). */
export function schluesselBytes(b64) {
  const s = atob(b64.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (b64.length % 4)) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

/** Kurze Bezeichnung des Geräts aus dem User-Agent (nur für die eigene Liste). */
export function geraetName(ua = navigator.userAgent || '') {
  const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
  const system = /Android/.test(ua) ? 'Android' : /iPhone|iPad|iPod/.test(ua) ? 'iOS' : /Mac OS X/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : '';
  return system ? `${browser} · ${system}` : browser;
}
