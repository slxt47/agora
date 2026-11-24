"""Tests Sicherheitsereignisse des eigenen Kontos (GET /api/konto/sicherheit) und Härtungen aus docs/SICHERHEIT.md."""
import pathlib
import re

import jwt
from test_api import T, aufrufe, frisch, konto, meeting, neu_client  # noqa: F401

from app import db, sicherheit
from app import livekit_dienst as lk


def test_eigene_sicherheitsereignisse():
    c, email, pw = konto()
    andere, _e2, _p2 = konto(name='Bea')
    assert neu_client().get('/api/konto/sicherheit').status_code == 401
    neu_client().post('/api/konto/anmelden', json={'email': email, 'passwort': 'falsch-falsch'})
    assert c.post('/api/konto/passwort-aendern', json={'alt': pw, 'neu': 'neues-passwort-1'}).status_code == 200
    neu_client().post('/api/konto/anmelden', json={'email': email, 'passwort': 'neues-passwort-1'})
    assert c.post('/api/raeume', json={'titel': 'X'}).status_code == 200      # Raum-Ereignisse gehören nicht in die Liste
    d = c.get('/api/konto/sicherheit').json()
    arten = [e['art'] for e in d['ereignisse']]
    assert d['tage'] == 30
    assert arten == ['anmeldung', 'passwort_geaendert', 'fehlversuch_passwort', 'email_bestaetigt'], arten
    assert all(re.match(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$', e['zeit']) for e in d['ereignisse'])
    # nur eigene Ereignisse
    assert [e['art'] for e in andere.get('/api/konto/sicherheit').json()['ereignisse']] == ['email_bestaetigt']


def test_arten_zuordnung_und_frontend_liste():
    assert sicherheit.art('angemeldet') == 'anmeldung'
    assert sicherheit.art('angemeldet (mit 2FA)') == 'anmeldung_2fa'
    assert sicherheit.art('2FA: Wiederherstellungscode benutzt (7 übrig)') == 'zwei_faktor_code_benutzt'
    assert sicherheit.art('Raum angelegt (sofort)') is None and sicherheit.art('angemeldetX') is None
    # Frontend kennt genau dieselben Arten (web/src/sicherheit-arten.js)
    quelle = pathlib.Path(__file__).parent.parent / 'web/src/sicherheit-arten.js'
    if not quelle.exists():          # im Docker-Testziel liegt nur das gebaute Frontend
        import pytest
        pytest.skip('Frontend-Quellen nicht im Image')
    js = quelle.read_text()
    assert set(re.findall(r'\b([a-z_0-9]+): \[', js)) == {a for _p, a in sicherheit.ARTEN}


def test_maximal_100():
    c, _email, _pw = konto()
    bid = c.get('/api/konto/ich').json()['ich']['id']
    db.verbindung().executemany('INSERT INTO protokoll VALUES (?, ?, NULL, ?)', [(i, bid, 'angemeldet') for i in range(150)])
    assert len(c.get('/api/konto/sicherheit').json()['ereignisse']) == 100


# ---------------------------------------------------------------- Größe des JSON-Bodys (Speicher-DoS ohne Anmeldung)
def _anfrage(kopf, teile):
    """Starlette-Request mit eigenem receive: zählt, wie viele Body-Teile die App wirklich liest."""
    from starlette.requests import Request
    gelesen = []

    async def receive():
        i = len(gelesen)
        gelesen.append(i)
        if i >= len(teile):
            raise AssertionError('mehr gelesen als gesendet')
        return {'type': 'http.request', 'body': teile[i], 'more_body': i < len(teile) - 1}
    scope = {'type': 'http', 'method': 'POST', 'path': '/', 'headers': [(k.encode(), v.encode()) for k, v in kopf.items()]}
    return Request(scope, receive), gelesen


def test_json_body_liest_nie_mehr_als_die_grenze():
    import asyncio

    import pytest
    from fastapi import HTTPException

    from app.kern import json_body
    # Content-Length zu groß → 413, ohne ein Byte zu lesen
    req, gelesen = _anfrage({'content-length': str(100 * 1024 ** 2)}, [b'{}'])
    with pytest.raises(HTTPException) as e:
        asyncio.run(json_body(req))
    assert e.value.status_code == 413 and gelesen == []
    # ohne Content-Length (chunked): Abbruch beim ersten Teil über der Grenze, nicht erst nach 100 MB
    req, gelesen = _anfrage({}, [b' ' * 10000] * 10000)
    with pytest.raises(HTTPException) as e:
        asyncio.run(json_body(req))
    assert e.value.status_code == 413 and len(gelesen) == 2
    req, _g = _anfrage({'content-length': 'abc'}, [b'{}'])
    with pytest.raises(HTTPException) as e:
        asyncio.run(json_body(req))
    assert e.value.status_code == 400
    req, _g = _anfrage({}, [b'{"a":', b' 1}'])
    assert asyncio.run(json_body(req)) == {'a': 1}


def test_json_body_grenzen_ueber_http():
    c = neu_client()
    j = {'Content-Type': 'application/json'}
    assert c.post('/api/konto/anmelden', content=b'{"email":"' + b'a' * 20000 + b'"}', headers=j).status_code == 413
    for body in (b'[' * 15000, b'{"a":' * 2500 + b'1' + b'}' * 2500):      # tief verschachtelt: 400 statt 500
        assert c.post('/api/konto/anmelden', content=body, headers=j).status_code == 400


# ---------------------------------------------------------------- LiveKit: Name/Attribute/Metadaten nur vom Server
def test_token_ohne_eigene_metadaten_und_hand_ueber_server():
    host, code, hz, [(g, gz)] = meeting(1)
    for z in (hz, gz):
        v = jwt.decode(z['token'], lk.API_SECRET, algorithms=['HS256'], options={'verify_iss': False})['video']
        assert not v.get('canUpdateOwnMetadata')          # sonst: selbst umbenennen trotz Recht aus, Attribute fälschen
    assert lk.berechtigung('gast', {}).can_update_metadata is False
    # Hand heben/senken geht über den Server (Attribut hand)
    r = g.post(f'/api/raeume/{code}/hand', json={'an': True}, headers=T(gz))
    assert r.status_code == 200 and r.json() == {'ok': True, 'hand': True}
    a = aufrufe('aktualisieren')[-1]
    assert a['identity'] == gz['identity'] and a['attribute'] == {'hand': '1'} and a['name'] is None and a['rolle'] is None
    assert g.post(f'/api/raeume/{code}/hand', json={'an': False}, headers=T(gz)).status_code == 200
    assert aufrufe('aktualisieren')[-1]['attribute'] == {'hand': ''}
    # nur mit gültiger Teilnahme, nur bool
    assert neu_client().post(f'/api/raeume/{code}/hand', json={'an': True}).status_code == 403
    assert g.post(f'/api/raeume/{code}/hand', json={'an': 'ja'}, headers=T(gz)).status_code == 400
    assert g.post(f'/api/raeume/{code}/hand', json={'an': True, 'rolle': 'host'}, headers=T(gz)).status_code == 200
    assert aufrufe('aktualisieren')[-1]['attribute'] == {'hand': '1'}       # nichts außer hand


# ---------------------------------------------------------------- Begrenzer: IPv6-/64 und Fehlversuche je Konto
def test_ipv6_wird_als_64er_netz_gezaehlt():
    from app.kern import ip_gruppe
    assert ip_gruppe('2001:db8:1:2:3:4:5:6') == ip_gruppe('2001:db8:1:2:ffff::1') == '2001:db8:1:2::/64'
    assert ip_gruppe('2001:db8:1:3::1') != ip_gruppe('2001:db8:1:2::1')
    assert ip_gruppe('::ffff:192.0.2.7') == '192.0.2.7' and ip_gruppe('192.0.2.7') == '192.0.2.7'
    assert ip_gruppe('fe80::1%eth0') == 'fe80::/64' and ip_gruppe('kaputt') == 'kaputt'
    _c, email, _pw = konto()
    st = [neu_client(f'2001:db8:7:7:{i:x}::{i + 1:x}').post('/api/konto/anmelden', json={'email': email, 'passwort': f'falsch-{i}-xxxx'}).status_code
          for i in range(11)]
    assert st == [400] * 10 + [429], st


def test_fehlversuche_je_konto_ueber_alle_ips():
    from app import konto as konto_modul
    _c, email, pw = konto()
    _c2, email2, pw2 = konto(name='Bea')
    for i in range(konto_modul.KONTO_FEHL):     # jeder Versuch von einer anderen IP
        assert neu_client().post('/api/konto/anmelden', json={'email': email, 'passwort': f'falsch-{i}-xxxx'}).status_code == 400
    # jetzt gesperrt – auch mit richtigem Passwort (sonst ginge das Raten weiter) und auch in anderer Schreibweise
    assert neu_client().post('/api/konto/anmelden', json={'email': email, 'passwort': pw}).status_code == 429
    assert neu_client().post('/api/konto/anmelden', json={'email': email.upper(), 'passwort': pw}).status_code == 429
    # andere Konten sind nicht betroffen
    assert neu_client().post('/api/konto/anmelden', json={'email': email2, 'passwort': pw2}).status_code == 200
