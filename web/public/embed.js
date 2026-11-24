/*! Agora – Meeting einbetten. Selbst gehostet, ohne Abhängigkeiten. Doku: docs/EINBETTEN.md
 *
 *   <div id="meeting"></div>
 *   <script src="https://agora.slxt.dev/embed.js"></script>
 *   <script>
 *     const m = Agora.einbetten('#meeting', { code: 'abc-defg-hij', name: 'Gast', hoehe: '640px' });
 *     m.on('beigetreten', (e) => console.log('drin', e.identity));
 *     m.on('verlassen', (e) => console.log('raus', e.grund));
 *   </script>
 *
 * Oder ohne eigenes Skript: <div data-agora-meeting="abc-defg-hij" data-agora-name="Gast" data-agora-hoehe="640px"></div>
 * Ereignisse (vom iframe per postMessage, nur von der Agora-Herkunft und nur aus diesem iframe angenommen):
 *   bereit · beigetreten {identity, rolle} · verlassen {grund}
 */
(function () {
  'use strict';
  var skript = document.currentScript;
  var HERKUNFT = new URL(skript && skript.src ? skript.src : '/embed.js', location.href).origin;
  var CODE = /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/;

  function einbetten(ziel, optionen) {
    optionen = optionen || {};
    var el = typeof ziel === 'string' ? document.querySelector(ziel) : ziel;
    if (!el) throw new Error('Agora: Ziel-Element nicht gefunden');
    if (!CODE.test(optionen.code || '')) throw new Error('Agora: ungültiger Meeting-Code (abc-defg-hij)');
    var url = new URL('/e/' + optionen.code, HERKUNFT);
    url.searchParams.set('herkunft', location.origin);
    if (optionen.name) url.searchParams.set('name', String(optionen.name).slice(0, 60));
    if (optionen.passcode) url.searchParams.set('p', String(optionen.passcode).slice(0, 64));

    var f = document.createElement('iframe');
    f.src = url.href;
    // Kamera, Mikrofon und Bildschirmfreigabe müssen ausdrücklich erlaubt werden (Permissions Policy)
    f.allow = 'camera; microphone; display-capture; fullscreen; autoplay; picture-in-picture; screen-wake-lock';
    f.setAttribute('allowfullscreen', '');
    f.title = optionen.titel || 'Agora-Meeting';
    f.style.width = optionen.breite || '100%';
    f.style.height = optionen.hoehe || '640px';
    f.style.border = '0';
    f.style.borderRadius = optionen.rundung || '16px';
    f.style.display = 'block';

    var hoerer = {};
    function melden(typ, daten) {
      var liste = (hoerer[typ] || []).concat(hoerer['*'] || []);
      for (var i = 0; i < liste.length; i++) {
        try { liste[i](daten); } catch (x) { setTimeout(function () { throw x; }); }
      }
    }
    function empfangen(e) {
      if (e.origin !== HERKUNFT || e.source !== f.contentWindow) return;      // nur Agora und nur dieses iframe
      var d = e.data;
      if (!d || typeof d !== 'object' || d.quelle !== 'agora' || typeof d.typ !== 'string') return;
      melden(d.typ, d);
    }
    window.addEventListener('message', empfangen);
    el.appendChild(f);

    return {
      iframe: f,
      on: function (typ, fn) { (hoerer[typ] = hoerer[typ] || []).push(fn); return this; },
      off: function (typ, fn) { hoerer[typ] = (hoerer[typ] || []).filter(function (x) { return x !== fn; }); return this; },
      entfernen: function () { window.removeEventListener('message', empfangen); if (f.parentNode) f.parentNode.removeChild(f); },
    };
  }

  function automatisch() {
    var els = document.querySelectorAll('[data-agora-meeting]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.getAttribute('data-agora-fertig')) continue;
      el.setAttribute('data-agora-fertig', '1');
      el.agora = einbetten(el, { code: el.getAttribute('data-agora-meeting'), name: el.getAttribute('data-agora-name'),
        hoehe: el.getAttribute('data-agora-hoehe') || undefined });
    }
  }

  window.Agora = window.Agora || {};
  window.Agora.einbetten = einbetten;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', automatisch);
  else automatisch();
})();
