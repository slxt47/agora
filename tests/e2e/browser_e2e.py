# Browser-E2E (Playwright, Kamera/Mikro-Attrappe): Host + Gast, Warteraum, Video, Chat, Steuerung, Beenden.
# Lauf: siehe tests/e2e/lauf.sh
import re, json, time, traceback
from playwright.sync_api import sync_playwright

import os
B = os.environ.get('BASIS', 'http://127.0.0.1:8111')
OUT = os.environ.get('AUSGABE', '/tmp/') 
fehler = []

def frames(page):
    """Summe der dekodierten Bilder aller fremden Videos (wächst, solange Video ankommt)."""
    return page.evaluate("""() => [...document.querySelectorAll('#buehne .kv.mit-video:not(.spiegeln) video')]
        .reduce((s, v) => s + (v.getVideoPlaybackQuality?.().totalVideoFrames || 0), 0)""")

def effekt_waehlen(page, wert, bild):
    page.click(f'.effekte [data-wert="{wert}"]')
    page.wait_for_selector('.effekte .effekt-status[data-zustand="aktiv"]', state='attached', timeout=45000)
    time.sleep(1.5)
    page.screenshot(path=OUT + bild)
    page.keyboard.press('Escape')

def beobachten(page, wer):
    page.on('pageerror', lambda e: fehler.append(f'[{wer}] pageerror: {e}'))
    page.on('console', lambda m: m.type == 'error' and fehler.append(f'[{wer}] console: {m.text}'))
    page.on('requestfailed', lambda r: print(f'[{wer}] Anfrage fehlgeschlagen: {r.url[:120]} {r.failure}'))

