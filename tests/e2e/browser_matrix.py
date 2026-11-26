# Browser-Kompatibilitätsmatrix (Playwright): repräsentativer Teil der E2E-Abläufe in Chromium, Firefox oder WebKit.
# Startseite + Service Worker, Registrierung, Lobby mit Vorschau, Beitritt, Video in beide Richtungen, Chat, Tastenkürzel,
# Bildschirmfreigabe, Whiteboard, Aufnahme, E2EE, Teams, Status-Seite, Video-Effekte. Jede Funktion bekommt ein Ergebnis:
#   ok · nicht unterstützt (der Browser kann es nicht, Agora zeigt das sauber an) · FEHLER
# Lauf: BROWSER=firefox sh tests/e2e/lauf.sh   (Ergebnis auch als JSON in tests/e2e/bilder/matrix-<browser>.json)
import json, os, re, sys, time, traceback
from playwright.sync_api import sync_playwright

B = os.environ.get('BASIS', 'http://127.0.0.1:8111')
OUT = os.environ.get('AUSGABE', '/tmp/')
BROWSER = os.environ.get('BROWSER', 'chromium')
ergebnisse = {}          # Funktion → (Ergebnis, Hinweis)
js_fehler = []

# Bekannte Meldungen der Browser selbst (kein Fehler von Agora)
BEKANNT = (
    'VideoFrame was garbage collected without being closed',      # Chromium + @livekit/track-processors
)
if BROWSER == 'webkit':
    # WebKit (Linux-Build) wendet die CSP auch auf die eingebauten Stile der <select>-Schattenbäume an und meldet das
    # je <select> – die Auswahlfelder funktionieren trotzdem; Agora selbst setzt keine Inline-Stile (style-src 'self').
    BEKANNT += ("Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline' does not appear in the style-src",
                # WebKit meldet eine beim Schließen der Seite abgebrochene (und in Agora abgefangene) Anfrage so
                'due to access control checks')

# Nur im Test: alle RTCPeerConnections merken, um mit getStats() die dekodierten Bilder zu zählen – unabhängig davon,
# ob der Browser headless überhaupt zeichnet (WebKit unter Linux zeichnet Kamera-Attrappen headless nicht).
PC_HAKEN = """(() => { const O = window.RTCPeerConnection; if (!O) return; window.__pcs = [];
  window.RTCPeerConnection = function (...a) { const pc = new O(...a); window.__pcs.push(pc); return pc; };
  window.RTCPeerConnection.prototype = O.prototype; Object.setPrototypeOf(window.RTCPeerConnection, O);
  const md = navigator.mediaDevices, gdm = md && md.getDisplayMedia;
  if (gdm) md.getDisplayMedia = function (...a) { return gdm.apply(md, a).catch((e) => { window.__gdmFehler = e.name + ': ' + e.message; throw e; }); };
})();"""


def ergebnis(name, wert, hinweis=''):
    ergebnisse[name] = (wert, hinweis)
    print(f'[{BROWSER}] {name}: {wert}{" – " + hinweis if hinweis else ""}', flush=True)


def pruefen(name, fn):
    """Führt einen Schritt aus; Ausnahme → FEHLER (mit Bildschirmfoto), Rückgabe (wert, hinweis) oder None = ok."""
    try:
        r = fn()
        wert, hinweis = r if isinstance(r, tuple) else ('ok', r or '')
        ergebnis(name, wert, hinweis)
        return wert == 'ok'
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ergebnis(name, 'FEHLER', str(e).splitlines()[0][:200])
        for i, s in enumerate(seiten):
            try:
                s.screenshot(path=f'{OUT}matrix-{BROWSER}-fehler-{name}-{i}.png')
            except Exception:  # noqa: BLE001
                pass
        return False


def warten_js(seite, ausdruck, sekunden=10):
    """wait_for_function wertet per eval aus – die CSP verbietet das, daher über evaluate."""
    ende = time.time() + sekunden
    while time.time() < ende:
        try:
            if seite.evaluate(ausdruck):
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.25)
    raise AssertionError(f'Zeitüberschreitung: {ausdruck[:120]}')


def frames(page):
    """Summe der dekodierten Bilder aller fremden Videos (wächst, solange Video ankommt)."""
    return page.evaluate("""() => [...document.querySelectorAll('#buehne .kv.mit-video:not(.spiegeln) video')]
        .reduce((s, v) => s + (v.getVideoPlaybackQuality?.().totalVideoFrames || v.mozPaintedFrames
            || (v.videoWidth ? Math.round(v.currentTime * 30) : 0)), 0)""")
