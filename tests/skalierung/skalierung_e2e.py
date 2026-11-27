# Skalierungsbeweis (ADR 0007) mit zwei echten LiveKit-Knoten, gemeinsamem Redis und Chromium (Kamera-Attrappe).
# Lauf: tests/skalierung/lauf.sh – das Skript startet/ändert/stoppt zwischen den Abschnitten die Container und
# meldet sich über Dateien in STEUER (<abschnitt>.fertig → lauf.sh handelt → <abschnitt>.weiter).
#
# Welcher Knoten einen Raum trägt, wird dreifach und unabhängig geprüft:
#   1. Redis: room_node_map[<code>] = Knoten-ID (LiveKit-Routing), Knoten-IDs aus den Prometheus-Metriken je Knoten
#   2. Prometheus je Knoten: livekit_room_total
#   3. Browser: die ICE-Kandidaten, die der Server jeder verbundenen PeerConnection genannt hat (UDP 7862 = Knoten A,
#      7872 = Knoten B) – die Medien dieser Verbindung gehen an genau diesen Knoten
# Medien fließen, wenn die Zahl der dekodierten Bilder fremder Videos in jedem Browser wächst.
import json, os, re, socket, time, traceback, urllib.request
from playwright.sync_api import sync_playwright

B = os.environ.get('BASIS', 'http://127.0.0.1:8261')
OUT = os.environ.get('AUSGABE', '/tmp/')
STEUER = os.environ.get('STEUER', OUT)
REDIS = ('127.0.0.1', 6392, 'skalierung-test-passwort')
KNOTEN = {'A': {'metriken': 'http://127.0.0.1:7863/metrics', 'udp': 7862, 'signal': 'ws://127.0.0.1:7860'},
          'B': {'metriken': 'http://127.0.0.1:7873/metrics', 'udp': 7872, 'signal': 'ws://127.0.0.1:7870'}}
H = {'X-Agora': '1', 'Content-Type': 'application/json'}
fehler, ergebnis = [], {}
BEKANNT = lambda text: 'could not find published track' in text or 'VideoFrame was garbage collected' in text

# Jede RTCPeerConnection merken (für getStats) – als Init-Skript, die CSP der Seite gilt dafür nicht
PCS = """(() => { const O = window.RTCPeerConnection; if (!O) return; window.__pcs = [];
  window.RTCPeerConnection = class extends O { constructor(...a) { super(...a); window.__pcs.push(this); } }; })();"""
# Je verbundener PeerConnection: Ports der Host-Kandidaten des Servers (kamen über die Signalisierung vom Knoten des
# Raums) und Art des Paars, über das der Transport gerade sendet. Hinweis: Auf diesem Rechner schickt Chromium manchmal
# von einer Docker-Brücken-Adresse an 127.0.0.1 – die Antwort kommt dann über das NAT der Brücke mit anderem Quellport
# zurück, und Chromium wählt ein „prflx“-Paar (Adresse verborgen). Ziel bleibt der Host-Kandidat derselben Verbindung.
PORTS = """async () => { const host = [], gewaehlt = [];
  for (const pc of window.__pcs || []) {
    if (pc.connectionState !== 'connected') continue;
    const s = await pc.getStats();
    s.forEach(r => { if (r.type === 'remote-candidate' && r.candidateType === 'host') host.push(r.protocol + ':' + r.port); });
    s.forEach(t => { if (t.type !== 'transport' || !t.selectedCandidatePairId) return;
      const paar = s.get(t.selectedCandidatePairId), rc = paar && s.get(paar.remoteCandidateId);
      if (rc) gewaehlt.push(rc.candidateType === 'host' ? rc.protocol + ':' + rc.port : rc.candidateType + ' (NAT)'); });
  }
  return [[...new Set(host)].sort(), gewaehlt]; }"""


def check(bed, text):
    print(('ok   ' if bed else 'FEHLER ') + text, flush=True)
    if not bed:
        fehler.append(text)


def redis(*args):
    s = socket.create_connection(REDIS[:2], timeout=3)
    def cmd(*a):
        s.sendall(f'*{len(a)}\r\n'.encode() + b''.join(f'${len(str(x).encode())}\r\n{x}\r\n'.encode() for x in a))
        f = s.makefile('rb')
        z = f.readline()
        if z.startswith(b'$'):
            n = int(z[1:])
            return None if n < 0 else f.read(n + 2)[:-2].decode()
        return z.decode().strip()
    cmd('AUTH', REDIS[2])
    erg = cmd(*args)
    s.close()
    return erg


