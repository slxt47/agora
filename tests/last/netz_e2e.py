# Netzsimulation: zwei Browser (Host + Gast, Kamera/Mikro-Attrappe) im Meeting; nach einer Messung mit gutem Netz
# wird eth0 dieses Containers mit tc/netem verschlechtert (Verlust 5 %, 150 ms Verzögerung, 1 Mbit/s – nur ausgehend).
# Erwartung: das Gespräch bleibt bestehen, Ton kommt weiter an, Video geht in der Qualität herunter, Agora zeigt
# „Schlecht“ im Popover „Verbindung“ und warnt nach ~30 s. Läuft nur über scripts/lasttest.sh netz (eigenes Docker-Netz,
# NET_ADMIN nur für diesen Container).
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback

from playwright.sync_api import sync_playwright

APP = os.environ['APP']                         # z. B. 10.250.77.11:8132 (App im eigenen Docker-Netz)
B = 'http://127.0.0.1:8132'                     # Kamera/Mikro gibt es nur in sicherem Kontext → lokal weiterleiten
OUT = os.environ.get('AUSGABE', '/tmp/')
NETEM = os.environ.get('NETEM', 'loss 5% delay 150ms rate 1mbit')
DAUER = int(os.environ.get('DAUER_S', '60'))
fehler = []

# Alle PeerConnections einsammeln, damit wir getStats() von außen lesen können
SAMMLER = """(() => { const O = window.RTCPeerConnection; window.__pcs = [];
  window.RTCPeerConnection = function (...a) { const p = new O(...a); window.__pcs.push(p); return p; };
  window.RTCPeerConnection.prototype = O.prototype; Object.setPrototypeOf(window.RTCPeerConnection, O); })();"""

MESSEN = """async () => {
  const r = { audioEin: 0, videoEin: 0, bilder: 0, breiteEin: 0, breiteAus: 0, grund: '', verlust: 0, pakete: 0, rtt: 0 };
  for (const pc of window.__pcs) {
    if (pc.connectionState === 'closed') continue;
    for (const s of (await pc.getStats()).values()) {
      if (s.type === 'inbound-rtp' && s.kind === 'audio') { r.audioEin += s.bytesReceived || 0; r.verlust += s.packetsLost || 0; r.pakete += s.packetsReceived || 0; }
      if (s.type === 'inbound-rtp' && s.kind === 'video') { r.videoEin += s.bytesReceived || 0; r.bilder += s.framesDecoded || 0; r.breiteEin = Math.max(r.breiteEin, s.frameWidth || 0); }
      if (s.type === 'outbound-rtp' && s.kind === 'video' && (s.frameWidth || 0) > r.breiteAus) { r.breiteAus = s.frameWidth; r.grund = s.qualityLimitationReason || ''; }
      if (s.type === 'candidate-pair' && s.nominated && s.state === 'succeeded' && s.currentRoundTripTime) r.rtt = Math.round(s.currentRoundTripTime * 1000);
    }
  }
  return r; }"""


def messen(seite, sekunden=10):
    a = seite.evaluate(MESSEN)
    time.sleep(sekunden)
    b = seite.evaluate(MESSEN)
    return {'audio_kbit': round((b['audioEin'] - a['audioEin']) * 8 / 1000 / sekunden), 'video_kbit': round((b['videoEin'] - a['videoEin']) * 8 / 1000 / sekunden),
            'bilder_s': round((b['bilder'] - a['bilder']) / sekunden, 1), 'breite_ein': b['breiteEin'], 'breite_aus': b['breiteAus'],
            'begrenzt_durch': b['grund'], 'audio_verlust_prozent': round(100 * (b['verlust'] - a['verlust']) / max(1, (b['verlust'] - a['verlust']) + (b['pakete'] - a['pakete'])), 1),
            'rtt_ms': b['rtt']}


def weiterleiten():
    """127.0.0.1:8132 → APP (nur HTTP der Seite; Medien gehen direkt an LiveKit im Docker-Netz)."""
    ziel = APP.split(':')
    server = socket.socket(); server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', 8132)); server.listen(64)

    def pumpen(a, b):
        try:
            while (d := a.recv(65536)):
                b.sendall(d)
        except OSError:
            pass
        finally:
            for x in (a, b):
                try: x.shutdown(socket.SHUT_RDWR)
                except OSError: pass

    def annehmen():
        while True:
            c, _ = server.accept()
            z = socket.create_connection((ziel[0], int(ziel[1])))
            threading.Thread(target=pumpen, args=(c, z), daemon=True).start()
            threading.Thread(target=pumpen, args=(z, c), daemon=True).start()
    threading.Thread(target=annehmen, daemon=True).start()


def tc(*args):
    r = subprocess.run(['tc', *args], capture_output=True, text=True)
    print('tc', ' '.join(args), '→', r.returncode, r.stderr.strip())
    return r.returncode == 0


def beobachten(page, wer):
    page.on('pageerror', lambda e: fehler.append(f'[{wer}] pageerror: {e}'))


