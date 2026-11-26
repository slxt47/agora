// Selbsttest der gebauten App (nur mit AGORA_DESKTOP_TEST=1 und AGORA_DESKTOP_SELBSTTEST=1, z. B. unter xvfb in Docker):
// lädt AGORA_ADRESSE (ein Raum der Testinstanz), tritt als Gast bei und prüft
//   Brücke (window.agoraDesktop klein, kein Node in der Seite), Kürzel-IPC (Mikro, Kamera, Hand → Seite reagiert),
//   Navigations-Sperre, Rechte (Benachrichtigungen verweigert), Bildschirmfreigabe über desktopCapturer.
// Ergebnis als eine Zeile „AGORA_SELBSTTEST {json}“ auf stdout, Exit 0/1.
'use strict';

const warten = (ms) => new Promise((r) => setTimeout(r, ms));

async function bis(wc, ausdruck, sekunden = 20) {
  const ende = Date.now() + sekunden * 1000;
  while (Date.now() < ende) {
    try { if (await wc.executeJavaScript(ausdruck)) return true; } catch { /* Seite lädt */ }
    await warten(250);
  }
  throw new Error(`Zeitüberschreitung: ${ausdruck.slice(0, 100)}`);
}

async function laufen({ fenster, kuerzelSenden, registriert, extern }) {
  const wc = fenster.webContents;
  const r = { schritte: {}, ok: true };
  const schritt = async (name, fn) => {
    try { r.schritte[name] = (await fn()) ?? 'ok'; } catch (e) { r.schritte[name] = `FEHLER: ${e.message}`; r.ok = false; }
  };
  const druck = (id) => wc.executeJavaScript(`document.querySelector('${id}')?.getAttribute('aria-pressed')`);

  await schritt('kuerzel registriert', async () => {
    // Unter xvfb ohne Fenstermanager klappt XGrabKey meist; scheitert es, ist das ein Hinweis, kein Abbruch
    return Object.entries(registriert).map(([a, ok]) => `${a}:${ok ? 'ja' : 'nein'}`).join(' ');
  });
  await schritt('seite geladen', () => bis(wc, "!!document.querySelector('#v-lobby:not([hidden])')", 30));
  await schritt('bruecke', async () => {
    const b = await wc.executeJavaScript(`({ keys: Object.keys(window.agoraDesktop || {}).sort().join(','), node: typeof require + '/' + typeof process + '/' + typeof module })`);
    if (b.keys !== 'beiKuerzel,desktop') throw new Error(`agoraDesktop: ${b.keys}`);
    if (b.node !== 'undefined/undefined/undefined') throw new Error(`Node in der Seite: ${b.node}`);
    return b.keys;
  });
  await schritt('beitritt', async () => {
    await wc.executeJavaScript(`(() => { const f = document.querySelector('#f-beitritt [name=name]'); f.value = 'Desktop Test';
      f.dispatchEvent(new Event('input', { bubbles: true })); document.querySelector('#beitreten-knopf').click(); })()`);
    await bis(wc, "!!document.querySelector('#v-meeting:not([hidden])')", 30);
    await bis(wc, "document.querySelector('#s-mic')?.getAttribute('aria-pressed') !== null", 10);
  });
  for (const [aktion, knopf] of [['mikro', '#s-mic'], ['kamera', '#s-cam'], ['hand', '#s-hand']]) {
    await schritt(`kuerzel ${aktion}`, async () => {
      const vorher = await druck(knopf);
      if (!kuerzelSenden(aktion)) throw new Error('nicht gesendet');
      await bis(wc, `document.querySelector('${knopf}').getAttribute('aria-pressed') !== '${vorher}'`, 10);
      kuerzelSenden(aktion);
      await bis(wc, `document.querySelector('${knopf}').getAttribute('aria-pressed') === '${vorher}'`, 10);
      return `${vorher} → ${vorher === 'true' ? 'false' : 'true'} → ${vorher}`;
    });
  }
  await schritt('fremde aktion verworfen', async () => {
    if (kuerzelSenden('verlassen')) throw new Error('unbekannte Aktion wurde gesendet');
    wc.send('agora:kuerzel', 'verlassen');            // direkt am Hauptprozess vorbei: die Brücke filtert
    await warten(500);
    if (!(await wc.executeJavaScript("!!document.querySelector('#v-meeting:not([hidden])')"))) throw new Error('Meeting verlassen');
  });
  await schritt('bildschirmfreigabe', async () => {
    // Fehler von getDisplayMedia mitschreiben (nur zur Diagnose im Ergebnis)
    await wc.executeJavaScript(`(() => { const md = navigator.mediaDevices, g = md.getDisplayMedia.bind(md);
      md.getDisplayMedia = (c) => g(c).catch((e) => { window.__gdm = e.name + ': ' + e.message + ' ' + JSON.stringify(c); throw e; }); window.__toasts = []; new MutationObserver(() => document.querySelectorAll('.toast').forEach((t) => { if (!window.__toasts.includes(t.textContent)) window.__toasts.push(t.textContent); })).observe(document.body, { childList: true, subtree: true }); })()`);
    await wc.executeJavaScript("document.querySelector('#s-teilen').click()", true);
    try {
      await bis(wc, "!!document.querySelector('#buehne .kv.bildschirm.eigene-freigabe')", 20);
    } catch (e) {
      const hinweis = await wc.executeJavaScript(`(async () => { const b = document.querySelector('#s-teilen');
        let gdm = ''; try { const s = await navigator.mediaDevices.getDisplayMedia({ audio: true, video: { width: 1920, height: 1080, frameRate: 15 }, systemAudio: 'include', selfBrowserSurface: 'exclude', surfaceSwitching: 'include' }); await new Promise((r) => setTimeout(r, 1500)); gdm = 'direkt ' + s.getTracks().map((t) => t.kind + ':' + t.readyState + ':' + JSON.stringify(t.getSettings())).join(','); s.getTracks().forEach((t) => t.stop()); }
        catch (x) { gdm = x.name + ': ' + x.message; }
        return (window.__gdm || '-') + ' | toasts=' + window.__toasts.join(' / ') + ' | gedrueckt=' + b.getAttribute('aria-pressed') + ' kacheln=' + [...document.querySelectorAll('#buehne .kv')].map((k) => k.className).join(';') + ' | Knopf hidden=' + b.hidden + ' disabled=' + b.disabled + ' | ' + gdm + ' | ' + [...document.querySelectorAll('.toast')].map((t) => t.textContent).join(' | '); })()`, true);
      throw new Error(`${e.message} ${hinweis}`);
    }
    await wc.executeJavaScript("document.querySelector('#s-teilen').click()", true);
    return 'desktopCapturer-Quelle geteilt';
  });
  // Zuletzt: ein Navigationsversuch löst beforeunload aus, und livekit-client trennt dabei das Meeting (auch wenn die
  // App die Navigation dann verwirft) – deshalb nach den Meeting-Schritten.
  await schritt('navigation gesperrt', async () => {
    const vorher = wc.getURL();
    await wc.executeJavaScript("location.href = 'https://example.org/boese'");
    await warten(1500);
    if (wc.getURL() !== vorher) throw new Error(`navigiert nach ${wc.getURL()}`);
    if (!extern.includes('https://example.org/boese')) throw new Error('nicht an den Standardbrowser übergeben');
    await wc.executeJavaScript("window.open('https://example.org/fenster')");
    await warten(500);
    return `extern: ${extern.join(', ')}`;
  });
  await schritt('rechte', async () => {
    const n = await wc.executeJavaScript('Notification.requestPermission()');
    if (n !== 'denied') throw new Error(`Benachrichtigungen: ${n}`);
    const geo = await wc.executeJavaScript("navigator.permissions.query({ name: 'geolocation' }).then((p) => p.state)");
    if (geo === 'granted') throw new Error('Standort erlaubt');
    return `Benachrichtigungen ${n}, Standort ${geo}`;
  });
  return r;
}

module.exports = { laufen };
