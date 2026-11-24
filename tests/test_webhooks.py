"""Tests Webhooks: Ziel-Prüfung (SSRF), Signatur, Ereignisse, Wiederholungen, Abschalten mit Mail, Protokoll.
Empfänger ist ein lokaler Fake-HTTP-Server (dafür erlaubt LOKAL_ERLAUBT im Test http:// und 127.0.0.1)."""
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from test_api import T, frisch, konto, mails_an, neu_client, raum, steuern, zugang  # noqa: F401
from test_api_v1 import schluessel, v1

from app import db, main, raeume, webhooks
from app import livekit_dienst as lk
from app.kern import jetzt


class Empfaenger:
    """Fake-Empfänger: merkt sich (Pfad, Kopfzeilen, Body); antwortet mit self.status (und Location bei 3xx)."""
    def __init__(self):
        self.anfragen, self.status = [], 200
        e = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get('content-length') or 0)
                e.anfragen.append((self.path, dict(self.headers), self.rfile.read(n)))
                self.send_response(e.status)
                if 300 <= e.status < 400:
                    self.send_header('Location', f'http://127.0.0.1:{e.port}/umgeleitet')
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'ok')

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.server.server_address[1]
        self.url = f'http://127.0.0.1:{self.port}/hook?x=1'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def ereignisse(self):
        return [json.loads(b)['ereignis'] for _p, _h, b in self.anfragen]


@pytest.fixture
def empf(monkeypatch):
    monkeypatch.setattr(webhooks, 'LOKAL_ERLAUBT', True)
    e = Empfaenger()
    yield e
    e.server.shutdown()


def anlegen(c, url, ereignisse=webhooks.EREIGNISSE):
    r = c.post('/api/konto/webhooks', json={'url': url, 'ereignisse': list(ereignisse)})
    assert r.status_code == 200, r.text
    return r.json()['webhook'], r.json()['geheimnis']


def zustellen():
    webhooks.faellige_zustellen()


# ---------------------------------------------------------------- Ziel-Prüfung
@pytest.mark.parametrize('url', ['http://example.org/x', 'https://127.0.0.1/x', 'https://localhost/x', 'https://10.1.2.3/',
                                 'https://192.168.0.5/', 'https://169.254.169.254/latest', 'https://[::1]/', 'https://[fd00::1]/',
                                 'https://[::ffff:127.0.0.1]/', 'https://user:pw@93.184.215.14/', 'https://93.184.215.14/#frag',
                                 'ftp://93.184.215.14/', 'https://0.0.0.0/', 'https://100.64.0.1/', 'x' * 600,
                                 # IPv6 mit eingebetteter privater IPv4 (NAT64, 6to4, IPv4-kompatibel) und Dezimal-/Hex-Schreibweisen
                                 'https://[64:ff9b::7f00:1]/', 'https://[64:ff9b::a9fe:a9fe]/', 'https://[2002:7f00:1::1]/',
                                 'https://[::127.0.0.1]/', 'https://2130706433/', 'https://0x7f000001/', 'https://127.1/'])
def test_ziel_im_eigenen_netz_oder_ohne_https_abgelehnt(url):
    c, _e, _p = konto()
    r = c.post('/api/konto/webhooks', json={'url': url, 'ereignisse': ['meeting.gestartet']})
    assert r.status_code == 400, (url, r.text)


def test_oeffentliches_ziel_angenommen_aber_kein_lokaler_test_ohne_freigabe():
    c, _e, _p = konto()
    w, geheim = anlegen(c, 'https://93.184.215.14/agora', ['meeting.gestartet'])
    assert geheim.startswith('whsec_') and w['aktiv'] and w['ereignisse'] == ['meeting.gestartet']
    roh = db.verbindung().execute('SELECT geheim FROM webhooks WHERE id = ?', (w['id'],)).fetchone()['geheim']
    assert geheim not in roh and roh.startswith('v1.')                      # verschlüsselt gespeichert
    assert geheim not in str(c.get('/api/konto/webhooks').json())
    # auch bei der Zustellung wird erneut geprüft (z. B. DNS zeigt inzwischen ins eigene Netz)
    status, text, _ms = webhooks.senden('https://127.0.0.1:9/x', 'g', 'test', 'z', b'{}')
    assert status is None and 'eigenen Netz' in text


