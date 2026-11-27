// Fernsteuerung im Hauptprozess: Dialog „Erlauben?“, roter Rahmen, Kürzel/Tray zum Beenden und das Weitergeben
// geprüfter Eingaben an den Eingabe-Adapter (eingabe.js). Alle Regeln stehen in fernsteuerung.js; hier nur Electron.
//
// Entscheidungen (docs/DESKTOP.md):
//  • Zustimmung nur über einen nativen Dialog der App (dialog.showMessageBox) – die Seite allein kann nichts erlauben.
//  • Nur ganze Bildschirme (bei Fenstern kennt Electron die Lage des fremden Fensters nicht).
//  • Solange ein Agora-Fenster den Fokus hat oder ein Dialog offen ist, gehen Tasten, Klicks und Rad NICHT weiter;
//    Klicks/Rad auf Punkte innerhalb eines Agora-Fensters nie. So kann niemand aus der Ferne den Erlauben-Dialog, die
//    Bildschirmauswahl oder die App selbst bedienen. Mausbewegungen dürfen (harmlos, zeigen der Person, wohin man zeigt).
//  • Tastenkombinationen, die eigene systemweite Kürzel auslösen würden (Mikro, Kamera, Hand, Beenden), gehen nie weiter.
//  • Ende: Kürzel (Vorgabe Strg/Cmd+Alt+X), Tray „Fernsteuerung beenden“, Ende der Freigabe, Neuladen/Navigation der
//    Seite, Server nimmt die Freigabe zurück, zu viele verworfene Ereignisse (Missbrauch).
'use strict';
const crypto = require('node:crypto');
const path = require('node:path');
const { BrowserWindow, dialog, globalShortcut, screen, ipcMain } = require('electron');
const F = require('./fernsteuerung');
const { adapterLaden } = require('./eingabe');

