"""Push-Benachrichtigungen: Verschlüsselung (RFC 8291 Testvektor), VAPID (RFC 8292), Abos, Zustellung an einen
gefälschten Push-Dienst (lokal), Ereignisse (Warteraum, Einladung, Erinnerung, Server-Aufnahme), 404/410 → Abo weg."""
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from test_api import T, beitreten, konto, neu_client, raum, zugang  # noqa: F401

from app import db, egress, push, untertitel, webhooks, webpush
from app import livekit_dienst as lk

# RFC 8291, Anhang A
RFC = {
    'klar': 'V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24',  # gitleaks:allow (RFC-8291-Testvektor, kein Geheimnis)
    'as_privat': 'yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw',  # gitleaks:allow (RFC-8291-Testvektor, kein Geheimnis)
    'as_oeffentlich': 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8',  # gitleaks:allow (RFC-8291-Testvektor, kein Geheimnis)
    'ua_privat': 'q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94',  # gitleaks:allow (RFC-8291-Testvektor, kein Geheimnis)
    'ua_oeffentlich': 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4',  # gitleaks:allow (RFC-8291-Testvektor, kein Geheimnis)
    'auth': 'BTBZMqHH6r4Tts7J_aSIgg',  # gitleaks:allow (RFC-8291-Testvektor, kein Geheimnis)
    'salz': 'DGv6ra1nlYgDCS1FRnbzlw',  # gitleaks:allow (RFC-8291-Testvektor, kein Geheimnis)
    'body': 'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQ'  # gitleaks:allow
            'pu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN',  # gitleaks:allow (RFC-8291-Testvektor, kein Geheimnis)
}


def test_rfc8291_testvektor():
    asp = webpush.privat_aus_roh(webpush.unb64(RFC['as_privat']))
    assert webpush.b64(webpush.oeffentlich_roh(asp)) == RFC['as_oeffentlich']
    body = webpush.verschluesseln(webpush.unb64(RFC['klar']), RFC['ua_oeffentlich'], RFC['auth'], _as_privat=asp,
                                  _salz=webpush.unb64(RFC['salz']))
    assert webpush.b64(body) == RFC['body']
    uap = webpush.privat_aus_roh(webpush.unb64(RFC['ua_privat']))
    assert webpush.entschluesseln(body, uap, webpush.unb64(RFC['auth'])) == b'When I grow up, I want to be a watermelon'
    # Zufälliger Absender-Schlüssel + Salz: jedes Mal anders, entschlüsselbar
    a, b = webpush.verschluesseln(b'x', RFC['ua_oeffentlich'], RFC['auth']), webpush.verschluesseln(b'x', RFC['ua_oeffentlich'], RFC['auth'])
    assert a != b and webpush.entschluesseln(a, uap, webpush.unb64(RFC['auth'])) == b'x'
    with pytest.raises(ValueError):
        webpush.verschluesseln(b'x' * 4000, RFC['ua_oeffentlich'], RFC['auth'])
    with pytest.raises(ValueError):
        webpush.ua_schluessel_pruefen('BAAA', RFC['auth'])
    with pytest.raises(ValueError):          # Punkt nicht auf der Kurve
        webpush.ua_schluessel_pruefen(webpush.b64(b'\x04' + b'\x01' * 64), RFC['auth'])


def jwt_pruefen(kopf, oeffentlich_b64, aud):
    """Authorization: vapid t=…, k=… → Claims; prüft die ES256-Signatur mit dem öffentlichen Schlüssel."""
    assert kopf.startswith('vapid t=')
    t, k = kopf[len('vapid t='):].split(', k=')
    assert k == oeffentlich_b64
    h, c, s = t.split('.')
    assert json.loads(webpush.unb64(h)) == {'typ': 'JWT', 'alg': 'ES256'}
    roh = webpush.unb64(s)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64(k))
    pub.verify(encode_dss_signature(int.from_bytes(roh[:32], 'big'), int.from_bytes(roh[32:], 'big')), f'{h}.{c}'.encode(),
               ec.ECDSA(hashes.SHA256()))
    claims = json.loads(webpush.unb64(c))
    assert claims['aud'] == aud and claims['exp'] > time.time() + 3600 and claims['exp'] <= time.time() + 24 * 3600
    assert claims['sub'].startswith(('mailto:', 'https://'))
    return claims


# ---------------------------------------------------------------- gefälschter Push-Dienst
class Dienst:
    def __init__(self):
        self.anfragen, self.status = [], 201
        dienst = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):     # noqa: N802
                body = self.rfile.read(int(self.headers['Content-Length']))
                dienst.anfragen.append({'pfad': self.path, 'kopf': dict(self.headers), 'body': body})
                self.send_response(dienst.status)
                self.end_headers()

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.basis = f'http://127.0.0.1:{self.server.server_port}'


@pytest.fixture
def dienst(monkeypatch):
    monkeypatch.setattr(webhooks, 'LOKAL_ERLAUBT', True)
    d = Dienst()
    yield d
    d.server.shutdown()


