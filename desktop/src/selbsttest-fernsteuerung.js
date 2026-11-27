// Selbsttest der Fernsteuerung (nur mit AGORA_DESKTOP_TEST=1, AGORA_DESKTOP_SELBSTTEST=1 und
// AGORA_DESKTOP_SELBSTTEST_ART=fernsteuerung, unter xvfb in Docker – desktop/scripts/docker-smoke-fernsteuerung.sh).
// Geht den echten Weg Seite → Brücke (preload.js) → Hauptprozess (steuerung.js) → Eingabe-Adapter → X-Server und
// beobachtet die Wirkung außerhalb der App: Mausposition (xdotool getmouselocation) und ein fremdes Zielfenster
// (Tk, schreibt Klicks/Tasten/Rad nach AGORA_ZIEL_LOG). Die Zustimmung beantwortet AGORA_DESKTOP_TEST_ZUSTIMMUNG.
// Ergebnis als eine Zeile „AGORA_SELBSTTEST {json}“.
'use strict';
const fsx = require('node:fs');
const { execFile } = require('node:child_process');
const { BrowserWindow, screen } = require('electron');

const warten = (ms) => new Promise((r) => setTimeout(r, ms));
const xdo = (...a) => new Promise((fertig) => execFile('xdotool', a, { timeout: 3000 }, (f, aus) => fertig(f ? `FEHLER ${f.message}` : String(aus).trim())));
const ZIEL = { x: 40, y: 40, b: 520, h: 300 };          // muss zu ziel.py passen