def test_hoechstens_fuenf_und_ereignisse_pruefen(empf):
    c, _e, _p = konto()
    assert c.post('/api/konto/webhooks', json={'url': empf.url, 'ereignisse': ['quatsch']}).status_code == 400
    assert c.post('/api/konto/webhooks', json={'url': empf.url, 'ereignisse': []}).status_code == 400
    for _ in range(5):
        anlegen(c, empf.url)
    assert c.post('/api/konto/webhooks', json={'url': empf.url, 'ereignisse': ['meeting.beendet']}).status_code == 409


# ---------------------------------------------------------------- Signatur, Test senden, Weiterleitung
def test_test_senden_mit_signatur(empf):
    c, _e, _p = konto()
    w, geheim = anlegen(c, empf.url)
    r = c.post(f'/api/konto/webhooks/{w["id"]}/test').json()
    assert r['ok'] and r['status'] == 200
    pfad, kopf, body = empf.anfragen[-1]
    assert pfad == '/hook?x=1' and kopf['X-Agora-Ereignis'] == 'test' and kopf['Content-Type'] == 'application/json'
    t, v1sig = [x.split('=', 1)[1] for x in kopf['X-Agora-Signatur'].split(',')]
    assert abs(int(t) - jetzt()) < 5
    assert hmac.compare_digest(v1sig, hmac.new(geheim.encode(), f'{t}.'.encode() + body, hashlib.sha256).hexdigest())
    assert json.loads(body)['ereignis'] == 'test' and kopf['X-Agora-Zustellung'].startswith('zst_')
    log = c.get(f'/api/konto/webhooks/{w["id"]}/zustellungen').json()['zustellungen']
    assert log[0]['ereignis'] == 'test' and log[0]['ok'] and log[0]['status'] == 200
    # neues Geheimnis: alte Signatur passt nicht mehr
    neu = c.post(f'/api/konto/webhooks/{w["id"]}/geheimnis').json()['geheimnis']
    assert neu != geheim
    c.post(f'/api/konto/webhooks/{w["id"]}/test')
    _p2, kopf, body = empf.anfragen[-1]
    t, sig = [x.split('=', 1)[1] for x in kopf['X-Agora-Signatur'].split(',')]
    assert sig == hmac.new(neu.encode(), f'{t}.'.encode() + body, hashlib.sha256).hexdigest()
    # fremde Konten
    andere, _e2, _p3 = konto(name='Bea')
    assert andere.post(f'/api/konto/webhooks/{w["id"]}/test').status_code == 404
    assert andere.get(f'/api/konto/webhooks/{w["id"]}/zustellungen').status_code == 404
    assert andere.delete(f'/api/konto/webhooks/{w["id"]}').status_code == 404


def test_weiterleitung_wird_nicht_verfolgt(empf):
    c, _e, _p = konto()
    w, _g = anlegen(c, empf.url)
    empf.status = 302
    r = c.post(f'/api/konto/webhooks/{w["id"]}/test').json()
    assert not r['ok'] and r['status'] == 302 and 'Weiterleitung' in r['fehler']
    assert [p for p, _k, _b in empf.anfragen] == ['/hook?x=1']                    # /umgeleitet nie aufgerufen