def geraet_anmelden(c, endpunkt, name='Testbrowser'):
    ua = webpush.neuer_schluessel()
    auth = b'0123456789abcdef'
    r = c.post('/api/push/abo', json={'endpoint': endpunkt, 'keys': {'p256dh': webpush.b64(webpush.oeffentlich_roh(ua)), 'auth': webpush.b64(auth)},
                                      'geraet': name})
    assert r.status_code == 200, r.text
    return ua, auth, r.json()['geraet']


def nachricht(anfrage, ua, auth):
    return json.loads(webpush.entschluesseln(anfrage['body'], ua, auth))


def test_abo_pruefungen_ohne_lokale_ziele():
    c, _e, _p = konto()
    assert neu_client().get('/api/push').status_code == 401
    d = c.get('/api/push').json()
    assert d['geraete'] == [] and d['ereignisse'] == list(push.EREIGNISSE) and len(webpush.unb64(d['schluessel'])) == 65
    keys = {'p256dh': RFC['ua_oeffentlich'], 'auth': RFC['auth']}
    for falsch in ('http://fcm.googleapis.com/fcm/send/x', 'https://127.0.0.1/x', 'https://boese.example.org/fcm.googleapis.com',
                   'https://fcm.googleapis.com.boese.example/x', 'https://user:pw@fcm.googleapis.com/x', 'kein-url'):
        assert c.post('/api/push/abo', json={'endpoint': falsch, 'keys': keys}).status_code == 400, falsch
    assert c.post('/api/push/abo', json={'endpoint': 'https://fcm.googleapis.com/fcm/send/abc', 'keys': {'p256dh': 'x', 'auth': 'y'}}).status_code == 400
    r = c.post('/api/push/abo', json={'endpoint': 'https://fcm.googleapis.com/fcm/send/abc', 'keys': keys, 'geraet': 'Chrome · Android'})
    assert r.status_code == 200 and r.json()['geraet']['geraet'] == 'Chrome · Android'
    assert c.post('/api/push/abo', json={'endpoint': 'https://web.push.apple.com/QGx', 'keys': keys}).status_code == 200
    g = c.get('/api/push').json()['geraete']
    assert len(g) == 2 and 'endpunkt' not in g[0] and len(g[0]['kennung']) == 16
    # gleiches Gerät erneut → ersetzt, kein Duplikat; anderes Konto übernimmt das Gerät
    c.post('/api/push/abo', json={'endpoint': 'https://fcm.googleapis.com/fcm/send/abc', 'keys': keys})
    assert len(c.get('/api/push').json()['geraete']) == 2
    c2, _e, _p = konto()
    c2.post('/api/push/abo', json={'endpoint': 'https://fcm.googleapis.com/fcm/send/abc', 'keys': keys})
    assert len(c.get('/api/push').json()['geraete']) == 1
    # Ereignisse wählen
    assert c.put('/api/push/ereignisse', json={'ereignisse': ['spam']}).status_code == 400
    assert c.put('/api/push/ereignisse', json={'ereignisse': ['fertig', 'warteraum']}).json() == {'ereignisse': ['warteraum', 'fertig']}
    # löschen nur eigene
    gid = c.get('/api/push').json()['geraete'][0]['id']
    assert c2.delete(f'/api/push/abo/{gid}').status_code == 404
    assert c.delete(f'/api/push/abo/{gid}').status_code == 200
    assert c.get('/api/push').json()['geraete'] == []
    assert c.post('/api/push/test').status_code == 400
    # Export ohne Adressen
    ex = c2.get('/api/konto/export').json()['push']
    assert ex['geraete'][0]['geraet'] == '' and 'endpunkt' not in json.dumps(ex)


def test_zustellung_verschluesselt_mit_vapid_und_410_loescht(dienst):
    c, _e, _p = konto()
    ua, auth, g = geraet_anmelden(c, dienst.basis + '/senden/geraet1?x=1')
    oeff = c.get('/api/push').json()['schluessel']
    r = c.post('/api/push/test')
    assert r.status_code == 200 and r.json() == {'geraete': 1}
    assert push.warten() == [201]
    a = dienst.anfragen[-1]
    assert a['pfad'] == '/senden/geraet1?x=1'
    assert a['kopf']['Content-Encoding'] == 'aes128gcm' and a['kopf']['TTL'] == '86400' and a['kopf']['Topic'] == 'test'
    jwt_pruefen(a['kopf']['Authorization'], oeff, dienst.basis)
    n = nachricht(a, ua, auth)
    assert n['titel'] == 'Agora' and n['url'] == '/konto#profil' and 'Benachrichtigungen' in n['text']
    assert c.get('/api/push').json()['geraete'][0]['benutzt']
    # Dienst meldet „weg“ → Gerät gelöscht
    dienst.status = 410
    c.post('/api/push/test')
    assert push.warten() == [410]
    assert c.get('/api/push').json()['geraete'] == []