def prometheus():
    """{'A': (node_id, rooms) | None, 'B': …} aus den Metriken je Knoten."""
    erg = {}
    for k, v in KNOTEN.items():
        try:
            t = urllib.request.urlopen(v['metriken'], timeout=3).read().decode()
            m = re.search(r'^livekit_room_total\{node_id="(ND_\w+)"[^}]*\} (\d+)', t, re.M)
            erg[k] = (m.group(1), int(m.group(2))) if m else None
        except OSError:
            erg[k] = None
    return erg


def wo(code):
    """Knoten (A/B) des LiveKit-Raums laut Redis-Routing."""
    nid = redis('HGET', 'room_node_map', code)
    ids = {v[0]: k for k, v in prometheus().items() if v}
    return ids.get(nid, nid)


def frames(page):
    return page.evaluate("""() => [...document.querySelectorAll('#buehne .kv.mit-video:not(.spiegeln) video')]
        .reduce((s, v) => s + (v.getVideoPlaybackQuality?.().totalVideoFrames || 0), 0)""")


def schritt(name):
    open(f'{STEUER}{name}.fertig', 'w').close()
    print(f'--- {name} fertig, warte auf lauf.sh …', flush=True)
    ende = time.time() + 300
    while not os.path.exists(f'{STEUER}{name}.weiter'):
        if time.time() > ende:
            raise AssertionError(f'lauf.sh hat nach {name} nicht weitergemacht')
        time.sleep(0.5)