# Firefox zählt totalVideoFrames bei MediaStreams nicht (bleibt 0) – dort mozPaintedFrames bzw. die laufende Zeit


def dekodiert(page):
    """Summe framesDecoded aller eingehenden Videospuren (WebRTC-Statistik)."""
    return page.evaluate("""async () => { let n = 0; for (const pc of (window.__pcs || [])) { if (pc.connectionState === 'closed') continue;
        try { (await pc.getStats()).forEach(r => { if (r.type === 'inbound-rtp' && r.kind === 'video') n += r.framesDecoded || 0; }); } catch {} }
        return n; }""")


def video_kommt_an(page, sekunden=4):
    f0, d0 = frames(page), dekodiert(page); time.sleep(sekunden); f1, d1 = frames(page), dekodiert(page)
    if d1 <= d0:
        raise AssertionError(f'kein Video dekodiert ({d0} → {d1}, gezeichnet {f0} → {f1})')
    if f1 <= f0 and BROWSER != 'webkit':
        raise AssertionError(f'Video dekodiert, aber nicht gezeichnet ({f0} → {f1})')
    return f'{d1 - d0} dekodiert' + (f'/{f1 - f0} gezeichnet' if f1 > f0 else ' (headless nicht gezeichnet)') + f' in {sekunden} s'


def beobachten(page, wer):
    page.on('pageerror', lambda e: not any(b in str(e) for b in BEKANNT) and js_fehler.append(f'[{wer}] pageerror: {e}'))
    page.on('console', lambda m: m.type == 'error' and not any(b in m.text for b in BEKANNT) and js_fehler.append(f'[{wer}] console: {m.text[:300]}'))


def starten(p):
    """Browser mit Kamera/Mikro-Attrappe; Rechte ohne Nachfrage."""
    if BROWSER == 'chromium':
        br = p.chromium.launch(args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
                                     '--autoplay-policy=no-user-gesture-required'])
        return br, {'permissions': ['camera', 'microphone']}
    if BROWSER == 'firefox':
        # Firefox kennt in Playwright keine camera/microphone-Rechte – stattdessen Attrappe + Nachfrage aus über Prefs
        br = p.firefox.launch(firefox_user_prefs={
            'media.navigator.streams.fake': True, 'media.navigator.permission.disabled': True,
            'media.autoplay.default': 0, 'media.autoplay.blocking_policy': 0,
            'media.getdisplaymedia.enabled': True,
            # Test-LiveKit lauscht nur auf 127.0.0.1: Firefox sammelt Loopback-Kandidaten erst mit diesen Prefs
            'media.peerconnection.ice.loopback': True, 'media.peerconnection.ice.obfuscate_host_addresses': False,
            # headless ohne GPU schaltet Firefox WebGL ab – für die Effekte (MediaPipe braucht WebGL2) erzwingen
            'webgl.force-enabled': True, 'webgl.disabled': False,
        })
        return br, {}
    if BROWSER == 'webkit':
        # WebKit (Playwright-Build für Linux) bringt eigene Attrappen-Geräte („Mock video device“) mit
        return p.webkit.launch(), {'permissions': ['camera', 'microphone']}
    raise SystemExit(f'Unbekannter Browser: {BROWSER}')


seiten = []
MAIL = f'host-{BROWSER}-{int(time.time())}@example.com'      # je Lauf neu (Testinstanz kann wiederverwendet werden)