async function laufen({ fenster, fst }) {
  const wc = fenster.webContents;
  const r = { schritte: {}, ok: true };
  const schritt = async (name, fn) => {
    try { r.schritte[name] = (await fn()) ?? 'ok'; } catch (e) { r.schritte[name] = `FEHLER: ${e.message}`; r.ok = false; }
  };
  const js = (code) => wc.executeJavaScript(code, true);
  const log = () => { try { return fsx.readFileSync(process.env.AGORA_ZIEL_LOG || '/tmp/ziel.log', 'utf8'); } catch { return ''; } };
  const anz = screen.getPrimaryDisplay();
  const norm = (px, py) => ({ x: (px - anz.bounds.x) / anz.bounds.width, y: (py - anz.bounds.y) / anz.bounds.height });
  const senden = (absender, ev, verschluesselt = false) => js(`window.agoraDesktop.fernsteuerung.ereignis(${JSON.stringify(absender)}, ${verschluesselt}, new TextEncoder().encode(${JSON.stringify(JSON.stringify(ev))}))`);
  const grund = async (absender) => { await warten(300); return fst.zaehler; };
  const mitte = { x: ZIEL.x + 200, y: ZIEL.y + 150 };
  let kennung = '';

  await schritt('seite geladen', async () => {
    for (let i = 0; i < 60 && !(await js('!!window.agoraDesktop').catch(() => false)); i++) await warten(250);
    if (!(await js('Object.keys(window.agoraDesktop.fernsteuerung).sort().join(",")')).includes('ereignis')) throw new Error('Brücke ohne fernsteuerung');
  });
  await schritt('eingabe-adapter', async () => {
    const a = await fst.bereit;
    if (!a) throw new Error('kein Adapter');
    for (let i = 0; i < 20 && !(await js('window.agoraDesktop.fernsteuerung.verfuegbar()')); i++) await warten(100);
    return a.name;
  });
  // Agora-Fenster aus dem Weg (rechts unten), Zielfenster bekommt den Fokus
  fenster.setBounds({ x: anz.bounds.width - 560, y: anz.bounds.height - 520, width: 540, height: 500 });
  await schritt('ohne freigabe nichts', async () => {
    await senden('steuernde', { t: 'bewegen', ...norm(mitte.x, mitte.y) });
    await warten(300);
    if (fst.zaehler.verworfen['keine-freigabe'] !== 1) throw new Error(JSON.stringify(fst.zaehler));
  });
  await schritt('bildschirm geteilt', async () => {
    const ok = await js(`navigator.mediaDevices.getDisplayMedia({ video: true }).then((s) => { window.__strom = s; return s.getVideoTracks()[0].label; })`);
    await js(`window.agoraDesktop.fernsteuerung.freigabe(true, { ich: 'praesentator', e2ee: false })`);
    await warten(300);
    if (fst.zustand.freigabe?.art !== 'bildschirm') throw new Error(JSON.stringify(fst.zustand.freigabe));
    return `${ok} → ${JSON.stringify(fst.zustand.freigabe.bildschirm.bounds)}`;
  });
  await schritt('zustimmung im dialog', async () => {
    const a = await js(`window.agoraDesktop.fernsteuerung.anfrage('steuernde', 'Stefan \\u202eSteuer')`);
    if (!a.ok || !/^[A-Za-z0-9_-]{22,64}$/.test(a.kennung)) throw new Error(JSON.stringify(a));
    kennung = a.kennung;
    if (fst.aktiv()) throw new Error('aktiv ohne Server-Freigabe');
    const zweite = await js(`window.agoraDesktop.fernsteuerung.anfrage('andere', 'X')`);
    if (zweite.ok) throw new Error('zweite Anfrage sofort erlaubt');
    return `Kennung ${kennung.length} Zeichen; zweite Anfrage: ${zweite.grund}`;
  });
  await schritt('ohne server-freigabe nichts', async () => {
    await senden('steuernde', { t: 'bewegen', ...norm(mitte.x, mitte.y) });
    await warten(300);
    if (!fst.zaehler.verworfen['keine-server-freigabe']) throw new Error(JSON.stringify(fst.zaehler));
  });
  const metadaten = (g) => js(`window.agoraDesktop.fernsteuerung.metadaten(${JSON.stringify(JSON.stringify({ fernsteuerung: g }))})`);
  await schritt('server-freigabe + roter rahmen', async () => {
    await metadaten([{ von: 'praesentator', an: 'steuernde', an_name: 'Stefan', id: 'x'.repeat(30) }]);     // falsche Kennung
    await warten(300);
    if (fst.aktiv()) throw new Error('aktiv mit fremder Kennung');
    await metadaten([{ von: 'praesentator', an: 'steuernde', an_name: 'Stefan', id: kennung }]);
    await warten(800);
    if (!fst.aktiv()) throw new Error('nicht aktiv');
    const rahmen = BrowserWindow.getAllWindows().filter((w) => w !== fenster && w.webContents.getURL().includes('rahmen.html'));
    if (rahmen.length !== 1 || !rahmen[0].isVisible() || !rahmen[0].isAlwaysOnTop()) throw new Error(`Rahmen: ${rahmen.length}`);
    const text = await rahmen[0].webContents.executeJavaScript('document.getElementById("text").textContent');
    return text;
  });
  fenster.blur();
  await xdo('search', '--name', 'Zielfenster', 'windowfocus');
  await warten(300);
  await schritt('maus bewegen (xdotool getmouselocation)', async () => {
    await senden('steuernde', { t: 'bewegen', ...norm(mitte.x, mitte.y) });
    await warten(400); await fst.warten();
    const ort = await xdo('getmouselocation');
    const m = /x:(\d+) y:(\d+)/.exec(ort);
    if (!m || Math.abs(+m[1] - mitte.x) > 1 || Math.abs(+m[2] - mitte.y) > 1) throw new Error(`${ort}, erwartet ${mitte.x},${mitte.y}`);
    return ort;
  });
  await schritt('falscher absender verworfen', async () => {
    await senden('boese', { t: 'bewegen', x: 0.9, y: 0.9 });
    await senden('boese', { t: 'klick', ...norm(mitte.x, mitte.y), knopf: 'left' });
    await warten(400);
    const ort = await xdo('getmouselocation');
    if (!ort.includes(`x:${mitte.x} y:${mitte.y}`) || fst.zaehler.verworfen['falscher-absender'] !== 2 || /klick/.test(log())) throw new Error(`${ort} ${JSON.stringify(fst.zaehler)}`);
  });
  await schritt('klick ins zielfenster', async () => {
    await senden('steuernde', { t: 'klick', ...norm(mitte.x, mitte.y), knopf: 'left' });
    await warten(800); await fst.warten();
    if (!/klick 1 /.test(log())) throw new Error(`Ziel-Log: ${log()}`);
    return log().trim().split('\n').filter((z) => z.startsWith('klick')).join(' | ');
  });
  await schritt('tasten und text', async () => {
    await xdo('search', '--name', 'Zielfenster', 'windowfocus');
    for (const ev of [{ t: 'taste', taste: 'a', mod: [] }, { t: 'taste', taste: 'B', mod: ['shift'] }, { t: 'taste', taste: 'ä', mod: [] }, { t: 'taste', taste: 'Enter', mod: [] }]) await senden('steuernde', ev);
    await warten(1200); await fst.warten();
    const tasten = log().split('\n').filter((z) => z.startsWith('taste')).join(' | ');
    for (const s of ['taste a', 'taste B', 'taste ä', 'taste Return']) if (!tasten.includes(s)) throw new Error(`${s} fehlt: ${tasten}`);
    if (tasten.indexOf('taste ä') > tasten.indexOf('taste Return')) throw new Error(`Reihenfolge: ${tasten}`);
    return tasten;
  });
  await schritt('rad', async () => {
    await senden('steuernde', { t: 'rollen', dx: 0, dy: 2 });
    await warten(800); await fst.warten();
    if (!/rad unten/.test(log())) throw new Error(`Ziel-Log: ${log()}`);
  });
  await schritt('eigene kuerzel nie weiter', async () => {
    const vorher = fst.zaehler.verworfen['eigenes-kuerzel'] || 0;
    await senden('steuernde', { t: 'taste', taste: 'm', mod: ['control', 'shift'] });
    await senden('steuernde', { t: 'taste', taste: 'x', mod: ['control', 'alt'] });
    await warten(300);
    if ((fst.zaehler.verworfen['eigenes-kuerzel'] || 0) - vorher !== 2) throw new Error(JSON.stringify(fst.zaehler));
  });
  await schritt('agora-fenster tabu', async () => {
    const b = fenster.getBounds();
    await senden('steuernde', { t: 'klick', ...norm(b.x + 50, b.y + 50), knopf: 'left' });
    fenster.focus();
    await warten(300);
    const fokus = fenster.isFocused();
    await senden('steuernde', { t: 'klick', ...norm(mitte.x, mitte.y), knopf: 'left' });
    await senden('steuernde', { t: 'taste', taste: 'q', mod: [] });
    await warten(400);
    const v = fst.zaehler.verworfen;
    if (!v['agora-fenster'] || (fokus && (v['agora-fokussiert'] || 0) < 2) || /taste q/.test(log())) throw new Error(`Fokus ${fokus}: ${JSON.stringify(v)}`);
    fenster.blur();
    return `im Fenster: ${v['agora-fenster']}, bei Fokus (${fokus ? 'Agora vorne' : 'ohne WM kein Fokus'}): ${v['agora-fokussiert'] || 0}`;
  });
  await schritt('e2ee: nur verschlüsselt', async () => {
    fst.zustand.e2ee = true;
    await senden('steuernde', { t: 'bewegen', x: 0.1, y: 0.1 }, false);
    await warten(200);
    const unv = fst.zaehler.verworfen.unverschluesselt;
    await senden('steuernde', { t: 'bewegen', ...norm(mitte.x, mitte.y) }, true);
    await warten(300);
    fst.zustand.e2ee = false;
    if (unv !== 1) throw new Error(JSON.stringify(fst.zaehler));
  });
  await schritt('format und grenzen', async () => {
    for (const ev of [{ t: 'bewegen', x: 2, y: 0 }, { t: 'shell', cmd: 'rm' }, { t: 'taste', taste: 'a', mod: ['meta'] }, { t: 'rollen', dx: 0, dy: 999 }]) await senden('steuernde', ev);
    const n = 400;
    await js(`(() => { const e = new TextEncoder().encode(JSON.stringify({ t: 'bewegen', x: 0.2, y: 0.2 })); for (let i = 0; i < ${n}; i++) window.agoraDesktop.fernsteuerung.ereignis('steuernde', false, e); })()`);
    await warten(1500);
    const v = fst.zaehler.verworfen;
    if (v.format !== 4 || !(v.grenze > 50)) throw new Error(JSON.stringify(v));
    return `Format verworfen ${v.format}, über Grenze ${v.grenze} von ${n}`;
  });
  await schritt('server nimmt zurück → ende', async () => {
    await metadaten([]);
    await warten(500);
    if (fst.aktiv() || BrowserWindow.getAllWindows().some((w) => w.webContents.getURL().includes('rahmen.html'))) throw new Error('noch aktiv');
  });
  await schritt('kuerzel beendet (echter Tastendruck über X)', async () => {
    const a = await js(`new Promise((r) => setTimeout(() => window.agoraDesktop.fernsteuerung.anfrage('steuernde', 'Stefan').then(r), 3100))`);
    await metadaten([{ von: 'praesentator', an: 'steuernde', an_name: 'Stefan', id: a.kennung }]);
    await warten(500);
    if (!fst.aktiv()) throw new Error('nicht wieder aktiv');
    const ende = js(`new Promise((r) => { window.agoraDesktop.fernsteuerung.beiEnde(r); setTimeout(() => r('zeit'), 5000); })`);
    await xdo('key', 'ctrl+alt+x');
    const g = await ende;
    if (fst.aktiv() || g !== 'kuerzel') throw new Error(`Ende: ${g}`);
    return `Seite bekam Ende: ${g}`;
  });
  await schritt('freigabe endet → ende', async () => {
    const a = await js(`new Promise((r) => setTimeout(() => window.agoraDesktop.fernsteuerung.anfrage('steuernde', 'Stefan').then(r), 3100))`);
    await metadaten([{ von: 'praesentator', an: 'steuernde', an_name: 'Stefan', id: a.kennung }]);
    await warten(300);
    if (!fst.aktiv()) throw new Error('nicht aktiv');
    await js(`window.__strom.getTracks().forEach((t) => t.stop()); window.agoraDesktop.fernsteuerung.freigabe(false)`);
    await warten(300);
    if (fst.aktiv()) throw new Error('noch aktiv');
    await senden('steuernde', { t: 'bewegen', x: 0.5, y: 0.5 });
    await warten(200);
    return JSON.stringify(fst.zaehler);
  });
  return r;
}

module.exports = { laufen };