with sync_playwright() as p:
    br = p.chromium.launch(args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'])
    seiten = []

    def kontext(land=None):
        ctx = br.new_context(locale='de-DE', viewport={'width': 640, 'height': 480}, permissions=['camera', 'microphone'],
                             extra_http_headers={'CF-IPCountry': land} if land else {})
        ctx.add_init_script(PCS)
        return ctx

    def verwaltung(abschnitt):
        """Konto anlegen (die App im Testmodus hält alles im Speicher – nach jedem Neustart neu)."""
        ctx = kontext()
        pg = ctx.new_page()
        pg.goto(B + '/')
        email = f'skal-{abschnitt}@example.com'
        r = pg.request.post(B + '/api/konto/registrieren', headers=H, data=json.dumps({'name': 'Skalierung', 'email': email, 'passwort': 'geheimespasswort1'}))
        assert r.ok, r.text()
        mail = pg.request.get(B + '/api/test/posteingang').json()['mails'][-1]['text']
        pg.goto(re.search(r'https?://\S+bestaetigen\?t=[\w\-.]+', mail).group(0))
        return pg

    def meeting(admin, titel):
        r = admin.request.post(B + '/api/raeume', headers=H, data=json.dumps({'titel': titel}))
        assert r.ok, r.text()
        return r.json()['raum']['code']

    def beitreten(code, name, land=None):
        """Gast tritt über die Oberfläche bei → (Seite, Signal-URL aus der Antwort der App)."""
        pg = kontext(land).new_page()
        pg.on('pageerror', lambda e: fehler.append(f'[{name}] pageerror: {e}'))
        pg.on('console', lambda m: m.type == 'error' and not BEKANNT(m.text) and print(f'[{name}] console: {m.text[:160]}'))
        pg.goto(f'{B}/r/{code}')
        pg.wait_for_selector('#v-lobby:not([hidden])')
        pg.fill('#f-beitritt [name=name]', name)
        with pg.expect_response(lambda r: r.url.endswith('/beitreten') and r.request.method == 'POST') as antwort:
            pg.click('#beitreten-knopf')
        url = antwort.value.json()['zugang']['url']
        pg.wait_for_selector('#v-meeting:not([hidden])', timeout=30000)
        seiten.append(pg)
        return pg, url

    def medien(pg, wer, sekunden=3):
        """(Ports der Medienverbindungen, Bilder vorher, nachher) – wartet, bis ein fremdes Video da ist."""
        ende = time.time() + 30
        while time.time() < ende and frames(pg) == 0:
            time.sleep(0.5)
        f0 = frames(pg); time.sleep(sekunden); f1 = frames(pg)
        ports, gewaehlt = pg.evaluate(PORTS)
        print(f'  {wer}: Server-Kandidaten {ports}, gewähltes Paar {gewaehlt}, Bilder {f0} → {f1}', flush=True)
        return ports, f0, f1

    def raum_pruefen(code, erwartet, teilnehmer, titel):
        knoten = wo(code)
        check(knoten == erwartet, f'{titel}: Raum {code} liegt auf Knoten {knoten} (erwartet {erwartet})')
        for pg, name in teilnehmer:
            ports, f0, f1 = medien(pg, name)
            u = KNOTEN[erwartet]['udp']
            check(f'udp:{u}' in ports and set(ports) <= {f'udp:{u}', f'tcp:{u - 1}'},
                  f'{titel}/{name}: Medien direkt an Knoten {erwartet} (UDP {u}): {ports}')
            check(f1 > f0, f'{titel}/{name}: Video läuft ({f0} → {f1} Bilder)')
        return knoten

    def alle_schliessen():
        for pg in seiten:
            pg.context.close()
        seiten.clear()

    try:
        # ============ 1. Verteilung: beide Knoten in derselben Region, LiveKit verteilt die Räume ============
        print('=== 1. Verteilung (beide Knoten eu-central, sort_by: rooms) ===', flush=True)
        admin = verwaltung('1')
        codes = [meeting(admin, f'Verteilung {i}') for i in range(4)]
        erste = []
        for i, code in enumerate(codes):
            erste.append(beitreten(code, f'Erste {i}'))
            time.sleep(3)          # Knotenstatistik (Räume je Knoten) in Redis aktualisiert sich alle paar Sekunden
        zweite = [beitreten(code, f'Zweite {i}') for i, code in enumerate(codes)]
        time.sleep(4)
        verteilung = {code: wo(code) for code in codes}
        print('  Räume → Knoten (Redis):', verteilung, flush=True)
        check(set(verteilung.values()) == {'A', 'B'}, f'Räume auf beiden Knoten verteilt: {sorted(verteilung.values())}')
        prom = prometheus()
        print('  Prometheus livekit_room_total:', prom, flush=True)
        for k in ('A', 'B'):
            check(prom[k] and prom[k][1] == list(verteilung.values()).count(k), f'Prometheus Knoten {k}: {prom[k]} Räume = Redis')
        for i, code in enumerate(codes):
            raum_pruefen(code, verteilung[code], [(erste[i][0], f'Erste {i}'), (zweite[i][0], f'Zweite {i}')], f'Verteilung {i}')
        check(all(u == KNOTEN['A']['signal'] for _p, u in erste + zweite), 'Signal-URL ohne Region: erster Knoten (A) – LiveKit leitet zum Raum-Knoten weiter')
        lage = admin.request.get(B + '/api/gesund?tief=1').json()['pruefungen']['livekit']
        check(lage['ok'] and lage['verbunden'] == 8, f'App: LiveKit ok, {lage["verbunden"]} verbunden (ListRooms über einen Knoten zählt alle Räume)')
        erste[0][0].screenshot(path=OUT + 'skal-1-verteilung.png')
        ergebnis['verteilung'] = verteilung
        alle_schliessen()
        schritt('verteilung')

        # ============ 2. Regionen: A eu-central, B eu-west ============
        print('=== 2. Regionen (A eu-central, B eu-west, regionaware) ===', flush=True)
        admin = verwaltung('2')
        de, fr, ohne = meeting(admin, 'DE'), meeting(admin, 'FR'), meeting(admin, 'ohne Land')
        d1, u_d1 = beitreten(de, 'Dora', 'DE')
        d2, _u = beitreten(de, 'Dieter', 'DE')
        f1, u_f1 = beitreten(fr, 'Fleur', 'FR')
        f2, _u = beitreten(fr, 'Florent', 'FR')
        x, u_x = beitreten(fr, 'Xaver (aus DE)', 'DE')     # Signal über Knoten A, Raum auf Knoten B
        o1, u_o1 = beitreten(ohne, 'Otto')
        o2, _u = beitreten(ohne, 'Olga')
        time.sleep(3)
        check(u_d1 == KNOTEN['A']['signal'] and u_f1 == KNOTEN['B']['signal'] and u_o1 == KNOTEN['A']['signal'],
              f'Signal-URL je Land: DE {u_d1}, FR {u_f1}, ohne {u_o1}')
        check(u_x == KNOTEN['A']['signal'], f'Xaver (DE) im FR-Raum signalisiert über Knoten A: {u_x}')
        raum_pruefen(de, 'A', [(d1, 'Dora'), (d2, 'Dieter')], 'Region DE')
        raum_pruefen(fr, 'B', [(f1, 'Fleur'), (f2, 'Florent'), (x, 'Xaver (aus DE)')], 'Region FR')
        raum_pruefen(ohne, 'A', [(o1, 'Otto'), (o2, 'Olga')], 'ohne Land')
        x.screenshot(path=OUT + 'skal-2-quer.png')
        ergebnis['regionen'] = {'de': wo(de), 'fr': wo(fr), 'ohne': wo(ohne)}
        for pg in (d1, d2, o1, o2):          # Räume auf A verlassen; der FR-Raum (Knoten B) läuft über den Ausfall weiter
            pg.context.close()
            seiten.remove(pg)
        schritt('regionen')

        # ============ 3. Ausfall: Knoten A hart beendet (docker kill) ============
        print('=== 3. Ausfall von Knoten A ===', flush=True)
        _p, g0, g1 = medien(f1, 'Fleur (Raum auf B, nach dem Ausfall von A)')
        check(g1 > g0, 'Raum auf dem überlebenden Knoten B läuft weiter')
        try:
            print('  Xaver (signalisiert über A):', x.evaluate("document.querySelector('#m-banner')?.innerText || 'kein Hinweis'")[:120])
        except Exception as e:     # noqa: BLE001
            print('  Xaver:', type(e).__name__)
        prom = prometheus()
        check(prom['A'] is None and prom['B'] is not None, f'Prometheus: A weg, B da ({prom})')
        time.sleep(8)              # LiveKit hält einen hart beendeten Knoten noch ≈ 5–8 s für verfügbar (Statistik in Redis)
        g = admin.request.get(B + '/api/gesund?tief=1').json()['pruefungen']['livekit']
        print('  App /api/gesund?tief=1 livekit:', g, flush=True)
        check(g['ok'] and [k['ok'] for k in g['knoten']] == [False, True], 'App: LiveKit ok über Knoten B, Knoten A als ausgefallen gemeldet')
        t = admin.request.get(B + '/api/metriken').text()
        check('agora_livekit_knoten_erreichbar{knoten="eu-central-1",region="eu-central"} 0' in t
              and 'agora_livekit_knoten_erreichbar{knoten="eu-west-2",region="eu-west"} 1' in t, 'App-Metrik je Knoten: A 0, B 1')
        neu = meeting(admin, 'nach dem Ausfall')
        n1, u_n1 = beitreten(neu, 'Nora')       # ohne Land → wäre A; A ist weg → B
        n2, _u = beitreten(neu, 'Nils', 'DE')   # DE → wäre A
        check(u_n1 == KNOTEN['B']['signal'], f'Signal-URL nach dem Ausfall: {u_n1}')
        raum_pruefen(neu, 'B', [(n1, 'Nora'), (n2, 'Nils')], 'Neuer Raum nach Ausfall')
        e1, _u = beitreten(de, 'Dora wieder', 'DE')       # Raum lag auf A → wird auf B neu angelegt
        e2, _u = beitreten(de, 'Dieter wieder', 'DE')
        raum_pruefen(de, 'B', [(e1, 'Dora wieder'), (e2, 'Dieter wieder')], 'DE-Raum nach Ausfall neu')
        n1.screenshot(path=OUT + 'skal-3-ausfall.png')
        ergebnis['ausfall'] = {'neu': wo(neu), 'de': wo(de)}
    except Exception:
        traceback.print_exc()
        fehler.append('Ablauf abgebrochen')
    print('Ergebnis:', json.dumps(ergebnis))
    print('\n'.join(fehler) or 'Skalierungsbeweis ok')
    br.close()
    open(f'{STEUER}ende', 'w').write('fehler' if fehler else 'ok')
