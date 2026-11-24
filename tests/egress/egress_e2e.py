# Egress-Test (Playwright, Kamera-Attrappe): Host startet „Auf dem Server aufzeichnen“, stoppt, die MP4 landet in der
# Bibliothek. Danach Livestream zu einem nicht erreichbaren RTMP-Ziel → Fehlermeldung muss beim Host ankommen.
# Lauf: tests/egress/lauf.sh
import os, re, time, traceback
from playwright.sync_api import sync_playwright

B = os.environ.get('BASIS', 'http://127.0.0.1:8172')
OUT = os.environ.get('AUSGABE', '/tmp/')
fehler = []
H = {'X-Agora': '1', 'Content-Type': 'application/json'}

with sync_playwright() as p:
    br = p.chromium.launch(args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'])
    ctx = br.new_context(viewport={'width': 1280, 'height': 800}, permissions=['camera', 'microphone'])
    hp = ctx.new_page()
    hp.on('pageerror', lambda e: fehler.append(f'pageerror: {e}'))
    try:
        hp.goto(B + '/')
        r = hp.request.post(B + '/api/konto/registrieren', headers=H, data='{"name":"Egon Egress","email":"egon@example.com","passwort":"geheimespasswort1"}')
        assert r.ok, r.text()
        mail = hp.request.get(B + '/api/test/posteingang').json()['mails'][-1]['text']
        hp.goto(re.search(r'https?://\S+bestaetigen\?t=[\w\-.]+', mail).group(0))
        code = hp.evaluate("""async () => (await (await fetch('/api/raeume', {method:'POST', headers:{'X-Agora':'1','Content-Type':'application/json'}, body: JSON.stringify({titel:'Egress-Test'})})).json()).raum.code""")
        hp.goto(f'{B}/r/{code}')
        hp.wait_for_selector('#v-lobby:not([hidden])')
        hp.click('#beitreten-knopf')
        hp.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        time.sleep(2)
        # Server-Aufnahme
        hp.click('#s-mehr')
        hp.click('.menue [role=menuitem] >> text=Auf dem Server aufzeichnen')
        hp.wait_for_selector('dialog .egress-form')
        hp.select_option('dialog [name=layout]', 'sprecher')
        hp.screenshot(path=OUT + 'e1-aufnahme-dialog.png')
        t0 = time.time()
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#m-rec:not([hidden])', timeout=20000)
        hp.wait_for_selector('.toast:has-text("Der Server zeichnet auf")', timeout=90000)
        print(f'Server-Aufnahme läuft nach {time.time() - t0:.1f} s')
        hp.screenshot(path=OUT + 'e2-aufnahme-laeuft.png')
        time.sleep(20)
        hp.click('#s-mehr')
        hp.click('.menue [role=menuitem] >> text=Server-Aufnahme beenden')
        t1 = time.time()
        ende = time.time() + 120
        liste = []
        while time.time() < ende:
            liste = hp.request.get(B + '/api/aufnahmen').json()['aufnahmen']
            if liste:
                break
            time.sleep(2)
        if not liste:
            fehler.append('Server-Aufnahme nicht in der Bibliothek')
        else:
            a = liste[0]
            print(f'In der Bibliothek nach {time.time() - t1:.1f} s: {a["titel"]} – {a["groesse"]} Bytes, {a["dauer"]} s, {a["typ"]}')
            daten = hp.request.get(B + f'/api/aufnahmen/{a["id"]}/datei').body()
            if a['typ'] != 'video/mp4' or daten[4:8] != b'ftyp' or len(daten) < 50000:
                fehler.append(f'Aufnahme kaputt: {a["typ"]} {daten[:12]!r} {len(daten)}')
        hp.wait_for_selector('#m-rec', state='hidden', timeout=30000)
        # Livestream zu einem Ziel, das nicht antwortet → Fehler beim Host
        hp.click('#s-mehr')
        hp.click('.menue [role=menuitem] >> text=Livestream starten')
        hp.fill('dialog [name=url]', 'rtmp://1.1.1.1/live')
        hp.fill('dialog [name=schluessel]', 'sk_test_GEHEIM_42')
        hp.screenshot(path=OUT + 'e3-stream-dialog.png')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#m-live:not([hidden])', timeout=20000)
        hp.screenshot(path=OUT + 'e4-live.png')
        hp.wait_for_selector('#m-banner.err:has-text("Livestream")', timeout=150000)
        text = hp.inner_text('#m-banner')
        print('Livestream-Fehler beim Host:', text)
        if 'sk_test_GEHEIM' in text: fehler.append('Schlüssel in der Fehlermeldung')
        hp.screenshot(path=OUT + 'e5-stream-fehler.png')
    except Exception:
        traceback.print_exc()
        fehler.append('Ablauf abgebrochen')
        hp.screenshot(path=OUT + 'e-fehler.png')
    print('\n'.join(fehler) or 'Egress-Test ok')
    br.close()
import sys
sys.exit(1 if fehler else 0)