# ---------------------------------------------------------------- Ereignisse
def test_meeting_und_teilnehmer_ereignisse(empf):
    host, _e, _p = konto()
    anlegen(host, empf.url)
    r = raum(host, titel='Hook-Meeting')
    code = r['code']
    hz = zugang(host, code)
    g = neu_client()
    gz = zugang(g, code, name='Gast Eins')
    zustellen()
    assert empf.ereignisse() == ['meeting.gestartet', 'teilnehmer.beigetreten', 'teilnehmer.beigetreten']
    d = json.loads(empf.anfragen[0][2])
    assert d['id'].startswith('evt_') and d['daten']['meeting'] == {'code': code, 'titel': 'Hook-Meeting', 'link': f'https://agora.slxt.dev/r/{code}'}
    tn = [json.loads(b)['daten']['teilnehmer'] for _p, _h, b in empf.anfragen[1:]]
    assert tn == [{'identity': hz['identity'], 'name': 'Ada', 'rolle': 'host'}, {'identity': gz['identity'], 'name': 'Gast Eins', 'rolle': 'gast'}]
    # minütliche Prüfung: beide verbunden, dann geht der Gast
    import asyncio
    lk.FAKE_TEILNEHMER[code] = [{'identity': hz['identity'], 'name': 'Ada', 'tracks': []}, {'identity': gz['identity'], 'name': 'Gast Eins', 'tracks': []}]
    asyncio.run(raeume.leere_pruefen())
    zustellen()
    assert len(empf.anfragen) == 3
    lk.FAKE_TEILNEHMER[code] = [{'identity': hz['identity'], 'name': 'Ada', 'tracks': []}]
    asyncio.run(raeume.leere_pruefen())
    zustellen()
    d = json.loads(empf.anfragen[-1][2])
    assert d['ereignis'] == 'teilnehmer.gegangen' and d['daten']['teilnehmer']['identity'] == gz['identity'] and d['daten']['grund'] == 'verlassen'
    # Host beendet: Host „geht“, dann meeting.beendet
    assert steuern(host, code, hz, aktion='beenden').status_code == 200
    zustellen()
    assert empf.ereignisse()[-2:] == ['teilnehmer.gegangen', 'meeting.beendet']
    assert json.loads(empf.anfragen[-2][2])['daten']['grund'] == 'meeting_beendet'
    assert db.verbindung().execute('SELECT COUNT(*) FROM webhook_anwesend').fetchone()[0] == 0
    # nur abonnierte Ereignisse, und nur für eigene Räume
    andere, _e2, _p2 = konto(name='Bea')
    zugang(andere, raum(andere)['code'])
    zustellen()
    assert empf.ereignisse()[-1] == 'meeting.beendet'


def test_ohne_abo_kein_anwesenheits_merker(empf):
    host, _e, _p = konto()
    anlegen(host, empf.url, ['meeting.beendet'])
    code = raum(host)['code']
    zugang(host, code)
    zustellen()
    assert empf.anfragen == [] and db.verbindung().execute('SELECT COUNT(*) FROM webhook_anwesend').fetchone()[0] == 0


def test_aufnahme_und_webinar_anmeldung(empf):
    host, _e, _p = konto()
    anlegen(host, empf.url, ['aufnahme.fertig', 'webinar.anmeldung'])
    r = host.post('/api/aufnahmen', content=b'\x1aE\xdf\xa3' + b'x' * 50, headers={'Content-Type': 'video/webm', 'X-Titel': 'Probe'})
    aid = r.json()['aufnahme']['id']
    code = raum(host, titel='Webinar')['code']
    assert host.patch(f'/api/raeume/{code}/webinar', json={'an': True, 'registrierung': True}).status_code == 200
    assert neu_client().post(f'/api/webinare/{code}/anmelden', json={'name': 'Ana', 'email': 'ana@example.org'}).status_code == 200
    zustellen()
    a, w = [json.loads(b)['daten'] for _p, _h, b in empf.anfragen]
    assert a['aufnahme']['id'] == aid and a['aufnahme']['titel'] == 'Probe' and a['aufnahme']['groesse'] == 54
    assert w['meeting']['code'] == code and w['anmeldung']['name'] == 'Ana' and w['anmeldung']['email'] == 'ana@example.org'
    assert empf.ereignisse() == ['aufnahme.fertig', 'webinar.anmeldung']


