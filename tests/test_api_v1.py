"""Tests öffentliche API: API-Schlüssel im Konto und /api/v1 (Bearer, ohne Cookies/CSRF-Header)."""
import os
import pathlib
import re
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from test_api import frisch, konto, neu_client, raum  # noqa: F401

from app import api_schluessel, aufnahmen, db, main
from app import livekit_dienst as lk
from app.kern import grenze, jetzt


def schluessel(c, rechte=('meetings:lesen', 'meetings:schreiben', 'aufnahmen:lesen'), **kw):
    r = c.post('/api/konto/api-schluessel', json={'name': 'Skript', 'rechte': list(rechte)} | kw)
    assert r.status_code == 200, r.text
    return r.json()['token'], r.json()['schluessel']


def v1(token=None, ip='198.51.100.7'):
    """Client ohne Cookies und ohne X-Agora – wie ein fremdes Programm."""
    kopf = {'CF-Connecting-IP': ip}
    if token:
        kopf['Authorization'] = f'Bearer {token}'
    return TestClient(main.app, headers=kopf)


def test_schluessel_anlegen_einmal_sichtbar_gehasht_widerrufen():
    c, _e, _p = konto()
    assert neu_client().get('/api/konto/api-schluessel').status_code == 401
    token, k = schluessel(c, gueltig_tage=90)
    assert re.match(r'^agora_[\w-]{43}$', token) and k['anfang'] == token[:12]
    assert k['rechte'] == ['meetings:lesen', 'meetings:schreiben', 'aufnahmen:lesen'] and k['benutzt'] is None and k['gueltig_bis']
    roh = db.verbindung().execute('SELECT * FROM api_schluessel WHERE id = ?', (k['id'],)).fetchone()
    assert token not in str(dict(roh)) and roh['schluessel_hash'] != token        # nur Hash gespeichert
    liste = c.get('/api/konto/api-schluessel').json()
    assert 'token' not in str(liste) and token not in str(liste) and liste['schluessel'][0]['id'] == k['id']
    # ungültige Eingaben
    for body in ({'name': 'x', 'rechte': []}, {'name': 'x', 'rechte': ['admin']}, {'name': '', 'rechte': ['webhooks']},
                 {'name': 'x', 'rechte': ['webhooks'], 'gueltig_tage': 7}):
        assert c.post('/api/konto/api-schluessel', json=body).status_code == 400, body
    # Nutzung setzt „zuletzt benutzt“
    assert v1(token).get('/api/v1/meetings').status_code == 200
    assert c.get('/api/konto/api-schluessel').json()['schluessel'][0]['benutzt']
    export = c.get('/api/konto/export').json()
    assert export['api_schluessel'][0]['anfang'] == token[:12] and token not in str(export)
    # fremdes Konto kann nicht widerrufen
    andere, _e2, _p2 = konto(name='Bea')
    assert andere.delete(f'/api/konto/api-schluessel/{k["id"]}').status_code == 404
    assert c.delete(f'/api/konto/api-schluessel/{k["id"]}').status_code == 200
    r = v1(token).get('/api/v1/meetings')
    assert r.status_code == 401 and 'Bearer' in r.headers['www-authenticate']
    arten = [e['art'] for e in c.get('/api/konto/sicherheit').json()['ereignisse']]
    assert arten[:2] == ['api_schluessel_weg', 'api_schluessel_neu']


def test_hoechstens_zehn_schluessel():
    c, _e, _p = konto()
    for _ in range(10):
        schluessel(c)
    assert c.post('/api/konto/api-schluessel', json={'name': 'x', 'rechte': ['webhooks']}).status_code == 409


def test_bearer_pflicht_rechte_und_kein_cookie():
    c, _e, _p = konto()
    assert v1().get('/api/v1/meetings').status_code == 401
    assert v1('agora_falsch-falsch-falsch-falsch').get('/api/v1/meetings').status_code == 401
    # Cookie allein reicht nicht
    assert c.get('/api/v1/meetings').status_code == 401
    nur_lesen, _k = schluessel(c, rechte=['meetings:lesen'])
    assert v1(nur_lesen).get('/api/v1/meetings').status_code == 200
    r = v1(nur_lesen).post('/api/v1/meetings', json={'titel': 'X'})
    assert r.status_code == 403 and 'meetings:schreiben' in r.json()['fehler']
    assert v1(nur_lesen).get('/api/v1/aufnahmen').status_code == 403
    # gesperrte Konten: Schlüssel gilt nicht mehr
    bid = c.get('/api/konto/ich').json()['ich']['id']
    db.verbindung().execute('UPDATE benutzer SET gesperrt = 1 WHERE id = ?', (bid,))
    assert v1(nur_lesen).get('/api/v1/meetings').status_code == 401
    db.verbindung().execute('UPDATE benutzer SET gesperrt = 0 WHERE id = ?', (bid,))
    # abgelaufen
    db.verbindung().execute('UPDATE api_schluessel SET gueltig_bis = ? WHERE benutzer_id = ?', (jetzt() - 60, bid))
    assert v1(nur_lesen).get('/api/v1/meetings').status_code == 401
    api_schluessel.aufraeumen(jetzt())
    assert len(c.get('/api/konto/api-schluessel').json()['schluessel']) == 1          # noch sichtbar (abgelaufen)
    api_schluessel.aufraeumen(jetzt() + 31 * 86400)
    assert c.get('/api/konto/api-schluessel').json()['schluessel'] == []


