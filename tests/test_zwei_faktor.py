"""Tests Zwei-Faktor-Anmeldung (TOTP). Hilfen und Umgebung kommen aus test_api (setzt AGORA_* vor dem App-Import)."""
import base64
import hashlib
import hmac
import struct
import time

from test_api import frisch, konto, neu_client  # noqa: F401  (frisch = autouse-Fixture)

from app import db, zwei_faktor


def totp(geheim, t=None, schritt_versatz=0):
    """Eigene, unabhängige TOTP-Rechnung (RFC 6238) für die Tests."""
    k = base64.b32decode(geheim + '=' * (-len(geheim) % 8))
    n = int((t or time.time()) // 30) + schritt_versatz
    d = hmac.new(k, struct.pack('>Q', n), hashlib.sha1).digest()
    o = d[-1] & 15
    return str((struct.unpack('>I', d[o:o + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


class Uhr:
    """Verstellbare Uhr für zwei_faktor (Zeitschritte gezielt wechseln)."""
    def __init__(self, monkeypatch):
        self.t = (int(time.time()) // 30) * 30 + 5.0
        monkeypatch.setattr(zwei_faktor, '_zeit', lambda: self.t)

    def weiter(self, schritte=1):
        self.t += 30 * schritte


def einschalten(c, pw, uhr):
    r = c.post('/api/konto/2fa/einrichten', json={'passwort': pw})
    assert r.status_code == 200, r.text
    geheim = r.json()['geheim']
    assert r.json()['uri'].startswith('otpauth://totp/Agora:') and f'secret={geheim}' in r.json()['uri']
    r = c.post('/api/konto/2fa/bestaetigen', json={'code': totp(geheim, uhr.t)})
    assert r.status_code == 200, r.text
    codes = r.json()['wiederherstellungscodes']
    assert len(codes) == 10 and len(set(codes)) == 10
    return geheim, codes


def anmelden(c, email, pw):
    r = c.post('/api/konto/anmelden', json={'email': email, 'passwort': pw})
    assert r.status_code == 200, r.text
    return r.json()


def test_rfc6238_testvektor():
    k = b'12345678901234567890'
    assert zwei_faktor.hotp(k, 59 // 30, 8) == '94287082'
    assert zwei_faktor.hotp(k, 1111111109 // 30, 8) == '07081804'
    assert zwei_faktor.hotp(k, 0) == '755224'          # RFC 4226 Anhang D


def test_verschluesselung_und_manipulation():
    v = zwei_faktor.verschluesseln('JBSWY3DPEHPK3PXP')
    assert v.startswith('v1.') and 'JBSWY3DP' not in v
    assert zwei_faktor.entschluesseln(v) == 'JBSWY3DPEHPK3PXP'
    assert zwei_faktor.verschluesseln('JBSWY3DPEHPK3PXP') != v          # Zufalls-Nonce
    roh = bytearray(base64.urlsafe_b64decode(v[3:]))
    roh[20] ^= 1
    assert zwei_faktor.entschluesseln('v1.' + base64.urlsafe_b64encode(bytes(roh)).decode()) is None
    assert zwei_faktor.entschluesseln('kaputt') is None and zwei_faktor.entschluesseln(None) is None


def test_einschalten_anmelden_toleranz_und_wiederverwendung(monkeypatch):
    uhr = Uhr(monkeypatch)
    c, email, pw = konto()
    assert c.get('/api/konto/2fa').json() == {'an': False, 'codes_uebrig': 0}
    assert c.post('/api/konto/2fa/einrichten', json={'passwort': 'falsch-falsch'}).status_code == 400
    r = c.post('/api/konto/2fa/einrichten', json={'passwort': pw})
    geheim = r.json()['geheim']
    assert c.post('/api/konto/2fa/bestaetigen', json={'code': '000000' if totp(geheim, uhr.t) != '000000' else '111111'}).status_code == 400
    assert c.get('/api/konto/ich').json()['ich']['zwei_faktor'] is False
    uhr.weiter()
    geheim, codes = (geheim, c.post('/api/konto/2fa/bestaetigen', json={'code': totp(geheim, uhr.t)}).json()['wiederherstellungscodes'])
    assert len(codes) == 10
    assert c.get('/api/konto/2fa').json() == {'an': True, 'codes_uebrig': 10}
    assert c.get('/api/konto/ich').json()['ich']['zwei_faktor'] is True
    # Geheimnis liegt verschlüsselt, Codes nur als Hash
    row = db.verbindung().execute('SELECT totp_geheim FROM benutzer WHERE email = ?', (email,)).fetchone()
    assert geheim not in row[0] and zwei_faktor.entschluesseln(row[0]) == geheim
    alle = ' '.join(x[0] for x in db.verbindung().execute('SELECT code_hash FROM wiederherstellung').fetchall())
    assert not any(cd.replace('-', '') in alle for cd in codes)

    # Anmelden: Passwort allein reicht nicht mehr
    neu = neu_client()
    a = anmelden(neu, email, pw)
    assert a['zwei_faktor'] is True and 'ich' not in a and len(a['schritt']) > 20
    assert neu.get('/api/konto/ich').json() == {'angemeldet': False}
    # Code aus dem gleichen Schritt wie beim Einschalten → schon verbraucht (Wiederverwendung)
    r = neu.post('/api/konto/anmelden/code', json={'schritt': a['schritt'], 'code': totp(geheim, uhr.t)})
    assert r.status_code == 400 and r.json()['fehler'] == 'Der Code stimmt nicht.'
    # ein Schritt später, Handy-Uhr 30 s nach: Code vom vorigen Schritt? schon verbraucht; vom nächsten (+1) → ok
    r = neu.post('/api/konto/anmelden/code', json={'schritt': a['schritt'], 'code': totp(geheim, uhr.t, 1)})
    assert r.status_code == 200 and r.json()['ich']['email'] == email, r.text
    assert neu.get('/api/konto/ich').json()['angemeldet']
    # Schritt ist verbraucht
    assert neu.post('/api/konto/anmelden/code', json={'schritt': a['schritt'], 'code': totp(geheim, uhr.t, 1)}).status_code == 400
    # derselbe Code (+1) kann nicht noch einmal benutzt werden, auch nicht in einer neuen Anmeldung
    zweit = neu_client()
    a2 = anmelden(zweit, email, pw)
    assert zweit.post('/api/konto/anmelden/code', json={'schritt': a2['schritt'], 'code': totp(geheim, uhr.t, 1)}).status_code == 400
    # zwei Schritte weiter ist +1 → jetzt −1 (Toleranz), aber schon verbraucht; aktueller Schritt geht
    uhr.weiter(2)
    assert zweit.post('/api/konto/anmelden/code', json={'schritt': a2['schritt'], 'code': totp(geheim, uhr.t, -1)}).status_code == 400
    assert zweit.post('/api/konto/anmelden/code', json={'schritt': a2['schritt'], 'code': totp(geheim, uhr.t, 2)}).status_code == 400
    assert zweit.post('/api/konto/anmelden/code', json={'schritt': a2['schritt'], 'code': totp(geheim, uhr.t)}).status_code == 200


def test_wiederherstellungscode_einmalig(monkeypatch):
    uhr = Uhr(monkeypatch)
    c, email, pw = konto()
    _geheim, codes = einschalten(c, pw, uhr)
    neu = neu_client()
    a = anmelden(neu, email, pw)
    r = neu.post('/api/konto/anmelden/code', json={'schritt': a['schritt'], 'code': codes[0].upper().replace('-', ' ')})
    assert r.status_code == 200 and r.json()['codes_uebrig'] == 9
    a = anmelden(neu_client(), email, pw)
    assert neu_client().post('/api/konto/anmelden/code', json={'schritt': a['schritt'], 'code': codes[0]}).status_code == 400
    ereignisse = [x[0] for x in db.verbindung().execute(
        'SELECT ereignis FROM protokoll WHERE benutzer_id = (SELECT id FROM benutzer WHERE email = ?)', (email,)).fetchall()]
    assert '2FA eingeschaltet' in ereignisse and 'angemeldet (mit Wiederherstellungscode)' in ereignisse


def test_versuche_begrenzt_und_ablauf(monkeypatch):
    uhr = Uhr(monkeypatch)
    c, email, pw = konto()
    geheim, _codes = einschalten(c, pw, uhr)
    uhr.weiter()
    x = neu_client()
    a = anmelden(x, email, pw)
    falsch = '000000' if totp(geheim, uhr.t) != '000000' else '111111'
    for i in range(4):
        assert x.post('/api/konto/anmelden/code', json={'schritt': a['schritt'], 'code': falsch}).json()['fehler'] == 'Der Code stimmt nicht.'
    r = x.post('/api/konto/anmelden/code', json={'schritt': a['schritt'], 'code': falsch})
    assert r.status_code == 400 and 'Zu viele' in r.json()['fehler']
    # Schritt ist weg – auch der richtige Code hilft nicht mehr
    assert x.post('/api/konto/anmelden/code', json={'schritt': a['schritt'], 'code': totp(geheim, uhr.t)}).status_code == 400
    assert x.post('/api/konto/anmelden/code', json={'schritt': 'x' * 43, 'code': '123456'}).status_code == 400
    # Grenze je Konto: 10 Versuche in 10 min, über mehrere Anmeldungen
    for _ in range(5):
        b = anmelden(neu_client(), email, pw)
        neu_client().post('/api/konto/anmelden/code', json={'schritt': b['schritt'], 'code': falsch})
    b = anmelden(neu_client(), email, pw)
    assert neu_client().post('/api/konto/anmelden/code', json={'schritt': b['schritt'], 'code': totp(geheim, uhr.t)}).status_code == 429
    # abgelaufener Schritt
    db.verbindung().execute('UPDATE anmeldeschritte SET gueltig_bis = 0')
    from app.kern import grenze
    grenze.d.clear()
    assert neu_client().post('/api/konto/anmelden/code', json={'schritt': b['schritt'], 'code': totp(geheim, uhr.t)}).status_code == 400


def test_ausschalten_und_codes_neu(monkeypatch):
    uhr = Uhr(monkeypatch)
    c, email, pw = konto()
    geheim, codes = einschalten(c, pw, uhr)
    assert c.post('/api/konto/2fa/einrichten', json={'passwort': pw}).status_code == 409
    uhr.weiter()
    assert c.post('/api/konto/2fa/codes', json={'passwort': 'falsch-falsch', 'code': totp(geheim, uhr.t)}).status_code == 400
    r = c.post('/api/konto/2fa/codes', json={'passwort': pw, 'code': totp(geheim, uhr.t)})
    assert r.status_code == 200 and len(r.json()['wiederherstellungscodes']) == 10
    neue = r.json()['wiederherstellungscodes']
    assert not set(neue) & set(codes)
    # Ausschalten braucht Passwort und Code (alter Wiederherstellungscode gilt nicht mehr, neuer schon)
    assert c.post('/api/konto/2fa/ausschalten', json={'passwort': pw, 'code': codes[1]}).status_code == 400
    assert c.post('/api/konto/2fa/ausschalten', json={'passwort': pw}).status_code == 400
    assert c.post('/api/konto/2fa/ausschalten', json={'passwort': pw, 'code': neue[3]}).json() == {'ok': True}
    assert c.get('/api/konto/2fa').json() == {'an': False, 'codes_uebrig': 0}
    r = neu_client().post('/api/konto/anmelden', json={'email': email, 'passwort': pw})
    assert r.status_code == 200 and r.json()['ok'] and 'zwei_faktor' not in r.json()
    assert db.verbindung().execute('SELECT totp_geheim FROM benutzer WHERE email = ?', (email,)).fetchone()[0] is None
    assert neu_client().post('/api/konto/2fa/einrichten', json={'passwort': pw}).status_code == 401