def test_fehlschlaege_zaehlen_und_englisch(dienst):
    c, _e, _p = konto()
    assert c.patch('/api/konto/ich', json={'sprache': 'en'}).status_code == 200
    ua, auth, _g = geraet_anmelden(c, dienst.basis + '/x')
    dienst.status = 500
    c.post('/api/push/test')
    assert push.warten() == [500]
    assert db.verbindung().execute('SELECT fehler_folge FROM push_abos WHERE endpunkt = ?', (dienst.basis + '/x',)).fetchone()[0] == 1
    dienst.status = 201
    c.post('/api/push/test')
    push.warten()
    assert nachricht(dienst.anfragen[-1], ua, auth)['text'] == 'This is what notifications from Agora look like.'
    assert db.verbindung().execute('SELECT fehler_folge FROM push_abos WHERE endpunkt = ?', (dienst.basis + '/x',)).fetchone()[0] == 0


def test_warteraum_nur_ohne_host_im_meeting(dienst):
    host, _e, _p = konto(name='Hanna')
    ua, auth, _g = geraet_anmelden(host, dienst.basis + '/h')
    code = raum(host, titel='Sprechstunde', warteraum=True)['code']
    hz = zugang(host, code)                    # Host hat das Meeting gestartet …
    lk.FAKE_TEILNEHMER[code] = [{'identity': hz['identity'], 'name': 'Hanna', 'tracks': []}]
    assert beitreten(neu_client(), code, name='Gustav').json()['status'] == 'wartet'
    assert push.warten() == [] and not dienst.anfragen          # … und ist verbunden → kein Push
    lk.FAKE_TEILNEHMER[code] = []                               # Host weg
    assert beitreten(neu_client(), code, name='Gerda').json()['status'] == 'wartet'
    assert push.warten() == [201]
    a = dienst.anfragen[-1]
    n = nachricht(a, ua, auth)
    assert n['titel'] == 'Jemand wartet: Sprechstunde' and 'Gerda' in n['text'] and n['url'] == f'/r/{code}'
    assert a['kopf']['Urgency'] == 'high'
    beitreten(neu_client(), code, name='Gisela')                # innerhalb von 2 min nicht noch einmal
    assert push.warten() == []
    # Ereignis abgewählt → nichts
    host.put('/api/push/ereignisse', json={'ereignisse': ['fertig']})
    from app.kern import grenze
    grenze.d.clear()
    beitreten(neu_client(), code, name='Greta')
    assert push.warten() == []


def test_einladung_an_vorhandenes_konto(dienst):
    gast, gmail, _p = konto(name='Gina')
    ua, auth, _g = geraet_anmelden(gast, dienst.basis + '/g')
    host, _e, _p = konto(name='Hugo')
    code = raum(host, titel='Planung')['code']
    r = host.post(f'/api/raeume/{code}/einladen', json={'emails': [gmail, 'fremd@example.org']})
    assert r.status_code == 200
    assert push.warten() == [201]
    n = nachricht(dienst.anfragen[-1], ua, auth)
    assert n['titel'] == 'Einladung: Planung' and n['text'] == 'Hugo hat dich eingeladen.' and n['url'] == f'/r/{code}'


def test_erinnerung_einmal_im_fenster(dienst):
    c, _e, _p = konto()
    ua, auth, _g = geraet_anmelden(c, dienst.basis + '/e')
    from datetime import datetime, timedelta, timezone
    start = datetime.now(timezone.utc) + timedelta(hours=2)
    r = raum(c, titel='Jour fixe', geplant={'start': start.strftime('%Y-%m-%dT%H:%M:00Z'), 'dauer_min': 30, 'zeitzone': 'Europe/Berlin'})
    termin = int(datetime.fromisoformat(r['naechster_termin'].replace('Z', '+00:00')).timestamp())
    assert push.erinnerungen_pruefen(termin - 20 * 60) == 0           # vor dem Fenster (Standard 10 min)
    assert push.erinnerungen_pruefen(termin - 9 * 60) == 1
    assert push.erinnerungen_pruefen(termin - 8 * 60) == 0            # nur einmal
    assert push.warten() == [201]
    n = nachricht(dienst.anfragen[-1], ua, auth)
    assert n['titel'] == 'Jour fixe beginnt bald' and 'in 10 Minuten' in n['text']
    assert dienst.anfragen[-1]['kopf']['TTL'] == '600'


def test_fertig_server_aufnahme_und_transkript(dienst, monkeypatch):
    c, _e, _p = konto()
    ua, auth, _g = geraet_anmelden(c, dienst.basis + '/f')
    bid = db.verbindung().execute('SELECT benutzer_id FROM push_abos WHERE endpunkt = ?', (dienst.basis + '/f',)).fetchone()[0]
    zeile = {'id': 'A' * 22, 'benutzer_id': bid, 'titel': 'Server-Aufnahme'}
    for f in egress.NACH_SERVER_AUFNAHME:
        f(zeile)
    for f in untertitel.NACH_TRANSKRIPT:
        f(zeile)
    assert push.warten() == [201, 201]
    titel = sorted(nachricht(a, ua, auth)['titel'] for a in dienst.anfragen)
    assert titel == ['Aufnahme fertig', 'Transkript fertig']


def test_vapid_schluessel_bleibt():
    a = push.vapid()
    push._vapid = None
    assert push.vapid()[1] == a[1]
    assert base64.urlsafe_b64decode(a[1] + '=')[:1] == b'\x04'
