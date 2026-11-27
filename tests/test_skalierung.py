"""Mehrere LiveKit-Knoten (ADR 0007): Konfiguration, Wunsch-Region beim Beitritt, Wechsel auf den nächsten Knoten,
wenn einer nicht erreichbar ist, CSP und Metriken je Knoten. Der echte Beweis mit zwei LiveKit-Knoten und Browsern
steht in tests/skalierung/lauf.sh."""
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_api import aufrufe, frisch, konto, lokal, metrik, neu_client, raum, zugang  # noqa: F401

from app import livekit_dienst as lk
from app import kern

A = {'name': 'eu-central-1', 'region': 'eu-central', 'signal': 'wss://rtc-a.example.org', 'api': 'http://127.0.0.1:9'}
B = {'name': 'eu-west-2', 'region': 'eu-west', 'signal': 'wss://rtc-b.example.org', 'api': 'http://127.0.0.1:9'}


class FalschesLiveKit(BaseHTTPRequestHandler):
    """Antwortet wie die Twirp-API: leere Protobuf-Antwort (gültig für ListRooms/CreateRoom …), DeleteRoom → not_found."""
    aufrufe = []

    def do_POST(self):     # noqa: N802
        self.rfile.read(int(self.headers.get('content-length') or 0))
        FalschesLiveKit.aufrufe.append((self.server.server_address[1], self.path.rsplit('/', 1)[-1]))
        if self.path.endswith('/DeleteRoom'):
            body, art, status = b'{"code":"not_found","msg":"room not found"}', 'application/json', 404
        else:
            body, art, status = b'', 'application/protobuf', 200
        self.send_response(status)
        self.send_header('Content-Type', art)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def lebendig():
    s = ThreadingHTTPServer(('127.0.0.1', 0), FalschesLiveKit)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    FalschesLiveKit.aufrufe = []
    yield f'http://127.0.0.1:{s.server_address[1]}', s.server_address[1]
    s.shutdown()


def test_knoten_lesen():
    k = lk.knoten_lesen(' eu-central wss://rtc-fsn.example.org/ http://10.0.0.2:7880 ,eu-west ws://127.0.0.1:7870 http://127.0.0.1:7870,')
    assert k == [{'name': 'eu-central-1', 'region': 'eu-central', 'signal': 'wss://rtc-fsn.example.org', 'api': 'http://10.0.0.2:7880'},
                 {'name': 'eu-west-2', 'region': 'eu-west', 'signal': 'ws://127.0.0.1:7870', 'api': 'http://127.0.0.1:7870'}]
    assert lk.knoten_lesen('') == []
    for falsch in ('eu-central wss://a', 'eu http://a http://b', 'eu wss://a ws://b', 'eu wss://a http://b x'):
        with pytest.raises(ValueError):
            lk.knoten_lesen(falsch)
    assert lk.laender_lesen('de:eu-central, FR : eu-west,,x:,:y') == {'DE': 'eu-central', 'FR': 'eu-west'}


def test_ein_knoten_wie_bisher():
    assert lk.KNOTEN == [] and lk.knoten()[0]['signal'] == kern.LIVEKIT_URL
    assert lk.signal_url() == lk.signal_url('eu-west') == kern.LIVEKIT_URL
    assert lk.region_wahl('eu-west', 'FR') is None                  # ohne Knoten-Liste gibt es keine Regionen
    host, _e, _p = konto()
    code = raum(host)['code']
    assert zugang(host, code, region='eu-west')['url'] == kern.LIVEKIT_URL
    assert 'region' not in aufrufe('raum_anlegen')[-1]


def test_region_wahl_und_reihenfolge(monkeypatch):
    monkeypatch.setattr(lk, 'KNOTEN', [A, B])
    monkeypatch.setattr(lk, 'LAENDER', {'FR': 'eu-west', 'XX': 'mars'})
    assert lk.region_wahl('eu-west') == 'eu-west'
    assert lk.region_wahl('mars', 'fr') == 'eu-west'                # unbekannte Region → Land
    assert lk.region_wahl(None, 'XX') is None and lk.region_wahl(['eu-west']) is None
    assert [k['name'] for k in lk.reihenfolge()] == ['eu-central-1', 'eu-west-2']
    assert [k['name'] for k in lk.reihenfolge('eu-west')] == ['eu-west-2', 'eu-central-1']
    assert lk.signal_url('eu-west') == B['signal'] and lk.signal_url() == A['signal']
    monkeypatch.setitem(lk._GEMIEDEN, A['api'], 1e18)              # A gerade nicht erreichbar → B zuerst (A bleibt als letzte Wahl)
    a2 = dict(A, api='http://127.0.0.2:9')
    monkeypatch.setattr(lk, 'KNOTEN', [dict(A), dict(B, api='http://127.0.0.3:9'), a2])
    assert [k['api'] for k in lk.reihenfolge('eu-central')] == ['http://127.0.0.2:9', 'http://127.0.0.3:9', A['api']]


