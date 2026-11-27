# Neubeitritt (ADR 0007): Host + Gast im Meeting, dann fällt der LiveKit-Knoten 45 s aus (das Shell-Skript stoppt ihn,
# sobald /e2e/bereit existiert, und legt /e2e/wieder an, wenn er wieder läuft). Erwartet: beide Browser verbinden sich
# ohne Neuladen selbst neu und bekommen wieder Bild vom anderen.
import json, os, re, sys, time, urllib.request
from playwright.sync_api import sync_playwright

B = os.environ.get('BASIS', 'http://127.0.0.1:8111')
D = '/e2e/'
for f in ('bereit', 'wieder'):
    try:
        os.remove(D + f)
    except FileNotFoundError:
        pass


def bilder(pg):
    return pg.evaluate("() => [...document.querySelectorAll('video')].filter(v => v.videoWidth > 0 && !v.closest('.selbst'))"
                       ".map(v => v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality().totalVideoFrames : 0)")


def laeuft(pg):
    a = bilder(pg); time.sleep(3); b = bilder(pg)
    return any(y > x for x, y in zip(a, b))


with sync_playwright() as p:
    br = p.chromium.launch(args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'])
    h = br.new_context(permissions=['camera', 'microphone'], locale='de-DE'); hp = h.new_page()
    g = br.new_context(permissions=['camera', 'microphone'], locale='de-DE'); gp = g.new_page()
    mail = f'neu{int(time.time())}@example.org'
    hp.goto(B + '/')
    hp.evaluate("""async (m) => fetch('/api/konto/registrieren', {method: 'POST', headers: {'X-Agora': '1', 'Content-Type': 'application/json'},
        body: JSON.stringify({name: 'Host Neu', email: m, passwort: 'geheimespasswort1'})})""", mail)
    post = hp.request.get(B + '/api/test/posteingang').json()['mails']
    link = re.search(r'https?://\S+bestaetigen\?t=[\w\-.]+', [x for x in post if x['an'] == mail][-1]['text']).group(0)
    hp.goto(link)
    code = hp.evaluate("""async () => (await (await fetch('/api/raeume', {method: 'POST', headers: {'X-Agora': '1', 'Content-Type': 'application/json'},
        body: JSON.stringify({titel: 'Neubeitritt'})})).json()).raum.code""")
    hp.goto(f'{B}/r/{code}'); hp.wait_for_selector('#beitreten-knopf'); time.sleep(1); hp.click('#beitreten-knopf')
    gp.goto(f'{B}/r/{code}'); gp.wait_for_selector('#beitreten-knopf'); gp.fill('#f-beitritt [name=name]', 'Gast Neu'); time.sleep(1)
    gp.click('#beitreten-knopf')
    time.sleep(10)
    assert laeuft(hp) and laeuft(gp), 'vor dem Ausfall kein Bild'
    print('vorher: Bild in beide Richtungen')
    open(D + 'bereit', 'w').close()
    for _ in range(240):
        if os.path.exists(D + 'wieder'):
            break
        time.sleep(1)
    else:
        sys.exit('Knoten kam nicht wieder')
    print('Knoten wieder da – warte auf Neubeitritt')
    ok = False
    for _ in range(30):                       # bis 90 s
        time.sleep(3)
        if hp.is_visible('#v-meeting') and gp.is_visible('#v-meeting') and laeuft(hp) and laeuft(gp):
            ok = True
            break
    ende_host, ende_gast = hp.is_visible('#v-ende'), gp.is_visible('#v-ende')
    hp.screenshot(path=D + 'bilder/50-neubeitritt-host.png'); gp.screenshot(path=D + 'bilder/50-neubeitritt-gast.png')
    print('nachher:', 'Bild in beide Richtungen' if ok else 'KEIN Bild', '| Ende-Ansicht Host/Gast:', ende_host, ende_gast)
    br.close()
    sys.exit(0 if ok else 1)
