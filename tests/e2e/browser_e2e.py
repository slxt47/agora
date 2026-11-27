# Browser-E2E (Playwright, Kamera/Mikro-Attrappe): Host + Gast, Warteraum, Video, Chat, Steuerung, Beenden.
# Lauf: siehe tests/e2e/lauf.sh
import re, json, time, traceback
import base64, hashlib, hmac, struct, zlib
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

# Bekannte Browser-Meldung aus @livekit/track-processors (Chromium räumt VideoFrames selbst auf) – kein Fehler von Agora
# „could not find published track“: livekit-client meldet unter Last eine verspätete Track-Info (>3 s) als Fehler,
# verbindet sich aber selbst weiter – kein Fehler von Agora (beobachtet 26./27.11.2026 bei Serverlast 4–5).
BEKANNT = lambda text: ('VideoFrame was garbage collected without being closed' in text
                        or 'could not find published track' in text)


def totp(geheim, versatz=0):
    """TOTP (RFC 6238, SHA-1, 6 Stellen, 30 s) selbst gerechnet – ohne pyotp."""
    k = base64.b32decode(geheim + '=' * (-len(geheim) % 8))
    d = hmac.new(k, struct.pack('>Q', int(time.time() // 30) + versatz), hashlib.sha1).digest()
    o = d[-1] & 15
    return str((struct.unpack('>I', d[o:o + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


def neuer_zeitschritt():
    """Warten, bis ein neuer 30-s-Schritt beginnt (der Server nimmt jeden Schritt nur einmal an)."""
    s = int(time.time() // 30)
    while int(time.time() // 30) == s:
        time.sleep(0.5)
    time.sleep(1)


def warten_js(seite, ausdruck, sekunden=10):
    """Wie wait_for_function, aber über evaluate (wait_for_function wertet den Text per eval aus – die CSP verbietet das)."""
    ende = time.time() + sekunden
    while time.time() < ende:
        try:
            if seite.evaluate(ausdruck):
                return
        except Exception:      # noqa: BLE001 – Seite lädt gerade neu
            pass
        time.sleep(0.25)
    raise AssertionError(f'Zeitüberschreitung: {ausdruck}')


def beobachten(page, wer):
    page.on('pageerror', lambda e: fehler.append(f'[{wer}] pageerror: {e}'))
    page.on('console', lambda m: m.type == 'error' and not BEKANNT(m.text) and fehler.append(f'[{wer}] console: {m.text}'))
    page.on('requestfailed', lambda r: print(f'[{wer}] Anfrage fehlgeschlagen: {r.url[:120]} {r.failure}'))
    page.on('response', lambda r: r.status >= 400 and print(f'[{wer}] HTTP {r.status} {r.request.method} {r.url[:120]}'))

with sync_playwright() as p:
    br = p.chromium.launch(args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'])
    host = br.new_context(locale='de-DE', viewport={'width': 1280, 'height': 800}, permissions=['camera', 'microphone'])
    hp = host.new_page(); beobachten(hp, 'host')
    try:
        hp.goto(B + '/')
        hp.screenshot(path=OUT + '01-start.png')
        # Monitoring: /api/metriken und /api/gesund?tief=1 nur lokal – über den Tunnel (CF-Connecting-IP) gibt es sie nicht
        for pfad in ('/api/metriken', '/api/gesund?tief=1'):
            tunnel = hp.request.get(B + pfad, headers={'CF-Connecting-IP': '203.0.113.7'})
            direkt = hp.request.get(B + pfad)
            print(f'{pfad}: über Tunnel {tunnel.status}, lokal {direkt.status}')
            if tunnel.status != 404: fehler.append(f'{pfad} über den Tunnel erreichbar ({tunnel.status})')
            if direkt.status != 200: fehler.append(f'{pfad} lokal nicht erreichbar ({direkt.status})')
        if 'agora_meetings_aktiv' not in hp.request.get(B + '/api/metriken').text(): fehler.append('Metriken unvollständig')
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
        # Kalender: Links je Meeting (Google/Outlook/Office 365/.ics) und privates Kalender-Abo im Profil
        hp.click('#liste-geplant [data-kalender]')
        hp.wait_for_selector('dialog .kal-link')
        kal = hp.eval_on_selector_all('dialog .kal-link', 'els => els.map(e => e.href)')
        print('Kalender-Links:', [k.split('?')[0] for k in kal])
        if not (any(k.startswith('https://calendar.google.com/calendar/render?action=TEMPLATE') for k in kal)
                and any('outlook.office.com' in k for k in kal) and any('outlook.live.com' in k for k in kal) and any(k.endswith('/ics') for k in kal)):
            fehler.append(f'Kalender-Links fehlen: {kal}')
        hp.screenshot(path=OUT + '04b-kalender.png')
        hp.click('dialog .blatt-kopf .icon-btn')
        hp.click('#tab-profil')
        hp.click('#kal-abo-an')
        hp.wait_for_selector('#kal-abo-url')
        feed = hp.request.get(hp.inner_text('#kal-abo-url').strip())
        if feed.status != 200 or 'SUMMARY:Wochenbesprechung' not in feed.text(): fehler.append(f'Kalender-Abo liefert nichts ({feed.status})')
        hp.screenshot(path=OUT + '04c-kalender-abo.png')
        hp.click('#tab-meetings')
        # Vorlage anlegen und daraus planen
        hp.click('#vorlage-neu')
        hp.wait_for_selector('dialog [name=name]')
        hp.fill('dialog [name=name]', 'Sprechstunde')
        hp.fill('dialog [name=titel]', 'Offene Sprechstunde')
        hp.click('dialog label.schalter:has([name=stumm_beitritt])')
        hp.click('dialog label.schalter:has([name=recht_bildschirm])')
        hp.screenshot(path=OUT + '04d-vorlage.png')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#liste-vorlagen li[data-vorlage]')
        hp.click('#liste-vorlagen li[data-vorlage] .aktionen button:has-text("Meeting planen")')
        hp.wait_for_selector('dialog [name=titel]')
        if hp.input_value('dialog [name=titel]') != 'Offene Sprechstunde': fehler.append('Vorlage füllt den Titel nicht vor')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#liste-geplant li .name:has-text("Offene Sprechstunde")')
        hp.screenshot(path=OUT + '04e-aus-vorlage.png', full_page=True)
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

        gast = br.new_context(locale='de-DE', viewport={'width': 390, 'height': 844}, permissions=['camera', 'microphone'], has_touch=True, is_mobile=True)
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
        # Verbindungsqualität: Popover „Verbindung“ an der eigenen Kachel mit echten getStats-Werten
        hp.click('#buehne .kv.spiegeln .kv-menue')
        hp.click('.menue [role=menuitem] >> text=Verbindung')
        hp.wait_for_selector('.menue .stat-popover .stat-liste', timeout=20000)
        werte = hp.inner_text('.menue .stat-popover').replace('\n', ' | ')
        print('Verbindung (Host):', werte)
        if 'ms' not in werte or 'kbit/s' not in werte: fehler.append(f'Verbindungs-Popover ohne Werte: {werte}')
        hp.screenshot(path=OUT + '12b-verbindung.png')
        hp.keyboard.press('Escape')
        # Sprecheransicht + Mehr-Menü
        hp.keyboard.press('Escape'); hp.keyboard.press('g'); time.sleep(1)
        hp.screenshot(path=OUT + '13-sprecher.png')
        hp.click('#s-mehr'); time.sleep(0.3)
        hp.screenshot(path=OUT + '14-mehr.png')
        if hp.locator('.menue [role=menuitem]:has-text("Auf dem Server aufzeichnen")').count(): fehler.append('Server-Aufnahme sichtbar ohne AGORA_EGRESS')
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
        # Host sieht die Verbindungsqualität je Person (Punkt aus den Server-Zusammenfassungen)
        hp.wait_for_selector('#panel-leute .leute-liste li:nth-child(2) .stat-punkt', timeout=25000)
        print('Qualität Gast (Host-Sicht):', hp.get_attribute('#panel-leute .leute-liste li:nth-child(2) .stat-punkt', 'aria-label'))
        hp.click('#panel-leute .leute-liste li:nth-child(2) .icon-btn')
        hp.screenshot(path=OUT + '15-aktionen.png')
        hp.click('.menue [role=menuitem] >> text=Stummschalten')
        time.sleep(2)
        gp.screenshot(path=OUT + '16-gast-stumm.png')
        # Melden: Gast meldet den Host über die Teilnehmerliste → gespeichert, Mail an Admin, Hinweis (hier niemand sonst)
        gp.click('#s-leute'); time.sleep(0.5)
        gp.click('#panel-leute .leute-liste li:nth-child(2) .icon-btn')
        gp.click('.menue [role=menuitem] >> text=Melden')
        gp.wait_for_selector('dialog .melden-gruende')
        gp.click('dialog .melden-gruende label:has([value=stoerung])')
        gp.fill('dialog [name=text]', 'E2E-Test: stört')
        gp.screenshot(path=OUT + '16b-gast-melden.png')
        gp.click('dialog button[type=submit]')
        gp.wait_for_selector('.toast:has-text("Meldung ist angekommen")', timeout=10000)
        mails = hp.request.get(B + '/api/test/posteingang').json()['mails']
        if not any(m['an'] == 'host@example.com' and 'Meldung' in m['betreff'] and 'E2E-Test' in m['text'] for m in mails):
            fehler.append('Meldung: keine Mail an den Admin')
        gp.click('#leiste-zu')
        print('Meldung abgegeben, Mail an Admin')
        # Umfrage: Host legt an und startet, Gast stimmt ab, Host sieht live und teilt das Ergebnis
        hp.click('#tab-umfragen')
        hp.click('#panel-umfragen .leute-fuss .btn.primary')
        hp.wait_for_selector('dialog [name=frage]')
        hp.fill('dialog [name=frage]', 'Wann treffen wir uns?')
        hp.fill('dialog .umfrage-antworten li:nth-child(1) input', 'Montag')
        hp.fill('dialog .umfrage-antworten li:nth-child(2) input', 'Mittwoch')
        hp.click('dialog .umfrage-antworten + div button')
        hp.fill('dialog .umfrage-antworten li:nth-child(3) input', 'Freitag')
        hp.click('dialog label.schalter:has([name=anonym])')
        hp.screenshot(path=OUT + '17a-umfrage-neu.png')
        hp.click('dialog button[value=starten]')
        hp.wait_for_selector('#panel-umfragen .umfrage.laeuft')
        gp.click('.toast button:has-text("Abstimmen")', timeout=10000)      # Hinweis „Neue Umfrage“ → Seitenleiste
        gp.wait_for_selector('#panel-umfragen .umfrage-option', timeout=10000)
        gp.click('#panel-umfragen .umfrage-option >> nth=1')
        gp.screenshot(path=OUT + '17b-gast-umfrage.png')
        gp.click('#panel-umfragen .umfrage-form button[type=submit]')
        gp.wait_for_selector('#panel-umfragen .umfrage-eigene')
        hp.wait_for_selector('#panel-umfragen .umfrage-summe:has-text("1 haben")', timeout=10000)
        hp.screenshot(path=OUT + '17c-host-umfrage-live.png')
        hp.click('#panel-umfragen .umfrage-aktionen button:has-text("Ergebnis teilen")')
        gp.wait_for_selector('#panel-umfragen .umfrage-ergebnis li.mein', timeout=10000)
        print('Umfrage beim Gast:', gp.inner_text('#panel-umfragen .umfrage-ergebnis').replace('\n', ' | '))
        time.sleep(0.6)
        gp.screenshot(path=OUT + '17d-gast-ergebnis.png')
        hp.evaluate("window.slxtModus.setzen('hell')"); time.sleep(0.5)
        hp.screenshot(path=OUT + '17e-host-ergebnis-hell.png')
        hp.evaluate("window.slxtModus.setzen('dunkel')")
        # ==== Zusammenarbeit: Whiteboard – Host zeichnet (Stift, Rechteck, Notizzettel), zeigt es allen, Gast sieht es ====
        TINTE = """() => { const c = document.querySelector('.wb .wb-tafel'); if (!c || !c.width) return 0;
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0;
            for (let i = 0; i < d.length; i += 16) if (d[i] + d[i + 1] + d[i + 2] < 600) n++; return n; }"""
        if gp.locator('#seitenleiste:not([hidden])').count():
            gp.click('#leiste-zu')
        hp.click('#s-mehr'); hp.click('.menue [role=menuitem] >> text=Whiteboard')
        hp.wait_for_selector('.wb:not([hidden]) .wb-tafel')
        box = hp.locator('.wb .wb-tafel').bounding_box()
        def zug(punkte):
            hp.mouse.move(box['x'] + punkte[0][0], box['y'] + punkte[0][1]); hp.mouse.down()
            for x, y in punkte[1:]:
                hp.mouse.move(box['x'] + x, box['y'] + y, steps=6)
            hp.mouse.up()
        zug([(120, 160), (180, 220), (240, 170), (320, 260), (400, 180)])          # Stift
        hp.click('.wb-leiste [data-werkzeug=rechteck]'); hp.click('.wb-leiste [data-farbe="#2f80ed"]')
        zug([(460, 140), (640, 260)])
        hp.click('.wb-leiste [data-werkzeug=pfeil]')
        zug([(420, 320), (560, 300)])
        hp.click('.wb-leiste [data-werkzeug=notiz]'); hp.click('.wb-leiste [data-farbe="#f08c00"]')
        hp.mouse.click(box['x'] + 260, box['y'] + 360)
        hp.wait_for_selector('.wb .wb-editor.notiz')
        hp.keyboard.type('Ideen sammeln')
        hp.keyboard.press('Control+Enter')
        hp.wait_for_selector('.wb .wb-editor', state='detached')
        time.sleep(0.5)
        hp.screenshot(path=OUT + '17f-host-whiteboard.png')
        hp.click('.wb [data-aktion=zeigen]')
        gp.wait_for_selector('.wb:not([hidden]) .wb-tafel', timeout=10000)
        gp.wait_for_function(f'(n) => ({TINTE})() > n', arg=200, timeout=10000)
        print('Whiteboard beim Gast: Tinte', gp.evaluate(f'({TINTE})()'))
        time.sleep(0.5)
        gp.screenshot(path=OUT + '17g-gast-whiteboard.png')
        # Host sperrt → Gast darf nicht mehr zeichnen; wieder freigeben, Gast zeichnet, Host sieht es
        hp.click('.wb [data-aktion=sperren]')
        gp.wait_for_selector('.wb .wb-status:has-text("Nur ansehen")', timeout=10000)
        if not gp.locator('.wb-leiste [data-werkzeug=stift]').is_disabled(): fehler.append('Whiteboard gesperrt, Gast-Stift trotzdem aktiv')
        hp.click('.wb [data-aktion=sperren]')
        gp.wait_for_selector('.wb .wb-status', state='hidden', timeout=10000)
        vorher = hp.evaluate(f'({TINTE})()')
        gbox = gp.locator('.wb .wb-tafel').bounding_box()
        gp.click('.wb-leiste [data-werkzeug=stift]')
        unten = gbox['y'] + gbox['height'] - 110          # unterhalb der Hinweise (Toasts oben), oberhalb der Werkzeugleiste
        gp.mouse.move(gbox['x'] + 40, unten - 60); gp.mouse.down()
        gp.mouse.move(gbox['x'] + 250, unten, steps=8); gp.mouse.up()
        hp.wait_for_function(f'(n) => ({TINTE})() > n', arg=vorher, timeout=10000)
        hp.screenshot(path=OUT + '17h-host-whiteboard-gast.png')
        gp.click('.wb [data-aktion=schliessen]'); hp.click('.wb [data-aktion=schliessen]')
        print('Whiteboard: Host zeichnet, Gast sieht es; Sperre wirkt; Gast zeichnet, Host sieht es')
        # ==== Zusammenarbeit: Notizen – Host schreibt, Gast ergänzt, beide sehen den gemeinsamen Stand ====
        hp.click('#s-chat'); hp.click('#tab-notizen')
        hp.fill('#panel-notizen textarea', '# Agenda\n- Punkt A\n- [ ] Protokoll verschicken')
        hp.wait_for_selector('#panel-notizen .notizen-status[data-art="gespeichert"]', timeout=10000)
        gp.click('#s-chat'); gp.click('#tab-notizen')
        gp.wait_for_function("() => (document.querySelector('#panel-notizen textarea')?.value || '').includes('Punkt A')", timeout=10000)
        gp.click('#panel-notizen textarea'); gp.keyboard.press('Control+End')
        gp.keyboard.type('\n- Punkt B von **Anna**')
        hp.wait_for_function("() => document.querySelector('#panel-notizen textarea').value.includes('Punkt B von')", timeout=10000)
        gp.wait_for_selector('#panel-notizen .notizen-status[data-art="gespeichert"]', timeout=10000)
        hp.click('#panel-notizen .notizen-fuss button:has-text("Vorschau")')
        hp.wait_for_selector('#panel-notizen .notizen-vorschau li b:has-text("Anna")')
        print('Notizen (Host):', hp.input_value('#panel-notizen textarea').replace('\n', ' | '))
        time.sleep(0.3)
        hp.screenshot(path=OUT + '17i-host-notizen.png')
        gp.screenshot(path=OUT + '17j-gast-notizen.png')
        hp.click('#panel-notizen .notizen-fuss button:has-text("Bearbeiten")')
        # ==== Zusammenarbeit: Q&A – Gast fragt anonym, Host stimmt hoch und beantwortet ====
        gp.click('#tab-fragen')
        gp.fill('#panel-fragen textarea[name=frage]', 'Gibt es die Folien danach?')
        gp.click('#panel-fragen .fragen-anonym')
        gp.click('#panel-fragen .fragen-form button[type=submit]')
        gp.wait_for_selector('#panel-fragen .frage .frage-kopf b:has-text("Du (anonym)")', timeout=10000)
        hp.click('#tab-fragen')
        hp.wait_for_selector('#panel-fragen .frage:has-text("Folien")', timeout=10000)
        wer = hp.inner_text('#panel-fragen .frage .frage-kopf b')
        if wer != 'Anonym': fehler.append(f'Q&A: Host sieht bei anonymer Frage „{wer}“')
        hp.click('#panel-fragen .frage .fragen-stimme')
        gp.wait_for_selector('#panel-fragen .frage .fragen-stimme b:has-text("1")', timeout=10000)
        hp.click('#panel-fragen .frage .frage-aktionen button:has-text("Beantwortet")')
        hp.fill('dialog textarea[name=antwort]', 'Ja, kommen per Mail.')
        hp.click('dialog button[type=submit]')
        gp.wait_for_selector('#panel-fragen .frage.beantwortet .frage-antwort:has-text("per Mail")', timeout=10000)
        print('Q&A beim Gast:', gp.inner_text('#panel-fragen .frage').replace('\n', ' | '))
        time.sleep(0.3)
        hp.screenshot(path=OUT + '17k-host-fragen.png')
        gp.screenshot(path=OUT + '17l-gast-fragen.png')
        # ==== Zusammenarbeit: Dateien – Host teilt PDF und Bild, Gast sieht Vorschau und lädt herunter ====
        def png(b, hh, farbe):
            roh = b''.join(b'\x00' + bytes(farbe) * b for _ in range(hh))
            teil = lambda k, d: struct.pack('>I', len(d)) + k + d + struct.pack('>I', zlib.crc32(k + d) & 0xffffffff)
            return b'\x89PNG\r\n\x1a\n' + teil(b'IHDR', struct.pack('>IIBBBBB', b, hh, 8, 2, 0, 0, 0)) + teil(b'IDAT', zlib.compress(roh)) + teil(b'IEND', b'')
        pdf = b'%PDF-1.4\n% Agora E2E\n'
        hp.click('#tab-dateien')
        hinweis = hp.inner_text('#panel-dateien .dateien-hinweis')
        if 'Nicht auf Viren geprüft' not in hinweis: fehler.append(f'Dateien: Hinweis fehlt ({hinweis})')
        hp.set_input_files('#panel-dateien input[type=file]', files=[
            {'name': 'Folien Q4.pdf', 'mimeType': 'application/pdf', 'buffer': pdf},
            {'name': 'skizze.png', 'mimeType': 'image/png', 'buffer': png(64, 40, (109, 74, 255))}])
        hp.wait_for_selector('#panel-dateien .datei:has-text("skizze.png")', timeout=15000)
        gp.click('#tab-dateien')
        gp.wait_for_selector('#panel-dateien .datei:has-text("Folien Q4.pdf")', timeout=10000)
        gp.wait_for_function("() => { const i = document.querySelector('#panel-dateien .dateien-bild'); return !!i && i.complete && i.naturalWidth === 64; }", timeout=10000)
        with gp.expect_download() as dl:
            gp.click('#panel-dateien .datei:has-text("Folien Q4.pdf") [data-aktion=laden]')
        datei = dl.value
        inhalt = open(datei.path(), 'rb').read()
        print('Datei beim Gast:', datei.suggested_filename, len(inhalt), 'Bytes')
        if inhalt != pdf or datei.suggested_filename != 'Folien Q4.pdf': fehler.append(f'Download falsch: {datei.suggested_filename} {inhalt[:20]!r}')
        ohne = gast.request.get(f'{B}/api/raeume/{code}/dateien')
        if ohne.status != 403: fehler.append(f'Dateiliste ohne Teilnahme: {ohne.status}')
        time.sleep(0.3)
        hp.screenshot(path=OUT + '17m-host-dateien.png')
        gp.screenshot(path=OUT + '17n-gast-dateien.png')
        gp.click('#leiste-zu')
        # Breakout-Räume: Host legt 2 Räume an (automatisch, Timer), öffnet; Gast landet im Breakout-Raum und ruft Hilfe;
        # Host besucht ihn über den Hinweis, schickt eine Nachricht an alle Räume und schließt (60 s Countdown) → alle zurück
        def warten_bis(seite, sel, anzahl, sekunden=20):
            ende = time.time() + sekunden
            while time.time() < ende:
                if seite.locator(sel).count() == anzahl:
                    return True
                time.sleep(0.3)
            raise AssertionError(f'{sel}: {seite.locator(sel).count()} statt {anzahl}')
        hp.click('#s-mehr'); hp.click('.menue [role=menuitem] >> text=Breakout-Räume')
        hp.wait_for_selector('dialog input[name=anzahl]')
        hp.fill('dialog input[name=anzahl]', '2')
        hp.click('dialog label.schalter:has([name=timer])')
        hp.fill('dialog input[name=minuten]', '5')
        hp.screenshot(path=OUT + '18a-breakout-anlegen.png')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('dialog .breakout-karte:not(.ohne) .breakout-chip')
        hp.screenshot(path=OUT + '18b-breakout-zuteilung.png')
        hp.click('dialog .breakout-knoepfe button:has-text("Räume öffnen")')
        hp.wait_for_selector('dialog .breakout-karte button:has-text("Besuchen")')
        hp.screenshot(path=OUT + '18c-breakout-offen.png')
        hp.click('dialog .blatt-kopf .icon-btn')
        if gp.locator('#seitenleiste:not([hidden])').count():
            gp.click('#leiste-zu')                           # Handy: Seitenleiste deckt die Kopfzeile ab
        gp.wait_for_selector('#m-breakout-hilfe:not([hidden])', timeout=20000)
        warten_bis(gp, '#buehne .kv', 1)                  # nur noch man selbst: Gast ist im Breakout-Raum
        print('Gast im Breakout:', gp.inner_text('#m-breakout').replace('\n', ' '))
        time.sleep(1)
        gp.screenshot(path=OUT + '18d-gast-breakout.png')
        gp.click('#m-breakout-hilfe')
        hp.click('.toast:has-text("um Hilfe") button:has-text("Besuchen")', timeout=10000)
        hp.wait_for_selector('#m-breakout:has-text("Breakout-Raum")', timeout=20000)
        warten_bis(hp, '#buehne .kv.mit-video', 2)         # Host sieht das Video des Gastes im Breakout-Raum
        warten_bis(gp, '#buehne .kv', 2)
        time.sleep(1)
        hp.screenshot(path=OUT + '18e-host-besucht.png')
        hp.click('#m-breakout'); hp.click('.menue [role=menuitem] >> text=Breakout-Räume')
        hp.fill('dialog input[name=nachricht]', 'Noch 5 Minuten!')
        hp.click('dialog .breakout-nachricht button[type=submit]')
        gp.wait_for_selector('.toast:has-text("Noch 5 Minuten!")', timeout=10000)
        hp.click('dialog .breakout-knoepfe button:has-text("Alle Räume schließen")')
        hp.click('dialog .btn.gefahr.voll')
        gp.wait_for_selector('#m-banner:has-text("schließen in")', timeout=10000)
        hp.click('dialog .blatt-kopf .icon-btn')
        time.sleep(1)
        gp.screenshot(path=OUT + '18f-gast-countdown.png')
        hp.screenshot(path=OUT + '18g-host-countdown.png')
        gp.wait_for_selector('#m-breakout', state='hidden', timeout=90000)
        hp.wait_for_selector('#m-breakout', state='hidden', timeout=30000)
        warten_bis(gp, '#buehne .kv', 2, 30)
        warten_bis(hp, '#buehne .kv.mit-video', 2, 30)
        print('Breakout geschlossen, beide wieder im Hauptraum')
        time.sleep(1)
        gp.screenshot(path=OUT + '18h-gast-zurueck.png')
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
        # Rückmeldung am Ende: Gast 4 Sterne + Problem + Text, Host 5 Sterne → Konto zeigt den Schnitt 4,5
        gp.wait_for_selector('#v-ende .feedback', timeout=10000)
        gp.click('#v-ende .feedback-stern:nth-child(4)')
        gp.click('#v-ende .feedback-chip:has([value=video])')
        gp.fill('#v-ende .feedback textarea', 'Bild war kurz unscharf')
        gp.screenshot(path=OUT + '18i-gast-feedback.png', full_page=True)
        gp.click('#v-ende .feedback button[type=submit]')
        gp.wait_for_selector('#v-ende .feedback-danke', timeout=10000)
        hp.click('#v-ende .feedback-stern:nth-child(5)')
        hp.click('#v-ende .feedback button[type=submit]')
        hp.wait_for_selector('#v-ende .feedback-danke', timeout=10000)
        hp.goto(B + '/konto')
        hp.wait_for_selector('#liste-vergangen .qualitaet-marke', timeout=10000)
        marke = hp.inner_text('#liste-vergangen .qualitaet-marke')
        print('Qualität im Konto:', marke.replace('\n', ' '))
        if '4,5' not in marke: fehler.append(f'Konto zeigt keinen Schnitt 4,5: {marke}')
        hp.wait_for_selector('#liste-verlauf .qualitaet-verlauf', timeout=10000)
        im_verlauf = hp.inner_text('#liste-verlauf .qualitaet-verlauf')
        print('Qualität im Meeting-Verlauf:', im_verlauf)
        if '4,5' not in im_verlauf: fehler.append(f'Meeting-Verlauf zeigt keinen Schnitt 4,5: {im_verlauf}')
        hp.click('#liste-vergangen button:has-text("Qualität")')
        hp.wait_for_selector('dialog .qualitaet-tabelle')
        hp.screenshot(path=OUT + '18j-konto-qualitaet.png')
        hp.keyboard.press('Escape')
        # Meeting-Verlauf: das beendete Meeting steht unter „Vergangene Meetings“
        hp.goto(B + '/konto#meetings')
        hp.wait_for_selector('#liste-verlauf li[data-verlauf] .name:has-text("Testmeeting")', timeout=10000)
        print('Verlauf:', hp.inner_text('#liste-verlauf li[data-verlauf]').replace('\n', ' | '))
        hp.screenshot(path=OUT + '18i-verlauf.png', full_page=True)
        # Zwei-Faktor-Anmeldung: im Profil einschalten (QR lokal gezeichnet, Code im Test berechnet), abmelden,
        # mit Passwort + Code wieder anmelden
        hp.goto(B + '/konto#profil')
        hp.click('#zf-einschalten')
        hp.fill('dialog [name=passwort]', 'geheimespasswort1')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('dialog .zf-qr svg path')
        hp.click('dialog .zf-geheim summary')
        geheim = hp.inner_text('#zf-geheim').replace(' ', '').strip()
        hp.screenshot(path=OUT + '20a-2fa-qr.png')
        hp.fill('dialog [name=code]', totp(geheim))
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('dialog .zf-codes li')
        n_codes = hp.locator('dialog .zf-codes li').count()
        if n_codes != 10: fehler.append(f'{n_codes} statt 10 Wiederherstellungscodes')
        hp.screenshot(path=OUT + '20b-2fa-codes.png')
        hp.click('#zf-fertig')
        hp.wait_for_selector('#zwei-faktor .marker.ok')
        hp.click('#abmelden')
        hp.wait_for_selector('#abgemeldet:not([hidden])')
        neuer_zeitschritt()
        hp.fill('#f-anmelden [name=email]', 'host@example.com')
        hp.fill('#f-anmelden [name=passwort]', 'geheimespasswort1')
        hp.click('#f-anmelden button[type=submit]')
        hp.wait_for_selector('#f-code:not([hidden])')
        hp.screenshot(path=OUT + '20c-2fa-anmelden.png')
        hp.fill('#f-code [name=code]', totp(geheim))
        hp.click('#f-code button[type=submit]')
        hp.wait_for_selector('#angemeldet:not([hidden])')
        print('2FA: eingeschaltet, mit Code angemeldet')
        # Sicherheit im Konto: eigene Ereignisse (2FA eingeschaltet, Anmeldung mit Code)
        hp.goto(B + '/konto#profil')
        hp.wait_for_selector('#liste-sicherheit li[data-art="anmeldung_2fa"]')
        hp.wait_for_selector('#liste-sicherheit li[data-art="zwei_faktor_an"]', state='attached')
        hp.locator('#liste-sicherheit').scroll_into_view_if_needed()
        hp.screenshot(path=OUT + '20d-sicherheit.png')
        print('Sicherheit:', hp.locator('#liste-sicherheit li').count(), 'Ereignisse')
        # Admin-Bereich (host@example.com steht in AGORA_ADMIN_MAILS): Link im Konto, Übersicht, Meldung erledigen,
        # Konten, Protokoll mit der Admin-Aktion; für andere 404
        if gast.request.get(B + '/admin').status != 404 or gast.request.get(B + '/api/admin/uebersicht').status != 404:
            fehler.append('Admin-Bereich für Nicht-Admins sichtbar')
        hp.goto(B + '/konto')
        hp.click('#admin-link')
        hp.wait_for_selector('#zahlen .admin-kachel')
        time.sleep(0.5)
        hp.screenshot(path=OUT + '21a-admin-uebersicht.png', full_page=True)
        hp.click('#tab-meldungen')
        hp.wait_for_selector('#liste-meldungen li[data-meldung]')
        hp.screenshot(path=OUT + '21b-admin-meldungen.png', full_page=True)
        hp.click('#liste-meldungen li[data-meldung] .btn.primary')
        hp.fill('dialog [name=notiz]', 'E2E: geprüft')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#liste-meldungen li.leer')
        hp.click('#tab-konten')
        hp.wait_for_selector('#liste-konten li[data-konto]')
        hp.screenshot(path=OUT + '21c-admin-konten.png', full_page=True)
        hp.click('#tab-protokoll')
        hp.wait_for_selector('#tabelle-protokoll tbody tr.admin-zeile')
        if 'Admin: Meldung' not in hp.inner_text('#tabelle-protokoll'): fehler.append('Admin-Aktion fehlt im Protokoll')
        hp.screenshot(path=OUT + '21d-admin-protokoll.png')
        hp.evaluate("window.slxtModus.setzen('hell')"); hp.click('#tab-uebersicht'); time.sleep(0.5)
        hp.screenshot(path=OUT + '21e-admin-hell.png')
        hp.evaluate("window.slxtModus.setzen('dunkel')")
        print('Admin-Bereich: Übersicht, Meldung erledigt, Konten, Protokoll')
        # ==== Webinar + Registrierung: Anmeldeseite, Mail mit persönlichem Link, Zuschauer sendet nichts, aufs Podium holen ====
        r = hp.evaluate("""async () => (await (await fetch('/api/raeume', {method:'POST', headers:{'X-Agora':'1','Content-Type':'application/json'}, body: JSON.stringify({titel:'Webinar-Test'})})).json())""")
        wcode = r['raum']['code']
        hp.goto(B + '/konto')
        hp.click(f'[data-webinar="{wcode}"]')
        hp.wait_for_selector('dialog .webinar-form')
        hp.click('dialog label.schalter:has([name=an])')
        hp.click('dialog label.schalter:has([name=registrierung])')
        hp.fill('dialog [name=kapazitaet]', '50')
        hp.click('dialog .webinar-form button:has-text("Frage hinzufügen")')
        hp.fill('dialog .webinar-fragen li:nth-child(1) input', 'Aus welcher Firma kommst du?')
        hp.click('dialog .webinar-form button[type=submit]')
        hp.wait_for_selector('dialog .webinar-anmeldungen .link-feld')
        hp.fill('dialog .webinar-panel-neu input', 'Referentin')
        hp.click('dialog .webinar-panel-neu button')
        hp.wait_for_selector('dialog .webinar-panel li[data-panel]')
        hp.screenshot(path=OUT + '22a-webinar-einstellungen.png')
        hp.keyboard.press('Escape')
        zuschauer = br.new_context(locale='de-DE', viewport={'width': 1100, 'height': 760}, permissions=['camera', 'microphone'])
        zp = zuschauer.new_page(); beobachten(zp, 'zuschauer')
        zp.goto(f'{B}/w/{wcode}')
        zp.wait_for_selector('#f-anmelden:not([hidden])')
        zp.fill('#f-anmelden [name=name]', 'Zoe Zuschauerin')
        zp.fill('#f-anmelden [name=email]', 'zoe@example.com')
        zp.fill('#f-anmelden [name=frage0]', 'slxt')
        zp.screenshot(path=OUT + '22b-webinar-anmeldung.png')
        zp.click('#f-anmelden button[type=submit]')
        zp.wait_for_selector('#status.ok')
        zp.screenshot(path=OUT + '22c-webinar-angemeldet.png')
        mails = hp.request.get(B + '/api/test/posteingang').json()['mails']
        wlink = re.search(r'https?://\S+/r/' + wcode + r'\?anmeldung=[\w-]+', [m for m in mails if m['an'] == 'zoe@example.com'][-1]['text']).group(0)
        # Ohne persönlichen Link kein Zutritt
        ohne = zp.request.post(f'{B}/api/raeume/{wcode}/beitreten', data=json.dumps({'name': 'X'}), headers={'X-Agora': '1', 'Content-Type': 'application/json'})
        if ohne.status != 403: fehler.append(f'Webinar ohne Anmeldung betretbar ({ohne.status})')
        hp.goto(f'{B}/r/{wcode}')
        hp.wait_for_selector('#v-lobby:not([hidden])')
        hp.click('#beitreten-knopf')
        hp.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        zp.goto(wlink)
        zp.wait_for_selector('#v-lobby:not([hidden])')
        zp.fill('#f-beitritt [name=name]', 'Zoe Zuschauerin')
        zp.screenshot(path=OUT + '22d-webinar-lobby.png')
        zp.click('#beitreten-knopf')
        zp.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        zp.wait_for_selector('body.webinar-zuschauer')
        time.sleep(3)
        if zp.locator('#s-mic').is_visible(): fehler.append('Zuschauer sieht den Mikrofon-Knopf')
        if zp.evaluate("[...document.querySelectorAll('#buehne .kv')].length") != 1: fehler.append('Zuschauer sieht nicht nur das Panel')
        warten_bis(hp, '#buehne .kv', 1)                   # Host sieht nur das Panel (sich selbst), nicht die Zuschauerin
        hp.wait_for_selector('#m-webinar:has-text("1 Zuschauer")', timeout=25000)
        hp.screenshot(path=OUT + '22e-webinar-host.png')
        zp.screenshot(path=OUT + '22f-webinar-zuschauer.png')
        # Aufs Podium holen → Zuschauerin darf senden, Kachel erscheint beim Host
        hp.click('#s-leute'); time.sleep(0.5)
        hp.click('#panel-leute .leute-liste li:nth-child(2) .icon-btn')
        hp.click('.menue [role=menuitem] >> text=Aufs Podium holen')
        zp.click('.toast button:has-text("Mikrofon an")', timeout=10000)
        zp.wait_for_selector('body:not(.webinar-zuschauer)')
        zp.click('#s-cam')
        warten_bis(hp, '#buehne .kv', 2, 20)
        warten_bis(hp, '#buehne .kv.mit-video', 2, 20)
        print('Webinar: Zuschauerin aufs Podium geholt, sendet Video')
        hp.screenshot(path=OUT + '22g-webinar-podium.png')
        # … und zurück zu den Zuschauern
        hp.click('#panel-leute .leute-liste li:nth-child(2) .icon-btn')
        hp.click('.menue [role=menuitem] >> text=Zu den Zuschauern')
        zp.wait_for_selector('body.webinar-zuschauer', timeout=10000)
        warten_bis(hp, '#buehne .kv', 1, 20)
        liste = hp.request.get(f'{B}/api/raeume/{wcode}/webinar/anmeldungen').json()['anmeldungen']
        if not liste or not liste[0]['beigetreten'] or liste[0]['antworten'] != ['slxt']: fehler.append(f'Anmeldeliste falsch: {liste}')
        csv = hp.request.get(f'{B}/api/raeume/{wcode}/webinar/anmeldungen.csv').text()
        if 'zoe@example.com' not in csv: fehler.append('CSV ohne Anmeldung')
        # Webinar für alle beenden
        hp.click('#s-verlassen'); hp.click('.menue [role=menuitem] >> text=Für alle beenden')
        hp.click('dialog .btn.gefahr')
        zp.wait_for_selector('#v-ende:not([hidden])', timeout=15000)
        time.sleep(1)                                     # Kontext offen lassen: Schließen mitten im Abbau meldet DataChannel-Fehler
        # ==== Live-Untertitel: Anzeige an, Abschnitt über die API (Web Speech gibt es headless nicht), Zustimmung, Transkript am Ende ====
        r = hp.evaluate("""async () => (await (await fetch('/api/raeume', {method:'POST', headers:{'X-Agora':'1','Content-Type':'application/json'}, body: JSON.stringify({titel:'Untertitel-Test'})})).json())""")
        ucode = r['raum']['code']
        hp.request.patch(f'{B}/api/raeume/{ucode}/transkript', data=json.dumps({'speichern': True}), headers={'X-Agora': '1', 'Content-Type': 'application/json'})
        hp.goto(f'{B}/r/{ucode}')
        hp.wait_for_selector('#v-lobby:not([hidden])')
        hp.click('#beitreten-knopf')
        hp.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        hp.wait_for_selector('.toast:has-text("als Transkript")', timeout=10000)      # Hinweis „Transkript wird gespeichert“
        api_gast = p.request.new_context(base_url=B, extra_http_headers={'X-Agora': '1'})
        gzug = api_gast.post(f'/api/raeume/{ucode}/beitreten', data={'name': 'Berta API'}).json()['zugang']
        hp.click('#s-mehr'); hp.click('.menue [role^=menuitem] >> text=Untertitel anzeigen')
        r = api_gast.post(f'/api/raeume/{ucode}/untertitel', data={'text': 'Hallo aus der Untertitel-API', 'final': True, 'dauer_ms': 1500},
                          headers={'X-Teilnahme': gzug['teilnahme']})
        if r.status != 200: fehler.append(f'Untertitel senden: {r.status}')
        hp.wait_for_selector('#untertitel .ut-zeile:has-text("Berta API: Hallo aus der Untertitel-API")', timeout=10000)
        hp.screenshot(path=OUT + '23a-untertitel.png')
        # Eigene Sprache: nie ohne Zustimmung – Dialog (oder Hinweis, wenn der Browser es nicht kann)
        hp.click('#s-mehr'); hp.click('.menue [role^=menuitem] >> text=Meine Sprache transkribieren')
        hp.wait_for_selector('dialog:has-text("Live-Untertitel für deine Sprache"), .toast:has-text("keine Spracherkennung")', timeout=10000)
        if hp.locator('dialog:has-text("Live-Untertitel für deine Sprache")').count():
            if 'Google' not in hp.inner_text('dialog'): fehler.append('Zustimmungsdialog ohne Hinweis auf Google')
            hp.screenshot(path=OUT + '23b-untertitel-zustimmung.png')
            hp.click('dialog button:has-text("Abbrechen")')
            print('Untertitel: Zustimmungsdialog gezeigt, abgelehnt')
        # Übersetzen nur auf dem Gerät: Zielsprachen (mit Translator-API) oder klarer Hinweis (ohne) – nie ein fremder Dienst
        hp.click('#s-mehr'); hp.click('.menue [role^=menuitem] >> text=Untertitel übersetzen')
        hp.wait_for_selector('dialog #ut-ueb-hinweis, dialog .ut-ziele', timeout=10000)
        hp.screenshot(path=OUT + '23c-untertitel-uebersetzen.png')
        print('Untertitel übersetzen:', 'Hinweis (keine Translator-API)' if hp.locator('dialog #ut-ueb-hinweis').count() else 'Zielsprachen angeboten')
        hp.click('dialog .blatt-kopf .icon-btn')
        hp.click('#s-verlassen'); hp.click('.menue [role=menuitem] >> text=Für alle beenden')
        hp.click('dialog .btn.gefahr')
        hp.wait_for_selector('#v-ende .ende-transkript a', timeout=15000)
        txt = hp.request.get(hp.get_attribute('#v-ende .ende-transkript a', 'href') if hp.get_attribute('#v-ende .ende-transkript a', 'href').startswith('http') else B + hp.get_attribute('#v-ende .ende-transkript a', 'href')).text()
        print('Transkript am Ende:', txt.strip().splitlines()[-1])
        if 'Berta API: Hallo aus der Untertitel-API' not in txt: fehler.append('Transkript fehlt')
        hp.screenshot(path=OUT + '23c-transkript-ende.png')
        api_gast.dispose()
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
        try:
            warten_js(hp, "!document.querySelector('.offline-karte')")
        except AssertionError:      # selten: das „online“-Ereignis kommt, bevor das Netz wirklich da ist → „Erneut versuchen“
            print('Offline-Seite lädt nicht von selbst neu – „Erneut versuchen“')
            hp.click('#erneut')
            warten_js(hp, "!document.querySelector('.offline-karte')")
        print('Wieder online:', hp.url)
        # ==== Block E2EE: Host schaltet in der Lobby ein (Schlüssel im Fragment), Gast mit Link entschlüsselt das Video,
        # Gast mit falschem Schlüssel bekommt eine klare Meldung und kommt nicht hinein
        ep = host.new_page(); beobachten(ep, 'host-e2ee')
        ep.goto(B + '/')
        r = ep.evaluate("""async () => (await (await fetch('/api/raeume', {method:'POST', headers:{'X-Agora':'1','Content-Type':'application/json'}, body: JSON.stringify({titel:'Geheimrunde'})})).json())""")
        ecode = r['raum']['code']
        ep.goto(f'{B}/r/{ecode}')
        ep.wait_for_selector('#feld-e2ee:not([hidden]) input[name=e2ee]')
        ep.click('#feld-e2ee label.schalter')
        ep.wait_for_selector('#merkmal-e2ee')
        elink = ep.url
        if not re.search(r'#k=[A-Za-z0-9_-]{43}$', elink): fehler.append(f'E2EE: kein Schlüssel im Link ({elink})')
        info = ep.request.get(f'{B}/api/raeume/{ecode}').json()['raum']
        if not info['e2ee'] or elink.split('#k=')[-1] in json.dumps(info): fehler.append('E2EE: Server-Zustand falsch oder Schlüssel beim Server')
        ep.screenshot(path=OUT + '30a-e2ee-lobby-host.png')
        ep.click('#beitreten-knopf')
        ep.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        ep.wait_for_selector('#m-e2ee:not([hidden])', timeout=10000)
        eg = br.new_context(locale='de-DE', viewport={'width': 1024, 'height': 700}, permissions=['camera', 'microphone'])
        egp = eg.new_page(); beobachten(egp, 'gast-e2ee')
        egp.goto(elink)
        egp.wait_for_selector('#merkmal-e2ee')
        if egp.is_visible('#feld-schluessel'): fehler.append('E2EE: Lobby fragt trotz Schlüssel im Link')
        egp.fill('#f-beitritt [name=name]', 'Eva Verschlüsselt')
        egp.click('#beitreten-knopf')
        egp.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        egp.wait_for_selector('#m-e2ee:not([hidden])', timeout=10000)
        egp.wait_for_selector('#buehne .kv.mit-video:not(.spiegeln) video', timeout=20000)
        time.sleep(2)
        f0 = frames(egp); time.sleep(3); f1 = frames(egp)
        print('E2EE: Gast entschlüsselt Bilder vom Host:', f0, '→', f1)
        if f1 <= f0: fehler.append('E2EE: Video kommt beim Gast nicht entschlüsselt an')
        if egp.is_visible('#m-banner.err'): fehler.append('E2EE: Entschlüsselungsfehler beim Gast mit richtigem Schlüssel: ' + egp.inner_text('#m-banner'))
        egp.screenshot(path=OUT + '30b-e2ee-gast-meeting.png')
        ep.screenshot(path=OUT + '30c-e2ee-host-meeting.png')
        # falscher Schlüssel im Link → Lobby meldet es, Feld für den richtigen erscheint
        fg = br.new_context(locale='de-DE', viewport={'width': 1024, 'height': 700}, permissions=['camera', 'microphone'])
        fgp = fg.new_page(); beobachten(fgp, 'gast-falsch')
        fgp.goto(f'{B}/r/{ecode}#k=' + 'A' * 43)
        fgp.wait_for_selector('#merkmal-e2ee')
        fgp.fill('#f-beitritt [name=name]', 'Falscher Schlüssel')
        fgp.click('#beitreten-knopf')
        fgp.wait_for_selector('#beitritt-status.err:has-text("passt nicht")', timeout=10000)
        if not fgp.is_visible('#feld-schluessel'): fehler.append('E2EE: Schlüsselfeld fehlt nach falschem Schlüssel')
        if not fgp.is_visible('#v-lobby'): fehler.append('E2EE: Gast mit falschem Schlüssel ist trotzdem beigetreten')
        print('E2EE: falscher Schlüssel →', fgp.inner_text('#beitritt-status'))
        fgp.screenshot(path=OUT + '30d-e2ee-falscher-schluessel.png')
        fg.close()
        # ==== Ende Block E2EE ====
        # ==== Block Audio-Modi: Lobby-Menü, Originalton (Musik) im Meeting, starke Rauschunterdrückung (RNNoise, selbst gehostet)
        lg = br.new_context(locale='de-DE', viewport={'width': 1024, 'height': 700}, permissions=['camera', 'microphone'])
        lgp = lg.new_page(); beobachten(lgp, 'lobby-audio')
        lgp.goto(elink)
        lgp.wait_for_selector('#lobby-audio')
        lgp.click('#lobby-audio')
        lgp.click('.menue .audio-menue label.schalter:has([name=musik])')
        warten_js(lgp, "JSON.parse(localStorage.getItem('agora-audio') || '{}').musik === true")
        if not lgp.is_disabled('.menue .audio-menue select[name=rauschen]'): fehler.append('Audio: Rauschunterdrückung im Musikmodus nicht gesperrt')
        time.sleep(1)
        lgp.screenshot(path=OUT + '31a-audio-lobby.png')
        lg.close()
        egp.click('#s-mehr'); egp.click('.menue [role=menuitemcheckbox]:has-text("Originalton (Musik)")')
        egp.wait_for_selector('.toast:has-text("Originalton an")', timeout=10000)
        egp.wait_for_selector('body[data-mikrofon="roh"]', state='attached', timeout=10000)
        print('Audio: Originalton an, Mikrofon ohne Verarbeitung')
        # wieder aus – diesmal über die Einstellungen (Schalter im Blatt)
        egp.click('#s-mehr'); egp.click('.menue [role=menuitem]:has-text("Einstellungen")')
        egp.wait_for_selector('dialog input[name=musik]')
        if not egp.is_checked('dialog input[name=musik]'): fehler.append('Audio: Musikmodus in den Einstellungen nicht an')
        egp.click('dialog label.schalter:has([name=musik])')
        try:
            egp.wait_for_selector('body[data-mikrofon="sprache"]', state='attached', timeout=15000)
        except Exception:
            print('Audio-Diagnose:', egp.evaluate("[document.body.dataset.mikrofon, localStorage.getItem('agora-audio'), [...document.querySelectorAll('.toast')].map(t => t.textContent).join(' | ')]"))
            raise
        egp.wait_for_selector('dialog select[name=rauschen]:not([disabled])')
        egp.select_option('dialog select[name=rauschen]', 'stark')
        egp.wait_for_selector('body[data-rauschfilter="aktiv"]', state='attached', timeout=20000)
        egp.screenshot(path=OUT + '31b-audio-einstellungen.png')
        egp.keyboard.press('Escape')
        quellen = egp.evaluate("performance.getEntriesByType('resource').map(e => e.name)")
        rn = [q.rsplit('/', 1)[-1] for q in quellen if '/vendor/rnnoise-' in q]
        print('RNNoise-Dateien:', rn, '| Zustand:', egp.get_attribute('body', 'data-rauschfilter'))
        if not any(q.endswith('.wasm') for q in rn): fehler.append(f'RNNoise nicht selbst gehostet geladen: {rn}')
        # Das Worklet (addModule) taucht nicht in den Resource-Timings auf → direkt prüfen
        wasm_url = next(q for q in quellen if '/vendor/rnnoise-' in q)
        wl = egp.request.get(wasm_url.rsplit('/', 1)[0] + '/rnnoise-worklet.js')
        if wl.status != 200 or 'javascript' not in wl.headers.get('content-type', '') or 'immutable' not in wl.headers.get('cache-control', ''):
            fehler.append(f"RNNoise-Worklet: {wl.status} {wl.headers.get('content-type')} {wl.headers.get('cache-control')}")
        fremd = [q for q in quellen if not q.startswith(B) and not q.startswith(('blob:', 'data:'))]
        if fremd: fehler.append(f'Fremde Quellen geladen: {fremd}')
        f0 = frames(ep); time.sleep(2); f1 = frames(ep)
        if f1 <= f0: fehler.append('Audio-Modi: Video des Gastes kommt beim Host nicht mehr an')
        # ==== Ende Block Audio-Modi ====
        # ==== Block Anmerkungen: Host teilt den Bildschirm, Gast zeichnet (Stift + Text), Host sieht es auf seiner Vorschau,
        # Host löscht alle und sperrt Anmerkungen → Gast kann nicht mehr
        ep.click('#s-teilen')
        ep.wait_for_selector('#buehne .kv.bildschirm.eigene-freigabe', timeout=20000)
        egp.wait_for_selector('#buehne .kv.bildschirm.mit-video .anm-knopf', timeout=20000)
        time.sleep(1.5)
        egp.click('#buehne .kv.bildschirm .anm-knopf')
        egp.wait_for_selector('#buehne .kv.bildschirm .anm-leiste[role=toolbar]')
        box = egp.locator('#buehne .kv.bildschirm canvas.anm-flaeche').bounding_box()
        x0, y0 = box['x'] + box['width'] * 0.2, box['y'] + box['height'] * 0.3
        egp.mouse.move(x0, y0); egp.mouse.down()
        for i in range(1, 15):
            egp.mouse.move(x0 + i * box['width'] * 0.03, y0 + (i % 4) * 6)
            time.sleep(0.03)
        egp.mouse.up()
        # Text per Tastatur-Werkzeugleiste: Pfeiltasten bis „Text“
        egp.focus('#buehne .anm-leiste [data-anm="stift"]')
        egp.keyboard.press('ArrowRight'); egp.keyboard.press('ArrowRight'); egp.keyboard.press('ArrowRight')
        if egp.evaluate("document.activeElement.dataset.anm") != 'text': fehler.append('Anmerkungen: Pfeiltasten in der Werkzeugleiste gehen nicht')
        egp.keyboard.press('Enter')
        egp.mouse.click(box['x'] + box['width'] * 0.5, box['y'] + box['height'] * 0.6)
        egp.wait_for_selector('#buehne .anm-text')
        egp.keyboard.type('Hier!')
        egp.keyboard.press('Enter')
        ep.wait_for_selector('#buehne .kv.bildschirm.eigene-freigabe .anm-ebene[data-striche="2"]', state='attached', timeout=10000)
        ep.wait_for_selector('#buehne .kv.bildschirm.eigene-freigabe .anm-ebene.mit-vorschau', timeout=5000)
        time.sleep(1)
        print('Anmerkungen: Strich + Text vom Gast beim Host angekommen')
        egp.screenshot(path=OUT + '32a-anmerkungen-gast.png')
        ep.screenshot(path=OUT + '32b-anmerkungen-host.png')
        # Vollbild der Kachel: Ebene skaliert mit
        egp.click('#buehne .kv.bildschirm .kv-menue')
        egp.click('.menue [role=menuitem]:has-text("Vollbild")')
        warten_js(egp, "!!document.fullscreenElement", 5)
        time.sleep(1)
        r1 = egp.evaluate("(() => { const c = document.querySelector('#buehne .kv.bildschirm canvas.anm-flaeche').getBoundingClientRect(); return [c.width, c.height]; })()")
        egp.screenshot(path=OUT + '32c-anmerkungen-vollbild.png')
        egp.evaluate("document.exitFullscreen().catch(() => {})")
        if r1[0] <= box['width']: fehler.append(f'Anmerkungen: Ebene wächst im Vollbild nicht mit ({r1} vs {box})')
        # Host: alle löschen und sperren
        ep.click('#buehne .kv.bildschirm.eigene-freigabe .anm-knopf')
        ep.click('#buehne .anm-leiste [data-anm="alle"]')
        egp.wait_for_selector('#buehne .kv.bildschirm .anm-ebene[data-striche="0"]', state='attached', timeout=10000)
        ep.click('#buehne .anm-leiste [data-anm="recht"]')
        egp.wait_for_selector('.toast:has-text("nur für Host")', timeout=10000)
        if egp.is_visible('#buehne .kv.bildschirm .anm-knopf'): fehler.append('Anmerkungen: Gast kann trotz Sperre anmerken')
        if egp.locator('#buehne .anm-leiste').count(): fehler.append('Anmerkungen: Werkzeugleiste beim Gast bleibt trotz Sperre offen')
        print('Anmerkungen: gelöscht und gesperrt')
        ep.keyboard.press('Escape')
        ep.click('#s-teilen')
        # ==== Ende Block Anmerkungen ====
        eg.close(); ep.close()      # E2EE-Meeting verlassen (sonst laufen dessen Ereignisse in die folgenden Blöcke)
        # ==== Block Englisch (ADR 0006): Browser mit Accept-Language en → englische Oberfläche ohne deutsche Reste ====
        DEUTSCH = re.compile(r'[äöüÄÖÜß„]|\b(und|oder|nicht|bitte|Bitte|der|die|das|mit|für|Meeting starten|Beitreten|Anmelden|'
                             r'Konto|Einstellungen|Teilnehmende|Kamera|Mikrofon|Verlassen|Stumm|Teilen|Aufnahmen|Speichern)\b')

        def deutsch_rest(seite, wo):
            """Sichtbaren Text (inkl. aria-label/title/placeholder) auf deutsche Reste prüfen."""
            text = seite.evaluate("""() => {
                const t = [document.body.innerText];
                for (const el of document.querySelectorAll('[aria-label],[title],[placeholder]')) {
                  if (el.closest('[hidden]') || el.closest('[aria-hidden=true]')) continue;
                  t.push(el.getAttribute('aria-label') || '', el.getAttribute('title') || '', el.getAttribute('placeholder') || '');
                }
                return t.join('\\n'); }""")
            rest = sorted({z.strip() for z in text.split('\n') if DEUTSCH.search(z) and 'Deutsch' not in z})
            if rest: fehler.append(f'Englisch ({wo}): deutsche Reste {rest[:8]}')
            return rest

        en = br.new_context(viewport={'width': 1280, 'height': 800}, permissions=['camera', 'microphone'], locale='en-US')
        enp = en.new_page(); beobachten(enp, 'en')
        enp.goto(B + '/')
        enp.wait_for_selector('#abgemeldet:not([hidden])')
        if enp.evaluate("document.documentElement.lang") != 'en': fehler.append('Englisch: <html lang> nicht en')
        if 'Meet without detours' not in enp.inner_text('h1'): fehler.append('Englisch: Startseite nicht übersetzt')
        if enp.inner_text('[data-sprach-schalter]') != 'DE': fehler.append('Englisch: Sprachschalter fehlt')
        if '(German)' not in enp.inner_text('footer'): fehler.append('Englisch: Hinweis (German) bei Rechtstexten fehlt')
        enp.screenshot(path=OUT + '40a-en-start.png')
        deutsch_rest(enp, 'Start')
        # API-Fehler auf Englisch
        enp.fill('#f-anmelden [name=email]', 'niemand@example.com')
        enp.fill('#f-anmelden [name=passwort]', 'falschfalsch1')
        enp.click('#f-anmelden button[type=submit]')
        enp.wait_for_selector('#status:not([hidden])')
        if 'Wrong email or password' not in enp.inner_text('#status'): fehler.append(f"Englisch: API-Fehler {enp.inner_text('#status')!r}")
        time.sleep(0.5)    # die absichtlich falsche Anmeldung (400) meldet der Browser als Konsolenfehler – hier gewollt
        fehler[:] = [f for f in fehler if not (f.startswith('[en] console: Failed to load resource') and '400' in f)]
        # Registrieren → Konto englisch, Bestätigungsmail englisch
        enp.click('#tab-registrieren')
        enp.fill('#f-registrieren [name=name]', 'Emma English')
        enp.fill('#f-registrieren [name=email]', 'emma@example.com')
        enp.fill('#f-registrieren [name=passwort]', 'geheimespasswort1')
        enp.click('#f-registrieren button[type=submit]')
        enp.wait_for_selector('#status.ok')
        m = [x for x in enp.request.get(B + '/api/test/posteingang').json()['mails'] if x['an'] == 'emma@example.com'][-1]
        if m['betreff'] != 'Agora: confirm your email address': fehler.append(f"Englisch: Mail-Betreff {m['betreff']!r}")
        enp.goto(re.search(r'https?://\S+bestaetigen\?t=[\w\-.]+', m['text']).group(0))
        enp.wait_for_selector('#angemeldet:not([hidden])')
        if enp.request.get(B + '/api/konto/ich').json()['ich']['sprache'] != 'en': fehler.append('Englisch: Konto-Sprache nicht en')
        deutsch_rest(enp, 'Start angemeldet')
        enp.goto(B + '/konto')
        enp.wait_for_selector('#liste-geplant')
        time.sleep(1.5)
        enp.screenshot(path=OUT + '40b-en-konto.png', full_page=True)
        deutsch_rest(enp, 'Konto Meetings')
        enp.click('#tab-profil')
        time.sleep(1)
        enp.screenshot(path=OUT + '40c-en-profil.png', full_page=True)
        deutsch_rest(enp, 'Konto Profil')
        # Sofort-Meeting: Lobby und Meeting
        enp.goto(B + '/')
        enp.click('#neues-meeting')
        enp.wait_for_selector('#v-lobby:not([hidden])')
        time.sleep(1.5)
        enp.screenshot(path=OUT + '40d-en-lobby.png')
        deutsch_rest(enp, 'Lobby')
        en_code = enp.url.split('/r/')[1].split('?')[0].split('#')[0]
        enp.click('#beitreten-knopf')
        enp.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        time.sleep(2)
        enp.click('#s-leute')
        time.sleep(1)
        enp.screenshot(path=OUT + '40e-en-meeting.png')
        deutsch_rest(enp, 'Meeting')
        enp.click('#s-mehr')
        time.sleep(0.5)
        enp.screenshot(path=OUT + '40f-en-mehr.png')
        deutsch_rest(enp, 'Meeting Mehr-Menü')
        enp.keyboard.press('Escape')
        # Gast ohne Konto mit englischem Browser
        eng = br.new_context(viewport={'width': 390, 'height': 844}, permissions=['camera', 'microphone'], locale='en-GB',
                             has_touch=True, is_mobile=True)
        engp = eng.new_page(); beobachten(engp, 'en-gast')
        engp.goto(f'{B}/r/{en_code}')
        engp.wait_for_selector('#v-lobby:not([hidden])')
        engp.fill('#f-beitritt [name=name]', 'Gary Guest')
        time.sleep(1)
        engp.screenshot(path=OUT + '40g-en-gast-lobby.png', full_page=True)
        deutsch_rest(engp, 'Gast-Lobby')
        engp.click('#beitreten-knopf')
        engp.wait_for_selector('#v-meeting:not([hidden]), #v-warten:not([hidden])', timeout=20000)
        time.sleep(2)
        engp.screenshot(path=OUT + '40h-en-gast.png')
        deutsch_rest(engp, 'Gast')
        eng.close()
        enp.click('#s-verlassen')
        time.sleep(1)
        en.close()
        # Sprachschalter: deutscher Browser → EN → bleibt nach Neuladen, DE zurück
        dsw = br.new_context(viewport={'width': 1024, 'height': 700}, locale='de-DE')
        dp = dsw.new_page(); beobachten(dp, 'schalter')
        dp.goto(B + '/')
        if dp.evaluate("document.documentElement.lang") != 'de': fehler.append('Sprachschalter: deutscher Browser nicht deutsch')
        dp.click('[data-sprach-schalter="en"]')
        dp.wait_for_selector('html[lang=en] [data-sprach-schalter="de"]')
        dp.reload()
        if dp.evaluate("document.documentElement.lang") != 'en': fehler.append('Sprachschalter: Wahl nicht gemerkt')
        dp.click('[data-sprach-schalter="de"]')
        dp.wait_for_selector('html[lang=de] [data-sprach-schalter="en"]')
        dsw.close()
        print('Englisch: Start, Konto, Lobby, Meeting, Gast, Sprachschalter geprüft')
        # ==== Ende Block Englisch ====
        # ==== Block Öffentliche API: Schlüssel im Konto anlegen (nur einmal sichtbar), damit per API ein Meeting planen ====
        hp.goto(B + '/konto#entwickler')
        hp.wait_for_selector('#p-entwickler:not([hidden])')
        hp.click('#api-neu')
        hp.wait_for_selector('dialog [name=name]')
        hp.fill('dialog [name=name]', 'E2E-Skript')
        hp.check('dialog [name=recht][value="meetings:schreiben"]')
        hp.screenshot(path=OUT + '50a-api-schluessel-neu.png')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#dev-geheim')
        api_token = hp.inner_text('#dev-geheim').strip()
        hp.screenshot(path=OUT + '50b-api-schluessel-einmal.png')
        hp.click('dialog button:has-text("Fertig")')
        hp.wait_for_selector('#liste-api li[data-schluessel] .name:has-text("E2E-Skript")')
        if not re.match(r'^agora_[\w-]{43}$', api_token): fehler.append(f'API-Schlüssel sieht falsch aus: {api_token[:10]}…')
        if api_token in hp.content(): fehler.append('API-Schlüssel steht nach dem Schließen noch in der Seite')
        api_kontext = p.request.new_context(base_url=B, extra_http_headers={'Authorization': f'Bearer {api_token}'})
        r = api_kontext.post('/api/v1/meetings', data={'titel': 'Per API geplant', 'warteraum': True,
                             'geplant': {'start': '2031-03-03T10:00', 'dauer_min': 45, 'zeitzone': 'Europe/Berlin', 'wiederholung': 'woechentlich'}})
        if r.status != 201: fehler.append(f'API: Meeting anlegen {r.status} {r.text()[:200]}')
        api_code = r.json()['meeting']['code']
        if api_code not in [m['code'] for m in api_kontext.get('/api/v1/meetings?art=geplant').json()['meetings']]: fehler.append('API: Meeting fehlt in der Liste')
        if p.request.new_context(base_url=B).get('/api/v1/meetings').status != 401: fehler.append('API ohne Schlüssel nicht 401')
        hp.goto(B + '/konto#meetings'); hp.reload()           # nur das Fragment geändert → neu laden
        hp.wait_for_selector(f'#liste-geplant li:has-text("{api_code}")')
        hp.goto(B + '/konto#entwickler'); hp.reload()
        hp.wait_for_selector('#liste-api li[data-schluessel] .info:has-text("zuletzt benutzt")')
        hp.screenshot(path=OUT + '50c-api-schluessel-liste.png', full_page=True)
        print('Öffentliche API: Schlüssel angelegt, Meeting', api_code, 'per API geplant')
        # ==== Ende Block Öffentliche API ====
        # ==== Block Webhooks: lokaler Fake-Empfänger (AGORA_WEBHOOK_LOKAL nur im Test), anlegen, „Test senden“, Signatur prüfen ====
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        wh_empfangen = []

        class WhEmpfaenger(BaseHTTPRequestHandler):
            def do_POST(self):
                wh_empfangen.append((dict(self.headers), self.rfile.read(int(self.headers.get('content-length') or 0))))
                self.send_response(204); self.end_headers()

            def log_message(self, *a):
                pass
        wh_server = ThreadingHTTPServer(('127.0.0.1', 0), WhEmpfaenger)
        threading.Thread(target=wh_server.serve_forever, daemon=True).start()
        hp.click('#webhook-neu')
        hp.wait_for_selector('dialog [name=url]')
        hp.fill('dialog [name=url]', f'http://127.0.0.1:{wh_server.server_address[1]}/agora-hook')
        hp.check('dialog [name=ereignis][value="aufnahme.fertig"]')
        hp.screenshot(path=OUT + '51a-webhook-neu.png')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#dev-geheim')
        wh_geheim = hp.inner_text('#dev-geheim').strip()
        hp.click('dialog button:has-text("Fertig")')
        hp.wait_for_selector('#liste-webhooks li[data-webhook]')
        hp.click('#liste-webhooks li[data-webhook] [data-wh-test]')
        hp.wait_for_selector('#liste-webhooks li[data-webhook] .status.ok')
        if len(wh_empfangen) != 1: fehler.append(f'Webhook: {len(wh_empfangen)} Aufrufe statt 1')
        else:
            kopf, body = wh_empfangen[0]
            teile = dict(x.split('=', 1) for x in kopf.get('X-Agora-Signatur', '').split(','))
            soll = hmac.new(wh_geheim.encode(), teile.get('t', '').encode() + b'.' + body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(soll, teile.get('v1', '')): fehler.append('Webhook: Signatur stimmt nicht')
            if json.loads(body)['ereignis'] != 'test': fehler.append('Webhook: falsches Ereignis')
        hp.click('#liste-webhooks li[data-webhook] [data-wh-protokoll]')
        hp.wait_for_selector('dialog .dev-protokoll li[data-ok="true"]')
        hp.screenshot(path=OUT + '51b-webhook-protokoll.png')
        hp.keyboard.press('Escape')
        hp.screenshot(path=OUT + '51c-entwickler.png', full_page=True)
        wh_server.shutdown()
        print('Webhooks: Test-Ereignis signiert angekommen')
        # ==== Ende Block Webhooks ====
        # ==== Block Einbetten: Besitzer erlaubt eine Test-Herkunft (zweiter http.server auf anderem Port), Fremdseite bettet
        # per /embed.js ein, Gast tritt im iframe bei, postMessage beigetreten/verlassen kommen nur dort an
        class FremdSeite(BaseHTTPRequestHandler):
            def do_GET(self):
                html = FREMD_HTML.encode()
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html))); self.end_headers(); self.wfile.write(html)

            def log_message(self, *a):
                pass
        fremd_server = ThreadingHTTPServer(('127.0.0.1', 0), FremdSeite)
        fremd = f'http://127.0.0.1:{fremd_server.server_address[1]}'
        threading.Thread(target=fremd_server.serve_forever, daemon=True).start()
        emb = hp.evaluate("""async () => (await (await fetch('/api/raeume', {method:'POST', headers:{'X-Agora':'1','Content-Type':'application/json'}, body: JSON.stringify({titel:'Eingebettet'})})).json())""")
        emb_code = emb['raum']['code']
        if hp.request.get(f'{B}/e/{emb_code}').status != 404: fehler.append('Einbetten: /e/ ohne Freigabe nicht 404')
        hp.goto(B + '/konto#meetings'); hp.reload()
        hp.click(f'[data-einbetten="{emb_code}"]')
        hp.wait_for_selector('dialog [name=herkuenfte]')
        hp.click('dialog label.schalter:has([name=erlaubt])')
        hp.fill('dialog [name=herkuenfte]', fremd)
        hp.screenshot(path=OUT + '52a-einbetten-einstellen.png')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector(f'#liste-geplant li:has-text("{emb_code}") .marker:has-text("einbettbar")')
        kopf = hp.request.get(f'{B}/e/{emb_code}').headers
        print('Einbetten: CSP frame-ancestors =', kopf['content-security-policy'].split('frame-ancestors ')[1].split(';')[0])
        if f'frame-ancestors {fremd}' not in kopf['content-security-policy'] or 'x-frame-options' in kopf: fehler.append(f'Einbetten: Kopfzeilen falsch {kopf}')
        if hp.request.get(f'{B}/r/{emb_code}').headers.get('x-frame-options') != 'DENY': fehler.append('Einbetten: /r/ ohne X-Frame-Options')
        js = hp.request.get(B + '/embed.js')
        if js.status != 200 or js.headers.get('cross-origin-resource-policy') != 'cross-origin': fehler.append(f'embed.js: {js.status} {js.headers}')
        FREMD_HTML = f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Fremde Seite</title></head>
<body style="margin:0;background:#fff;font-family:sans-serif"><h1 style="margin:8px">Fremde Seite</h1><div id="m"></div>
<script src="{B}/embed.js"></script>
<script>window.ereignisse = []; window.boese = [];
Agora.einbetten('#m', {{ code: '{emb_code}', name: 'Eingebettete Ella', hoehe: '700px' }}).on('*', function (d) {{ window.ereignisse.push(d.typ); }});
window.addEventListener('message', function (e) {{ if (e.origin !== '{B}') window.boese.push(e.origin); }});
</script></body></html>"""
        ef = br.new_context(viewport={'width': 1100, 'height': 860}, permissions=['camera', 'microphone'])
        efp = ef.new_page(); beobachten(efp, 'fremdseite')
        efp.goto(fremd + '/')
        rahmen = efp.frame_locator('#m iframe')
        rahmen.locator('#v-lobby:not([hidden])').wait_for(timeout=20000)
        if rahmen.locator('[name=name]').input_value() != 'Eingebettete Ella': fehler.append('Einbetten: Name nicht vorbelegt')
        warten_js(efp, "window.ereignisse.includes('bereit')", 10)
        efp.screenshot(path=OUT + '52b-einbetten-lobby.png')
        rahmen.locator('#beitreten-knopf').click()
        rahmen.locator('#v-meeting:not([hidden])').wait_for(timeout=30000)
        warten_js(efp, "window.ereignisse.includes('beigetreten')", 15)
        time.sleep(2)
        efp.screenshot(path=OUT + '52c-einbetten-meeting.png')
        erlaubt_iframe = efp.evaluate("document.querySelector('#m iframe').allow")
        if 'camera' not in erlaubt_iframe or 'microphone' not in erlaubt_iframe: fehler.append(f'Einbetten: allow fehlt ({erlaubt_iframe})')
        rahmen.locator('#s-verlassen').click()
        rahmen.locator('.menue [role=menuitem]').first.click()
        warten_js(efp, "window.ereignisse.includes('verlassen')", 15)
        if efp.evaluate('window.boese.length'): fehler.append('Einbetten: Nachrichten von fremder Herkunft')
        print('Einbetten: Ereignisse auf der Fremdseite', efp.evaluate('window.ereignisse'))
        efp.screenshot(path=OUT + '52d-einbetten-verlassen.png')
        ef.close()
        fremd_server.shutdown()
        # ==== Ende Block Einbetten ====
        # ==== Block Teams / Organisationen: anlegen, Emma per Mail einladen, annehmen, Meeting der Organisation sichtbar ====
        hp.goto(B + '/konto#teams'); hp.reload()
        hp.wait_for_selector('#p-teams:not([hidden]) #liste-orgs')
        hp.click('#org-neu')
        hp.wait_for_selector('dialog [name=name]')
        hp.fill('dialog [name=name]', 'E2E Team')
        hp.screenshot(path=OUT + '60a-org-neu.png')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#org-detail:not([hidden]) #org-titel:has-text("E2E Team")')
        hp.click('[data-org-einladen]')
        hp.wait_for_selector('dialog [name=emails]')
        hp.fill('dialog [name=emails]', 'emma@example.com')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#liste-org-offen li:has-text("emma@example.com")')
        mails = [x for x in hp.request.get(B + '/api/test/posteingang').json()['mails'] if x['an'] == 'emma@example.com']
        einladung = re.search(r'https?://\S+/konto\?einladung=[\w-]+#teams', mails[-1]['text'])
        if not einladung or 'E2E Team' not in mails[-1]['betreff']: fehler.append(f"Organisation: Einladungsmail fehlt ({mails[-1]['betreff'] if mails else '-'})")
        # Meeting der Organisation planen (Planen-Dialog aus konto.js)
        hp.click('[data-org-planen]')
        hp.wait_for_selector('dialog [name=titel]')
        hp.fill('dialog [name=titel]', 'Team-Weekly')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#liste-org-raeume li[data-org-raum]:has-text("Team-Weekly")')
        org_code = hp.get_attribute('#liste-org-raeume li[data-org-raum]', 'data-org-raum')
        hp.screenshot(path=OUT + '60b-org-detail.png', full_page=True)
        if org_code in hp.request.get(B + '/api/raeume').text(): fehler.append('Organisation: Meeting steht in der eigenen Liste')
        # Emma meldet sich an, öffnet den Link aus der Mail und nimmt an
        em = br.new_context(viewport={'width': 1100, 'height': 800}, locale='en-US')
        emp = em.new_page(); beobachten(emp, 'org-emma')
        emp.goto(B + '/')
        emp.fill('#f-anmelden [name=email]', 'emma@example.com')
        emp.fill('#f-anmelden [name=passwort]', 'geheimespasswort1')
        emp.click('#f-anmelden button[type=submit]')
        emp.wait_for_selector('#angemeldet:not([hidden])')
        emp.goto(einladung.group(0))
        emp.wait_for_selector('dialog [data-link-annehmen]')
        emp.screenshot(path=OUT + '60c-org-einladung.png')
        emp.click('dialog [data-link-annehmen]')
        emp.wait_for_selector(f'#org-detail:not([hidden]) #liste-org-raeume li[data-org-raum="{org_code}"]')
        if emp.query_selector('[data-org-planen]'): fehler.append('Organisation: Mitglied sieht „Meeting planen“')
        if emp.query_selector(f'#liste-org-raeume li[data-org-raum="{org_code}"] .gefahr'): fehler.append('Organisation: Mitglied darf löschen')
        emp.screenshot(path=OUT + '60d-org-mitglied.png', full_page=True)
        deutsch_rest(emp, 'Teams')
        info = emp.request.get(B + '/api/orgs').json()['orgs']
        if [o['rolle'] for o in info] != ['mitglied']: fehler.append(f'Organisation: Emma ist {info}')
        em.close()
        hp.goto(B + '/konto#teams'); hp.reload()
        hp.click('[data-org-oeffnen]')
        hp.wait_for_selector('#liste-org-mitglieder li:has-text("Emma English")')
        print('Organisation: angelegt, Emma eingeladen und beigetreten, Meeting', org_code, 'für Mitglieder sichtbar')
        # ==== Ende Block Teams / Organisationen ====
        # ==== Block Status-Seite: Admin schreibt eine Meldung, /status zeigt sie (ohne Anmeldung, DE und EN) ====
        hp.goto(B + '/admin#status'); hp.reload()
        hp.wait_for_selector('#p-status:not([hidden]) #liste-status-meldungen')
        hp.click('#status-neu')
        hp.wait_for_selector('dialog [name=titel]')
        hp.select_option('dialog [name=art]', 'wartung')
        hp.fill('dialog [name=titel]', 'E2E-Wartung')
        hp.fill('dialog [name=text]', 'E2E 12:00 UTC')  # Admin-Text ist einsprachig (wird nicht übersetzt)
        hp.check('dialog [name=dienst][value=medien]')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#liste-status-meldungen li:has-text("E2E-Wartung")')
        hp.screenshot(path=OUT + '61a-admin-status.png', full_page=True)
        sk = br.new_context(viewport={'width': 1100, 'height': 900}, locale='de-DE')
        sp = sk.new_page(); beobachten(sp, 'status')
        sp.goto(B + '/status')
        sp.wait_for_selector('#dienste [data-dienst="app"] .status-balken i')
        sp.wait_for_selector('#meldungen li:has-text("E2E-Wartung")')
        anzahl = sp.eval_on_selector_all('#dienste [data-dienst="app"] .status-balken i', 'e => e.length')
        if anzahl != 90: fehler.append(f'Status: {anzahl} Balken statt 90')
        sp.click('.status-zeitraum label:has-text("24 Stunden")')
        if sp.eval_on_selector_all('#dienste [data-dienst="app"] .status-balken i', 'e => e.length') != 24: fehler.append('Status: 24-h-Ansicht falsch')
        sp.screenshot(path=OUT + '61b-status.png', full_page=True)
        js = sp.request.get(B + '/api/status')
        roh_text = js.text().lower()
        if js.status != 200 or any(x in roh_text for x in ('livekit', '127.0.0.1', 'version', 'smtp')): fehler.append('Status: /api/status verrät Interna')
        sk.close()
        se = br.new_context(viewport={'width': 390, 'height': 844}, locale='en-US', is_mobile=True, has_touch=True)
        sep = se.new_page(); beobachten(sep, 'status-en')
        sep.goto(B + '/status')
        sep.wait_for_selector('#dienste [data-dienst="medien"]')
        if 'Video and audio' not in sep.inner_text('#dienste'): fehler.append('Status: Englisch fehlt')
        if sep.evaluate('document.documentElement.scrollWidth > innerWidth + 1'): fehler.append('Status: waagrechtes Scrollen am Handy')
        sep.screenshot(path=OUT + '61c-status-en.png', full_page=True)
        deutsch_rest(sep, 'Status')
        se.close()
        print('Status-Seite: Meldung angelegt und sichtbar, Balken 90/24, JSON ohne Interna, Englisch')
        # ==== Ende Block Status-Seite ====
        # ==== Block Push: Chromium headless hat keinen Push-Dienst (FCM) – Knopf versuchen, dann den Server-Weg prüfen:
        # Schlüssel per WebCrypto, Abo mit lokalem Fake-Dienst (AGORA_WEBHOOK_LOKAL), „Test senden“ und Warteraum-Ereignis;
        # die Nachricht entschlüsselt der Browser selbst nach RFC 8291 (WebCrypto) – das prüft die Server-Verschlüsselung.
        push_empfangen = []

        class PushDienst(BaseHTTPRequestHandler):
            def do_POST(self):
                push_empfangen.append((dict(self.headers), self.rfile.read(int(self.headers.get('content-length') or 0))))
                self.send_response(201); self.end_headers()

            def log_message(self, *a):
                pass
        push_server = ThreadingHTTPServer(('127.0.0.1', 0), PushDienst)
        threading.Thread(target=push_server.serve_forever, daemon=True).start()
        host.grant_permissions(['notifications'], origin=B)
        hp.goto(B + '/konto#profil'); hp.reload()
        hp.wait_for_selector('#push p.sec')
        hp.screenshot(path=OUT + '53a-push-aus.png', full_page=True)
        print('Push im Profil:', hp.inner_text('#push').replace('\n', ' | ')[:300])
        if hp.locator('#push-hier').count():
            hp.click('#push-hier')
            try:
                hp.wait_for_selector('#push-geraete li', timeout=8000)
                print('Push: echtes Abo im Browser angelegt')
            except Exception:      # noqa: BLE001 – erwartet ohne FCM
                print('Push: Browser-Abo nicht möglich (headless ohne Push-Dienst) – prüfe den Server-Weg')
        time.sleep(0.5)
        schl = hp.evaluate("""async () => {
            const k = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
            const roh = new Uint8Array(await crypto.subtle.exportKey('raw', k.publicKey));
            const auth = crypto.getRandomValues(new Uint8Array(16));
            const b64 = (a) => btoa(String.fromCharCode(...a)).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
            return { jwk: await crypto.subtle.exportKey('jwk', k.privateKey), p256dh: b64(roh), auth: b64(auth) };
        }""")
        r = hp.request.post(B + '/api/push/abo', headers={'X-Agora': '1'}, data={
            'endpoint': f'http://127.0.0.1:{push_server.server_address[1]}/push/e2e', 'keys': {'p256dh': schl['p256dh'], 'auth': schl['auth']},
            'geraet': 'E2E · Fake-Dienst'})
        if r.status != 200: fehler.append(f'Push-Abo: {r.status} {r.text()[:120]}')
        hp.reload()
        hp.wait_for_selector('#push-geraete li:has-text("E2E · Fake-Dienst")')
        hp.click('#push-test')
        hp.wait_for_selector('.toast:has-text("Test an")')
        hp.screenshot(path=OUT + '53b-push-geraete.png', full_page=True)
        ENTSCHLUESSELN = """async ([body64, jwk, auth64]) => {
            const ab = (s) => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0));
            const cat = (...t) => { const o = new Uint8Array(t.reduce((n, x) => n + x.length, 0)); let i = 0; for (const x of t) { o.set(x, i); i += x.length; } return o; };
            const enc = (s) => new TextEncoder().encode(s);
            const hm = async (k, d) => new Uint8Array(await crypto.subtle.sign('HMAC', await crypto.subtle.importKey('raw', k, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']), d));
            const b = ab(body64), salz = b.slice(0, 16), idlen = b[20], asPub = b.slice(21, 21 + idlen), geheim = b.slice(21 + idlen);
            const priv = await crypto.subtle.importKey('jwk', jwk, { name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveBits']);
            const { d, ...pubJwk } = jwk;
            const uaPub = new Uint8Array(await crypto.subtle.exportKey('raw', await crypto.subtle.importKey('jwk', { ...pubJwk, key_ops: [] }, { name: 'ECDH', namedCurve: 'P-256' }, true, [])));
            const asKey = await crypto.subtle.importKey('raw', asPub, { name: 'ECDH', namedCurve: 'P-256' }, false, []);
            const ecdh = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: asKey }, priv, 256));
            const ikm = await hm(await hm(ab(auth64), ecdh), cat(enc('WebPush: info\\0'), uaPub, asPub, new Uint8Array([1])));
            const prk = await hm(salz, ikm);
            const cek = (await hm(prk, enc('Content-Encoding: aes128gcm\\0\\x01'))).slice(0, 16);
            const nonce = (await hm(prk, enc('Content-Encoding: nonce\\0\\x01'))).slice(0, 12);
            const k = await crypto.subtle.importKey('raw', cek, 'AES-GCM', false, ['decrypt']);
            let klar = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: nonce }, k, geheim));
            let n = klar.length; while (n && klar[n - 1] === 0) n--;
            if (klar[n - 1] !== 2) throw new Error('kein Ende-Begrenzer');
            return JSON.parse(new TextDecoder().decode(klar.slice(0, n - 1)));
        }"""
        ende = time.time() + 10
        while not push_empfangen and time.time() < ende: time.sleep(0.2)
        if not push_empfangen: fehler.append('Push: Test kam beim Fake-Dienst nicht an')
        else:
            kopf, body = push_empfangen[-1]
            if kopf.get('Content-Encoding') != 'aes128gcm' or not kopf.get('Authorization', '').startswith('vapid t='): fehler.append(f'Push: Kopfzeilen {kopf}')
            n = hp.evaluate(ENTSCHLUESSELN, [base64.urlsafe_b64encode(body).decode().rstrip('='), schl['jwk'], schl['auth']])
            print('Push (Test), im Browser entschlüsselt:', n)
            if n.get('titel') != 'Agora' or n.get('url') != '/konto#profil': fehler.append(f'Push-Inhalt falsch: {n}')
        # Warteraum: Host ist nicht im Meeting → Push „Jemand wartet“
        r = hp.evaluate("""async () => (await (await fetch('/api/raeume', {method:'POST', headers:{'X-Agora':'1','Content-Type':'application/json'}, body: JSON.stringify({titel:'Push-Warteraum', warteraum:true})})).json())""")
        pcode = r['raum']['code']
        vorher = len(push_empfangen)
        wk = br.new_context()
        w = wk.request.post(B + f'/api/raeume/{pcode}/beitreten', headers={'X-Agora': '1'}, data={'name': 'Wanda Wartend'})
        if w.json().get('status') != 'wartet': fehler.append(f'Push-Warteraum: {w.text()[:120]}')
        ende = time.time() + 10
        while len(push_empfangen) <= vorher and time.time() < ende: time.sleep(0.2)
        if len(push_empfangen) <= vorher: fehler.append('Push: Warteraum-Benachrichtigung fehlt')
        else:
            n = hp.evaluate(ENTSCHLUESSELN, [base64.urlsafe_b64encode(push_empfangen[-1][1]).decode().rstrip('='), schl['jwk'], schl['auth']])
            print('Push (Warteraum):', n)
            if 'Wanda Wartend' not in n.get('text', '') or n.get('url') != f'/r/{pcode}': fehler.append(f'Push-Warteraum-Inhalt falsch: {n}')
        wk.close()
        push_server.shutdown()
        # ==== Ende Block Push ====
        # ==== Block Dolmetschen: Host legt Sprachen fest und macht Dora zur Dolmetscherin (DE ⇄ EN), Dora sendet auf
        # „dolmetsch:en“ (Mikro-Attrappe), Host wählt den Kanal Englisch → abonniert genau diese Spur, Saal leiser
        r = hp.evaluate("""async () => (await (await fetch('/api/raeume', {method:'POST', headers:{'X-Agora':'1','Content-Type':'application/json'}, body: JSON.stringify({titel:'Dolmetsch-Test'})})).json())""")
        dcode = r['raum']['code']
        hp.goto(f'{B}/r/{dcode}')
        hp.wait_for_selector('#v-lobby:not([hidden])')
        hp.click('#beitreten-knopf')
        hp.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        dk = br.new_context(locale='de-DE', viewport={'width': 1100, 'height': 760}, permissions=['camera', 'microphone'])
        dp = dk.new_page(); beobachten(dp, 'dolmetscherin')
        dp.goto(f'{B}/r/{dcode}')
        dp.wait_for_selector('#v-lobby:not([hidden])')
        dp.fill('#f-beitritt [name=name]', 'Dora Dolmetsch')
        dp.click('#beitreten-knopf')
        dp.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        warten_bis(hp, '#buehne .kv', 2)
        hp.click('#s-mehr')
        hp.click('.menue [role=menuitem]:has-text("Dolmetschen verwalten")')
        hp.wait_for_selector('dialog .dm-sprachen')
        hp.check('dialog .dm-sprache input[value="de"]')
        hp.check('dialog .dm-sprache input[value="en"]')
        hp.click('#dm-sprachen-speichern')
        hp.wait_for_selector('dialog form.dm-zuweisen')
        hp.select_option('dialog form.dm-zuweisen select[name=ziel]', label='Dora Dolmetsch')
        hp.select_option('dialog form.dm-zuweisen select[name=a]', 'de')
        hp.select_option('dialog form.dm-zuweisen select[name=b]', 'en')
        hp.click('dialog form.dm-zuweisen button[type=submit]')
        hp.wait_for_selector('dialog .dm-liste li[data-dolmetscher]')
        hp.screenshot(path=OUT + '54a-dolmetsch-verwalten.png')
        hp.click('dialog .blatt-kopf .icon-btn')         # Schließen-Knopf (der Inhalt wurde neu gezeichnet, Fokus liegt nicht im Dialog)
        hp.wait_for_selector('dialog.blatt', state='detached')
        dp.wait_for_selector('#dolmetsch-pult:not([hidden]) #dm-senden', timeout=10000)
        dp.click('#dm-senden')
        dp.wait_for_selector('#dolmetsch-pult .dm-status[data-zustand="sendet"]', timeout=15000)
        dp.screenshot(path=OUT + '54b-dolmetsch-pult.png')
        hp.click('#s-mehr')
        hp.click('.menue [role=menuitem]:has-text("Sprachkanal")')
        hp.wait_for_selector('dialog .dm-kanal[data-kanal="en"]')
        hp.screenshot(path=OUT + '54c-dolmetsch-kanalwahl.png')
        hp.click('dialog .dm-kanal[data-kanal="en"]')
        hp.wait_for_selector('#audio-ablage audio[data-dolmetsch="en"]', state='attached', timeout=20000)
        warten_js(hp, "[...document.querySelectorAll('#audio-ablage audio:not([data-dolmetsch])')].some((a) => Math.abs(a.volume - 0.2) < 0.01)", 10)
        laut = hp.evaluate("[...document.querySelectorAll('#audio-ablage audio')].map((a) => [a.dataset.dolmetsch || 'saal', a.volume])")
        print('Dolmetschen: Host hört', laut, '| Kanal-Anzeige:', hp.inner_text('#m-dolmetsch'))
        if not any(k == 'en' and v == 1 for k, v in laut): fehler.append(f'Dolmetschen: Kanal nicht laut: {laut}')
        if any(k == 'saal' and v > 0.21 for k, v in laut): fehler.append(f'Dolmetschen: Saal nicht leiser: {laut}')
        hp.screenshot(path=OUT + '54d-dolmetsch-zuhoeren.png')
        # zurück zum Originalton → Spur abbestellt, Saal wieder laut
        hp.click('#s-mehr')
        hp.click('.menue [role=menuitem]:has-text("Sprachkanal")')
        hp.click('dialog .dm-kanal[data-kanal=""]')
        hp.wait_for_selector('#audio-ablage audio[data-dolmetsch]', state='detached', timeout=15000)
        warten_js(hp, "[...document.querySelectorAll('#audio-ablage audio')].every((a) => a.volume === 1)", 10)
        print('Dolmetschen: zurück zum Originalton, Dolmetsch-Spur abbestellt')
        dk.close()
        hp.goto(B + '/konto')                          # Meeting verlassen (endet nach Leerlauf von selbst)
        time.sleep(1)
        # ==== Ende Block Dolmetschen ====
        # ==== Block Zusammenfassung: Fake-KI (OpenAI-kompatibel, AGORA_KI_URL im Test-Stapel) auf KI_PORT; Transkript per API,
        # im Konto erst Stichwort-Kapitel (ohne KI), dann „Mit KI“ nach Bestätigung des Hinweises
        ki_anfragen = []

        class FakeKI(BaseHTTPRequestHandler):
            def do_POST(self):
                d = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)))
                ki_anfragen.append(d)
                inhalt = {'zusammenfassung': 'Kurze Runde zum Budget; Folien kommen per Mail.', 'kapitel': [{'zeit': '00:00', 'titel': 'Begrüßung und Budget'}],
                          'aufgaben': [{'text': 'Folien verschicken', 'wer': 'Florian'}]}
                body = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': json.dumps(inhalt, ensure_ascii=False)}}]}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def log_message(self, *a):
                pass
        ki_server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('KI_PORT', '8116'))), FakeKI)
        threading.Thread(target=ki_server.serve_forever, daemon=True).start()
        r = hp.evaluate("""async () => (await (await fetch('/api/raeume', {method:'POST', headers:{'X-Agora':'1','Content-Type':'application/json'}, body: JSON.stringify({titel:'Budgetrunde'})})).json())""")
        zcode = r['raum']['code']
        J = {'X-Agora': '1', 'Content-Type': 'application/json'}
        hp.request.patch(f'{B}/api/raeume/{zcode}/transkript', data=json.dumps({'speichern': True}), headers=J)
        zz = hp.request.post(f'{B}/api/raeume/{zcode}/beitreten', data=json.dumps({'name': 'Florian Test'}), headers=J).json()['zugang']
        for satz in ('Willkommen zur Budgetrunde, heute geht es um das Budget.', 'Die Folien zum Budget schicke ich per Mail.'):
            hp.request.post(f'{B}/api/raeume/{zcode}/untertitel', data=json.dumps({'text': satz, 'final': True, 'dauer_ms': 1500}),
                            headers={**J, 'X-Teilnahme': zz['teilnahme']})
            time.sleep(0.3)
        hp.goto(B + '/konto#meetings'); hp.reload()
        hp.click(f'[data-transkripte="{zcode}"]')
        hp.click(f'dialog [data-zusammenfassung="{zcode}"]')
        hp.wait_for_selector('dialog #zf-stichwort')
        hp.click('dialog #zf-stichwort')
        hp.wait_for_selector('dialog #zf-status[data-art="stichwort"]')
        st = hp.inner_text('dialog .zf-kapitel')
        print('Stichwort-Kapitel:', st.replace('\n', ' | '))
        if 'Budget' not in st: fehler.append(f'Stichwort-Kapitel ohne Stichwort: {st}')
        hp.screenshot(path=OUT + '55a-stichwort-kapitel.png')
        hp.click('dialog #zf-ki')
        hp.wait_for_selector('dialog:has-text("Transkript an den KI-Dienst schicken?")')
        hinweis = hp.inner_text('dialog:has-text("Transkript an den KI-Dienst schicken?")')
        if '127.0.0.1' not in hinweis: fehler.append('KI-Hinweis nennt das Ziel nicht')
        if ki_anfragen: fehler.append('KI vor der Bestätigung aufgerufen')
        hp.click('dialog button:has-text("Ja, schicken")')
        hp.wait_for_selector('dialog #zf-status[data-art="ki"]', timeout=30000)
        ki_text = hp.inner_text('#zf-inhalt')
        print('KI-Zusammenfassung:', ki_text.replace('\n', ' | ')[:200])
        if 'Folien kommen per Mail' not in ki_text or 'Folien verschicken' not in ki_text: fehler.append('KI-Zusammenfassung fehlt')
        if not ki_anfragen or 'Florian Test: Willkommen zur Budgetrunde' not in ki_anfragen[0]['messages'][-1]['content']:
            fehler.append('KI bekam das Transkript nicht')
        vtt = hp.request.get(f'{B}/api/raeume/{zcode}/kapitel.vtt').text()
        if 'Begrüßung und Budget' not in vtt: fehler.append(f'Kapitel-VTT falsch: {vtt[:80]}')
        hp.screenshot(path=OUT + '55b-ki-zusammenfassung.png')
        ki_server.shutdown()
        hp.goto(B + '/konto')
        # ==== Ende Block Zusammenfassung ====
    except Exception:
        traceback.print_exc()
        fehler.append('Ablauf abgebrochen')
        for i, pg in enumerate(host.pages):
            pg.screenshot(path=OUT + f'fehler-host-{i}.png')
    print('\n'.join(fehler) or 'keine JS-Fehler')
    br.close()
import sys
sys.exit(1 if fehler else 0)
