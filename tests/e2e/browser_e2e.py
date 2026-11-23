# Browser-E2E (Playwright, Kamera/Mikro-Attrappe): Host + Gast, Warteraum, Video, Chat, Steuerung, Beenden.
# Lauf: siehe tests/e2e/lauf.sh
import re, json, time, traceback
from playwright.sync_api import sync_playwright

import os
B = os.environ.get('BASIS', 'http://127.0.0.1:8111')
OUT = os.environ.get('AUSGABE', '/tmp/') 
fehler = []

def beobachten(page, wer):
    page.on('pageerror', lambda e: fehler.append(f'[{wer}] pageerror: {e}'))
    page.on('console', lambda m: m.type == 'error' and fehler.append(f'[{wer}] console: {m.text}'))

with sync_playwright() as p:
    br = p.chromium.launch(args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'])
    host = br.new_context(viewport={'width': 1280, 'height': 800}, permissions=['camera', 'microphone'])
    hp = host.new_page(); beobachten(hp, 'host')
    try:
        hp.goto(B + '/')
        hp.screenshot(path=OUT + '01-start.png')
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
        # Host: Gast stummschalten über Teilnehmerliste
        hp.click('#s-leute'); time.sleep(0.5)
        hp.click('#panel-leute .leute-liste li:nth-child(2) .icon-btn')
        hp.screenshot(path=OUT + '15-aktionen.png')
        hp.click('.menue [role=menuitem] >> text=Stummschalten')
        time.sleep(2)
        gp.screenshot(path=OUT + '16-gast-stumm.png')
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
    except Exception:
        traceback.print_exc()
        fehler.append('Ablauf abgebrochen')
        for i, pg in enumerate(host.pages):
            pg.screenshot(path=OUT + f'fehler-host-{i}.png')
    print('\n'.join(fehler) or 'keine JS-Fehler')
    br.close()
import sys
sys.exit(1 if fehler else 0)