ergebnis = {'netem': NETEM}
weiterleiten()
with sync_playwright() as p:
    br = p.chromium.launch(args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required',
                                 ])
    host = br.new_context(viewport={'width': 1280, 'height': 800}, permissions=['camera', 'microphone'])
    gast = br.new_context(viewport={'width': 1280, 'height': 800}, permissions=['camera', 'microphone'])
    for c in (host, gast):
        c.add_init_script(SAMMLER)
    hp, gp = host.new_page(), gast.new_page()
    beobachten(hp, 'host'); beobachten(gp, 'gast')
    try:
        # Konto + Raum über die API (Testmodus: Mail liegt im Speicher)
        H = {'X-Agora': '1'}
        hp.request.post(B + '/api/konto/registrieren', headers=H, data={'name': 'Last Host', 'email': 'last@example.com', 'passwort': 'geheimespasswort1'})
        link = re.search(r'https?://\S+bestaetigen\?t=[\w\-.]+', hp.request.get(B + '/api/test/posteingang').json()['mails'][-1]['text']).group(0)
        hp.goto(B + link[link.index('/api/'):])
        code = hp.request.post(B + '/api/raeume', headers=H, data={'titel': 'Netztest'}).json()['raum']['code']
        hp.goto(f'{B}/r/{code}'); hp.wait_for_selector('#v-lobby:not([hidden])'); time.sleep(1)
        hp.click('#beitreten-knopf'); hp.wait_for_selector('#v-meeting:not([hidden])', timeout=30000)
        gp.goto(f'{B}/r/{code}'); gp.wait_for_selector('#v-lobby:not([hidden])')
        gp.fill('#f-beitritt [name=name]', 'Last Gast'); time.sleep(1)
        gp.click('#beitreten-knopf'); gp.wait_for_selector('#v-meeting:not([hidden])', timeout=30000)
        hp.wait_for_selector('#buehne .kv.mit-video:not(.spiegeln)', timeout=30000)
        time.sleep(15)                                   # Simulcast/Bandbreitenschätzung einschwingen lassen
        ergebnis['vorher'] = {'host_empfaengt': messen(hp), 'gast_empfaengt': messen(gp, 5)}
        print('Vorher:', json.dumps(ergebnis['vorher']))
        hp.screenshot(path=OUT + 'netz-1-vorher.png')

        if not tc('qdisc', 'add', 'dev', 'eth0', 'root', 'netem', *NETEM.split()):
            raise RuntimeError('tc/netem ging nicht (NET_ADMIN?)')
        t0 = time.time()
        while time.time() - t0 < DAUER - 20:
            time.sleep(5)
        ergebnis['nachher'] = {'host_empfaengt': messen(hp), 'gast_empfaengt': messen(gp, 10)}
        print('Mit netem:', json.dumps(ergebnis['nachher']))
        # Agora selbst: Popover „Verbindung“ und Warnhinweis
        hp.click('#buehne .kv.spiegeln .kv-menue'); hp.click('.menue [role=menuitem] >> text=Verbindung')
        hp.wait_for_selector('.menue .stat-popover .stat-liste', timeout=20000)
        ergebnis['popover_host'] = hp.inner_text('.menue .stat-popover').replace('\n', ' | ')
        hp.screenshot(path=OUT + 'netz-2-popover.png')
        hp.keyboard.press('Escape')
        ergebnis['warnung_gezeigt'] = hp.locator('.toast:has-text("Verbindung ist seit einer Weile schlecht")').count() > 0 \
            or gp.locator('.toast:has-text("Verbindung ist seit einer Weile schlecht")').count() > 0
        ergebnis['noch_im_meeting'] = hp.locator('#v-meeting:not([hidden])').count() == 1 and gp.locator('#v-meeting:not([hidden])').count() == 1
        gp.screenshot(path=OUT + 'netz-3-gast.png')
        tc('qdisc', 'del', 'dev', 'eth0', 'root')

        n, v = ergebnis['nachher'], ergebnis['vorher']
        if not ergebnis['noch_im_meeting']:
            fehler.append('Meeting abgebrochen')
        if n['host_empfaengt']['audio_kbit'] < 8 or n['gast_empfaengt']['audio_kbit'] < 8:
            fehler.append('Ton kommt mit netem nicht mehr an')
        if n['host_empfaengt']['bilder_s'] <= 0:
            fehler.append('Video steht mit netem komplett')
        herunter = (n['host_empfaengt']['breite_ein'] < v['host_empfaengt']['breite_ein'] or n['host_empfaengt']['video_kbit'] < v['host_empfaengt']['video_kbit'] * 0.7
                    or n['gast_empfaengt']['begrenzt_durch'] == 'bandwidth' or n['host_empfaengt']['begrenzt_durch'] == 'bandwidth')
        ergebnis['video_heruntergeregelt'] = herunter
        if not herunter:
            fehler.append('Video wurde nicht heruntergeregelt')
    except Exception:
        traceback.print_exc()
        fehler.append('Ablauf abgebrochen')
        hp.screenshot(path=OUT + 'netz-fehler-host.png')
    ergebnis['fehler'] = fehler
    print('ERGEBNIS ' + json.dumps(ergebnis, ensure_ascii=False))
    br.close()
sys.exit(1 if fehler else 0)