with sync_playwright() as p:
    br = p.chromium.launch(args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'])
    host = br.new_context(viewport={'width': 1280, 'height': 800}, permissions=['camera', 'microphone'])
    hp = host.new_page(); beobachten(hp, 'host')
    try:
        hp.goto(B + '/')
        hp.screenshot(path=OUT + '01-start.png')
        # PWA: Service Worker meldet sich an und übernimmt die Seite
        sw = hp.evaluate("navigator.serviceWorker.ready.then(r => r.active && r.active.scriptURL)")
        hp.wait_for_function("!!navigator.serviceWorker.controller", timeout=10000)
        caches = hp.evaluate("caches.keys()")
        print('Service Worker:', sw, '| Caches:', caches)
        if not sw or not sw.endswith('/sw.js') or not any(c.startswith('agora-') for c in caches): fehler.append('Service Worker fehlt')
        hp.click('#tab-registrieren')
        hp.fill('#f-registrieren [name=name]', 'Florian Test')
        hp.fill('#f-registrieren [name=email]', 'host@example.com')
        hp.fill('#f-registrieren [name=passwort]', 'geheimespasswort1')
        hp.click('#f-registrieren button[type=submit]')
        hp.wait_for_selector('#status.ok')
        mails = hp.request.get(B + '/api/test/posteingang').json()['mails']
        link = re.search(r'https?://\S+bestaetigen\?t=[\w\-.]+', mails[-1]['text']).group(0)
        hp.goto(link)
        hp.wait_for_selector('#angemeldet:not([hidden])')
        hp.screenshot(path=OUT + '02-angemeldet.png')
        # Konto + planen
        hp.goto(B + '/konto#planen')
        hp.wait_for_selector('dialog.blatt')
        hp.fill('dialog [name=titel]', 'Wochenbesprechung')
        hp.screenshot(path=OUT + '03-planen.png')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#liste-geplant li .name')
        hp.screenshot(path=OUT + '04-konto.png', full_page=True)
        # Sofort-Meeting mit Warteraum
        r = hp.evaluate("""async () => (await (await fetch('/api/raeume', {method:'POST', headers:{'X-Agora':'1','Content-Type':'application/json'}, body: JSON.stringify({titel:'Testmeeting', warteraum:true})})).json())""")
        code = r['raum']['code']
        hp.goto(f'{B}/r/{code}')
        hp.wait_for_selector('#v-lobby:not([hidden])')
        time.sleep(1.5)
        hp.screenshot(path=OUT + '05-lobby.png')
        # Video-Effekte in der Lobby: virtueller Hintergrund (MediaPipe selbst gehostet)
        hp.click('#lobby-effekte')
        hp.wait_for_selector('.menue .effekte')
        effekt_waehlen(hp, 'nachthimmel', '05b-lobby-effekte.png')
        time.sleep(1)
        hp.screenshot(path=OUT + '05c-lobby-hintergrund.png')
        hp.click('#beitreten-knopf')
        hp.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        time.sleep(2)
        hp.screenshot(path=OUT + '06-meeting-allein.png')

        gast = br.new_context(viewport={'width': 390, 'height': 844}, permissions=['camera', 'microphone'], has_touch=True, is_mobile=True)
        gp = gast.new_page(); beobachten(gp, 'gast')
        gp.goto(f'{B}/r/{code}')
        gp.wait_for_selector('#v-lobby:not([hidden])')
        gp.fill('#f-beitritt [name=name]', 'Anna Gast')
        time.sleep(1)
        gp.screenshot(path=OUT + '07-gast-lobby.png', full_page=True)
        gp.click('#beitreten-knopf')
        gp.wait_for_selector('#v-warten:not([hidden])')
        gp.screenshot(path=OUT + '08-gast-warten.png')
        # Host lässt zu
        hp.wait_for_selector('#s-leute .zaehler', timeout=15000)
        hp.click('#s-leute')
        hp.wait_for_selector('#panel-leute .btn.primary', timeout=10000)
        hp.screenshot(path=OUT + '09-host-warteraum.png')
        hp.click('#panel-leute .btn.primary')
        gp.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        time.sleep(3)
        # Chat
        hp.click('#tab-chat')
        hp.fill('#panel-chat textarea', 'Hallo! Doku: https://slxt.org/impressum/ 👍')
        hp.keyboard.press('Enter')
        gp.wait_for_selector('#s-chat .zaehler', timeout=10000)
        # Reaktion + Hand vom Host
        hp.click('#s-reaktion'); hp.click('.menue .emoji-raster button >> nth=0')
        hp.keyboard.press('h')
        time.sleep(1.5)
        hp.screenshot(path=OUT + '10-host-meeting.png')
        gp.screenshot(path=OUT + '11-gast-meeting.png')
        # Handy quer: kompakte Leiste, alles ≥ 44 px
        gp.set_viewport_size({'width': 844, 'height': 390}); time.sleep(1)
        gp.screenshot(path=OUT + '11b-gast-quer.png')
        klein = gp.evaluate("[...document.querySelectorAll('.m-leiste button')].filter(b => b.offsetParent && (b.offsetWidth < 44 || b.offsetHeight < 44)).map(b => b.id)")
        if klein: fehler.append(f'Knöpfe unter 44 px: {klein}')
        gp.set_viewport_size({'width': 390, 'height': 844}); time.sleep(0.5)
        gp.click('#s-chat'); time.sleep(0.5)
        gp.screenshot(path=OUT + '12-gast-chat.png')
        n_host = hp.locator('#buehne .kv').count(); n_gast = gp.locator('#buehne .kv').count()
        mitvideo = hp.locator('#buehne .kv.mit-video').count()
        print('Kacheln host/gast:', n_host, n_gast, 'mit Video (host):', mitvideo)
        # Sprecheransicht + Mehr-Menü
        hp.keyboard.press('Escape'); hp.keyboard.press('g'); time.sleep(1)
        hp.screenshot(path=OUT + '13-sprecher.png')
        hp.click('#s-mehr'); time.sleep(0.3)
        hp.screenshot(path=OUT + '14-mehr.png')
        hp.keyboard.press('Escape')
        # Effekt im Meeting umstellen: starke Unschärfe; das Video muss beim Gast weiter ankommen
        hp.click('#s-mehr'); hp.click('.menue [role=menuitem] >> text=Hintergrund und Effekte')
        hp.wait_for_selector('.menue .effekte')
        effekt_waehlen(hp, 'stark', '14b-effekte-meeting.png')
        gp.click('#leiste-zu')   # Chat beim Gast zu, damit das Bild zu sehen ist
        f0 = frames(gp); time.sleep(3); f1 = frames(gp)
        print('Gast empfängt Bilder vom Host (mit Unschärfe):', f0, '→', f1)
        if f1 <= f0: fehler.append('Video kommt mit Effekt nicht beim Gast an')
        gp.screenshot(path=OUT + '14c-gast-sieht-effekt.png')
        # Nur „Aussehen verbessern“ (Canvas-Filter ohne MediaPipe)
        hp.click('#s-mehr'); hp.click('.menue [role=menuitem] >> text=Hintergrund und Effekte')
        hp.click('.effekte [data-wert="aus"]')
        hp.wait_for_selector('.effekte .effekt-status[data-zustand="aus"]', state='attached')
        hp.click('.effekte .schalter input')
        effekt_waehlen(hp, 'aus', '14d-verbessern.png')
        f0 = frames(gp); time.sleep(2); f1 = frames(gp)
        print('Gast empfängt Bilder vom Host (verbessert):', f0, '→', f1)
        if f1 <= f0: fehler.append('Video kommt mit „Aussehen verbessern“ nicht beim Gast an')
        quellen = hp.evaluate("performance.getEntriesByType('resource').map(e => e.name)")
        fremd = [q for q in quellen if not q.startswith(B) and not q.startswith(('blob:', 'data:'))]
        mp = [q for q in quellen if '/vendor/mediapipe-' in q]
        print('MediaPipe-Dateien:', [q.rsplit('/', 1)[-1] for q in mp])
        if fremd: fehler.append(f'Fremde Quellen geladen: {fremd}')
        if not any(q.endswith('.wasm') for q in mp) or not any(q.endswith('.tflite') for q in mp): fehler.append('MediaPipe nicht selbst gehostet geladen')
        wasm = hp.request.get(next(q for q in mp if q.endswith('.wasm')))
        if wasm.headers.get('content-type') != 'application/wasm' or 'immutable' not in wasm.headers.get('cache-control', ''):
            fehler.append(f"WASM-Header: {wasm.headers.get('content-type')} / {wasm.headers.get('cache-control')}")
        # Host: Gast stummschalten über Teilnehmerliste
        hp.click('#s-leute'); time.sleep(0.5)
        hp.click('#panel-leute .leute-liste li:nth-child(2) .icon-btn')
        hp.screenshot(path=OUT + '15-aktionen.png')
        hp.click('.menue [role=menuitem] >> text=Stummschalten')
        time.sleep(2)
        gp.screenshot(path=OUT + '16-gast-stumm.png')
        # Hell-Modus Screenshot
        hp.evaluate("window.slxtModus.setzen('hell')"); time.sleep(0.5)
        hp.screenshot(path=OUT + '17-hell.png')
        hp.evaluate("window.slxtModus.setzen('dunkel')")
        # Beenden für alle
        hp.click('#s-verlassen'); hp.click('.menue [role=menuitem] >> text=Für alle beenden')
        hp.click('dialog .btn.gefahr')
        gp.wait_for_selector('#v-ende:not([hidden])', timeout=15000)
        hp.wait_for_selector('#v-ende:not([hidden])', timeout=15000)
        print('Gast-Ende:', gp.inner_text('#ende-titel'), '| Host-Ende:', hp.inner_text('#ende-titel'))
        gp.screenshot(path=OUT + '18-gast-ende.png')
        # PWA: im Cache nur App-Hülle/statische Dateien – nie API, Medien, MediaPipe
        gecacht = hp.evaluate("(async () => { const out = []; for (const k of await caches.keys()) for (const r of await (await caches.open(k)).keys()) out.push(new URL(r.url).pathname); return out; })()")
        print('Im Cache:', len(gecacht), 'Einträge')
        falsch = [u for u in gecacht if u.startswith(('/api', '/rtc', '/vendor')) or not u.startswith(('/assets/', '/icons/', '/offline.html', '/modus.js', '/manifest.webmanifest'))]
        if falsch: fehler.append(f'Falsch gecacht: {falsch}')
        # Offline: Seitenaufruf ohne Netz → „Keine Verbindung“, wieder online → Seite lädt
        host.set_offline(True)
        hp.goto(B + '/konto')
        hp.wait_for_selector('.offline-karte h1')
        titel = hp.inner_text('.offline-karte h1')
        print('Offline-Seite:', titel)
        if 'Keine Verbindung' not in titel: fehler.append('Offline-Seite fehlt')
        hp.screenshot(path=OUT + '19-offline.png')
        host.set_offline(False)      # „online“-Ereignis → die Offline-Seite lädt von selbst neu
        hp.wait_for_function("!document.querySelector('.offline-karte')", timeout=10000)
        print('Wieder online:', hp.url)
    except Exception:
        traceback.print_exc()
        fehler.append('Ablauf abgebrochen')
        for i, pg in enumerate(host.pages):
            pg.screenshot(path=OUT + f'fehler-host-{i}.png')
    print('\n'.join(fehler) or 'keine JS-Fehler')
    br.close()
import sys
sys.exit(1 if fehler else 0)