with sync_playwright() as p:
    br, rechte = starten(p)
    version = br.version
    print(f'Browser: {BROWSER} {version}')

    def kontext(**kw):
        k = br.new_context(locale='de-DE', viewport={'width': 1180, 'height': 780}, **rechte, **kw)
        k.add_init_script(PC_HAKEN)
        return k

    host = kontext()
    hp = host.new_page(); beobachten(hp, 'host'); seiten.append(hp)
    zustand = {}

    # ---- Startseite + PWA ----
    def startseite():
        hp.goto(B + '/')
        hp.wait_for_selector('#tab-registrieren')
        sw = hp.evaluate("navigator.serviceWorker.ready.then(r => r.active && r.active.scriptURL)")
        warten_js(hp, '!!navigator.serviceWorker.controller', 15)
        if not sw or not sw.endswith('/sw.js'):
            raise AssertionError(f'Service Worker fehlt ({sw})')
        hp.screenshot(path=f'{OUT}matrix-{BROWSER}-01-start.png')
        return 'Service Worker aktiv'
    pruefen('Startseite + Service Worker', startseite)

    # ---- Registrierung (Formular, Mail-Link) ----
    def registrieren():
        hp.click('#tab-registrieren')
        hp.fill('#f-registrieren [name=name]', 'Host Matrix')
        hp.fill('#f-registrieren [name=email]', MAIL)
        hp.fill('#f-registrieren [name=passwort]', 'geheimespasswort1')
        hp.click('#f-registrieren button[type=submit]')
        hp.wait_for_selector('#status.ok')
        mails = hp.request.get(B + '/api/test/posteingang').json()['mails']
        mail = [m for m in mails if m['an'] == MAIL][-1]
        hp.goto(re.search(r'https?://\S+bestaetigen\?t=[\w\-.]+', mail['text']).group(0))
        hp.wait_for_selector('#angemeldet:not([hidden])')
    angemeldet = pruefen('Registrierung + Anmeldung', registrieren)

    # ---- Lobby: Vorschau mit Kamera-Attrappe ----
    def lobby():
        r = hp.evaluate("""async () => (await (await fetch('/api/raeume', {method:'POST', headers:{'X-Agora':'1','Content-Type':'application/json'},
            body: JSON.stringify({titel:'Matrix'})})).json())""")
        zustand['code'] = r['raum']['code']
        hp.goto(f"{B}/r/{zustand['code']}")
        hp.wait_for_selector('#v-lobby:not([hidden])')
        # Vorschau: Kameraspur läuft am <video>; gezeichnet wird headless nur in Chromium/Firefox
        warten_js(hp, """(() => { const v = document.querySelector('#v-lobby video'); const t = v?.srcObject?.getVideoTracks()[0];
            return t && t.readyState === 'live'; })()""", 15)
        if BROWSER != 'webkit':
            warten_js(hp, "(() => { const v = document.querySelector('#v-lobby video'); return v && v.videoWidth > 0; })()", 15)
        hp.wait_for_selector('#wahl-kamera option', state='attached')
        hp.wait_for_selector('#wahl-mikrofon option', state='attached')
        hp.screenshot(path=f'{OUT}matrix-{BROWSER}-02-lobby.png')
        return hp.evaluate("""(() => { const v = document.querySelector('#v-lobby video'); const s = v.srcObject.getVideoTracks()[0].getSettings();
            return `Kamera ${s.width}×${s.height}` + (v.videoWidth ? '' : ' (headless nicht gezeichnet)'); })()""")
    lobby_ok = angemeldet and pruefen('Lobby (Vorschau, Geräte)', lobby)

    # ---- Beitritt: Host, dann Gast im selben Browser-Typ ----
    gast = kontext(); gp = gast.new_page(); beobachten(gp, 'gast'); seiten.append(gp)

    def beitreten():
        hp.click('#beitreten-knopf')
        hp.wait_for_selector('#v-meeting:not([hidden])', timeout=25000)
        gp.goto(f"{B}/r/{zustand['code']}")
        gp.wait_for_selector('#v-lobby:not([hidden])')
        gp.fill('#f-beitritt [name=name]', 'Gast Matrix')
        gp.click('#beitreten-knopf')
        gp.wait_for_selector('#v-meeting:not([hidden])', timeout=25000)
        hp.wait_for_selector('#buehne .kv:nth-child(2)', timeout=15000)
    im_meeting = lobby_ok and pruefen('Beitritt (Host + Gast)', beitreten)

    if im_meeting:
        def video():
            gp.wait_for_selector('#buehne .kv.mit-video:not(.spiegeln) video', timeout=20000)
            hp.wait_for_selector('#buehne .kv.mit-video:not(.spiegeln) video', timeout=20000)
            time.sleep(2)
            a = video_kommt_an(gp); b = video_kommt_an(hp)
            hp.screenshot(path=f'{OUT}matrix-{BROWSER}-03-meeting-host.png')
            gp.screenshot(path=f'{OUT}matrix-{BROWSER}-04-meeting-gast.png')
            return f'Host→Gast {a}, Gast→Host {b}'
        pruefen('Video in beide Richtungen', video)

        def ton():
            # Ton kommt an: das <audio>-Element des Gegenübers hat einen laufenden Track (Pegel misst die Attrappe nicht überall)
            warten_js(gp, """(() => [...document.querySelectorAll('audio')].some(a => a.srcObject && a.srcObject.getAudioTracks().some(t => t.readyState === 'live')))()""", 15)
            return 'Audio-Track des Gegenübers live'
        pruefen('Ton (Audio-Track empfangen)', ton)

        def chat():
            hp.click('#s-chat')
            hp.fill('#panel-chat textarea', f'Hallo aus {BROWSER} 👍')
            hp.keyboard.press('Enter')
            gp.wait_for_selector('#s-chat .zaehler', timeout=10000)
            gp.click('#s-chat')
            gp.wait_for_selector(f'#panel-chat .chat-liste li:has-text("Hallo aus {BROWSER}")', timeout=10000)
            gp.fill('#panel-chat textarea', 'Antwort vom Gast')
            gp.keyboard.press('Enter')
            hp.wait_for_selector('#panel-chat .chat-liste li:has-text("Antwort vom Gast")', timeout=10000)
            gp.click('#leiste-zu'); hp.click('#leiste-zu')
        pruefen('Chat', chat)

        def kuerzel():
            hp.locator('#buehne').click(position={'x': 5, 'y': 5})       # Fokus auf die Seite, nicht in ein Feld
            vorher = hp.get_attribute('#s-mic', 'aria-pressed')
            hp.keyboard.press('m')
            warten_js(hp, f"document.querySelector('#s-mic').getAttribute('aria-pressed') !== '{vorher}'", 5)
            hp.keyboard.press('m')
            warten_js(hp, f"document.querySelector('#s-mic').getAttribute('aria-pressed') === '{vorher}'", 5)
            hp.keyboard.press('h')
            warten_js(hp, "document.querySelector('#s-hand').getAttribute('aria-pressed') === 'true'", 5)
            hp.keyboard.press('h')
            return 'M (Mikro), H (Hand)'
        pruefen('Tastenkürzel', kuerzel)

        def teilen():
            if hp.evaluate("!navigator.mediaDevices?.getDisplayMedia"):
                if hp.is_visible('#s-teilen'):
                    raise AssertionError('Teilen-Knopf sichtbar ohne getDisplayMedia')
                return 'nicht unterstützt', 'kein getDisplayMedia – Knopf ausgeblendet'
            hp.click('#s-teilen')
            try:
                hp.wait_for_selector('#buehne .kv.bildschirm.eigene-freigabe', timeout=15000)
            except Exception:  # noqa: BLE001
                # Headless ohne Bildschirm-Auswahl: der Browser lehnt ab; Agora muss sauber zurückfallen
                if hp.get_attribute('#s-teilen', 'aria-pressed') == 'true':
                    raise
                grund = hp.evaluate('window.__gdmFehler || ""')
                if grund and not grund.startswith(('NotAllowedError', 'AbortError', 'NotFoundError')):
                    raise AssertionError(f'getDisplayMedia: {grund}')
                return 'nicht automatisierbar', f'headless keine Bildschirmauswahl ({grund[:80] or "keine Antwort"}); Knopf sauber zurückgesetzt'
            gp.wait_for_selector('#buehne .kv.bildschirm.mit-video video', timeout=20000)
            time.sleep(1)
            gp.screenshot(path=f'{OUT}matrix-{BROWSER}-05-gast-sieht-bildschirm.png')
            hp.click('#s-teilen')
            gp.wait_for_selector('#buehne .kv.bildschirm', state='detached', timeout=15000)
            return 'Gast sieht den geteilten Bildschirm'
        pruefen('Bildschirmfreigabe', teilen)

        TINTE = """() => { const c = document.querySelector('.wb .wb-tafel'); if (!c || !c.width) return 0;
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0;
            for (let i = 0; i < d.length; i += 16) if (d[i] + d[i + 1] + d[i + 2] < 600) n++; return n; }"""

        def whiteboard():
            hp.click('#s-mehr'); hp.click('.menue [role=menuitem] >> text=Whiteboard')
            hp.wait_for_selector('.wb:not([hidden]) .wb-tafel')
            box = hp.locator('.wb .wb-tafel').bounding_box()
            hp.mouse.move(box['x'] + 120, box['y'] + 160); hp.mouse.down()
            for x, y in [(180, 220), (240, 170), (320, 260), (400, 180)]:
                hp.mouse.move(box['x'] + x, box['y'] + y, steps=6)
            hp.mouse.up()
            hp.click('.wb-leiste [data-werkzeug=rechteck]')
            hp.mouse.move(box['x'] + 460, box['y'] + 140); hp.mouse.down()
            hp.mouse.move(box['x'] + 640, box['y'] + 260, steps=6); hp.mouse.up()
            hp.click('.wb [data-aktion=zeigen]')
            gp.wait_for_selector('.wb:not([hidden]) .wb-tafel', timeout=10000)
            warten_js(gp, f'({TINTE})() > 200', 10)
            # Gast zeichnet zurück
            vorher = hp.evaluate(f'({TINTE})()')
            gbox = gp.locator('.wb .wb-tafel').bounding_box()
            gp.click('.wb-leiste [data-werkzeug=stift]')
            unten = gbox['y'] + gbox['height'] - 110
            gp.mouse.move(gbox['x'] + 40, unten - 60); gp.mouse.down()
            gp.mouse.move(gbox['x'] + 250, unten, steps=8); gp.mouse.up()
            warten_js(hp, f'({TINTE})() > {vorher}', 10)
            gp.screenshot(path=f'{OUT}matrix-{BROWSER}-06-gast-whiteboard.png')
            gp.click('.wb [data-aktion=schliessen]'); hp.click('.wb [data-aktion=schliessen]')
            return 'Host → Gast und Gast → Host'
        pruefen('Whiteboard', whiteboard)

        def aufnahme():
            # Lokale Aufnahme (Canvas + Web Audio + MediaRecorder) → Upload in die Bibliothek
            hp.click('#s-mehr')
            eintrag = hp.locator('.menue [role=menuitem]:has-text("Aufnahme starten")')
            if eintrag.is_disabled():
                hp.keyboard.press('Escape')
                if hp.evaluate("typeof MediaRecorder === 'undefined'"):
                    return 'nicht unterstützt', 'kein MediaRecorder – Menüeintrag gesperrt'
                raise AssertionError('Aufnahme gesperrt, obwohl MediaRecorder da ist')
            eintrag.click()
            hp.click('dialog button:has-text("Aufnahme starten")')
            gp.click('dialog button:has-text("Verstanden")', timeout=10000)      # Gast sieht den Aufnahme-Hinweis
            time.sleep(4)
            hp.click('#s-mehr'); hp.click('.menue [role=menuitem]:has-text("Aufnahme beenden")')
            hp.wait_for_selector('.upload-box:has-text("In deiner Bibliothek gespeichert")', timeout=30000)
            liste = hp.evaluate("async () => (await (await fetch('/api/aufnahmen', {headers: {'X-Agora': '1'}})).json())")
            a = (liste.get('aufnahmen') or [])[0]
            hp.click('.upload-box button:has-text("Schließen")')
            return f"{a['typ']}, {a['groesse'] // 1024} KB"
        pruefen('Lokale Aufnahme + Upload', aufnahme)

        def verlassen():
            gp.click('#s-verlassen'); gp.click('.menue [role=menuitem] >> nth=0')
            hp.wait_for_selector('#buehne .kv:nth-child(2)', state='detached', timeout=15000)
        pruefen('Verlassen', verlassen)
        gast.close(); seiten.remove(gp)
    else:
        for name in ('Video in beide Richtungen', 'Ton (Audio-Track empfangen)', 'Chat', 'Tastenkürzel', 'Bildschirmfreigabe', 'Whiteboard', 'Verlassen'):
            ergebnis(name, 'FEHLER', 'nicht geprüft (Beitritt fehlgeschlagen)')

    # ---- Ende-zu-Ende-Verschlüsselung ----
    def e2ee():
        ep = host.new_page(); beobachten(ep, 'host-e2ee'); seiten.append(ep)
        ep.goto(B + '/')
        r = ep.evaluate("""async () => (await (await fetch('/api/raeume', {method:'POST', headers:{'X-Agora':'1','Content-Type':'application/json'},
            body: JSON.stringify({titel:'Matrix E2EE'})})).json())""")
        ecode = r['raum']['code']
        ep.goto(f'{B}/r/{ecode}')
        ep.wait_for_selector('#feld-e2ee:not([hidden]) input[name=e2ee]')
        if not ep.evaluate("(() => { try { return !!(window.RTCRtpScriptTransform || (window.RTCRtpSender && 'createEncodedStreams' in RTCRtpSender.prototype)); } catch { return false; } })()"):
            # Browser ohne Encoded Transforms: Schalter gesperrt; ein E2EE-Raum (anderswo angelegt) lässt nicht beitreten
            if not ep.is_disabled('#feld-e2ee input[name=e2ee]'):
                raise AssertionError('E2EE-Schalter aktiv, obwohl der Browser es nicht kann')
            # Raum so stellen, als hätte ihn ein anderer Browser verschlüsselt (Prüfwert egal: der Beitritt endet vorher)
            st = ep.evaluate(f"""async () => (await fetch('/api/raeume/{ecode}', {{method:'PATCH', headers:{{'X-Agora':'1','Content-Type':'application/json'}},
                body: JSON.stringify({{e2ee: true, e2ee_pruef: '{'0' * 32}'}})}})).status""")
            if st != 200:
                raise AssertionError(f'E2EE-Raum anlegen: HTTP {st}')
            ep.goto(f'{B}/r/{ecode}#k=' + 'A' * 43)
            ep.reload()                                   # goto mit nur neuem Fragment lädt nicht neu
            ep.wait_for_selector('#beitritt-status.err', timeout=10000)
            if not ep.is_disabled('#beitreten-knopf'):
                raise AssertionError('Beitritt zu E2EE-Raum möglich, obwohl der Browser es nicht kann')
            ep.screenshot(path=f'{OUT}matrix-{BROWSER}-07-e2ee-nicht-unterstuetzt.png')
            meldung = ep.inner_text('#beitritt-status')
            ep.close(); seiten.remove(ep)
            return 'nicht unterstützt', f'kein RTCRtpScriptTransform/Insertable Streams – Lobby sperrt mit Hinweis „{meldung[:60]}…“'
        ep.click('#feld-e2ee label.schalter')
        ep.wait_for_selector('#merkmal-e2ee')
        elink = ep.url
        if not re.search(r'#k=[A-Za-z0-9_-]{43}$', elink):
            raise AssertionError(f'kein Schlüssel im Link ({elink})')
        ep.click('#beitreten-knopf')
        ep.wait_for_selector('#v-meeting:not([hidden])', timeout=25000)
        ep.wait_for_selector('#m-e2ee:not([hidden])', timeout=10000)
        eg = kontext(); egp = eg.new_page(); beobachten(egp, 'gast-e2ee'); seiten.append(egp)
        egp.goto(elink)
        egp.wait_for_selector('#merkmal-e2ee')
        egp.fill('#f-beitritt [name=name]', 'Gast E2EE')
        egp.click('#beitreten-knopf')
        egp.wait_for_selector('#v-meeting:not([hidden])', timeout=25000)
        egp.wait_for_selector('#m-e2ee:not([hidden])', timeout=10000)
        egp.wait_for_selector('#buehne .kv.mit-video:not(.spiegeln) video', timeout=20000)
        ep.wait_for_selector('#buehne .kv.mit-video:not(.spiegeln) video', timeout=20000)
        time.sleep(2)
        a = video_kommt_an(egp); b = video_kommt_an(ep)
        if egp.is_visible('#m-banner.err'):
            raise AssertionError('Entschlüsselungsfehler: ' + egp.inner_text('#m-banner'))
        egp.screenshot(path=f'{OUT}matrix-{BROWSER}-07-e2ee-gast.png')
        eg.close(); seiten.remove(egp); ep.close(); seiten.remove(ep)
        return f'entschlüsselt: Host→Gast {a}, Gast→Host {b}'
    if angemeldet:
        pruefen('Ende-zu-Ende-Verschlüsselung', e2ee)
    else:
        ergebnis('Ende-zu-Ende-Verschlüsselung', 'FEHLER', 'nicht geprüft (Anmeldung fehlgeschlagen)')

    # ---- Teams / Organisationen im Konto: anlegen, Meeting der Organisation planen ----
    def teams():
        hp.goto(B + '/konto#teams')
        hp.wait_for_selector('#p-teams:not([hidden]) #liste-orgs')
        hp.click('#org-neu')
        hp.wait_for_selector('dialog [name=name]')
        hp.fill('dialog [name=name]', f'Team {BROWSER}')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector(f'#org-detail:not([hidden]) #org-titel:has-text("Team {BROWSER}")')
        hp.click('[data-org-planen]')
        hp.wait_for_selector('dialog [name=titel]')
        hp.fill('dialog [name=titel]', 'Team-Weekly')
        hp.click('dialog button[type=submit]')
        hp.wait_for_selector('#liste-org-raeume li[data-org-raum]:has-text("Team-Weekly")')
        hp.screenshot(path=f'{OUT}matrix-{BROWSER}-09-teams.png', full_page=True)
        return 'Organisation angelegt, Meeting geplant'
    if angemeldet:
        pruefen('Teams / Organisationen', teams)

    # ---- Öffentliche Status-Seite (ohne Anmeldung) ----
    def status_seite():
        sk = kontext(); sp = sk.new_page(); beobachten(sp, 'status'); seiten.append(sp)
        sp.goto(B + '/status')
        sp.wait_for_selector('#dienste [data-dienst="app"] .status-balken i')
        n90 = sp.eval_on_selector_all('#dienste [data-dienst="app"] .status-balken i', 'e => e.length')
        sp.click('.status-zeitraum label:has-text("24 Stunden")')
        n24 = sp.eval_on_selector_all('#dienste [data-dienst="app"] .status-balken i', 'e => e.length')
        if (n90, n24) != (90, 24):
            raise AssertionError(f'Balken {n90}/{n24} statt 90/24')
        sp.set_viewport_size({'width': 390, 'height': 844}); time.sleep(0.5)
        if sp.evaluate('document.documentElement.scrollWidth > innerWidth + 1'):
            raise AssertionError('waagrechtes Scrollen bei 390 px')
        sp.screenshot(path=f'{OUT}matrix-{BROWSER}-10-status.png', full_page=True)
        sk.close(); seiten.remove(sp)
        return 'Balken 90/24, schmal ohne Querscrollen'
    pruefen('Status-Seite', status_seite)

    # ---- Video-Effekte (MediaPipe, selbst gehostet) in der Lobby – eigener Kontext, die Wahl bleibt im localStorage ----
    def effekte():
        fx = kontext(); fp = fx.new_page(); beobachten(fp, 'effekte'); seiten.append(fp)
        fp.goto(f"{B}/r/{zustand['code']}")
        fp.wait_for_selector('#v-lobby:not([hidden])')
        fp.click('#lobby-effekte')
        fp.wait_for_selector('.menue .effekte')
        if fp.locator('.menue .effekte .status.warn').count():
            hinweis = fp.inner_text('.menue .effekte .status.warn')
            webgl2 = fp.evaluate("!!document.createElement('canvas').getContext('webgl2')")
            fx.close(); seiten.remove(fp)
            if not webgl2:
                return 'nicht automatisierbar', 'headless ohne WebGL2 (keine GPU im Container) – Menü zeigt den Hinweis korrekt'
            return 'nicht unterstützt', f'Menü zeigt „{hinweis[:50]}…“'
        fp.click('.effekte [data-wert="leicht"]')
        fp.wait_for_selector('.effekte .effekt-status[data-zustand="aktiv"], .effekte .effekt-status[data-zustand="fehler"]',
                             state='attached', timeout=45000)
        z = fp.get_attribute('.effekte .effekt-status', 'data-zustand')
        if z != 'aktiv':
            raise AssertionError('Effekt-Fehler: ' + fp.inner_text('.effekte .effekt-status'))
        time.sleep(1.5)
        fp.screenshot(path=f'{OUT}matrix-{BROWSER}-08-effekt.png')
        fp.keyboard.press('Escape')
        fx.close(); seiten.remove(fp)
        return 'Hintergrund leicht unscharf aktiv'
    if lobby_ok:
        pruefen('Video-Effekte (Unschärfe)', effekte)

    # JS-Fehler der Seiten zählen als Fehler
    ergebnis('Keine JS-Fehler', 'ok' if not js_fehler else 'FEHLER', '; '.join(js_fehler[:5]))
    br.close()

with open(f'{OUT}matrix-{BROWSER}.json', 'w') as f:
    json.dump({'browser': BROWSER, 'version': version, 'ergebnisse': {k: {'ergebnis': w, 'hinweis': h} for k, (w, h) in ergebnisse.items()}},
              f, ensure_ascii=False, indent=1)
print(f'\n=== {BROWSER} {version} ===')
for k, (w, h) in ergebnisse.items():
    print(f'{w:22} {k}{"  (" + h + ")" if h else ""}')
sys.exit(1 if any(w == 'FEHLER' for w, _ in ergebnisse.values()) else 0)
