"""Optionales Redis für Begrenzer und Präsenz: RESP-Client, Rückfall auf den Arbeitsspeicher, Leitung der
Hintergrund-Aufgaben (flock) – und Integrationstests gegen einen Wegwerf-Redis (übersprungen ohne; `sh tests/geteilt/lauf.sh`),
darunter zwei echte uvicorn-Worker, die sich einen Begrenzer teilen."""
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import pytest

from test_api import neu_client  # noqa: F401

from app import geteilt, leitung
from app.kern import Begrenzer
from app.redis_klein import Redis, RedisFehler

REDIS_URL = os.environ.get('AGORA_TEST_REDIS') or 'redis://:agoratest-geheim@127.0.0.1:6391/3'


# ---------------------------------------------------------------- ohne Redis
def test_resp_kodierung():
    assert Redis._kodieren(['SET', 'a', b'\x00b']) == b'*3\r\n$3\r\nSET\r\n$1\r\na\r\n$2\r\n\x00b\r\n'
    r = Redis('redis://:p%40ss@h:6390/2')
    assert (r.host, r.port, r.passwort, r.db, r.benutzer) == ('h', 6390, 'p@ss', 2, None)
    assert Redis('rediss://nutzer:pw@h').tls and Redis('rediss://nutzer:pw@h').benutzer == 'nutzer'
    with pytest.raises(ValueError):
        Redis('http://h')


class FakeDatei:
    def __init__(self, daten):
        self.daten = daten

    def readline(self):
        i = self.daten.index(b'\r\n') + 2
        z, self.daten = self.daten[:i], self.daten[i:]
        return z

    def read(self, n):
        z, self.daten = self.daten[:n], self.daten[n:]
        return z


def test_resp_antworten_lesen():
    r = Redis('redis://h')
    r._lokal.f = FakeDatei(b'+OK\r\n:42\r\n$3\r\nabc\r\n$-1\r\n*2\r\n:1\r\n$1\r\nx\r\n-ERR kaputt\r\n')
    assert r._lesen() == 'OK' and r._lesen() == 42 and r._lesen() == b'abc' and r._lesen() is None
    assert r._lesen() == [1, b'x']
    assert isinstance(r._lesen(), RedisFehler)


def test_ohne_redis_url_alles_im_speicher(monkeypatch):
    monkeypatch.delenv('AGORA_REDIS_URL', raising=False)
    assert geteilt.redis() is None and geteilt.grenze('x', 1, 10) is None
    b = Begrenzer()
    assert [b.ok('k', 2, 60) for _ in range(3)] == [True, True, False] and b.voll('k', 2, 60)


def test_redis_weg_rueckfall_auf_speicher(monkeypatch):
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()                                      # Port ist jetzt zu → Verbindung abgelehnt
    monkeypatch.setenv('AGORA_REDIS_URL', f'redis://127.0.0.1:{port}/0')
    b = Begrenzer()
    t0 = time.time()
    assert [b.ok('weg', 2, 60) for _ in range(3)] == [True, True, False]       # Arbeitsspeicher greift
    assert time.time() - t0 < 5
    geteilt.gesehen('konten', 'r1')
    assert geteilt.anzahl('konten') >= 1


def test_leitung_nur_ein_prozess(tmp_path):
    """Zwei Prozesse, eine Sperre: der zweite bekommt sie erst, wenn der erste endet."""
    code = ('import os,sys,time; os.environ["AGORA_DATEN"]=sys.argv[1]; from app import leitung; '
            'print(leitung.versuchen(), flush=True); time.sleep(float(sys.argv[2]))')
    erster = subprocess.Popen([sys.executable, '-c', code, str(tmp_path), '3'], stdout=subprocess.PIPE, text=True, cwd=os.getcwd())
    assert erster.stdout.readline().strip() == 'True'
    zweiter = subprocess.run([sys.executable, '-c', code, str(tmp_path), '0'], capture_output=True, text=True, cwd=os.getcwd())
    assert zweiter.stdout.strip() == 'False'
    erster.wait()
    dritter = subprocess.run([sys.executable, '-c', code, str(tmp_path), '0'], capture_output=True, text=True, cwd=os.getcwd())
    assert dritter.stdout.strip() == 'True'


def test_hintergrund_zeit_fuer_alle_worker():
    leitung.gelaufen()
    assert time.time() - leitung.letzter_lauf() < 5


# ---------------------------------------------------------------- mit Redis (Wegwerf-Container)
def redis_da():
    u = urlsplit(REDIS_URL)
    try:
        socket.create_connection((u.hostname, u.port or 6379), timeout=1).close()
        return True
    except OSError:
        return False