def test_beitritt_mit_wunsch_region(monkeypatch):
    monkeypatch.setattr(lk, 'KNOTEN', [A, B])
    monkeypatch.setattr(lk, 'LAENDER', {'FR': 'eu-west'})
    host, _e, _p = konto()
    code = raum(host)['code']
    z = zugang(host, code, region='eu-west')
    assert z['url'] == B['signal'] and aufrufe('raum_anlegen')[-1]['region'] == 'eu-west'
    g = neu_client()                                               # Land aus Cloudflare (CF-IPCountry)
    assert g.post(f'/api/raeume/{code}/beitreten', json={'name': 'Fleur'}, headers={'CF-IPCountry': 'FR'}).json()['zugang']['url'] == B['signal']
    assert zugang(neu_client(), code, name='Ohne')['url'] == A['signal']               # ohne Wunsch: erster Knoten
    assert zugang(neu_client(), code, name='Mars', region='mars')['url'] == A['signal']
    # Warteraum: die Region kommt aus der Abfrage, mit der der Gast seinen Zugang abholt
    code2 = raum(host, warteraum=True)['code']
    zugang(host, code2)
    w = neu_client()
    a = w.post(f'/api/raeume/{code2}/beitreten', json={'name': 'Wanda'}).json()
    assert a['status'] == 'wartet'
    from app import db                                              # Zulassen direkt in der DB (Weg des Hosts: test_api)
    db.verbindung().execute("UPDATE anfragen SET status = 'zugelassen' WHERE id = ?", (a['anfrage'],))
    r = w.get(f'/api/raeume/{code2}/warten/{a["anfrage"]}', params={'g': a['anfrage_geheim'], 'region': 'eu-west'}).json()
    assert r['status'] == 'zugelassen' and r['zugang']['url'] == B['signal']


def test_breakout_folgt_der_region_des_hauptraums(monkeypatch, lebendig):
    url, _port = lebendig
    monkeypatch.setattr(lk, 'FAKE', False)
    monkeypatch.setattr(lk, 'KNOTEN', [dict(A, api=url), dict(B, api=url.replace('127.0.0.1', 'localhost'))])
    assert asyncio.run(lk.raum_anlegen('abc-defg-hij', {}, 10, region='eu-west')) is True
    assert lk.raum_region('abc-defg-hij') == lk.raum_region('abc-defg-hij__b2') == 'eu-west'
    assert lk.signal_url(lk.raum_region('abc-defg-hij__b1')) == B['signal']


def test_wechsel_auf_naechsten_knoten(monkeypatch, lebendig):
    url, port = lebendig
    monkeypatch.setattr(lk, 'FAKE', False)
    tot = dict(A)                                                   # 127.0.0.1:9 – dort lauscht nichts
    monkeypatch.setattr(lk, 'KNOTEN', [tot, dict(B, api=url)])
    assert asyncio.run(lk.raeume_liste()) == []                     # A verweigert die Verbindung → B antwortet
    assert FalschesLiveKit.aufrufe == [(port, 'ListRooms')]
    assert lk._GEMIEDEN.get(tot['api'], 0) > 0                     # A wird eine Weile gemieden …
    assert lk.signal_url() == B['signal']                           # … auch für die Signal-URL neuer Teilnehmender
    assert asyncio.run(lk.raum_anlegen('abc-defg-hij', {}, 10)) is True and lk.raum_region('abc-defg-hij') == 'eu-west'
    t = lokal().get('/api/metriken').text
    assert metrik(t, 'agora_livekit_fehler_total{aufruf="knoten_eu-central-1"}') >= 1
    assert metrik(t, 'agora_livekit_knoten_erreichbar{knoten="eu-central-1",region="eu-central"}') == 0
    assert metrik(t, 'agora_livekit_knoten_erreichbar{knoten="eu-west-2",region="eu-west"}') == 1
    assert metrik(t, 'agora_livekit_erreichbar') == 1
    g = lokal().get('/api/gesund?tief=1').json()['pruefungen']['livekit']
    assert g['ok'] is True and [k['ok'] for k in g['knoten']] == [False, True]


def test_kein_wechsel_bei_fehler_des_knotens(monkeypatch, lebendig):
    """Antwortet der Knoten mit einem Fehler (hier not_found), hat er die Anfrage bekommen – kein zweiter Versuch woanders."""
    url, port = lebendig
    monkeypatch.setattr(lk, 'FAKE', False)
    monkeypatch.setattr(lk, 'KNOTEN', [dict(A, api=url), dict(B, api='http://127.0.0.1:9')])
    assert asyncio.run(lk.raum_loeschen('abc-defg-hij', mit_nebenraeumen=False)) is None
    assert FalschesLiveKit.aufrufe == [(port, 'DeleteRoom')] and not lk._GEMIEDEN


def test_csp_mit_allen_knoten(monkeypatch):
    monkeypatch.setattr(lk, 'KNOTEN', [A, dict(B, signal='ws://127.0.0.1:7870'), dict(A, name='x')])
    assert kern.livekit_herkunft() == 'wss://rtc-a.example.org https://rtc-a.example.org ws://127.0.0.1:7870 http://127.0.0.1:7870'