def test_kein_cors_und_kein_csrf_header_noetig():
    c, _e, _p = konto()
    token, _k = schluessel(c)
    r = v1(token).post('/api/v1/meetings', json={'titel': 'Ohne X-Agora'}, headers={'Origin': 'https://fremd.example'})
    assert r.status_code == 201 and 'access-control-allow-origin' not in r.headers
    r = v1(token).options('/api/v1/meetings', headers={'Origin': 'https://fremd.example', 'Access-Control-Request-Method': 'POST'})
    assert 'access-control-allow-origin' not in r.headers
    # die übrige API braucht X-Agora weiterhin
    assert TestClient(main.app).post('/api/raeume', json={}).status_code == 403


def test_grenze_je_schluessel_und_falsche_schluessel():
    c, _e, _p = konto()
    token, _k = schluessel(c)
    for _ in range(120):
        assert v1(token).get('/api/v1/meetings').status_code == 200
    r = v1(token).get('/api/v1/meetings')
    assert r.status_code == 429 and r.headers['retry-after'] == '60'
    grenze.d.clear()
    for _ in range(30):
        assert v1('agora_' + 'x' * 43, ip='198.51.100.99').get('/api/v1/meetings').status_code == 401
    assert v1('agora_' + 'x' * 43, ip='198.51.100.99').get('/api/v1/meetings').status_code == 429


def test_meetings_anlegen_aendern_loeschen_link():
    c, _e, _p = konto()
    token, _k = schluessel(c)
    a = v1(token)
    r = a.post('/api/v1/meetings', json={'titel': 'Sofort', 'warteraum': True, 'passcode': 'geheim1'})
    assert r.status_code == 201, r.text
    m = r.json()['meeting']
    assert m['art'] == 'sofort' and m['warteraum'] and m['passcode_noetig'] and m['link'].endswith('/r/' + m['code'])
    assert 'passcode' not in m and 'passcode_hash' not in str(m)
    r = a.post('/api/v1/meetings', json={'titel': 'Jour fixe', 'geplant': {'start': '2030-01-07T09:00', 'dauer_min': 30,
                                                                          'zeitzone': 'Europe/Berlin', 'wiederholung': 'woechentlich', 'bis': '2030-03-01'}})
    assert r.status_code == 201, r.text
    g = r.json()['meeting']
    assert g['art'] == 'geplant' and g['geplant']['wiederholung'] == 'woechentlich' and g['geplant']['start'] == '2030-01-07T08:00:00Z'
    assert g['naechster_termin'] == '2030-01-07T08:00:00Z'
    assert a.post('/api/v1/meetings', json={'titel': 'X', 'e2ee': True}).status_code == 400          # unbekanntes Feld
    assert a.post('/api/v1/meetings', json={'geplant': {'start': 'morgen'}}).status_code == 400
    # Liste (auch im Konto sichtbar), Filter
    codes = [x['code'] for x in a.get('/api/v1/meetings').json()['meetings']]
    assert m['code'] in codes and g['code'] in codes
    assert [x['code'] for x in a.get('/api/v1/meetings?art=geplant').json()['meetings']] == [g['code']]
    assert a.get('/api/v1/meetings?art=quatsch').status_code == 400
    assert g['code'] in [x['code'] for x in c.get('/api/raeume').json()['geplant']]
    # ändern
    r = a.patch(f'/api/v1/meetings/{g["code"]}', json={'titel': 'Jour fixe neu', 'geplant': {'start': '2030-01-08T10:00', 'zeitzone': 'Europe/Berlin'}})
    assert r.status_code == 200 and r.json()['meeting']['titel'] == 'Jour fixe neu' and r.json()['meeting']['geplant']['wiederholung'] == 'keine'
    assert a.patch(f'/api/v1/meetings/{m["code"]}', json={'geplant': {'start': '2030-01-08T10:00'}}).status_code == 400
    assert a.patch(f'/api/v1/meetings/{m["code"]}', json={'passcode': ''}).json()['meeting']['passcode_noetig'] is False
    # Link
    li = a.get(f'/api/v1/meetings/{g["code"]}/link').json()
    assert li['link'] == 'https://agora.slxt.dev/r/' + g['code'] and li['naechster_termin'] == '2030-01-08T09:00:00Z'
    # fremde Meetings: 404
    andere, _e2, _p2 = konto(name='Bea')
    fremd = raum(andere, titel='Fremd')['code']
    for weg in ('', '/link', '/teilnehmer'):
        assert a.get(f'/api/v1/meetings/{fremd}{weg}').status_code == 404
    assert a.patch(f'/api/v1/meetings/{fremd}', json={'titel': 'x'}).status_code == 404
    assert a.delete(f'/api/v1/meetings/{fremd}').status_code == 404
    # persönlicher Raum nicht löschbar, andere schon
    pers = c.get('/api/konto/ich').json()['ich']['persoenlicher_raum']
    assert a.delete(f'/api/v1/meetings/{pers}').status_code == 400
    assert a.delete(f'/api/v1/meetings/{g["code"]}').status_code == 200
    assert a.get(f'/api/v1/meetings/{g["code"]}').status_code == 404


