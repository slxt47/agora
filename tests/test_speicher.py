"""Objektspeicher: SigV4 gegen die Testvektoren der AWS-Dokumentation, Range, Konfiguration – und ein Integrationstest
gegen einen Wegwerf-MinIO (übersprungen, wenn keiner läuft; starten mit `sh tests/speicher/lauf.sh`)."""
import hashlib
import os
import secrets
import socket
from urllib.parse import urlsplit

import pytest

from test_api import H, konto, neu_client, raum, zugang  # noqa: F401

from app import aufnahmen, db, dateien, speicher, speicher_migrieren, untertitel
from app.s3 import LEER, S3Client, query_kodieren, signieren, signierschluessel

# ---------------------------------------------------------------- SigV4: Testvektoren (docs.aws.amazon.com)
GEHEIM_S3 = 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'
KEY_S3 = 'AKIAIOSFODNN7EXAMPLE'
ZEIT_S3 = '20130524T000000Z'


def sig(auth):
    return auth.rsplit('Signature=', 1)[1]


def test_signierschluessel_aws_beispiel():
    # „Examples of how to derive a signing key for Signature Version 4“ (IAM, us-east-1, 20120215)
    k = signierschluessel('wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY', '20120215', 'us-east-1', 'iam')
    assert k.hex() == 'f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d'