# ---------------------------------------------------------------- Wiederholungen, Abschalten, Protokoll
def test_wiederholungen_exponentiell_abschalten_mit_mail(empf):
    host, email, _p = konto()
    w, _g = anlegen(host, empf.url, ['meeting.gestartet', 'meeting.beendet'])
    empf.status = 500
    code = raum(host)['code']
    hz = zugang(host, code)                       # meeting.gestartet
    t = jetzt()
    webhooks.faellige_zustellen(t)
    assert len(empf.anfragen) == 1
    webhooks.faellige_zustellen(t + 30)            # noch nicht fällig
    assert len(empf.anfragen) == 1
    for abstand in (60, 60 + 300, 60 + 300 + 1800, 60 + 300 + 1800 + 7200):
        webhooks.faellige_zustellen(t + abstand + 1)
    assert len(empf.anfragen) == 5
    webhooks.faellige_zustellen(t + 86400)
    assert len(empf.anfragen) == 5                 # nach 5 Versuchen Schluss
    log = host.get(f'/api/konto/webhooks/{w["id"]}/zustellungen').json()['zustellungen']
    assert [x['versuch'] for x in log] == [5, 4, 3, 2, 1] and all(x['status'] == 500 and not x['ok'] for x in log)
    assert host.get('/api/konto/webhooks').json()['webhooks'][0]['fehler_folge'] == 1
    # Erfolg setzt den Zähler zurück
    empf.status = 200
    steuern(host, code, hz, aktion='beenden')
    webhooks.faellige_zustellen(t + 86400)
    assert host.get('/api/konto/webhooks').json()['webhooks'][0]['fehler_folge'] == 0
    # drei endgültige Fehlschläge in Folge → abgeschaltet + Mail
    empf.status = 503
    for _ in range(3):
        zugang(host, code)
        steuern(host, code, zugang(host, code), aktion='beenden')
    for i in range(6):
        webhooks.faellige_zustellen(t + 86400 * (i + 2))
    wh = host.get('/api/konto/webhooks').json()['webhooks'][0]
    assert wh['aktiv'] is False and 'HTTP 503' in wh['deaktiviert_grund']
    assert any('Webhook abgeschaltet' in m[1] for m in mails_an(email))
    webhooks.abgeschaltet_mail('en@example.org', 'Emma', 'https://x.example/h', 'HTTP 503', 'en')
    assert mails_an('en@example.org')[-1][1] == 'Agora: Webhook disabled' and 'Hello Emma' in mails_an('en@example.org')[-1][2]
    assert db.verbindung().execute('SELECT COUNT(*) FROM webhook_zustellungen').fetchone()[0] == 0
    n = len(empf.anfragen)
    zugang(host, code)
    webhooks.faellige_zustellen(t + 86400 * 20)
    assert len(empf.anfragen) == n                 # abgeschaltet: nichts mehr
    # wieder einschalten
    empf.status = 200
    wh = host.patch(f'/api/konto/webhooks/{w["id"]}', json={'aktiv': True}).json()['webhook']
    assert wh['aktiv'] and wh['fehler_folge'] == 0 and wh['deaktiviert_grund'] is None


def test_protokoll_hoechstens_50_und_14_tage(empf):
    host, _e, _p = konto()
    w, _g = anlegen(host, empf.url)
    for i in range(60):
        webhooks._log(w['id'], f'z{i}', 'test', 1, 200, None, 1)
    log = host.get(f'/api/konto/webhooks/{w["id"]}/zustellungen').json()['zustellungen']
    assert len(log) == 50 and log[0]['zustellung'] == 'z59'
    db.verbindung().execute('UPDATE webhook_log SET zeit = ? WHERE webhook_id = ?', (jetzt() - 15 * 86400, w['id']))
    webhooks.aufraeumen(jetzt())
    assert host.get(f'/api/konto/webhooks/{w["id"]}/zustellungen').json()['zustellungen'] == []
    # löschen nimmt alles mit
    assert host.delete(f'/api/konto/webhooks/{w["id"]}').status_code == 200
    assert host.get('/api/konto/webhooks').json()['webhooks'] == []


def test_webhooks_ueber_api_v1(empf):
    c, _e, _p = konto()
    ohne, _k = schluessel(c, rechte=['meetings:lesen'])
    assert v1(ohne).get('/api/v1/webhooks').status_code == 403
    token, _k = schluessel(c, rechte=['webhooks'])
    a = v1(token)
    r = a.post('/api/v1/webhooks', json={'url': empf.url, 'ereignisse': ['meeting.gestartet']})
    assert r.status_code == 201 and r.json()['geheimnis'].startswith('whsec_')
    wid = r.json()['webhook']['id']
    assert [x['id'] for x in a.get('/api/v1/webhooks').json()['webhooks']] == [wid]
    assert a.post(f'/api/v1/webhooks/{wid}/test').json()['ok']
    assert a.patch(f'/api/v1/webhooks/{wid}', json={'ereignisse': ['meeting.beendet']}).json()['webhook']['ereignisse'] == ['meeting.beendet']
    assert len(a.get(f'/api/v1/webhooks/{wid}/zustellungen').json()['zustellungen']) == 1
    assert a.delete(f'/api/v1/webhooks/{wid}').status_code == 200
    # Cookie-Variante braucht X-Agora
    assert TestClient(main.app).post('/api/konto/webhooks', json={}).status_code == 403
