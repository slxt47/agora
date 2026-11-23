"""Tests Sicherheitsereignisse des eigenen Kontos (GET /api/konto/sicherheit)."""
import pathlib
import re

from test_api import frisch, konto, neu_client  # noqa: F401

from app import db, sicherheit


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
    js = (pathlib.Path(__file__).parent.parent / 'web/src/sicherheit-arten.js').read_text()
    assert set(re.findall(r'\b([a-z_0-9]+): \[', js)) == {a for _p, a in sicherheit.ARTEN}


def test_maximal_100():
    c, _email, _pw = konto()
    bid = c.get('/api/konto/ich').json()['ich']['id']
    db.verbindung().executemany('INSERT INTO protokoll VALUES (?, ?, NULL, ?)', [(i, bid, 'angemeldet') for i in range(150)])
    assert len(c.get('/api/konto/sicherheit').json()['ereignisse']) == 100