def test_sigv4_suite_get_vanilla():
    # aws-sig-v4-test-suite „get-vanilla“
    auth, kan, _ = signieren('GET', '/', {}, {'Host': 'example.amazonaws.com', 'X-Amz-Date': '20150830T123600Z'}, LEER,
                             'AKIDEXAMPLE', 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY', 'us-east-1', 'service', '20150830T123600Z')
    assert kan == 'GET\n/\n\nhost:example.amazonaws.com\nx-amz-date:20150830T123600Z\n\nhost;x-amz-date\n' + LEER
    assert sig(auth) == '5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31'
    assert 'Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, SignedHeaders=host;x-amz-date' in auth


def test_sigv4_s3_get_objekt_mit_range():
    # S3-Doku „Signature Calculations for the Authorization Header“: GET /test.txt, Range bytes=0-9
    auth, _, _ = signieren('GET', '/test.txt', {}, {'Host': 'examplebucket.s3.amazonaws.com', 'Range': 'bytes=0-9',
                                                    'x-amz-content-sha256': LEER, 'x-amz-date': ZEIT_S3},
                           LEER, KEY_S3, GEHEIM_S3, 'us-east-1', zeit=ZEIT_S3)
    assert sig(auth) == 'f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41'
    assert 'SignedHeaders=host;range;x-amz-content-sha256;x-amz-date' in auth


def test_sigv4_s3_put_objekt():
    nutzlast = hashlib.sha256(b'Welcome to Amazon S3.').hexdigest()
    auth, _, _ = signieren('PUT', '/test$file.text', {}, {'Host': 'examplebucket.s3.amazonaws.com', 'Date': 'Fri, 24 May 2013 00:00:00 GMT',
                                                          'x-amz-date': ZEIT_S3, 'x-amz-storage-class': 'REDUCED_REDUNDANCY',
                                                          'x-amz-content-sha256': nutzlast},
                           nutzlast, KEY_S3, GEHEIM_S3, 'us-east-1', zeit=ZEIT_S3)
    assert sig(auth) == '98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd'


def test_sigv4_s3_get_lifecycle_und_liste():
    k = {'Host': 'examplebucket.s3.amazonaws.com', 'x-amz-content-sha256': LEER, 'x-amz-date': ZEIT_S3}
    auth, _, _ = signieren('GET', '/', {'lifecycle': ''}, k, LEER, KEY_S3, GEHEIM_S3, 'us-east-1', zeit=ZEIT_S3)
    assert sig(auth) == 'fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543'
    auth, _, _ = signieren('GET', '/', {'max-keys': '2', 'prefix': 'J'}, k, LEER, KEY_S3, GEHEIM_S3, 'us-east-1', zeit=ZEIT_S3)
    assert sig(auth) == '34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7'


def test_query_kodierung():
    assert query_kodieren({'prefix': 'a b/c', 'list-type': '2'}) == 'list-type=2&prefix=a%20b%2Fc'


def test_sse_kopf_wird_signiert(monkeypatch):
    c = S3Client('https://s3.example.org', 'b', 'k', 'g', 'eu-central-1', sse='aws:kms', kms_schluessel='arn:kms:1')
    gesehen = {}

    class V:
        def request(self, m, pfad, body=None, headers=None):
            gesehen.update(headers=headers, pfad=pfad)

        def getresponse(self):
            class R:
                status = 200

                def read(self, *_):
                    return b''
            return R()

        def close(self):
            pass
    monkeypatch.setattr(c, '_verbindung', lambda host: V())
    c.put_bytes('aufnahmen/x', b'abc')
    h = gesehen['headers']
    assert h['x-amz-server-side-encryption'] == 'aws:kms' and h['x-amz-server-side-encryption-aws-kms-key-id'] == 'arn:kms:1'
    assert 'x-amz-server-side-encryption' in h['authorization'] and gesehen['pfad'] == '/b/aufnahmen/x'
    assert h['x-amz-content-sha256'] == hashlib.sha256(b'abc').hexdigest()
    v = S3Client('https://s3.example.org', 'eimer', 'k', 'g', pfadstil=False)
    assert v._ziel('dateien/a') == ('eimer.s3.example.org', '/dateien/a')


# ---------------------------------------------------------------- Range, Konfiguration
def test_bereich_lesen():
    b = speicher.bereich_lesen
    assert b(None, 100) is None and b('bytes=0-9', 100) == (0, 9) and b('bytes=90-', 100) == (90, 99)
    assert b('bytes=-10', 100) == (90, 99) and b('bytes=50-1000', 100) == (50, 99)
    assert b('bytes=100-', 100) == 'ungueltig' and b('bytes=9-1', 100) == 'ungueltig' and b('bytes=-0', 100) == 'ungueltig'
    assert b('bytes=0-1,5-6', 100) is None and b('items=0-1', 100) == 'ungueltig'


def test_konfiguration():
    assert speicher.aus_umgebung({}).art == 'lokal'
    with pytest.raises(RuntimeError, match='AGORA_S3_SECRET'):
        speicher.aus_umgebung({'AGORA_S3_ENDPOINT': 'https://x', 'AGORA_S3_BUCKET': 'b', 'AGORA_S3_KEY': 'k'})
    with pytest.raises(RuntimeError, match='SSE'):
        speicher.aus_umgebung({'AGORA_S3_ENDPOINT': 'https://x', 'AGORA_S3_BUCKET': 'b', 'AGORA_S3_KEY': 'k', 'AGORA_S3_SECRET': 's',
                               'AGORA_S3_SSE': 'rot13'})
    s = speicher.aus_umgebung({'AGORA_S3_ENDPOINT': 'https://x', 'AGORA_S3_BUCKET': 'b', 'AGORA_S3_KEY': 'k', 'AGORA_S3_SECRET': 's',
                               'AGORA_S3_PRAEFIX': 'agora/'})
    assert s.art == 's3' and s.key('aufnahmen/abc') == 'agora/aufnahmen/abc' and s.client.region == 'us-east-1'
    with pytest.raises(ValueError):
        s.key('../etc/passwd')
    with pytest.raises(ValueError):
        speicher.Lokal().pfad('aufnahmen/../../x')


# ---------------------------------------------------------------- Integration gegen MinIO (Wegwerf-Container)
S3_URL = os.environ.get('AGORA_TEST_S3') or 'http://127.0.0.1:9351'
S3_KEY, S3_GEHEIM = 'agoratest', 'agoratest-geheim-123'


def minio_da():
    u = urlsplit(S3_URL)
    try:
        socket.create_connection((u.hostname, u.port or 80), timeout=1).close()
        return True
    except OSError:
        return False


@pytest.fixture
def s3():
    if not minio_da():
        pytest.skip(f'kein MinIO unter {S3_URL} (sh tests/speicher/lauf.sh)')
    bucket = 'agora-test-' + secrets.token_hex(4)
    c = S3Client(S3_URL, bucket, S3_KEY, S3_GEHEIM, 'us-east-1')
    c._fertig('PUT', '')                     # Bucket anlegen
    s = speicher.S3(c, 'p/')
    speicher.setzen(s)
    yield s
    speicher.setzen(None)
    for k, _t in c.liste(''):
        c.delete(k)
    c._fertig('DELETE', '', erlaubt=(200, 204))


def test_s3_aufnahme_hochladen_range_loeschen(s3):
    c, _e, _p = konto()
    daten = b'\x1aE\xdf\xa3' + secrets.token_bytes(3 * 1024 * 1024)          # 3 MB, gestreamt hoch und runter
    r = c.post('/api/aufnahmen', content=daten, headers={'Content-Type': 'video/webm', 'X-Titel': 'S3%20Test'})
    assert r.status_code == 200, r.text
    aid = r.json()['aufnahme']['id']
    assert not os.path.exists(aufnahmen.pfad(aid))                               # nicht lokal
    assert s3.client.head(f'p/aufnahmen/{aid}')['groesse'] == len(daten)
    assert not os.listdir(os.path.join(db.DATEN, 'tmp')) or all(not n.startswith(aid) for n in os.listdir(os.path.join(db.DATEN, 'tmp')))
    g = c.get(f'/api/aufnahmen/{aid}/datei')
    assert g.status_code == 200 and g.content == daten and g.headers['accept-ranges'] == 'bytes'
    assert g.headers['content-type'] == 'video/webm' and g.headers['content-disposition'].startswith('inline')
    assert g.headers['x-content-type-options'] == 'nosniff'
    t = c.get(f'/api/aufnahmen/{aid}/datei', headers={'Range': 'bytes=100-199'})
    assert t.status_code == 206 and t.content == daten[100:200] and t.headers['content-range'] == f'bytes 100-199/{len(daten)}'
    t = c.get(f'/api/aufnahmen/{aid}/datei', headers={'Range': 'bytes=-5'})
    assert t.status_code == 206 and t.content == daten[-5:]
    assert c.get(f'/api/aufnahmen/{aid}/datei', headers={'Range': f'bytes={len(daten)}-'}).status_code == 416
    assert c.get(f'/api/aufnahmen/{aid}/datei', headers={'Range': 'bytes=0-1,4-5'}).content == daten
    h = c.get(f'/api/aufnahmen/{aid}/datei?download=1', headers={'Range': 'bytes=0-0'})
    assert h.status_code == 206 and h.headers['content-length'] == '1'
    assert h.headers['content-disposition'].startswith('attachment')
    # Freigabe-Link spielt ebenfalls aus S3 ab
    link = c.post(f'/api/aufnahmen/{aid}/freigabe', json={'tage': 1}).json()['link']
    tok = link.rsplit('/', 1)[1]
    assert neu_client().get(f'/api/freigaben/{tok}/datei', headers={'Range': 'bytes=0-3'}).content == daten[:4]
    assert c.delete(f'/api/aufnahmen/{aid}').status_code == 200
    assert s3.client.head(f'p/aufnahmen/{aid}') is None


def test_s3_whisper_transkript(s3, monkeypatch):
    monkeypatch.setattr(untertitel, 'WHISPER_URL', 'http://whisper.test/v1')
    gesehen = {}

    def whisper(pfad, typ):
        with open(pfad, 'rb') as f:
            gesehen['daten'] = f.read()
        gesehen['pfad'] = pfad
        return 'WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\nHallo aus S3\n'
    monkeypatch.setattr(untertitel, 'whisper_anfrage', whisper)
    c, _e, _p = konto()
    aid = c.post('/api/aufnahmen', content=b'\x1aE\xdf\xa3inhalt', headers={'Content-Type': 'video/webm'}).json()['aufnahme']['id']
    assert c.post(f'/api/aufnahmen/{aid}/transkript').status_code == 200
    for _ in range(50):
        if c.get(f'/api/aufnahmen/{aid}/transkript').json()['transkript']['status'] != 'laeuft':
            break
        import time
        time.sleep(0.1)
    assert gesehen['daten'] == b'\x1aE\xdf\xa3inhalt' and not os.path.exists(gesehen['pfad'])      # Kopie in tmp wieder weg
    assert 'Hallo aus S3' in c.get(f'/api/aufnahmen/{aid}/transkript?format=txt').text
    assert s3.client.head(f'p/aufnahmen/{aid}.vtt')
    assert c.post(f'/api/aufnahmen/{aid}/zusammenfassung', json={'art': 'stichwort'}).status_code == 200
    c.delete(f'/api/aufnahmen/{aid}')
    assert s3.client.head(f'p/aufnahmen/{aid}.vtt') is None


def test_s3_dateien_im_meeting(s3):
    h, _e, _p = konto()
    code = raum(h)['code']
    z = zugang(h, code, name='Host')
    kopf = {'X-Teilnahme': z['teilnahme']}
    r = h.post(f'/api/raeume/{code}/dateien', content=b'%PDF-1.4 s3', headers={**kopf, 'X-Dateiname': 'bericht.pdf'})
    assert r.status_code == 200, r.text
    d = r.json()['datei']
    g = h.get(f'/api/raeume/{code}/dateien/{d["id"]}', headers=kopf)
    assert g.content == b'%PDF-1.4 s3' and "sandbox" in g.headers['content-security-policy']
    assert g.headers['content-disposition'] == 'attachment; filename="bericht.pdf"'
    # verwaiste Objekte (ohne Zeile, älter als 1 h) räumt aufraeumen() weg – hier: frisch → bleibt
    s3.client.put_bytes('p/dateien/' + 'W' * 22, b'waise')
    dateien.aufraeumen()
    assert s3.client.head('p/dateien/' + 'W' * 22)
    assert h.delete(f'/api/raeume/{code}/dateien/{d["id"]}', headers=kopf).status_code == 200
    assert s3.client.head(f'p/dateien/{d["id"]}') is None


def test_migration_lokal_nach_s3(s3, monkeypatch):
    speicher.setzen(speicher.Lokal())
    c, _e, _p = konto()
    aid = c.post('/api/aufnahmen', content=b'\x1aE\xdf\xa3lokal', headers={'Content-Type': 'video/webm'}).json()['aufnahme']['id']
    with open(untertitel.vtt_pfad(aid), 'w', encoding='utf-8') as f:
        f.write('WEBVTT\n')
    alle = list(speicher_migrieren.kandidaten())
    assert f'aufnahmen/{aid}' in alle and f'aufnahmen/{aid}.vtt' in alle
    monkeypatch.setattr(speicher_migrieren, 'kandidaten', lambda: iter([f'aufnahmen/{aid}', f'aufnahmen/{aid}.vtt']))   # nur diese
    meldungen = []
    assert speicher_migrieren.migrieren(s3, trocken=True, ausgabe=meldungen.append)[0] == 0
    assert s3.client.head(f'p/aufnahmen/{aid}') is None
    k, _u, f = speicher_migrieren.migrieren(s3, loeschen=True, ausgabe=meldungen.append)
    assert k == 2 and f == 0, meldungen
    assert s3.client.head(f'p/aufnahmen/{aid}')['groesse'] == 9 and not os.path.exists(aufnahmen.pfad(aid))
    assert speicher_migrieren.migrieren(s3, ausgabe=meldungen.append)[0] == 0            # zweiter Lauf: nichts mehr zu tun
    speicher.setzen(s3)
    assert c.get(f'/api/aufnahmen/{aid}/datei').content == b'\x1aE\xdf\xa3lokal'