function einrichten({ hauptfenster, eigeneFenster, konfig, TEST, quelleHolen, zustandGeaendert }) {
  let adapter = null;
  const bereit = adapterLaden().then((a) => { adapter = a; if (TEST) console.log('AGORA_TEST eingabe', a ? a.name : 'keiner'); return a; });
  const z = { freigabe: null, zustimmung: null, freigabeServer: null, ich: null, e2ee: false };
  let begrenzer = new F.Begrenzer(), rahmen = null, dialogOffen = false, letzteAnfrage = 0, gedrueckt = new Set(), aktivGemeldet = false;
  const zaehler = { angenommen: 0, verworfen: {} };

  const vonSeite = (e) => { const f = hauptfenster(); return f && !f.isDestroyed() && e.sender === f.webContents; };
  const aktiv = () => !!(z.freigabe?.aktiv && z.zustimmung && z.freigabeServer && z.freigabeServer.an === z.zustimmung.identity
    && z.freigabeServer.id === z.zustimmung.kennung);

  function anzeige() {
    const an = aktiv();
    if (an === aktivGemeldet) return;
    aktivGemeldet = an;
    if (an) {
      rahmenZeigen();
      try { if (konfig.kuerzelEnde) globalShortcut.register(konfig.kuerzelEnde, () => beenden('kuerzel')); } catch { /* belegt */ }
    } else {
      rahmen?.destroy(); rahmen = null;
      try { if (konfig.kuerzelEnde) globalShortcut.unregister(konfig.kuerzelEnde); } catch { /* egal */ }
    }
    zustandGeaendert(an);
  }

  function rahmenZeigen() {
    const b = z.freigabe.bildschirm.bounds;
    const name = String(z.freigabeServer.an_name || z.zustimmung.name || '').slice(0, 60);
    rahmen = new BrowserWindow({
      x: b.x, y: b.y, width: b.width, height: b.height, frame: false, transparent: true, focusable: false, skipTaskbar: true,
      resizable: false, movable: false, hasShadow: false, alwaysOnTop: true, show: false,
      webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false },
    });
    rahmen.setAlwaysOnTop(true, 'screen-saver');
    rahmen.setIgnoreMouseEvents(true);
    rahmen.setVisibleOnAllWorkspaces?.(true);
    rahmen.webContents.on('will-navigate', (e) => e.preventDefault());
    const kuerzel = konfig.kuerzelEnde ? ` – ${konfig.kuerzelEnde.replace('CommandOrControl', process.platform === 'darwin' ? 'Cmd' : 'Strg')} beendet` : '';
    rahmen.loadFile(path.join(__dirname, 'rahmen.html'), { query: { text: `${name || 'Jemand'} steuert deinen Bildschirm${kuerzel}` } });
    rahmen.once('ready-to-show', () => rahmen?.showInactive());
  }

  /** Steuerung lokal beenden (Zustimmung weg) und die Seite bitten, es dem Server zu melden. */
  function beenden(grund) {
    const war = aktiv() || z.zustimmung;
    for (const k of gedrueckt) { try { adapter?.knopf(k, false); } catch { /* egal */ } }
    gedrueckt = new Set();
    z.zustimmung = null;
    begrenzer = new F.Begrenzer();
    anzeige();
    if (war) {
      if (TEST) console.log('AGORA_TEST fernsteuerung-ende', grund);
      const f = hauptfenster();
      if (f && !f.isDestroyed()) f.webContents.send('agora-fs:ende', String(grund));
    }
  }

  function agoraFokussiert() {
    return dialogOffen || eigeneFenster().some((w) => w && !w.isDestroyed() && w.isFocused());
  }

  function agoraRechtecke() {
    return eigeneFenster().filter((w) => w && !w.isDestroyed() && w.isVisible() && !w.isMinimized()).map((w) => w.getBounds());
  }

  /** Ein Ereignis geprüft weitergeben → Grund des Verwerfens oder null. */
  function ausfuehren(absender, verschluesselt, roh) {
    const ok = F.darfSteuern(z, absender, verschluesselt);
    if (!ok.ok) return ok.grund;
    if (!adapter) return 'kein-adapter';
    const ev = F.ereignisLesen(roh);
    if (!ev) return 'format';
    if (!begrenzer.ok(ev)) {
      if (begrenzer.missbrauch()) beenden('missbrauch');
      return 'grenze';
    }
    if (ev.t === 'taste' && F.kuerzelTreffer(ev, [...Object.values(konfig.kuerzel), konfig.kuerzelEnde])) return 'eigenes-kuerzel';
    const anzeigeB = z.freigabe.bildschirm;
    const faktor = process.platform === 'darwin' ? 1 : (anzeigeB.scaleFactor || 1);
    if (ev.t !== 'bewegen' && agoraFokussiert()) return 'agora-fokussiert';
    if ('x' in ev) {
      const p = F.abbilden(ev.x, ev.y, anzeigeB);
      if (ev.t !== 'bewegen' && F.inRechteck(p, agoraRechtecke(), faktor)) return 'agora-fenster';
      adapter.bewegen(p.x, p.y);
    }
    if (ev.t === 'druecken') { adapter.knopf(ev.knopf, true); gedrueckt.add(ev.knopf); }
    else if (ev.t === 'loslassen') { adapter.knopf(ev.knopf, false); gedrueckt.delete(ev.knopf); }
    else if (ev.t === 'klick') adapter.klick(ev.knopf, ev.doppelt);
    else if (ev.t === 'rollen') adapter.rollen(ev.dx, ev.dy);
    else if (ev.t === 'taste') adapter.taste(ev.taste, ev.mod);
    else if (ev.t === 'text') adapter.text(ev.zeichen);
    return null;
  }

  // ---------------------------------------------------------------- Brücke (preload.js) → Hauptprozess
  ipcMain.handle('agora-fs:verfuegbar', async (e) => (vonSeite(e) ? !!(await bereit) : false));

  ipcMain.on('agora-fs:freigabe', (e, an, info) => {
    if (!vonSeite(e)) return;
    if (an === true && info && typeof info.ich === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(info.ich)) {
      const quelle = quelleHolen();
      const bildschirm = F.bildschirmZurQuelle(quelle, screen.getAllDisplays());
      z.ich = info.ich;
      z.e2ee = info.e2ee === true;
      z.freigabe = { aktiv: true, art: bildschirm ? 'bildschirm' : 'fenster', bildschirm };
    } else if (an === false) {
      z.freigabe = null;
      beenden('freigabe-ende');
    }
  });

  ipcMain.handle('agora-fs:anfrage', async (e, identity, name) => {
    if (!vonSeite(e) || typeof identity !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(identity)) return { ok: false, grund: 'ungueltig' };
    if (!z.freigabe?.aktiv) return { ok: false, grund: 'keine-freigabe' };
    if (z.freigabe.art !== 'bildschirm') return { ok: false, grund: 'kein-bildschirm' };
    if (!(await bereit)) return { ok: false, grund: 'kein-adapter' };
    if (dialogOffen || Date.now() - letzteAnfrage < 3000) return { ok: false, grund: 'zu-schnell' };
    letzteAnfrage = Date.now();
    const anzeigeName = String(name || '').replace(/[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩]/g, '').trim().slice(0, 60) || 'Jemand';
    let erlaubt;
    dialogOffen = true;
    try {
      if (TEST && process.env.AGORA_DESKTOP_TEST_ZUSTIMMUNG) erlaubt = process.env.AGORA_DESKTOP_TEST_ZUSTIMMUNG === 'ja';
      else {
        const f = hauptfenster();
        const r = await dialog.showMessageBox(f && !f.isDestroyed() ? f : undefined, {
          type: 'warning', buttons: ['Ablehnen', 'Erlauben'], defaultId: 0, cancelId: 0, noLink: true, title: 'Fernsteuerung',
          message: `„${anzeigeName}“ möchte deinen Bildschirm steuern.`,
          detail: 'Die Person kann dann Maus und Tastatur auf dem geteilten Bildschirm benutzen. Ein roter Rahmen zeigt das an. '
            + `Beenden jederzeit mit ${konfig.kuerzelEnde || 'dem Tray-Menü'}, über das Tray-Menü oder indem du das Teilen beendest.`,
        });
        erlaubt = r.response === 1;
      }
    } finally { dialogOffen = false; }
    if (!erlaubt || !z.freigabe?.aktiv) return { ok: false, grund: 'abgelehnt' };
    z.zustimmung = { identity, name: anzeigeName, kennung: crypto.randomBytes(24).toString('base64url') };
    begrenzer = new F.Begrenzer();
    anzeige();
    return { ok: true, kennung: z.zustimmung.kennung };
  });

  ipcMain.on('agora-fs:metadaten', (e, text) => {
    if (!vonSeite(e) || typeof text !== 'string' || text.length > 20000) return;
    const vorher = z.freigabeServer;
    z.freigabeServer = z.ich ? F.freigabeAusMetadaten(text, z.ich) : null;
    if (z.freigabeServer) {
      try { z.freigabeServer.an_name = JSON.parse(text).fernsteuerung.find((g) => g.von === z.ich)?.an_name || ''; } catch { /* egal */ }
    }
    if (vorher && !z.freigabeServer && z.zustimmung) beenden('server');
    else anzeige();
  });

  ipcMain.on('agora-fs:ereignis', (e, absender, verschluesselt, daten) => {
    if (!vonSeite(e)) return;
    const grund = ausfuehren(absender, verschluesselt === true, daten);
    if (grund) zaehler.verworfen[grund] = (zaehler.verworfen[grund] || 0) + 1; else zaehler.angenommen++;
    if (TEST) console.log('AGORA_TEST fs-ereignis', JSON.stringify({ absender, grund: grund || 'ok' }));
  });

  ipcMain.on('agora-fs:beenden', (e) => { if (vonSeite(e)) beenden('seite'); });

  return { beenden, aktiv, zaehler, zustand: z, bereit, ausfuehren, warten: () => adapter?.warten?.() };
}

module.exports = { einrichten };