@pytest.fixture
def redis(monkeypatch):
    if not redis_da():
        pytest.skip(f'kein Redis unter {REDIS_URL} (sh tests/geteilt/lauf.sh)')
    monkeypatch.setenv('AGORA_REDIS_URL', REDIS_URL)
    r = geteilt.redis()
    r.befehl('FLUSHDB')
    yield r
    r.befehl('FLUSHDB')


def test_redis_begrenzer_gemeinsam(redis):
    a, b = Begrenzer(), Begrenzer()                # zwei „Worker“ mit eigenem Arbeitsspeicher
    stati = [(a if i % 2 else b).ok('login:1.2.3.4', 5, 60) for i in range(8)]
    assert stati == [True] * 5 + [False] * 3
    assert not a.d and not b.d                     # nichts im Arbeitsspeicher
    assert a.voll('login:1.2.3.4', 5, 60) and not a.voll('anderer', 5, 60)
    # voll() zählt selbst nicht mit
    for _ in range(3):
        a.voll('nur-pruefen', 1, 60)
    assert a.ok('nur-pruefen', 1, 60)
    # Schlüssel nur als Hash, mit Ablauf
    keys = [k.decode() for k in redis.befehl('KEYS', 'agora:g:*')]
    assert keys and not any('1.2.3.4' in k for k in keys)
    assert 0 < redis.befehl('PTTL', keys[0]) <= 61000


def test_redis_gleitendes_fenster(redis):
    b = Begrenzer()
    assert [b.ok('fenster', 2, 1) for _ in range(3)] == [True, True, False]
    time.sleep(1.1)
    assert b.ok('fenster', 2, 1)


def test_redis_praesenz(redis, monkeypatch):
    monkeypatch.setattr(geteilt, '_gesehen', {})
    for wer in ('a', 'b', 'c'):
        geteilt.gesehen('meeting', wer)
    geteilt.gesehen('meeting', 'a')
    assert geteilt.anzahl('meeting') == 3
    monkeypatch.setattr(geteilt, '_gesehen', {})       # anderer Worker: eigener Speicher leer, Redis zählt trotzdem
    assert geteilt.anzahl('meeting') == 3 and geteilt.anzahl('konten') == 0
    redis.befehl('ZADD', 'agora:p:meeting', int(time.time()) - 400, 'alt')
    assert geteilt.anzahl('meeting', 300) == 3
    assert not any(b'a' == m for m in redis.befehl('ZRANGE', 'agora:p:meeting', 0, -1))     # nur Hashes


def test_redis_admin_uebersicht_online(redis, monkeypatch):
    from test_api import konto
    monkeypatch.setattr(geteilt, '_gesehen', {})
    c, email, _p = konto()
    monkeypatch.setenv('AGORA_ADMIN_MAILS', email)
    u = c.get('/api/admin/uebersicht').json()
    assert u['online']['geteilt'] is True and u['online']['konten'] >= 1


def _frei():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_zwei_worker_teilen_den_begrenzer(redis):
    """Zwei echte uvicorn-Worker (ein Socket, der Kernel verteilt): 30 Fehlerberichte je IP und 10 min – zusammen."""
    port = _frei()
    daten = tempfile.mkdtemp()
    env = {**os.environ, 'AGORA_TEST': '1', 'AGORA_DATEN': daten, 'AGORA_REDIS_URL': REDIS_URL, 'AGORA_WEB': '/nicht/vorhanden',
           'AGORA_COOKIE': 'agora', 'AGORA_COOKIE_SECURE': '0'}
    env.pop('AGORA_GRENZEN_AUS', None)
    p = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port), '--workers', '2',
                          '--no-access-log'], env=env, cwd=os.getcwd(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{port}/api/gesund', timeout=1).close()
                break
            except OSError:
                time.sleep(0.2)
        time.sleep(1.5)                            # beide Worker bereit
        ip = f'192.0.2.{secrets.randbelow(250)}'
        stati, pids = [], set()
        for i in range(40):
            anfrage = urllib.request.Request(f'http://127.0.0.1:{port}/api/fehlerbericht', method='POST',
                                             data=f'{{"art":"fehler","nachricht":"w{i}"}}'.encode(),
                                             headers={'X-Agora': '1', 'Content-Type': 'application/json', 'CF-Connecting-IP': ip,
                                                      'Connection': 'close'})
            try:
                with urllib.request.urlopen(anfrage, timeout=5) as r:
                    stati.append(r.status)
            except urllib.error.HTTPError as e:
                stati.append(e.code)
        assert stati.count(200) == 30 and stati.count(429) == 10, stati
        # beide Worker haben die Datenbank angelegt (Schema-Sperre), nur einer leitet den Hintergrund
        assert os.path.exists(os.path.join(daten, 'schema.lock'))
        del pids
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
