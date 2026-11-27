# HLS-Test (Playwright, Kamera-Attrappe): Host macht den Raum zum Webinar, startet „Öffentliche Übertragung (HLS)“
# mit 720p + 360p, der Test prüft Master-/Stufen-Playlists und ein Segment über die App und lässt die Zuschauerseite
# /h/<code> in Chromium abspielen (video.currentTime muss weiterlaufen). Danach Zuschauerzahl, Beenden, „beendet“.
# Lauf: tests/egress/hls-lauf.sh
import os, re, time, traceback
from playwright.sync_api import sync_playwright

B = os.environ.get('BASIS', 'http://127.0.0.1:8251')
OUT = os.environ.get('AUSGABE', '/tmp/')
ORDNER = os.environ.get('HLS_ORDNER', '')
fehler = []
H = {'X-Agora': '1', 'Content-Type': 'application/json'}


def bis(f, sekunden, schritt=1.0):
    ende = time.time() + sekunden
    while time.time() < ende:
        try:
            x = f()
            if x:
                return x
        except Exception:      # noqa: BLE001
            pass
        time.sleep(schritt)
    return None


with sync_playwright() as p:
    br = p.chromium.launch(args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'])
    ctx = br.new_context(viewport={'width': 1280, 'height': 800}, permissions=['camera', 'microphone'])
    hp = ctx.new_page()
    hp.on('pageerror', lambda e: fehler.append(f'pageerror Host: {e}'))
    try:
        hp.goto(B + '/')
        r = hp.request.post(B + '/api/konto/registrieren', headers=H, data='{"name":"Hanna HLS","email":"hanna@example.com","passwort":"geheimespasswort1"}')
        assert r.ok, r.text()
        mail = hp.request.get(B + '/api/test/posteingang').json()['mails'][-1]['text']
        hp.goto(re.search(r'https?://\S+bestaetigen\?t=[\w\-.]+', mail).group(0))
        hp.wait_for_load_state('networkidle')
        code = hp.evaluate("""async () => {
          const H = {'X-Agora':'1','Content-Type':'application/json'};
          const r = (await (await fetch('/api/raeume', {method:'POST', headers:H, body: JSON.stringify({titel:'HLS-Test'})})).json()).raum;
          await fetch(`/api/raeume/${r.code}/webinar`, {method:'PATCH', headers:H, body: JSON.stringify({an: true})});
          return r.code; }""")
        hp.goto(f'{B}/r/{code}')
        hp.wait_for_selector('#v-lobby:not([hidden])')
        hp.click('#beitreten-knopf')
        hp.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        time.sleep(3)
        hp.click('#s-mehr')
        hp.click('.menue [role=menuitem] >> text=Öffentliche Übertragung (HLS) starten')
        hp.wait_for_selector('dialog .egress-form [name=zugang]')
        hp.check('dialog [name=klein]')
        hp.screenshot(path=OUT + 'hls-1-dialog.png')
        t0 = time.time()
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#m-live:not([hidden]), dialog .status:not([hidden])', timeout=60000)
        if hp.is_visible('dialog .status'):
            raise RuntimeError('Start abgelehnt: ' + hp.inner_text('dialog .status'))
        hp.wait_for_selector('.toast:has-text("öffentliche Übertragung läuft")', timeout=90000)
        print(f'Egress „läuft“ nach {time.time() - t0:.1f} s')
        info = hp.request.get(f'{B}/api/hls/{code}').json()
        assert info['laeuft'] and info['quelle'], info
        quelle = info['quelle']
        master = hp.request.get(B + quelle)
        print('Master-Playlist:', master.headers.get('content-type'), master.headers.get('cache-control'), '\n' + master.text())
        if '720p-live.m3u8' not in master.text() or '360p-live.m3u8' not in master.text():
            fehler.append('Master-Playlist ohne beide Stufen')
        basis = quelle.rsplit('/', 1)[0]
        for stufe in ('720p', '360p'):
            pl = bis(lambda: (lambda x: x if x.ok and '.ts' in x.text() else None)(hp.request.get(f'{B}{basis}/{stufe}-live.m3u8')), 90)
            if not pl:
                fehler.append(f'{stufe}: keine Segmente in der Playlist')
                continue
            print(f'{stufe}-live.m3u8 nach {time.time() - t0:.1f} s ({pl.headers.get("cache-control")}):\n{pl.text()}')
            seg = [z for z in pl.text().splitlines() if z.endswith('.ts')][0]
            s = hp.request.get(f'{B}{basis}/{seg}')
            print(f'Segment {seg}: {s.status} {s.headers.get("content-type")} {len(s.body())} Bytes, {s.headers.get("cache-control")}')
            if s.status != 200 or s.body()[:1] != b'\x47' or len(s.body()) < 10000 or s.headers.get('content-type') != 'video/mp2t':
                fehler.append(f'{stufe}: Segment kaputt ({s.status}, {s.body()[:4]!r})')
        if ORDNER:
            for w, _d, f in os.walk(ORDNER):
                if f:
                    print('Dateien im Ausgabeordner', w.replace(ORDNER, ''), sorted(f)[:6], f'… ({len(f)})')
        hp.screenshot(path=OUT + 'hls-2-host-live.png')
        # Zuschauerseite ohne Konto, ohne Kamera/Mikro
        vctx = br.new_context(viewport={'width': 1100, 'height': 760})
        vp = vctx.new_page()
        vp.on('pageerror', lambda e: fehler.append(f'pageerror Zuschauer: {e}'))
        vp.on('console', lambda m: m.type == 'error' and print('Zuschauer-Konsole:', m.text[:200]))
        t1 = time.time()
        vp.goto(f'{B}/h/{code}')
        vp.wait_for_selector('#buehne:not([hidden])', timeout=30000)
        weg = vp.evaluate('document.body.dataset.abspielweg')
        ok = vp.wait_for_function('() => document.querySelector("#video").currentTime > 1', timeout=60000)
        z1 = vp.evaluate('document.querySelector("#video").currentTime')
        time.sleep(6)
        z2 = vp.evaluate('document.querySelector("#video").currentTime')
        v = vp.evaluate('(() => { const v = document.querySelector("#video"); return {w: v.videoWidth, h: v.videoHeight, pausiert: v.paused, ende: v.ended}; })()')
        print(f'Zuschauerseite: Abspielweg {weg}, spielt nach {time.time() - t1:.1f} s, currentTime {z1:.1f} → {z2:.1f} s, {v}')
        if not z2 > z1 + 3:
            fehler.append(f'Video läuft nicht weiter ({z1} → {z2})')
        if not v['w']:
            fehler.append('kein Videobild')
        vp.screenshot(path=OUT + 'hls-3-zuschauer.png')
        # Zweiter Zuschauer ohne eingebautes HLS (wie Firefox/ältere Chrome): hls.js muss übernehmen
        jctx = br.new_context(viewport={'width': 1100, 'height': 760})
        jctx.add_init_script("(() => { const o = HTMLMediaElement.prototype.canPlayType; HTMLMediaElement.prototype.canPlayType = function (t) { return /mpegurl/i.test(t) ? '' : o.call(this, t); }; })()")
        jp = jctx.new_page()
        jp.on('pageerror', lambda e: fehler.append(f'pageerror Zuschauer (hls.js): {e}'))
        t1 = time.time()
        jp.goto(f'{B}/h/{code}')
        jp.wait_for_selector('#buehne:not([hidden])', timeout=30000)
        jp.wait_for_function('() => document.querySelector("#video").currentTime > 1', timeout=60000)
        j1 = jp.evaluate('document.querySelector("#video").currentTime')
        time.sleep(6)
        j2 = jp.evaluate('document.querySelector("#video").currentTime')
        jweg = jp.evaluate('document.body.dataset.abspielweg')
        geladen = jp.evaluate("performance.getEntriesByType('resource').map((e) => e.name).filter((n) => n.includes('hls.light')).join(',')")
        print(f'Zuschauerseite (ohne natives HLS): Abspielweg {jweg}, spielt nach {time.time() - t1:.1f} s, currentTime {j1:.1f} → {j2:.1f} s, geladen: {geladen}')
        if jweg != 'hlsjs' or not j2 > j1 + 3 or '/assets/' not in geladen:
            fehler.append(f'hls.js-Weg spielt nicht ({jweg}, {j1} → {j2}, {geladen})')
        jctx.close()
        # Zuschauerzahl beim Host (die Seite meldet sich sofort)
        time.sleep(16)                 # Host fragt den Zustand während der Übertragung alle 15 s ab
        hp.click('#s-mehr')
        eintrag = hp.inner_text('.menue')
        m = re.search(r'≈ (\d+) Zuschauer', eintrag)
        print('Menü des Hosts:', m.group(0) if m else eintrag.replace('\n', ' | ')[:300])
        if not m or int(m.group(1)) < 1:
            # Menü wird beim Öffnen aus dem letzten Zustand gebaut (Abfrage alle 15 s) – einmal neu laden und nachsehen
            hp.keyboard.press('Escape')
            time.sleep(16)
            hp.click('#s-mehr')
            eintrag = hp.inner_text('.menue')
            m = re.search(r'≈ (\d+) Zuschauer', eintrag)
            print('Menü des Hosts (2):', m.group(0) if m else eintrag.replace('\n', ' | ')[:300])
            if not m or int(m.group(1)) < 1:
                fehler.append('Zuschauerzahl kommt beim Host nicht an')
        # Beenden
        hp.click('.menue [role=menuitem] >> text=Öffentliche Übertragung beenden')
        hp.wait_for_selector('#m-live', state='hidden', timeout=30000)
        info = bis(lambda: (lambda d: d if d['beendet'] else None)(hp.request.get(f'{B}/api/hls/{code}').json()), 30)
        if not info:
            fehler.append('/api/hls meldet nicht „beendet“')
        try:
            vp.wait_for_function('() => /beendet|has ended/.test(document.querySelector("#status").textContent)', timeout=70000)   # Sprache des Browsers
        except Exception:
            vp.screenshot(path=OUT + 'hls-fehler-zuschauer.png')
            print('Zuschauerseite beim Fehler:', vp.inner_text('main')[:500], vp.evaluate('document.body.dataset.abspielweg'))
            raise
        print('Zuschauerseite zeigt:', vp.inner_text('#status'))
        vp.screenshot(path=OUT + 'hls-4-beendet.png')
        r = hp.request.get(B + quelle)
        print('Nach dem Ende (Nachlauf 1 h): Master', r.status)
        vctx.close()
    except Exception:
        traceback.print_exc()
        fehler.append('Ablauf abgebrochen')
        hp.screenshot(path=OUT + 'hls-fehler.png')
    print('\n'.join(fehler) or 'HLS-Test ok')
    br.close()
import sys
sys.exit(1 if fehler else 0)
