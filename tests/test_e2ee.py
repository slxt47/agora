"""Tests Ende-zu-Ende-Verschlüsselung: nur Schalter + Prüfwert beim Server, Ändern nur ohne Verbundene. Hilfen aus test_api."""
from test_api import H, frisch, konto, main, neu_client, raum, zugang  # noqa: F401

from app import db
from app import livekit_dienst as lk

PRUEF = 'ab' * 16


def test_e2ee_einschalten_pruefwert_und_ausschalten():
    host, _e, _p = konto()
    r = raum(host)
    assert r['e2ee'] is False and r['e2ee_pruef'] is None
    code = r['code']
    # ohne oder mit kaputtem Prüfwert: abgelehnt
    for body in ({'e2ee': True}, {'e2ee': True, 'e2ee_pruef': 'xyz'}, {'e2ee': True, 'e2ee_pruef': 'AB' * 16},
                 {'e2ee': 'ja', 'e2ee_pruef': PRUEF}, {'e2ee_pruef': PRUEF}):
        assert host.patch(f'/api/raeume/{code}', json=body).status_code == 400, body
    r = host.patch(f'/api/raeume/{code}', json={'e2ee': True, 'e2ee_pruef': PRUEF})
    assert r.status_code == 200 and r.json()['raum']['e2ee'] is True and r.json()['raum']['e2ee_pruef'] == PRUEF
    # Gäste sehen Schalter und Prüfwert (damit die Lobby einen falschen Schlüssel erkennt), nie einen Schlüssel
    info = neu_client().get(f'/api/raeume/{code}').json()['raum']
    assert info['e2ee'] is True and info['e2ee_pruef'] == PRUEF
    spalten = [x[1] for x in db.verbindung().execute('PRAGMA table_info(raeume)').fetchall()]
    assert 'e2ee' in spalten and 'e2ee_pruef' in spalten and not any('schluessel' in s for s in spalten)
    # Fremde dürfen nicht
    fremd, _e2, _p2 = konto()
    assert fremd.patch(f'/api/raeume/{code}', json={'e2ee': False}).status_code == 403
    r = host.patch(f'/api/raeume/{code}', json={'e2ee': False})
    assert r.json()['raum']['e2ee'] is False and r.json()['raum']['e2ee_pruef'] is None
    assert db.verbindung().execute('SELECT e2ee_pruef FROM raeume WHERE code = ?', (code,)).fetchone()[0] is None


def test_e2ee_beim_anlegen_und_aendern_nur_ohne_verbundene():
    host, _e, _p = konto()
    r = raum(host, e2ee=True, e2ee_pruef=PRUEF)
    code = r['code']
    assert r['e2ee'] is True
    zugang(host, code)                                   # Meeting läuft
    lk.FAKE_TEILNEHMER[code] = [{'identity': 'u1-x', 'name': 'Host', 'tracks': []}]
    neu = {'e2ee': True, 'e2ee_pruef': 'cd' * 16}
    r = host.patch(f'/api/raeume/{code}', json=neu)
    assert r.status_code == 409 and 'niemand' in r.json()['fehler']
    assert host.patch(f'/api/raeume/{code}', json={'e2ee': False}).status_code == 409
    # gleicher Zustand nochmal schicken stört nicht; andere Felder bleiben änderbar
    assert host.patch(f'/api/raeume/{code}', json={'e2ee': True, 'e2ee_pruef': PRUEF, 'titel': 'Neu'}).status_code == 200
    lk.FAKE_TEILNEHMER[code] = []                        # alle weg (Meeting noch aktiv) → neuer Schlüssel erlaubt
    r = host.patch(f'/api/raeume/{code}', json=neu)
    assert r.status_code == 200 and r.json()['raum']['e2ee_pruef'] == 'cd' * 16


def test_csp_unveraendert_fuer_e2ee_worker():
    """E2EE-Worker kommt gebündelt von 'self' – die CSP braucht dafür nichts Neues (kein blob:/eval für Skripte)."""
    teile = {t.split()[0]: t.split()[1:] for t in neu_client().get('/api/gesund').headers['content-security-policy'].split('; ')}
    assert teile['worker-src'] == ["'self'", 'blob:']
    assert teile['script-src'] == ["'self'", "'wasm-unsafe-eval'"]