def test_teilnehmer_eines_laufenden_meetings():
    c, _e, _p = konto()
    token, _k = schluessel(c)
    m = v1(token).post('/api/v1/meetings', json={'titel': 'Läuft'}).json()['meeting']
    assert v1(token).get(f'/api/v1/meetings/{m["code"]}/teilnehmer').json() == {'aktiv': False, 'teilnehmer': []}
    hz = c.post(f'/api/raeume/{m["code"]}/beitreten', json={}).json()['zugang']
    g = neu_client()
    gz = g.post(f'/api/raeume/{m["code"]}/beitreten', json={'name': 'Gast Eins'}).json()['zugang']
    lk.FAKE_TEILNEHMER[m['code']] = [{'identity': hz['identity'], 'name': 'Ada', 'tracks': []},
                                     {'identity': gz['identity'], 'name': 'Gast Eins', 'tracks': []}]
    d = v1(token).get(f'/api/v1/meetings/{m["code"]}/teilnehmer').json()
    assert d['aktiv'] is True
    assert {(x['name'], x['rolle'], x['gast']) for x in d['teilnehmer']} == {('Ada', 'host', False), ('Gast Eins', 'gast', True)}
    assert all(x['beigetreten'] for x in d['teilnehmer'])
    assert 'ip' not in str(d)


def test_aufnahmen_und_signierter_download():
    c, _e, _p = konto()
    token, _k = schluessel(c)
    r = c.post('/api/aufnahmen', content=b'\x1aE\xdf\xa3' + b'x' * 100, headers={'Content-Type': 'video/webm', 'X-Titel': 'Probe'})
    assert r.status_code == 200, r.text
    aid = r.json()['aufnahme']['id']
    a = v1(token)
    liste = a.get('/api/v1/aufnahmen').json()['aufnahmen']
    assert [x['id'] for x in liste] == [aid] and liste[0]['titel'] == 'Probe' and 'freigabe' not in liste[0]
    assert a.get(f'/api/v1/aufnahmen/{aid}').json()['aufnahme']['groesse'] == 104
    d = a.get(f'/api/v1/aufnahmen/{aid}/download').json()
    u = urlsplit(d['url'])
    assert d['url'].startswith('https://agora.slxt.dev/api/v1/download/') and d['gueltig_bis']
    pfad = f'{u.path}?{u.query}'
    r = v1().get(pfad)                                   # ohne Schlüssel, nur mit Signatur
    assert r.status_code == 200 and r.content.startswith(b'\x1aE\xdf\xa3') and 'attachment' in r.headers['content-disposition']
    # Manipulationen
    assert v1().get(pfad.replace('sig=', 'sig=0')).status_code == 403
    bis = int(re.search(r'bis=(\d+)', pfad).group(1))
    assert v1().get(pfad.replace(f'bis={bis}', f'bis={bis + 1}')).status_code == 403
    assert v1().get(pfad.replace(f'bis={bis}', f'bis={bis + 86400}')).status_code == 403
    # fremde Aufnahme
    andere, _e2, _p2 = konto(name='Bea')
    t2, _k2 = schluessel(andere)
    assert v1(t2).get(f'/api/v1/aufnahmen/{aid}/download').status_code == 404
    # gelöscht → Link ungültig
    aufnahmen.aufnahme_entfernen(aid)
    assert v1().get(pfad).status_code == 403


def test_openapi_beschreibt_alle_v1_routen():
    """docs/openapi.yaml (von Hand gepflegt) muss jede /api/v1-Route enthalten; die Haupt-App hat keine Doku-Routen."""
    spec = pathlib.Path(__file__).parent.parent / 'docs/openapi.yaml'
    if not spec.exists():
        import pytest
        pytest.skip('docs/ nicht im Image')
    text = spec.read_text()
    from app import api_schluessel, api_v1
    alle = [r for modul in (api_v1, api_schluessel) for r in modul.router.routes]
    routen = {(m, r.path) for r in alle if r.path.startswith('/api/v1/') for m in r.methods - {'HEAD'}}
    assert routen
    for methode, pfad in routen:
        block = re.search(rf'^  {re.escape(pfad)}:\n((?:    .*\n|\n)*)', text, re.M)
        assert block and re.search(rf'^    {methode.lower()}:', block.group(1), re.M), (methode, pfad)
    for pfad in ('/docs', '/redoc', '/openapi.json'):
        assert neu_client().get(pfad).status_code == 404
    assert os.environ.get('AGORA_TEST') == '1'
