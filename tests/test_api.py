"""API-Tests für Agora (pytest, ohne Browser, ohne LiveKit-Server). Mails landen im Testmodus im Speicher,
LiveKit-Aufrufe in livekit_dienst.AUFRUFE."""
import os
import re
import secrets
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault('AGORA_DATEN', tempfile.mkdtemp())
os.makedirs(os.environ['AGORA_DATEN'], exist_ok=True)
os.environ.update(AGORA_TEST='1', AGORA_COOKIE='agora', AGORA_COOKIE_SECURE='0', AGORA_GEHEIM='x' * 64,
                  AGORA_WEB='/nicht/vorhanden', AGORA_LIVEKIT_URL='ws://127.0.0.1:7980', AGORA_SEITE='https://agora.slxt.dev')
os.environ.pop('LIVEKIT_API_HOST', None)
os.environ.pop('AGORA_GRENZEN_AUS', None)

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import aufnahmen, db, ics, mail, main, raeume  # noqa: E402
from app import livekit_dienst as lk  # noqa: E402
from app.kern import grenze  # noqa: E402

H = {'X-Agora': '1'}
CODE = re.compile(r'^[a-z]{3}-[a-z]{4}-[a-z]{3}$')


def neu_client(ip=None):
    ip = ip or f'10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250)}'
    return TestClient(main.app, headers={**H, 'CF-Connecting-IP': ip})


@pytest.fixture(autouse=True)
def frisch():
    grenze.d.clear()
    mail.POSTEINGANG_TEST.clear()
    lk.fake_zuruecksetzen()
    yield


def mails_an(email):
    return [m for m in mail.POSTEINGANG_TEST if m[0] == email]


def link_aus(email):
    text = mails_an(email)[-1][2]
    url = re.search(r'https://agora\.slxt\.dev(\S+)', text).group(1)
    return url


def konto(c=None, name='Ada', pw='geheim-passwort-1'):
    c = c or neu_client()
    email = f'u{secrets.token_hex(4)}@example.org'
    r = c.post('/api/konto/registrieren', json={'name': name, 'email': email, 'passwort': pw})
    assert r.status_code == 200 and r.json() == {'ok': True}, r.text
    r = c.get(link_aus(email), follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/?bestaetigt=1'
    return c, email, pw


def aufrufe(name):
    return [kw for n, kw in lk.AUFRUFE if n == name]


def ereignisse(typ):
    return [kw for n, kw in lk.AUFRUFE if n == 'senden' and kw['daten'].get('typ') == typ]


def raum(c, **kw):
    r = c.post('/api/raeume', json=kw)
    assert r.status_code == 200, r.text
    return r.json()['raum']


def beitreten(c, code, **kw):
    return c.post(f'/api/raeume/{code}/beitreten', json=kw)


def zugang(c, code, **kw):
    r = beitreten(c, code, **kw)
    assert r.status_code == 200 and r.json()['status'] == 'zugelassen', r.text
    return r.json()['zugang']


def T(z):
    return {'X-Teilnahme': z['teilnahme']}


def steuern(c, code, z, **kw):
    return c.post(f'/api/raeume/{code}/steuerung', json=kw, headers=T(z))


def meeting(n_gaeste=2, **raum_kw):
    """Host mit Raum + n Gäste (je eigene IP)."""
    host, _e, _p = konto()
    r = raum(host, **raum_kw)
    hz = zugang(host, r['code'])
    gaeste = []
    for i in range(n_gaeste):
        g = neu_client()
        gaeste.append((g, zugang(g, r['code'], name=f'Gast {i + 1}')))
    return host, r['code'], hz, gaeste


# ---------------------------------------------------------------- Grundlagen
def test_sicherheitsheader_und_csrf():
    c = neu_client()
    r = c.get('/api/gesund')
    assert r.json() == {'ok': True}
    csp = r.headers['content-security-policy']
    assert "connect-src 'self' ws://127.0.0.1:7980 http://127.0.0.1:7980" in csp
    assert "media-src 'self' blob:" in csp and "img-src 'self' data: blob:" in csp and "frame-ancestors 'none'" in csp
    pp = r.headers['permissions-policy']
    for f in ('camera=(self)', 'microphone=(self)', 'display-capture=(self)', 'fullscreen=(self)', 'picture-in-picture=(self)'):
        assert f in pp
    assert r.headers['x-frame-options'] == 'DENY' and r.headers['cache-control'] == 'no-store'
    ohne = TestClient(main.app)
    r = ohne.post('/api/konto/anmelden', json={'email': 'a@b.de', 'passwort': 'x'})
    assert r.status_code == 403 and r.json() == {'fehler': 'Anfrage abgelehnt'}
    assert ohne.request('DELETE', '/api/konto', json={}).status_code == 403
    assert c.get('/gibts-nicht').json() == {'fehler': 'Nicht gefunden'}


def test_csp_video_effekte():
    """Video-Effekte (MediaPipe-WASM, selbst gehostet): nur 'wasm-unsafe-eval' dazu – kein eval, kein inline, kein CDN."""
    r = neu_client().get('/api/gesund')
    teile = {t.split()[0]: t.split()[1:] for t in r.headers['content-security-policy'].split('; ')}
    assert teile['script-src'] == ["'self'", "'wasm-unsafe-eval'"]
    assert teile['style-src'] == ["'self'"] and teile['default-src'] == ["'self'"]
    assert teile['worker-src'] == ["'self'", 'blob:']
    assert teile['connect-src'][0] == "'self'" and all(q.startswith(('ws://127.0.0.1', 'http://127.0.0.1')) for q in teile['connect-src'][1:])
    csp = r.headers['content-security-policy']
    assert "'unsafe-eval'" not in csp and "'unsafe-inline'" not in csp
    for cdn in ('jsdelivr', 'googleapis', 'unpkg', 'gstatic', 'https:'):
        assert cdn not in csp
    # Fehlende Vendor-Dateien dürfen nicht dauerhaft gecacht werden (nur echte 200er sind „immutable“)
    v = neu_client().get('/vendor/mediapipe-0.0.0/wasm/fehlt.wasm')
    assert v.status_code == 404 and v.headers['cache-control'] == 'no-store'


def test_einstellungen_und_test_posteingang():
    c = neu_client()
    assert c.get('/api/einstellungen').json() == {'livekit_url': 'ws://127.0.0.1:7980', 'max_teilnehmer': 50, 'version': '1.0.0'}
    _c, email, _p = konto()
    mails = c.get('/api/test/posteingang').json()['mails']
    assert any(m['an'] == email and '/api/konto/bestaetigen?t=' in m['text'] for m in mails)


# ---------------------------------------------------------------- Konto
def test_registrieren_bestaetigen_anmelden_abmelden():
    c = neu_client()
    email, pw = f'neu{secrets.token_hex(3)}@example.org', 'sehr-geheim-123'
    assert c.post('/api/konto/registrieren', json={'name': 'Bo', 'email': email, 'passwort': 'kurz'}).status_code == 400
    assert c.post('/api/konto/registrieren', json={'name': 'Bo', 'email': 'kaputt', 'passwort': pw}).status_code == 400
    assert c.post('/api/konto/registrieren', json={'name': 'Bo', 'email': email, 'passwort': pw}).json() == {'ok': True}
    r = c.post('/api/konto/anmelden', json={'email': email, 'passwort': pw})
    assert r.status_code == 403 and 'nicht bestätigt' in r.json()['fehler']
    assert c.get('/api/konto/ich').json() == {'angemeldet': False}
    # falscher Link
    r = c.get('/api/konto/bestaetigen?t=' + 'x' * 43, follow_redirects=False)
    assert r.headers['location'] == '/?bestaetigt=0'
    r = c.get(link_aus(email), follow_redirects=False)
    assert r.headers['location'] == '/?bestaetigt=1'
    ich = c.get('/api/konto/ich').json()
    assert ich['angemeldet'] and ich['ich']['email'] == email and ich['ich']['name'] == 'Bo'
    assert CODE.match(ich['ich']['persoenlicher_raum']) and re.match(r'^#[0-9a-f]{6}$', ich['ich']['farbe'])
    assert ich['ich']['zeitzone'] == 'Europe/Berlin' and ich['ich']['sprache'] == 'de'
    # Link nur einmal gültig
    assert c.get(link_aus(email), follow_redirects=False).headers['location'] == '/?bestaetigt=0'
    assert c.post('/api/konto/abmelden').json() == {'ok': True}
    assert c.get('/api/konto/ich').json() == {'angemeldet': False}
    r = c.post('/api/konto/anmelden', json={'email': email, 'passwort': 'falsch-falsch'})
    assert r.status_code == 400 and r.json()['fehler'] == 'E-Mail oder Passwort falsch.'
    r = c.post('/api/konto/anmelden', json={'email': email.upper(), 'passwort': pw})
    assert r.status_code == 200 and r.json()['ich']['email'] == email
    assert c.get('/api/konto/ich').json()['angemeldet']


def test_registrieren_doppelt_verraet_nichts():
    c, email, _pw = konto()
    andere = neu_client()
    r = andere.post('/api/konto/registrieren', json={'name': 'X', 'email': email, 'passwort': 'anderes-passwort'})
    assert r.json() == {'ok': True}
    assert 'schon ein Konto' in mails_an(email)[-1][1]


def test_bestaetigung_erneut_und_mail_grenze():
    c = neu_client()
    email = f'm{secrets.token_hex(3)}@example.org'
    c.post('/api/konto/registrieren', json={'name': 'M', 'email': email, 'passwort': 'passwort-123'})
    for _ in range(5):
        assert c.post('/api/konto/bestaetigung-erneut', json={'email': email}).json() == {'ok': True}
    assert len(mails_an(email)) == 3          # 3 Mails pro Stunde je Adresse
    assert c.post('/api/konto/bestaetigung-erneut', json={'email': 'gibtsnicht@example.org'}).json() == {'ok': True}


def test_passwort_vergessen_und_setzen():
    c, email, pw = konto()
    zweit = neu_client()
    zweit.post('/api/konto/anmelden', json={'email': email, 'passwort': pw})
    assert zweit.get('/api/konto/ich').json()['angemeldet']
    assert c.post('/api/konto/passwort-vergessen', json={'email': 'niemand@example.org'}).json() == {'ok': True}
    assert c.post('/api/konto/passwort-vergessen', json={'email': email}).json() == {'ok': True}
    pfad = link_aus(email)
    assert pfad.startswith('/passwort?t=')
    token = pfad.split('t=')[1]
    assert c.post('/api/konto/passwort-setzen', json={'token': token, 'passwort': 'kurz'}).status_code == 400
    r = c.post('/api/konto/passwort-setzen', json={'token': token, 'passwort': 'ganz-neues-passwort'})
    assert r.json() == {'ok': True}
    assert zweit.get('/api/konto/ich').json() == {'angemeldet': False}      # alle Sitzungen ungültig
    assert c.get('/api/konto/ich').json() == {'angemeldet': False}
    assert c.post('/api/konto/passwort-setzen', json={'token': token, 'passwort': 'nochmal-passwort'}).status_code == 400
    assert c.post('/api/konto/anmelden', json={'email': email, 'passwort': pw}).status_code == 400
    assert c.post('/api/konto/anmelden', json={'email': email, 'passwort': 'ganz-neues-passwort'}).status_code == 200


def test_passwort_aendern_und_profil():
    c, email, pw = konto()
    assert c.post('/api/konto/passwort-aendern', json={'alt': 'falsch-falsch', 'neu': 'neues-passwort-1'}).status_code == 400
    assert c.post('/api/konto/passwort-aendern', json={'alt': pw, 'neu': 'neues-passwort-1'}).json() == {'ok': True}
    assert c.get('/api/konto/ich').json()['angemeldet']     # aktuelle Sitzung bleibt (neues Cookie)
    r = c.patch('/api/konto/ich', json={'name': 'Neu', 'zeitzone': 'America/New_York', 'sprache': 'en', 'farbe': '#123ABC'})
    assert r.status_code == 200
    ich = r.json()['ich']
    assert (ich['name'], ich['zeitzone'], ich['sprache'], ich['farbe']) == ('Neu', 'America/New_York', 'en', '#123abc')
    assert c.patch('/api/konto/ich', json={'zeitzone': 'Mond/Basis'}).status_code == 400
    assert c.patch('/api/konto/ich', json={'sprache': 'fr'}).status_code == 400
    assert c.patch('/api/konto/ich', json={'farbe': 'rot'}).status_code == 400
    assert neu_client().patch('/api/konto/ich', json={'name': 'x'}).status_code == 401


def test_grenzen_anmelden_und_registrieren():
    c, email, pw = konto()
    c.post('/api/konto/abmelden')
    codes = [c.post('/api/konto/anmelden', json={'email': email, 'passwort': 'falsch-falsch'}).status_code for _ in range(10)]
    assert set(codes) == {400}
    assert c.post('/api/konto/anmelden', json={'email': email, 'passwort': pw}).status_code == 429
    # andere IP darf weiter
    assert neu_client().post('/api/konto/anmelden', json={'email': email, 'passwort': pw}).status_code == 200
    r = neu_client('9.9.9.9')
    codes = [r.post('/api/konto/registrieren', json={'name': 'R', 'email': f'r{i}@example.org', 'passwort': 'passwort-123'}).status_code
             for i in range(6)]
    assert codes == [200] * 5 + [429]
    assert r.post('/api/konto/registrieren', json={}).json()['fehler'].startswith('Zu viele')


def test_export_und_konto_loeschen():
    c, email, pw = konto()
    r = raum(c, titel='Mein Meeting')
    up = c.post('/api/aufnahmen', content=b'x' * 100, headers={'Content-Type': 'video/webm', 'X-Raum': r['code']})
    assert up.status_code == 200
    ex = c.get('/api/konto/export')
    assert ex.status_code == 200 and 'attachment' in ex.headers['content-disposition']
    daten = ex.json()
    assert daten['konto']['email'] == email
    assert any(x['titel'] == 'Mein Meeting' for x in daten['raeume']) and len(daten['aufnahmen']) == 1
    aid = up.json()['aufnahme']['id']
    assert os.path.exists(aufnahmen.pfad(aid))
    assert c.request('DELETE', '/api/konto', json={'passwort': 'falsch-falsch'}).status_code == 400
    assert c.request('DELETE', '/api/konto', json={'passwort': pw}).json() == {'ok': True}
    assert not os.path.exists(aufnahmen.pfad(aid))
    assert c.get(f'/api/raeume/{r["code"]}').status_code == 404
    assert c.post('/api/konto/anmelden', json={'email': email, 'passwort': pw}).status_code == 400


# ---------------------------------------------------------------- Räume
def test_sofort_meeting_und_token():
    host, _e, _p = konto(name='Hanna')
    r = raum(host)
    assert CODE.match(r['code']) and r['link'] == f'https://agora.slxt.dev/r/{r["code"]}'
    assert r['ist_host'] and not r['aktiv'] and r['geplant'] is None and r['titel'] == 'Meeting von Hanna'
    hz = zugang(host, r['code'])
    assert hz['rolle'] == 'host' and hz['name'] == 'Hanna' and hz['url'] == 'ws://127.0.0.1:7980'
    assert re.match(r'^u\d+-[0-9a-f]{8}$', hz['identity'])
    claims = jwt.decode(hz['token'], lk.API_SECRET, algorithms=['HS256'], options={'verify_iss': False})
    assert claims['sub'] == hz['identity'] and claims['name'] == 'Hanna'
    v = claims['video']
    assert v['roomJoin'] and v['room'] == r['code'] and not v.get('canUpdateOwnMetadata') and v['canPublishData']
    assert set(v['canPublishSources']) == {'camera', 'microphone', 'screen_share', 'screen_share_audio'}
    assert claims['attributes']['rolle'] == 'host' and claims['attributes']['farbe'].startswith('#')
    anlegen = aufrufe('raum_anlegen')[-1]
    assert anlegen['code'] == r['code'] and anlegen['metadaten']['rechte']['chat'] is True and anlegen['metadaten']['aufnahme'] is False
    # Gast
    g = neu_client()
    assert beitreten(g, r['code']).status_code == 400
    gz = zugang(g, r['code'], name='Gerd')
    assert gz['rolle'] == 'gast' and gz['identity'].startswith('g-')
    info = g.get(f'/api/raeume/{r["code"]}').json()['raum']
    assert info['aktiv'] and not info['ist_host'] and info['besitzer_name'] == 'Hanna'
    z = g.get(f'/api/raeume/{r["code"]}/zustand', headers=T(gz)).json()
    assert z['rolle'] == 'gast' and z['rollen'] == {hz['identity']: 'host', gz['identity']: 'gast'} and z['aufnahme'] is False
    assert g.get(f'/api/raeume/{r["code"]}/zustand', headers={'X-Teilnahme': 'falsch'}).status_code == 403
    # Angemeldeter Teilnehmer, name überschreibt Kontoname
    t, _e2, _p2 = konto(name='Tom')
    tz = zugang(t, r['code'], name='Thomas')
    assert tz['rolle'] == 'teilnehmer' and tz['name'] == 'Thomas'
    assert neu_client().post('/api/raeume', json={}).status_code == 401
    assert g.get('/api/raeume/abc-defg-hij').status_code == 404
    assert g.get('/api/raeume/KAPUTT').status_code == 404


def test_passcode_und_versuche():
    host, _e, _p = konto()
    r = raum(host, passcode='1234abcd')
    assert r['passcode_noetig']
    oeffentlich = neu_client().get(f'/api/raeume/{r["code"]}').json()['raum']
    assert oeffentlich['passcode_noetig'] and 'passcode' not in oeffentlich and 'passcode_hash' not in oeffentlich
    zugang(host, r['code'])                       # Besitzer braucht keinen Passcode
    g = neu_client()
    r1 = beitreten(g, r['code'], name='G', passcode='falsch')
    assert r1.status_code == 403 and r1.json()['fehler'] == 'Falscher Passcode'
    assert beitreten(g, r['code'], name='G').status_code == 403
    zugang(g, r['code'], name='G', passcode='1234abcd')
    codes = [beitreten(g, r['code'], name='G', passcode='falsch').status_code for _ in range(10)]
    assert codes[-1] == 429


def test_warteraum_zulassen_ablehnen():
    host, code, hz, _ = meeting(0, warteraum=True)
    g1, g2 = neu_client(), neu_client()
    w1 = beitreten(g1, code, name='Wanda').json()
    assert w1['status'] == 'wartet' and w1['anfrage'] and w1['anfrage_geheim']
    w2 = beitreten(g2, code, name='Walter').json()
    ev = ereignisse('warteraum')[-1]
    assert ev['an'] == [hz['identity']] and ev['daten']['wartend'] == 2
    pfad1 = f'/api/raeume/{code}/warten/{w1["anfrage"]}'
    assert g1.get(pfad1 + '?g=' + w1['anfrage_geheim']).json() == {'status': 'wartet'}
    assert g1.get(pfad1 + '?g=falsch').status_code == 404
    liste = host.get(f'/api/raeume/{code}/warteraum', headers=T(hz)).json()['wartend']
    assert [x['name'] for x in liste] == ['Wanda', 'Walter'] and liste[0]['seit'].endswith('Z')
    assert g1.get(f'/api/raeume/{code}/warteraum', headers={'X-Teilnahme': 'x'}).status_code == 403
    assert host.get(f'/api/raeume/{code}/zustand', headers=T(hz)).json()['wartend'] == 2
    r = host.post(f'/api/raeume/{code}/warteraum/{w1["anfrage"]}', json={'aktion': 'zulassen'}, headers=T(hz))
    assert r.json() == {'ok': True}
    host.post(f'/api/raeume/{code}/warteraum/{w2["anfrage"]}', json={'aktion': 'ablehnen'}, headers=T(hz))
    assert ereignisse('warteraum')[-1]['daten']['wartend'] == 0
    z = g1.get(pfad1 + '?g=' + w1['anfrage_geheim']).json()
    assert z['status'] == 'zugelassen' and z['zugang']['name'] == 'Wanda' and z['zugang']['rolle'] == 'gast'
    assert g1.get(pfad1 + '?g=' + w1['anfrage_geheim']).status_code == 404       # Zugang nur einmal
    assert g2.get(f'/api/raeume/{code}/warten/{w2["anfrage"]}?g={w2["anfrage_geheim"]}').json() == {'status': 'abgelehnt'}
    # Gast darf nicht entscheiden
    w3 = beitreten(neu_client(), code, name='Xaver').json()
    gz = z['zugang']
    assert g1.post(f'/api/raeume/{code}/warteraum/{w3["anfrage"]}', json={'aktion': 'zulassen'}, headers=T(gz)).status_code == 403


def test_sperren_entfernen_und_sperrliste():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    assert steuern(host, code, hz, aktion='sperren', an=True).json() == {'ok': True}
    assert ereignisse('gesperrt')[-1]['daten'] == {'typ': 'gesperrt', 'an': True}
    assert aufrufe('metadaten')[-1]['daten']['gesperrt'] is True
    r = beitreten(neu_client(), code, name='Spät')
    assert r.status_code == 403 and r.json()['fehler'] == 'Meeting ist gesperrt'
    zugang(host, code)                                  # Besitzer kommt trotzdem rein
    steuern(host, code, hz, aktion='sperren', an=False)
    # entfernen → Ereignis an Ziel, LiveKit remove, Sperre für die IP (Gast) bzw. das Konto
    assert steuern(host, code, hz, aktion='entfernen', ziel=z1['identity']).json() == {'ok': True}
    assert ereignisse('entfernt')[-1]['an'] == [z1['identity']]
    assert aufrufe('entfernen')[-1] == {'code': code, 'identity': z1['identity']}
    assert g1.get(f'/api/raeume/{code}/zustand', headers=T(z1)).status_code == 403
    r = beitreten(g1, code, name='Wieder da')
    assert r.status_code == 403 and 'entfernt' in r.json()['fehler']
    t, _e, _p = konto()
    tz = zugang(t, code)
    steuern(host, code, hz, aktion='entfernen', ziel=tz['identity'])
    assert beitreten(t, code).status_code == 403
    assert steuern(host, code, hz, aktion='entfernen', ziel='gibts-nicht').status_code == 404
    assert steuern(host, code, hz, aktion='entfernen', ziel=hz['identity']).status_code == 400
    assert steuern(host, code, hz, aktion='quatsch').status_code == 400


def test_rechte_gast_und_cohost():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    # Gast darf nicht steuern
    for aktion in ('stumm', 'alle_stumm', 'entfernen', 'beenden', 'sperren'):
        assert steuern(g1, code, z1, aktion=aktion, ziel=z2['identity'], an=True).status_code == 403
    # Co-Host ernennen (nur Host)
    assert steuern(host, code, hz, aktion='rolle', ziel=z1['identity'], rolle='cohost').json() == {'ok': True}
    assert ereignisse('rolle')[-1]['daten'] == {'typ': 'rolle', 'identity': z1['identity'], 'rolle': 'cohost'}
    akt = aufrufe('aktualisieren')[-1]
    assert akt['identity'] == z1['identity'] and akt['rolle'] == 'cohost'
    assert g1.get(f'/api/raeume/{code}/zustand', headers=T(z1)).json()['rolle'] == 'cohost'
    # Co-Host: darf Gäste stummschalten, nicht den Host; nicht beenden/Rollen/übertragen
    assert steuern(g1, code, z1, aktion='stumm', ziel=z2['identity']).json() == {'ok': True}
    assert ereignisse('stumm')[-1] == {'code': code, 'daten': {'typ': 'stumm', 'von_name': 'Gast 1'}, 'topic': 'agora', 'an': [z2['identity']]}
    assert steuern(g1, code, z1, aktion='stumm', ziel=hz['identity']).status_code == 403
    assert steuern(g1, code, z1, aktion='entfernen', ziel=hz['identity']).status_code == 403
    assert steuern(g1, code, z1, aktion='beenden').status_code == 403
    assert steuern(g1, code, z1, aktion='rolle', ziel=z2['identity'], rolle='cohost').status_code == 403
    assert steuern(g1, code, z1, aktion='host_uebertragen', ziel=z2['identity']).status_code == 403
    assert steuern(g1, code, z1, aktion='kamera_bitten', ziel=z2['identity']).json() == {'ok': True}
    assert ereignisse('kamera_bitten')[-1]['an'] == [z2['identity']]
    # Rechte: Bildschirm aus → neue Gäste ohne screen_share, verbundene Teilnehmer werden angepasst
    lk.FAKE_TEILNEHMER[code] = [{'identity': z2['identity'], 'name': 'Gast 2', 'tracks': []}]
    assert steuern(host, code, hz, aktion='rechte', bildschirm=False, chat='ja').status_code == 400
    assert steuern(host, code, hz, aktion='rechte', bildschirm=False).json() == {'ok': True}
    assert ereignisse('rechte')[-1]['daten']['rechte']['bildschirm'] is False
    akt = aufrufe('aktualisieren')[-1]
    assert akt['identity'] == z2['identity'] and akt['rechte']['bildschirm'] is False
    assert set(p for p in lk.berechtigung('gast', akt['rechte']).can_publish_sources) == {lk.api.TrackSource.CAMERA, lk.api.TrackSource.MICROPHONE}
    neu = zugang(neu_client(), code, name='Neu')
    v = jwt.decode(neu['token'], options={'verify_signature': False})['video']
    assert set(v['canPublishSources']) == {'camera', 'microphone'}
    hv = jwt.decode(zugang(host, code)['token'], options={'verify_signature': False})['video']
    assert 'screen_share' in hv['canPublishSources']
    # Zurückstufen: Gast ohne Konto wird wieder „gast“
    steuern(host, code, hz, aktion='rolle', ziel=z1['identity'], rolle='teilnehmer')
    assert g1.get(f'/api/raeume/{code}/zustand', headers=T(z1)).json()['rolle'] == 'gast'


def test_umbenennen():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    assert steuern(g1, code, z1, aktion='umbenennen', ziel=z1['identity'], name='Neuer Name').json() == {'ok': True}
    assert aufrufe('aktualisieren')[-1] == {'code': code, 'identity': z1['identity'], 'name': 'Neuer Name', 'rolle': None,
                                            'farbe': None, 'rechte': None, 'attribute': None}
    assert steuern(g1, code, z1, aktion='umbenennen', ziel=z2['identity'], name='Fremd').status_code == 403
    steuern(host, code, hz, aktion='rechte', umbenennen=False)
    assert steuern(g1, code, z1, aktion='umbenennen', ziel=z1['identity'], name='Nochmal').status_code == 403
    assert steuern(host, code, hz, aktion='umbenennen', ziel=z1['identity'], name='Vom Host').json() == {'ok': True}


def test_stummschalten_mit_spuren():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    lk.FAKE_TEILNEHMER[code] = [
        {'identity': hz['identity'], 'name': 'H', 'tracks': [{'sid': 'TR_h', 'quelle': 'mikrofon', 'stumm': False}]},
        {'identity': z1['identity'], 'name': 'G1', 'tracks': [{'sid': 'TR_a', 'quelle': 'mikrofon', 'stumm': False},
                                                               {'sid': 'TR_v', 'quelle': 'kamera', 'stumm': False}]},
        {'identity': z2['identity'], 'name': 'G2', 'tracks': [{'sid': 'TR_b', 'quelle': 'mikrofon', 'stumm': False}]},
    ]
    steuern(host, code, hz, aktion='kamera_aus', ziel=z1['identity'])
    assert [m['sid'] for m in aufrufe('mute_published_track')] == ['TR_v']
    assert ereignisse('kamera_aus')[-1]['an'] == [z1['identity']]
    lk.AUFRUFE.clear()
    steuern(host, code, hz, aktion='alle_stumm')
    assert sorted(m['sid'] for m in aufrufe('mute_published_track')) == ['TR_a', 'TR_b']     # Host bleibt an
    assert sorted(ereignisse('stumm')[-1]['an']) == sorted([z1['identity'], z2['identity']])


def test_host_uebertragen_und_beenden():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    assert steuern(host, code, hz, aktion='host_uebertragen', ziel=z1['identity']).json() == {'ok': True}
    z = host.get(f'/api/raeume/{code}/zustand', headers=T(hz)).json()
    assert z['rolle'] == 'teilnehmer' and z['rollen'][z1['identity']] == 'host'
    assert steuern(host, code, hz, aktion='beenden').status_code == 403
    assert steuern(g1, code, z1, aktion='beenden').json() == {'ok': True}
    assert ereignisse('beendet')[-1]['daten'] == {'typ': 'beendet'} and ereignisse('beendet')[-1]['an'] is None
    assert aufrufe('raum_loeschen')[-1] == {'code': code}
    assert g2.get(f'/api/raeume/{code}/zustand', headers=T(z2)).status_code == 403
    info = g2.get(f'/api/raeume/{code}').json()['raum']
    assert not info['aktiv'] and info['beendet']
    r = beitreten(g2, code, name='Zu spät')
    assert r.status_code == 403 and r.json()['fehler'] == 'Meeting ist beendet'
    liste = host.get('/api/raeume').json()
    assert any(x['code'] == code for x in liste['vergangen'])
    # Besitzer kann das Sofort-Meeting wieder öffnen → neues Meeting
    zugang(host, code)
    assert zugang(g2, code, name='Jetzt')['rolle'] == 'gast'


def test_besitzer_kommt_zurueck_und_wird_host():
    host, code, hz, [(g1, z1)] = meeting(1)
    steuern(host, code, hz, aktion='host_uebertragen', ziel=z1['identity'])
    neu = zugang(host, code)
    assert neu['rolle'] == 'host'
    assert g1.get(f'/api/raeume/{code}/zustand', headers=T(z1)).json()['rolle'] == 'cohost'


def test_leerlauf_beendet_meeting():
    host, code, hz, _ = meeting(0)
    c = db.verbindung()
    import asyncio
    lk.FAKE_TEILNEHMER[code] = [{'identity': hz['identity'], 'name': 'H', 'tracks': []}]
    asyncio.run(raeume.leere_pruefen())
    r = c.execute('SELECT * FROM raeume WHERE code = ?', (code,)).fetchone()
    assert r['aktiv'] and r['leer_seit'] is None
    lk.FAKE_TEILNEHMER[code] = []
    t = r['letzte_aktivitaet']
    asyncio.run(raeume.leere_pruefen(t + 10))
    assert c.execute('SELECT leer_seit FROM raeume WHERE code = ?', (code,)).fetchone()[0] == t + 10
    asyncio.run(raeume.leere_pruefen(t + 100))
    assert c.execute('SELECT aktiv FROM raeume WHERE code = ?', (code,)).fetchone()[0] == 1
    asyncio.run(raeume.leere_pruefen(t + 10 + raeume.LEER_S))
    assert c.execute('SELECT aktiv FROM raeume WHERE code = ?', (code,)).fetchone()[0] == 0
    assert host.get(f'/api/raeume/{code}/zustand', headers=T(hz)).status_code == 403


def test_chat_privat_und_rechte():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    url = f'/api/raeume/{code}/chat'
    n = g1.post(url, json={'text': 'Hallo alle'}, headers=T(z1)).json()['nachricht']
    assert n['von'] == z1['identity'] and n['von_name'] == 'Gast 1' and n['an'] is None and n['zeit'].endswith('Z')
    s = aufrufe('senden')[-1]
    assert s['topic'] == 'chat' and s['an'] is None and s['daten']['text'] == 'Hallo alle'
    p = g1.post(url, json={'text': 'Psst', 'an': z2['identity']}, headers=T(z1)).json()['nachricht']
    assert p['an'] == z2['identity'] and p['an_name'] == 'Gast 2'
    assert sorted(aufrufe('senden')[-1]['an']) == sorted([z1['identity'], z2['identity']])
    sicht = {k: [m['text'] for m in c.get(url, headers=T(z)).json()['nachrichten']] for k, c, z in
             (('g1', g1, z1), ('g2', g2, z2), ('host', host, hz))}
    assert sicht == {'g1': ['Hallo alle', 'Psst'], 'g2': ['Hallo alle', 'Psst'], 'host': ['Hallo alle']}
    assert g1.post(url, json={'text': ''}, headers=T(z1)).status_code == 400
    assert g1.post(url, json={'text': 'x' * 2001}, headers=T(z1)).status_code == 400
    assert g1.post(url, json={'text': 'x', 'an': 'gibts-nicht'}, headers=T(z1)).status_code == 404
    assert g1.post(url, json={'text': 'x'}, headers={'X-Teilnahme': 'falsch'}).status_code == 403
    steuern(host, code, hz, aktion='rechte', chat=False)
    r = g1.post(url, json={'text': 'Darf ich?'}, headers=T(z1))
    assert r.status_code == 403
    assert host.post(url, json={'text': 'Host darf'}, headers=T(hz)).status_code == 200
    # Ende ohne chat_speichern → Chat weg
    steuern(host, code, hz, aktion='beenden')
    assert db.verbindung().execute('SELECT COUNT(*) FROM nachrichten n JOIN raeume r ON r.id = n.raum_id WHERE r.code = ?',
                                   (code,)).fetchone()[0] == 0


def test_chat_speichern_verlauf():
    host, _e, _p = konto()
    r = raum(host, chat_speichern=True)
    assert r['chat_speichern']
    code = r['code']
    hz = zugang(host, code)
    g, g2 = neu_client(), neu_client()
    gz, gz2 = zugang(g, code, name='G'), zugang(g2, code, name='G2')
    host.post(f'/api/raeume/{code}/chat', json={'text': 'Bleibt'}, headers=T(hz))
    g.post(f'/api/raeume/{code}/chat', json={'text': 'An den Host', 'an': hz['identity']}, headers=T(gz))
    g.post(f'/api/raeume/{code}/chat', json={'text': 'Unter Gästen', 'an': gz2['identity']}, headers=T(gz))
    steuern(host, code, hz, aktion='beenden')
    v = host.get(f'/api/raeume/{code}/verlauf').json()['nachrichten']
    assert [m['text'] for m in v] == ['Bleibt', 'An den Host'] and v[0]['sitzung'] == 1
    assert g.get(f'/api/raeume/{code}/verlauf').status_code == 401
    andere, _e2, _p2 = konto()
    assert andere.get(f'/api/raeume/{code}/verlauf').status_code == 403


def test_aufnahme_anzeige():
    host, code, hz, [(g1, z1)] = meeting(1)
    assert g1.post(f'/api/raeume/{code}/aufnahme', json={'aktiv': True}, headers=T(z1)).status_code == 403
    assert host.post(f'/api/raeume/{code}/aufnahme', json={'aktiv': True}, headers=T(hz)).json() == {'ok': True}
    assert g1.get(f'/api/raeume/{code}/zustand', headers=T(z1)).json()['aufnahme'] == {'von_name': 'Ada'}
    assert ereignisse('aufnahme')[-1]['daten'] == {'typ': 'aufnahme', 'aktiv': True, 'von_name': 'Ada'}
    assert aufrufe('metadaten')[-1]['daten']['aufnahme'] == {'von_name': 'Ada'}
    host.post(f'/api/raeume/{code}/aufnahme', json={'aktiv': False}, headers=T(hz))
    assert g1.get(f'/api/raeume/{code}/zustand', headers=T(z1)).json()['aufnahme'] is False


def test_raum_verwalten():
    host, _e, _p = konto()
    r = raum(host, titel='Alt', warteraum=False)
    code = r['code']
    neu = host.patch(f'/api/raeume/{code}', json={'titel': 'Neu', 'passcode': 'abcd', 'warteraum': True}).json()['raum']
    assert neu['titel'] == 'Neu' and neu['passcode_noetig'] and neu['warteraum']
    assert host.patch(f'/api/raeume/{code}', json={'passcode': None}).json()['raum']['passcode_noetig'] is False
    assert host.patch(f'/api/raeume/{code}', json={'passcode': 'ab'}).status_code == 400
    assert host.patch(f'/api/raeume/{code}', json={'geplant': {'start': '2030-01-01T10:00:00Z'}}).status_code == 400
    andere, _e2, _p2 = konto()
    assert andere.patch(f'/api/raeume/{code}', json={'titel': 'Fremd'}).status_code == 403
    assert andere.request('DELETE', f'/api/raeume/{code}').status_code == 403
    pers = host.get('/api/raeume').json()['persoenlich']
    assert pers['persoenlich'] and host.get('/api/konto/ich').json()['ich']['persoenlicher_raum'] == pers['code']
    assert host.request('DELETE', f'/api/raeume/{pers["code"]}').status_code == 400
    host.patch(f'/api/raeume/{pers["code"]}', json={'passcode': 'raum1234'})
    assert host.get(f'/api/raeume/{pers["code"]}').json()['raum']['passcode_noetig']
    assert host.request('DELETE', f'/api/raeume/{code}').json() == {'ok': True}
    assert host.get(f'/api/raeume/{code}').status_code == 404


# ---------------------------------------------------------------- Termine
def test_naechster_termin_wiederholung():
    berlin = ics.zone('Europe/Berlin')
    start = int(datetime(2026, 3, 23, 9, 0, tzinfo=berlin).timestamp())        # Montag, noch Winterzeit
    jetzt = int(datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    t = ics.naechster_termin(start, 60, 'woechentlich', 'Europe/Berlin', None, jetzt)
    # 06.04. 09:00 Sommerzeit = 07:00 UTC (Ortszeit bleibt über die Zeitumstellung gleich)
    assert datetime.fromtimestamp(t, timezone.utc) == datetime(2026, 4, 6, 7, 0, tzinfo=timezone.utc)
    t = ics.naechster_termin(start, 60, 'taeglich', 'Europe/Berlin', None, jetzt)
    assert datetime.fromtimestamp(t, berlin) == datetime(2026, 4, 2, 9, 0, tzinfo=berlin)
    laufend = int(datetime(2026, 4, 6, 7, 30, tzinfo=timezone.utc).timestamp())       # Termin läuft gerade → zählt noch
    assert ics.naechster_termin(start, 60, 'woechentlich', 'Europe/Berlin', None, laufend) == int(datetime(2026, 4, 6, 9, 0, tzinfo=berlin).timestamp())
    bis = int(datetime(2026, 4, 3, tzinfo=timezone.utc).timestamp())
    assert ics.naechster_termin(start, 60, 'woechentlich', 'Europe/Berlin', bis, jetzt) is None
    assert ics.naechster_termin(start, 60, 'keine', 'Europe/Berlin', None, jetzt) is None
    assert ics.naechster_termin(start, 60, 'keine', 'Europe/Berlin', None, start - 5) == start
    weit = int(datetime(2028, 7, 1, tzinfo=timezone.utc).timestamp())
    t = ics.naechster_termin(start, 60, 'woechentlich', 'Europe/Berlin', None, weit)
    lokal = datetime.fromtimestamp(t, berlin)
    assert lokal.weekday() == 0 and (lokal.hour, lokal.minute) == (9, 0) and t >= weit - 3600


def test_geplant_ics_und_einladung():
    host, _e, _p = konto(name='Planer')
    start = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0, second=0)
    lokal = start.astimezone(ics.zone('Europe/Berlin')).replace(tzinfo=None).isoformat()
    r = raum(host, titel='Wochenrunde; Team, alle', passcode='pass1234',
             geplant={'start': lokal, 'dauer_min': 45, 'zeitzone': 'Europe/Berlin', 'beschreibung': 'Agenda:\nPunkt 1',
                      'wiederholung': 'woechentlich', 'bis': None})
    g = r['geplant']
    assert g['start'] == start.strftime('%Y-%m-%dT%H:%M:%SZ') and g['dauer_min'] == 45 and g['wiederholung'] == 'woechentlich'
    assert r['naechster_termin'] == g['start'] and not r['persoenlich']
    liste = host.get('/api/raeume').json()
    assert [x['code'] for x in liste['geplant']] == [r['code']] and liste['vergangen'] == []
    k = neu_client().get(f'/api/raeume/{r["code"]}/ics')
    assert k.status_code == 200 and k.headers['content-type'].startswith('text/calendar')
    text = k.text
    for teil in ('BEGIN:VCALENDAR', 'BEGIN:VTIMEZONE', 'TZID:Europe/Berlin', 'BEGIN:DAYLIGHT', 'RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU',
                 'BEGIN:VEVENT', f'UID:{r["code"]}@agora.slxt.dev', 'RRULE:FREQ=WEEKLY', 'SUMMARY:Wochenrunde\\; Team\\, alle',
                 f'DTSTART;TZID=Europe/Berlin:{start.astimezone(ics.zone("Europe/Berlin")).strftime("%Y%m%dT%H%M%S")}', 'END:VCALENDAR'):
        assert teil in text, teil
    assert '\r\n' in text and all(len(z.encode()) <= 75 for z in text.split('\r\n'))
    assert 'pass1234' not in text
    # Einmaliger Termin: UTC-Zeiten, keine RRULE
    r2 = raum(host, geplant={'start': '2030-05-01T08:00:00Z', 'dauer_min': 30})
    t2 = host.get(f'/api/raeume/{r2["code"]}/ics').text
    assert 'DTSTART:20300501T080000Z' in t2 and 'DTEND:20300501T083000Z' in t2 and 'RRULE' not in t2.split('BEGIN:VEVENT')[1]
    assert host.get(f'/api/raeume/{raum(host)["code"]}/ics').status_code == 404
    # Ungültige Angaben
    assert host.post('/api/raeume', json={'geplant': {'start': 'morgen'}}).status_code == 400
    assert host.post('/api/raeume', json={'geplant': {'start': '2030-01-01T10:00', 'dauer_min': 2}}).status_code == 400
    assert host.post('/api/raeume', json={'geplant': {'start': '2030-01-01T10:00', 'wiederholung': 'jaehrlich'}}).status_code == 400
    # Einladung mit .ics
    assert host.post(f'/api/raeume/{r["code"]}/einladen', json={'emails': ['kaputt']}).status_code == 400
    assert host.post(f'/api/raeume/{r["code"]}/einladen', json={'emails': ['a@example.org'], 'passcode': 'falsch'}).status_code == 400
    res = host.post(f'/api/raeume/{r["code"]}/einladen', json={'emails': ['a@example.org', 'b@example.org', 'A@example.org'],
                                                              'passcode': 'pass1234'})
    assert res.json() == {'ok': True, 'gesendet': 2}
    an, betreff, text_, html_, anhaenge = mails_an('a@example.org')[-1]
    assert betreff == 'Einladung: Wochenrunde; Team, alle' and f'/r/{r["code"]}' in text_ and 'pass1234' in text_
    assert 'Planer' in text_ and anhaenge[0][0] == 'einladung.ics' and b'RRULE:FREQ=WEEKLY' in anhaenge[0][3]
    assert neu_client().post(f'/api/raeume/{r["code"]}/einladen', json={'emails': ['a@example.org']}).status_code == 401
    # Mails sind echt baubar (MIME mit Anhang)
    from email.message import EmailMessage
    m = EmailMessage()
    m.set_content(text_)
    m.add_alternative(html_, subtype='html')
    n, haupt, unter, daten, params = anhaenge[0]
    m.add_attachment(daten, maintype=haupt, subtype=unter, filename=n, params=params)
    assert 'text/calendar' in m.as_string()


# ---------------------------------------------------------------- Aufnahmen
def hochladen(c, daten, typ='video/webm', **h):
    return c.post('/api/aufnahmen', content=daten, headers={'Content-Type': typ, **h})


def test_aufnahmen_hochladen_range_loeschen():
    c, _e, _p = konto()
    daten = bytes(range(256)) * 40
    r = hochladen(c, daten, 'video/webm; codecs=vp9', **{'X-Raum': 'abc-defg-hij', 'X-Titel': 'Team%20M%C3%BCnchen', 'X-Dauer': '61'})
    assert r.status_code == 200, r.text
    a = r.json()['aufnahme']
    assert (a['titel'], a['raum_code'], a['groesse'], a['typ'], a['dauer'], a['freigabe']) == \
           ('Team München', 'abc-defg-hij', len(daten), 'video/webm', 61, None)
    erstellt = datetime.fromisoformat(a['erstellt'].replace('Z', '+00:00'))
    ablauf = datetime.fromisoformat(a['ablauf'].replace('Z', '+00:00'))
    assert (ablauf - erstellt).days == 90
    liste = c.get('/api/aufnahmen').json()
    assert liste['belegt'] == len(daten) and liste['kontingent'] == 5 * 1024 ** 3 and len(liste['aufnahmen']) == 1
    voll = c.get(f'/api/aufnahmen/{a["id"]}/datei')
    assert voll.status_code == 200 and voll.content == daten and voll.headers['content-type'].startswith('video/webm')
    teil = c.get(f'/api/aufnahmen/{a["id"]}/datei', headers={'Range': 'bytes=10-19'})
    assert teil.status_code == 206 and teil.content == daten[10:20]
    assert teil.headers['content-range'] == f'bytes 10-19/{len(daten)}'
    assert 'attachment' in c.get(f'/api/aufnahmen/{a["id"]}/datei?download=1').headers['content-disposition']
    andere, _e2, _p2 = konto()
    assert andere.get(f'/api/aufnahmen/{a["id"]}/datei').status_code == 404
    assert andere.request('DELETE', f'/api/aufnahmen/{a["id"]}').status_code == 404
    assert neu_client().get('/api/aufnahmen').status_code == 401
    assert hochladen(c, b'abc', 'text/plain').status_code == 415
    assert hochladen(c, b'', 'video/mp4').status_code == 400
    assert hochladen(c, b'x', 'video/mp4', **{'X-Raum': 'kaputt'}).status_code == 400
    assert c.request('DELETE', f'/api/aufnahmen/{a["id"]}').json() == {'ok': True}
    assert c.get('/api/aufnahmen').json()['belegt'] == 0


def test_aufnahmen_grenzen(monkeypatch):
    c, _e, _p = konto()
    monkeypatch.setattr(aufnahmen, 'MAX_DATEI', 1000)
    r = hochladen(c, b'x' * 1001)
    assert r.status_code == 413 and '500 MB' in r.json()['fehler']
    monkeypatch.setattr(aufnahmen, 'KONTINGENT', 1500)
    assert hochladen(c, b'x' * 1000).status_code == 200
    r = hochladen(c, b'x' * 600)
    assert r.status_code == 507 and 'voll' in r.json()['fehler']
    assert len(os.listdir(os.path.join(db.DATEN, 'tmp'))) == 0


def test_freigabe_mit_passwort_und_ablauf():
    c, _e, _p = konto()
    daten = os.urandom(5000)
    a = hochladen(c, daten, 'video/mp4', **{'X-Titel': 'Vortrag'}).json()['aufnahme']
    assert c.post(f'/api/aufnahmen/{a["id"]}/freigabe', json={'tage': 3}).status_code == 400
    link = c.post(f'/api/aufnahmen/{a["id"]}/freigabe', json={'tage': 7, 'passwort': 'offen-sesam'}).json()['link']
    assert link.startswith('https://agora.slxt.dev/a/')
    token = link.rsplit('/', 1)[1]
    fg = c.get('/api/aufnahmen').json()['aufnahmen'][0]['freigabe']
    assert fg['link'] == link and fg['passwort'] is True
    x = neu_client()
    info = x.get(f'/api/freigaben/{token}').json()
    assert info == {'titel': 'Vortrag', 'dauer': None, 'groesse': 5000, 'typ': 'video/mp4', 'passwort_noetig': True}
    assert x.get(f'/api/freigaben/{token}/datei').status_code == 403
    assert x.get(f'/api/freigaben/{token}/datei?p=falsch').json() == {'fehler': 'Falsches Passwort'}
    r = x.get(f'/api/freigaben/{token}/datei?p=offen-sesam', headers={'Range': 'bytes=100-'})
    assert r.status_code == 206 and r.content == daten[100:]
    # ohne Passwort, dann aufheben
    link2 = c.post(f'/api/aufnahmen/{a["id"]}/freigabe', json={'tage': 1}).json()['link']
    t2 = link2.rsplit('/', 1)[1]
    assert x.get(f'/api/freigaben/{token}').status_code == 404       # alter Link gilt nicht mehr
    assert x.get(f'/api/freigaben/{t2}/datei').content == daten
    db.verbindung().execute('UPDATE aufnahmen SET freigabe_bis = 1 WHERE id = ?', (a['id'],))
    assert x.get(f'/api/freigaben/{t2}').status_code == 404
    c.post(f'/api/aufnahmen/{a["id"]}/freigabe', json={'tage': 30})
    assert c.request('DELETE', f'/api/aufnahmen/{a["id"]}/freigabe').json() == {'ok': True}
    assert c.get('/api/aufnahmen').json()['aufnahmen'][0]['freigabe'] is None
    # Aufbewahrung: abgelaufene Aufnahmen räumt der Hintergrund weg
    db.verbindung().execute('UPDATE aufnahmen SET ablauf = 1 WHERE id = ?', (a['id'],))
    main.aufraeumen()
    assert c.get('/api/aufnahmen').json()['aufnahmen'] == [] and not os.path.exists(aufnahmen.pfad(a['id']))


def test_freigabe_passwort_versuche_begrenzt():
    c, _e, _p = konto()
    a = hochladen(c, b'v' * 50).json()['aufnahme']
    token = c.post(f'/api/aufnahmen/{a["id"]}/freigabe', json={'tage': 1, 'passwort': 'richtig1'}).json()['link'].rsplit('/', 1)[1]
    x = neu_client()
    codes = [x.get(f'/api/freigaben/{token}/datei?p=falsch{i}').status_code for i in range(21)]
    assert codes[:20] == [403] * 20 and codes[20] == 429


def test_aufraeumen_alte_raeume_und_sitzungen():
    host, _e, _p = konto()
    r = raum(host)
    c = db.verbindung()
    c.execute('UPDATE raeume SET letzte_aktivitaet = 1 WHERE code = ?', (r['code'],))
    c.execute('UPDATE sitzungen SET gueltig_bis = 1')
    main.aufraeumen()
    assert host.get(f'/api/raeume/{r["code"]}').status_code == 404
    assert host.get('/api/konto/ich').json() == {'angemeldet': False}


def test_reservierte_mail_domains_erkannt():
    # Reservierte Domains (RFC 2606/6761) bekommen außerhalb des Testmodus keine Mail – sonst Unzustellbar-Meldungen
    from app.konto import RESERVIERT_RE
    for e in ('a@example.invalid', 'a@x.test', 'a@example.com', 'a@mail.example.org', 'a@host.localhost'):
        assert RESERVIERT_RE.search(e), e
    for e in ('a@slxt.org', 'a@gmail.com', 'a@example-firma.at', 'a@testfirma.de'):
        assert not RESERVIERT_RE.search(e), e


# ---------------------------------------------------------------- Umfragen
def umfrage_neu(c, code, z, **kw):
    body = {'frage': 'Pizza oder Pasta?', 'optionen': ['Pizza', 'Pasta', 'Beides']} | kw
    return c.post(f'/api/raeume/{code}/umfragen', json=body, headers=T(z))


def test_umfrage_anlegen_rechte_und_pruefung():
    host, code, hz, [(g1, z1)] = meeting(1)
    assert umfrage_neu(g1, code, z1).status_code == 403
    assert umfrage_neu(host, code, hz, optionen=['nur eine']).status_code == 400
    assert umfrage_neu(host, code, hz, optionen=[str(i) for i in range(11)]).status_code == 400
    assert umfrage_neu(host, code, hz, optionen=['A', '']).status_code == 400
    assert umfrage_neu(host, code, hz, optionen=['A', 'a']).status_code == 400
    assert umfrage_neu(host, code, hz, frage='').status_code == 400
    assert umfrage_neu(host, code, hz, mehrfach='ja').status_code == 400
    assert umfrage_neu(host, code, hz, optionen='A,B').status_code == 400
    r = umfrage_neu(host, code, hz, anonym=False)
    assert r.status_code == 200, r.text
    u = r.json()['umfrage']
    assert u['status'] == 'entwurf' and u['optionen'] == ['Pizza', 'Pasta', 'Beides'] and not u['mehrfach'] and not u['anonym']
    assert u['ergebnis'] == {'stimmen': 0, 'zaehler': [0, 0, 0], 'namen': [[], [], []]} and u['von_name'] == 'Ada'
    # Entwurf sieht nur die Moderation; Ereignis „angelegt“ nur an Host/Co-Hosts
    assert ereignisse('umfrage')[-1]['an'] == [hz['identity']]
    assert g1.get(f'/api/raeume/{code}/umfragen', headers=T(z1)).json() == {'umfragen': []}
    assert len(host.get(f'/api/raeume/{code}/umfragen', headers=T(hz)).json()['umfragen']) == 1
    assert g1.post(f'/api/raeume/{code}/umfragen/{u["id"]}/abstimmen', json={'auswahl': [0]}, headers=T(z1)).status_code == 409
    assert g1.post(f'/api/raeume/{code}/umfragen/{u["id"]}/starten', headers=T(z1)).status_code == 403
    assert host.post(f'/api/raeume/{code}/umfragen/9999/starten', headers=T(hz)).status_code == 404


def test_umfrage_abstimmen_live_beenden_teilen():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    u = umfrage_neu(host, code, hz, anonym=False).json()['umfrage']
    url = f'/api/raeume/{code}/umfragen/{u["id"]}'
    r = host.post(url + '/starten', headers=T(hz)).json()['umfrage']
    assert r['status'] == 'laeuft' and r['gestartet'].endswith('Z')
    alle = [e for e in ereignisse('umfrage') if e['an'] is None][-1]['daten']
    assert alle['aktion'] == 'gestartet' and alle['umfrage']['ergebnis'] is None and alle['umfrage']['frage'] == 'Pizza oder Pasta?'
    assert host.post(url + '/starten', headers=T(hz)).status_code == 409
    # Gast sieht die laufende Umfrage, ohne Ergebnis
    sicht = g1.get(f'/api/raeume/{code}/umfragen', headers=T(z1)).json()['umfragen'][0]
    assert sicht['status'] == 'laeuft' and sicht['ergebnis'] is None and sicht['meine_auswahl'] is None
    # Abstimmen: Prüfungen, genau einmal je Teilnahme
    for falsch in ([], [3], [-1], [0, 1], ['0'], [True], 0, [0, 0]):
        assert g1.post(url + '/abstimmen', json={'auswahl': falsch}, headers=T(z1)).status_code == 400, falsch
    r = g1.post(url + '/abstimmen', json={'auswahl': [1]}, headers=T(z1))
    assert r.status_code == 200 and r.json()['umfrage']['meine_auswahl'] == [1] and r.json()['umfrage']['ergebnis'] is None
    assert g1.post(url + '/abstimmen', json={'auswahl': [0]}, headers=T(z1)).json()['fehler'] == 'Du hast schon abgestimmt.'
    assert g1.post(url + '/abstimmen', json={'auswahl': [0]}, headers={'X-Teilnahme': 'falsch'}).status_code == 403
    # Live-Ergebnis nur an Host/Co-Host (mit Namen, weil namentlich)
    live = ereignisse('umfrage')[-1]
    assert live['an'] == [hz['identity']] and live['daten']['aktion'] == 'stimme'
    assert live['daten']['umfrage']['ergebnis'] == {'stimmen': 1, 'zaehler': [0, 1, 0], 'namen': [[], ['Gast 1'], []]}
    g2.post(url + '/abstimmen', json={'auswahl': [1]}, headers=T(z2))
    host.post(url + '/abstimmen', json={'auswahl': [0]}, headers=T(hz))
    erg = host.get(f'/api/raeume/{code}/umfragen', headers=T(hz)).json()['umfragen'][0]
    assert erg['ergebnis']['zaehler'] == [1, 2, 0] and erg['meine_auswahl'] == [0]
    # Beenden: Gäste sehen noch kein Ergebnis; danach keine Stimmen mehr
    assert g1.post(url + '/beenden', headers=T(z1)).status_code == 403
    assert host.post(url + '/beenden', headers=T(hz)).json()['umfrage']['status'] == 'beendet'
    assert host.post(url + '/beenden', headers=T(hz)).status_code == 409
    g3 = neu_client()
    z3 = zugang(g3, code, name='Spät')
    assert g3.post(url + '/abstimmen', json={'auswahl': [2]}, headers=T(z3)).status_code == 409
    sicht = g2.get(f'/api/raeume/{code}/umfragen', headers=T(z2)).json()['umfragen'][0]
    assert sicht['status'] == 'beendet' and sicht['ergebnis'] is None and sicht['meine_auswahl'] == [1]
    # Teilen: alle sehen Zahlen, aber keine Namen
    assert host.post(url + '/teilen', headers=T(hz)).status_code == 200
    geteilt = [e for e in ereignisse('umfrage') if e['an'] is None][-1]['daten']
    assert geteilt['aktion'] == 'geteilt' and geteilt['umfrage']['ergebnis'] == {'stimmen': 3, 'zaehler': [1, 2, 0]}
    sicht = g3.get(f'/api/raeume/{code}/umfragen', headers=T(z3)).json()['umfragen'][0]
    assert sicht['geteilt'] and sicht['ergebnis'] == {'stimmen': 3, 'zaehler': [1, 2, 0]}


def test_umfrage_mehrfach_anonym_cohost_loeschen():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    steuern(host, code, hz, aktion='rolle', ziel=z1['identity'], rolle='cohost')
    u = umfrage_neu(g1, code, z1, frage='Welche Tage?', optionen=['Mo', 'Di', 'Mi'], mehrfach=True).json()['umfrage']
    assert u['anonym'] and u['mehrfach']
    url = f'/api/raeume/{code}/umfragen/{u["id"]}'
    g1.post(url + '/starten', headers=T(z1))
    assert g2.post(url + '/abstimmen', json={'auswahl': [0, 2]}, headers=T(z2)).status_code == 200
    erg = host.get(f'/api/raeume/{code}/umfragen', headers=T(hz)).json()['umfragen'][0]['ergebnis']
    assert erg == {'stimmen': 1, 'zaehler': [1, 0, 1]}                    # anonym → keine Namen, auch nicht für den Host
    assert sorted(ereignisse('umfrage')[-1]['an']) == sorted([hz['identity'], z1['identity']])
    # Teilen beendet eine laufende Umfrage gleich mit
    assert host.post(url + '/teilen', headers=T(hz)).json()['umfrage']['status'] == 'beendet'
    assert g2.delete(url, headers=T(z2)).status_code == 403
    assert host.delete(url, headers=T(hz)).json() == {'ok': True}
    assert ereignisse('umfrage')[-1]['daten'] == {'typ': 'umfrage', 'aktion': 'geloescht', 'umfrage': {'id': u['id']}}
    assert host.get(f'/api/raeume/{code}/umfragen', headers=T(hz)).json() == {'umfragen': []}


def test_umfragen_mit_meeting_geloescht_ausser_chat_speichern():
    host, code, hz, _ = meeting(0)
    u = umfrage_neu(host, code, hz).json()['umfrage']
    host.post(f'/api/raeume/{code}/umfragen/{u["id"]}/starten', headers=T(hz))
    steuern(host, code, hz, aktion='beenden')
    assert db.verbindung().execute('SELECT COUNT(*) FROM umfragen u JOIN raeume r ON r.id = u.raum_id WHERE r.code = ?',
                                   (code,)).fetchone()[0] == 0
    # mit chat_speichern: bleibt, Besitzer sieht sie im Verlauf
    r = raum(host, chat_speichern=True)
    hz2 = zugang(host, r['code'])
    u = umfrage_neu(host, r['code'], hz2).json()['umfrage']
    host.post(f'/api/raeume/{r["code"]}/umfragen/{u["id"]}/starten', headers=T(hz2))
    host.post(f'/api/raeume/{r["code"]}/umfragen/{u["id"]}/abstimmen', json={'auswahl': [2]}, headers=T(hz2))
    umfrage_neu(host, r['code'], hz2, frage='Nur Entwurf')
    steuern(host, r['code'], hz2, aktion='beenden')
    v = host.get(f'/api/raeume/{r["code"]}/umfragen/verlauf').json()['umfragen']
    assert [x['frage'] for x in v] == ['Pizza oder Pasta?'] and v[0]['ergebnis']['zaehler'] == [0, 0, 1] and v[0]['sitzung'] == 1
    andere, _e, _p = konto()
    assert andere.get(f'/api/raeume/{r["code"]}/umfragen/verlauf').status_code == 403
    # Neues Meeting im selben Raum sieht die alten Umfragen nicht
    hz3 = zugang(host, r['code'])
    assert host.get(f'/api/raeume/{r["code"]}/umfragen', headers=T(hz3)).json() == {'umfragen': []}


def test_schema_version_aktuell():
    assert db.verbindung().execute('SELECT v FROM schema_version').fetchone()[0] == len(db.SCHEMA)


# ---------------------------------------------------------------- Breakout-Räume
def bo(c, code, z, pfad='', methode='post', **kw):
    return getattr(c, methode)(f'/api/raeume/{code}/breakout{pfad}', headers=T(z), **({'json': kw} if methode != 'get' else {}))


def verbunden(code, *zs):
    lk.FAKE_TEILNEHMER[code] = [{'identity': z['identity'], 'name': z['name'], 'tracks': []} for z in zs]


def test_breakout_anlegen_rechte_und_automatisch():
    host, code, hz, gaeste = meeting(5)
    zs = [z for _c, z in gaeste]
    verbunden(code, hz, *zs)
    g1, z1 = gaeste[0]
    assert bo(g1, code, z1, anzahl=2).status_code == 403
    assert bo(g1, code, z1, '', 'get').json() == {'breakout': None}
    for falsch in ({'anzahl': 1}, {'anzahl': 51}, {'anzahl': '2'}, {'anzahl': 2, 'dauer_min': 0}, {'anzahl': 2, 'dauer_min': 241},
                   {'anzahl': 2, 'rueckkehr': 'ja'}):
        assert bo(host, code, hz, **falsch).status_code == 400, falsch
    r = bo(host, code, hz, anzahl=2, dauer_min=10, automatisch=True)
    assert r.status_code == 200, r.text
    b = r.json()['breakout']
    assert b['status'] == 'entwurf' and b['anzahl'] == 2 and b['dauer_min'] == 10 and b['rueckkehr'] and b['runde'] == 0
    groessen = sorted(len(x['teilnehmer']) for x in b['raeume'])
    assert groessen == [2, 3]                                      # gleichmäßig, Host nicht verteilt
    assert [p['identity'] for p in b['ohne_raum']] == [hz['identity']]
    assert {p['identity'] for x in b['raeume'] for p in x['teilnehmer']} == {z['identity'] for z in zs}
    assert ereignisse('breakout')[-1]['an'] == [hz['identity']]   # Entwurf: nur Moderation
    # Teilnehmende sehen nur ihre eigene Zuteilung
    g = bo(g1, code, z1, '', 'get').json()['breakout']
    assert 'raeume' not in g and 'ohne_raum' not in g and g['mein_raum'] in (1, 2) and g['ort'] == 0
    # Von Hand umsetzen
    assert bo(host, code, hz, '/zuteilung', 'put', zuteilung={z1['identity']: 3}).status_code == 400
    assert bo(host, code, hz, '/zuteilung', 'put', zuteilung={'gibts-nicht': 1}).status_code == 404
    assert bo(g1, code, z1, '/zuteilung', 'put', zuteilung={z1['identity']: 1}).status_code == 403
    b = bo(host, code, hz, '/zuteilung', 'put', zuteilung={z1['identity']: 2, hz['identity']: 1}).json()['breakout']
    assert any(p['identity'] == z1['identity'] for p in b['raeume'][1]['teilnehmer'])
    assert any(p['identity'] == hz['identity'] for p in b['raeume'][0]['teilnehmer'])
    b = bo(host, code, hz, '/zuteilung', 'put', zuteilung={z1['identity']: 0}).json()['breakout']
    assert z1['identity'] in [p['identity'] for p in b['ohne_raum']]
    b = bo(host, code, hz, '/zuteilung', 'put', automatisch=True).json()['breakout']
    assert sorted(len(x['teilnehmer']) for x in b['raeume']) == [2, 3] and [p['identity'] for p in b['ohne_raum']] == [hz['identity']]


def test_breakout_oeffnen_token_wechseln_und_grenzen():
    host, code, hz, [(g1, z1), (g2, z2), (g3, z3)] = meeting(3)
    verbunden(code, hz, z1, z2, z3)
    bo(host, code, hz, anzahl=2)
    bo(host, code, hz, '/zuteilung', 'put', zuteilung={z1['identity']: 1, z2['identity']: 2})
    # vor dem Öffnen: kein Breakout-Raum erreichbar
    assert bo(g1, code, z1, '/wechseln', nr=1).status_code == 400
    assert bo(g1, code, z1, '/oeffnen').status_code == 403
    lk.AUFRUFE.clear()
    b = bo(host, code, hz, '/oeffnen').json()['breakout']
    assert b['status'] == 'offen' and b['runde'] == 1 and b['rest_s'] is None and b['dauer_min'] is None
    assert [a['code'] for a in aufrufe('raum_anlegen')] == [f'{code}__b1', f'{code}__b2']
    assert ereignisse('breakout')[-1]['daten'] == {'typ': 'breakout', 'aktion': 'geoeffnet', 'runde': 1}
    # Ereignisse gehen jetzt an alle Räume (Hauptraum zuletzt)
    assert [e['code'] for e in ereignisse('breakout')] == [f'{code}__b1', f'{code}__b2', code]
    assert bo(host, code, hz, '/oeffnen').status_code == 409
    # Teilnehmer: nur in den eigenen Raum, Token für <code>__b1
    assert bo(g1, code, z1, '/wechseln', nr=2).status_code == 403
    assert bo(g1, code, z1, '/wechseln', nr=3).status_code == 400
    assert bo(g3, code, z3, '/wechseln', nr=1).status_code == 403              # nicht zugeteilt
    zg = bo(g1, code, z1, '/wechseln', nr=1).json()['zugang']
    assert zg['raum'] == f'{code}__b1' and zg['nr'] == 1 and zg['url'] == 'ws://127.0.0.1:7980'
    claims = jwt.decode(zg['token'], lk.API_SECRET, algorithms=['HS256'], options={'verify_iss': False})
    assert claims['video']['room'] == f'{code}__b1' and claims['sub'] == z1['identity'] and claims['attributes']['rolle'] == 'gast'
    assert bo(g1, code, z1, '', 'get').json()['breakout']['ort'] == 1
    # Der Breakout-Raum ist kein Meeting-Code: weder Info noch Beitreten
    assert neu_client().get(f'/api/raeume/{code}__b1').status_code == 404
    assert beitreten(neu_client(), f'{code}__b1', name='Fremd').status_code == 404
    # Host besucht jeden Raum, kommt zurück
    assert bo(host, code, hz, '/wechseln', nr=2).json()['zugang']['raum'] == f'{code}__b2'
    b = bo(host, code, hz, '', 'get').json()['breakout']
    assert b['ort'] == 2 and b['raeume'][1]['anwesend'] == 1 and b['raeume'][0]['anwesend'] == 1
    assert bo(host, code, hz, '/wechseln', nr=0).json()['zugang']['raum'] == code
    # Steuerung erreicht Leute im Breakout-Raum: Rolle/Name gehen an ihren LiveKit-Raum
    steuern(host, code, hz, aktion='umbenennen', ziel=z1['identity'], name='Anna')
    assert aufrufe('aktualisieren')[-1]['code'] == f'{code}__b1'
    lk.FAKE_TEILNEHMER[f'{code}__b1'] = [{'identity': z1['identity'], 'name': 'Anna', 'tracks': [{'sid': 'TR_x', 'quelle': 'mikrofon', 'stumm': False}]}]
    lk.FAKE_TEILNEHMER[code] = [{'identity': hz['identity'], 'name': 'H', 'tracks': []}]
    steuern(host, code, hz, aktion='stumm', ziel=z1['identity'])
    assert aufrufe('mute_published_track')[-1] == {'code': f'{code}__b1', 'identity': z1['identity'], 'sid': 'TR_x'}
    steuern(host, code, hz, aktion='entfernen', ziel=z3['identity'])
    assert [a['code'] for a in aufrufe('entfernen')][-3:] == [f'{code}__b1', f'{code}__b2', code]
    # Zurück in den Hauptraum: erlaubt (Standard) …
    assert bo(g1, code, z1, '/wechseln', nr=0).json()['zugang']['raum'] == code
    assert bo(g1, code, z1, '/wechseln', nr=1).status_code == 200


def test_breakout_rueckkehr_verboten_hilfe_nachricht():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    verbunden(code, hz, z1, z2)
    bo(host, code, hz, anzahl=2, rueckkehr=False)
    bo(host, code, hz, '/zuteilung', 'put', zuteilung={z1['identity']: 1})
    bo(host, code, hz, '/oeffnen')
    assert bo(g1, code, z1, '/hilfe').status_code == 400                       # noch im Hauptraum
    bo(g1, code, z1, '/wechseln', nr=1)
    r = bo(g1, code, z1, '/wechseln', nr=0)
    assert r.status_code == 403 and 'Rückkehr' in r.json()['fehler']
    assert bo(g2, code, z2, '/wechseln', nr=0).status_code == 200              # nicht zugeteilt → darf
    # Hilfe rufen → an Host/Co-Hosts, mit Raumnummer
    assert bo(g1, code, z1, '/hilfe').json() == {'ok': True}
    e = [x for x in lk.AUFRUFE if x[0] == 'senden' and x[1]['daten'].get('typ') == 'breakout_hilfe'][-1][1]
    assert e['an'] == [hz['identity']] and e['daten'] == {'typ': 'breakout_hilfe', 'nr': 1, 'identity': z1['identity'], 'von_name': 'Gast 1'}
    for _ in range(2):
        bo(g1, code, z1, '/hilfe')
    assert bo(g1, code, z1, '/hilfe').status_code == 429
    # Nachricht an alle Räume (nur Moderation)
    assert bo(g1, code, z1, '/nachricht', text='Hallo').status_code == 403
    assert bo(host, code, hz, '/nachricht', text='').status_code == 400
    assert bo(host, code, hz, '/nachricht', text='Noch 2 Minuten').json() == {'ok': True}
    gesendet = [x[1] for x in lk.AUFRUFE if x[0] == 'senden' and x[1]['daten'].get('typ') == 'breakout_nachricht']
    assert [x['code'] for x in gesendet] == [f'{code}__b1', f'{code}__b2', code] and gesendet[-1]['an'] is None
    assert gesendet[-1]['daten'] == {'typ': 'breakout_nachricht', 'text': 'Noch 2 Minuten', 'von_name': 'Ada'}
    # Chat erreicht ebenfalls alle Räume
    host.post(f'/api/raeume/{code}/chat', json={'text': 'An alle'}, headers=T(hz))
    assert [a['code'] for a in aufrufe('senden') if a['topic'] == 'chat'] == [f'{code}__b1', f'{code}__b2', code]


def test_breakout_schliessen_countdown_und_timer():
    import asyncio
    from app import breakout
    host, code, hz, [(g1, z1)] = meeting(1)
    verbunden(code, hz, z1)
    assert bo(host, code, hz, '/schliessen').status_code == 404
    bo(host, code, hz, anzahl=2, dauer_min=5, automatisch=True)
    assert bo(host, code, hz, '/schliessen').status_code == 409
    bo(host, code, hz, '/oeffnen')
    bo(g1, code, z1, '/wechseln', nr=1)
    b = bo(host, code, hz, '', 'get').json()['breakout']
    assert 295 <= b['rest_s'] <= 300 and b['endet'].endswith('Z')
    assert bo(g1, code, z1, '/schliessen').status_code == 403
    b = bo(host, code, hz, '/schliessen').json()['breakout']
    assert b['status'] == 'schliesst' and 55 <= b['schliesst_in_s'] <= 60
    assert ereignisse('breakout')[-1]['daten'] == {'typ': 'breakout', 'aktion': 'schliesst', 'runde': 1, 'schliesst_in_s': 60}
    assert bo(host, code, hz, anzahl=3).status_code == 409                     # während offen keine neue Runde
    assert bo(g1, code, z1, '/wechseln', nr=0).status_code == 200              # Countdown: zurück geht immer
    bo(g1, code, z1, '/wechseln', nr=1)
    # Countdown noch nicht vorbei → nichts passiert
    asyncio.run(breakout.faellige_pruefen())
    assert bo(host, code, hz, '', 'get').json()['breakout']['status'] == 'schliesst'
    lk.AUFRUFE.clear()
    asyncio.run(breakout.faellige_pruefen(t=breakout.jetzt() + 61))
    b = bo(host, code, hz, '', 'get').json()['breakout']
    assert b['status'] == 'geschlossen' and b['ort'] == 0
    assert [e['code'] for e in ereignisse('breakout')] == [f'{code}__b1', f'{code}__b2', code]
    assert ereignisse('breakout')[-1]['daten']['aktion'] == 'geschlossen'
    assert sorted(a['code'] for a in aufrufe('raum_loeschen')) == [f'{code}__b1', f'{code}__b2']
    assert bo(g1, code, z1, '/wechseln', nr=1).status_code == 400              # geschlossen
    assert bo(g1, code, z1, '/wechseln', nr=0).json()['zugang']['raum'] == code
    # nach dem Schließen gehen Ereignisse nur noch an den Hauptraum
    lk.AUFRUFE.clear()
    steuern(host, code, hz, aktion='sperren', an=True)
    assert [e['code'] for e in ereignisse('gesperrt')] == [code]
    # Neue Runde mit Timer: Ablauf startet den Countdown von selbst
    bo(host, code, hz, anzahl=2, dauer_min=1, automatisch=True)
    assert bo(host, code, hz, '/oeffnen').json()['breakout']['runde'] == 2
    asyncio.run(breakout.faellige_pruefen(t=breakout.jetzt() + 61))
    assert bo(host, code, hz, '', 'get').json()['breakout']['status'] == 'schliesst'


def test_breakout_mit_meeting_beendet():
    host, code, hz, [(g1, z1)] = meeting(1)
    verbunden(code, hz, z1)
    bo(host, code, hz, anzahl=2, automatisch=True)
    bo(host, code, hz, '/oeffnen')
    lk.AUFRUFE.clear()
    steuern(host, code, hz, aktion='beenden')
    assert [e['code'] for e in ereignisse('beendet')] == [f'{code}__b1', f'{code}__b2', code]
    assert [a['code'] for a in aufrufe('raum_loeschen')] == [f'{code}__b1', f'{code}__b2', code]
    assert db.verbindung().execute('SELECT COUNT(*) FROM breakout b JOIN raeume r ON r.id = b.raum_id WHERE r.code = ?', (code,)).fetchone()[0] == 0
    hz2 = zugang(host, code)
    assert bo(host, code, hz2, '', 'get').json() == {'breakout': None}


# ---------------------------------------------------------------- Verbindungsqualität (Statistik)
def statistik(c, code, z, **kw):
    return c.post(f'/api/raeume/{code}/statistik', json=kw, headers=T(z))


def test_statistik_melden_bewerten_und_host_sicht():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    assert statistik(g1, code, z1).json()['bewertung'] is None                         # nur anmelden, keine Werte
    r = statistik(g1, code, z1, rtt=40, verlust=0.5, jitter=8, ein=900, aus=1200, kandidat='udp', aufloesung='640x360', fps=24)
    assert r.json()['ok'] is True and r.json()['bewertung'] == 'gut'
    assert statistik(g2, code, z2, rtt=250, verlust=1, jitter=10, kandidat='relay').json()['bewertung'] == 'mittel'
    assert statistik(g2, code, z2, rtt=90, verlust=8, jitter=10, kandidat='tcp').json()['bewertung'] == 'schlecht'
    # Prüfung der Werte
    assert statistik(g1, code, z1, rtt=-1, verlust=0, jitter=0).status_code == 400
    assert statistik(g1, code, z1, rtt=10, verlust=101, jitter=0).status_code == 400
    assert statistik(g1, code, z1, rtt=10).status_code == 400                         # unvollständig
    assert statistik(g1, code, z1, rtt=10, verlust=0, jitter=0, kandidat='quic').status_code == 400
    assert statistik(g1, code, z1, rtt=10, verlust=0, jitter=0, aufloesung='<b>').status_code == 400
    assert statistik(g1, code, z1, rtt=True, verlust=0, jitter=0).status_code == 400
    assert g1.post(f'/api/raeume/{code}/statistik', json={}).status_code == 403          # ohne Teilnahme
    # Nur Host/Co-Host sehen die Werte je Person
    assert g1.get(f'/api/raeume/{code}/statistik', headers=T(z1)).status_code == 403
    s = host.get(f'/api/raeume/{code}/statistik', headers=T(hz)).json()
    assert set(s['teilnehmer']) == {z1['identity'], z2['identity']}
    assert s['teilnehmer'][z2['identity']]['bewertung'] == 'schlecht' and s['teilnehmer'][z2['identity']]['kandidat'] == 'tcp'
    assert s['teilnehmer'][z1['identity']]['aufloesung'] == '640x360'
    m = s['meeting']
    assert m['proben'] == 3 and m['teilnehmer'] == 2 and m['rtt'] == round((40 + 250 + 90) / 3) and m['verlust_max'] == 8
    assert m['schlecht_anteil'] == 33 and m['kandidaten'] == {'udp': 33, 'tcp': 33, 'relay': 33}
    # Keine IPs, keine Namen in der Tabelle
    spalten = [x[1] for x in db.verbindung().execute('PRAGMA table_info(qualitaet)')]
    assert not {'ip', 'name'} & set(spalten)


def test_statistik_begrenzt_und_beim_ende_geloescht():
    host, code, hz, [(g1, z1)] = meeting(1)
    codes = [statistik(g1, code, z1, rtt=10, verlust=0, jitter=1).status_code for _ in range(13)]
    assert codes[:12] == [200] * 12 and codes[12] == 429
    steuern(host, code, hz, aktion='beenden')
    c = db.verbindung()
    rid = c.execute('SELECT id FROM raeume WHERE code = ?', (code,)).fetchone()[0]
    assert c.execute('SELECT COUNT(*) FROM qualitaet_person WHERE raum_id = ?', (rid,)).fetchone()[0] == 0
    assert c.execute('SELECT proben FROM qualitaet WHERE raum_id = ?', (rid,)).fetchone()[0] == 12   # Schnitt bleibt
    assert statistik(g1, code, z1, rtt=10, verlust=0, jitter=1).status_code == 403                    # Meeting vorbei


# ---------------------------------------------------------------- Rückmeldung nach dem Meeting
def test_feedback_nach_dem_meeting_und_konto_schnitt():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    tok1 = statistik(g1, code, z1, rtt=40, verlust=1, jitter=5, kandidat='udp').json()['feedback']
    tok2 = statistik(g2, code, z2).json()['feedback']
    tokh = statistik(host, code, hz, rtt=20, verlust=0, jitter=2).json()['feedback']
    steuern(host, code, hz, aktion='beenden')                       # Teilnahmen weg – Rückmeldung geht trotzdem
    fb = lambda c, **kw: c.post(f'/api/raeume/{code}/feedback', json=kw)   # noqa: E731
    assert fb(g1, token=tok1, sterne=4, probleme=['video', 'ton'], text='Bild ruckelte kurz',
              statistik={'proben': 12, 'rtt': 40, 'verlust': 1.2, 'jitter': 5, 'ein': 800, 'aus': 900, 'schlecht_anteil': 0}).json() == {'ok': True}
    assert fb(g2, token=tok2, sterne=2, probleme=['verbindung']).status_code == 200
    assert fb(g2, token=tok2, sterne=3, probleme=['verbindung']).status_code == 200    # ändern statt doppelt zählen
    assert fb(host, token=tokh, sterne=5).status_code == 200
    # Prüfung
    assert fb(g1, token=tok1, sterne=0).status_code == 400
    assert fb(g1, token=tok1, sterne=6).status_code == 400
    assert fb(g1, token=tok1, sterne=True).status_code == 400
    assert fb(g1, token=tok1, sterne=3, probleme=['wetter']).status_code == 400
    assert fb(g1, token=tok1, sterne=3, statistik={'rtt': -5}).status_code == 400
    assert fb(g1, token=tok1, sterne=3, text='x' * 1001).status_code == 400
    assert fb(g1, token=tok1[:-1] + ('0' if tok1[-1] != '0' else '1'), sterne=3).status_code == 403   # gefälscht
    assert fb(g1, token='kaputt', sterne=3).status_code == 403
    sitzung, ident, zeit, _sig = tok1.split('.')
    from app.qualitaet import feedback_token
    rid = db.verbindung().execute('SELECT id FROM raeume WHERE code = ?', (code,)).fetchone()[0]
    alt = feedback_token(rid, int(sitzung), ident, int(zeit) - 25 * 3600)
    assert fb(g1, token=alt, sterne=3).status_code == 403                             # abgelaufen
    # anderer Raum: Token gilt nicht
    _h2, code2, _hz2, _g = meeting(0)
    assert g1.post(f'/api/raeume/{code2}/feedback', json={'token': tok1, 'sterne': 3}).status_code == 403
    # Besitzer sieht Schnitt und Texte ohne Namen
    u = host.get('/api/qualitaet').json()['raeume'][code]
    assert u['bewertungen'] == 3 and u['sterne'] == 4.0 and u['meetings'] == 1 and u['rtt'] == 30 and u['verlust'] == 0.5
    q = host.get(f'/api/raeume/{code}/qualitaet').json()['meetings']
    assert len(q) == 1 and q[0]['verbindung']['proben'] == 2
    f = q[0]['feedback']
    assert f['anzahl'] == 3 and f['sterne'] == 4.0 and f['probleme']['verbindung'] == 1 and f['probleme']['video'] == 1
    assert f['kommentare'] == ['Bild ruckelte kurz']
    assert 'identity' not in str(q) and z1['identity'] not in str(q) and 'Gast' not in str(q)
    assert g1.get(f'/api/raeume/{code}/qualitaet').status_code == 401
    anderer, _e, _p = konto()
    assert anderer.get(f'/api/raeume/{code}/qualitaet').status_code == 403
    assert code not in anderer.get('/api/qualitaet').json()['raeume']
    # je Meeting (für den Meeting-Verlauf): Schlüssel code:sitzung
    sitzung = db.verbindung().execute('SELECT sitzung FROM raeume WHERE code = ?', (code,)).fetchone()[0]
    jm = host.get('/api/qualitaet').json()['meetings'][f'{code}:{sitzung}']
    assert jm == {'bewertungen': 3, 'sterne': 4.0, 'rtt': 30, 'verlust': 0.5}
    v = [m for m in host.get('/api/verlauf').json()['meetings'] if m['code'] == code]
    assert v and v[0]['sitzung'] == sitzung                            # passt zum Eintrag im Meeting-Verlauf


# ---------------------------------------------------------------- Metriken und tiefe Gesundheitsprüfung
def lokal(**kw):
    return TestClient(main.app, client=('127.0.0.1', 40000), **kw)


def metrik(text, name):
    for z in text.splitlines():
        if z.split(' ')[0] == name:
            return float(z.rsplit(' ', 1)[1])
    return None


def test_metriken_nur_lokal_ohne_tunnel_oder_mit_token(monkeypatch):
    from app import metriken
    assert neu_client().get('/api/metriken').status_code == 404                           # von außen (Testclient-Host)
    assert lokal().get('/api/metriken', headers={'CF-Connecting-IP': '1.2.3.4'}).status_code == 404   # über den Tunnel
    assert lokal().get('/api/gesund?tief=1', headers={'CF-Connecting-IP': '1.2.3.4'}).status_code == 404
    assert neu_client().get('/api/gesund').json() == {'ok': True}                         # einfache Prüfung bleibt offen
    r = lokal().get('/api/metriken')
    assert r.status_code == 200 and r.headers['content-type'].startswith('text/plain; version=0.0.4')
    monkeypatch.setattr(metriken, 'TOKEN', 't' * 40)
    assert neu_client().get('/api/metriken', headers={'Authorization': 'Bearer ' + 't' * 40}).status_code == 200
    assert neu_client().get('/api/metriken', headers={'Authorization': 'Bearer falsch'}).status_code == 404
    monkeypatch.setattr(metriken, 'TOKEN', '')
    assert neu_client().get('/api/metriken', headers={'Authorization': 'Bearer '}).status_code == 404   # leeres Token zählt nie


def test_metriken_zaehler_und_momentwerte():
    vorher = lokal().get('/api/metriken').text
    c, email, pw = konto()
    neu_client().post('/api/konto/anmelden', json={'email': email, 'passwort': 'falsch-falsch-1'})
    c.post('/api/konto/anmelden', json={'email': email, 'passwort': pw})
    host, code, hz, [(g1, z1)] = meeting(1)
    lk.FAKE_TEILNEHMER[code] = [{'identity': hz['identity'], 'name': 'H', 'tracks': []}, {'identity': z1['identity'], 'name': 'G', 'tracks': []}]
    t = lokal().get('/api/metriken').text
    diff = lambda n: metrik(t, n) - (metrik(vorher, n) or 0)   # noqa: E731
    assert diff('agora_registrierungen_total') == 2
    assert diff('agora_anmeldungen_total{ergebnis="ok"}') == 1
    assert diff('agora_anmeldungen_total{ergebnis="fehlgeschlagen"}') == 1
    assert diff('agora_mails_total{ergebnis="ok"}') == 2                  # zwei Bestätigungsmails
    assert metrik(t, 'agora_meetings_aktiv') >= 1 and metrik(t, 'agora_teilnehmer_verbunden') == 2
    assert metrik(t, 'agora_livekit_erreichbar') == 1 and metrik(t, 'agora_daten_frei_bytes') > 0
    assert metrik(t, 'agora_konten{status="bestaetigt"}') >= 2 and metrik(t, 'agora_aufnahmen_bytes') is not None
    assert '# TYPE agora_http_dauer_sekunden histogram' in t
    assert metrik(t, 'agora_http_dauer_sekunden_count{gruppe="konto"}') >= 4
    assert metrik(t, 'agora_http_dauer_sekunden_bucket{gruppe="konto",le="+Inf"}') == metrik(t, 'agora_http_dauer_sekunden_count{gruppe="konto"}')
    assert code not in t and email not in t                                 # keine Codes/Adressen als Label


def test_livekit_fehler_und_hintergrund_zaehlen(monkeypatch):
    import asyncio
    from app import metriken
    monkeypatch.setattr(lk, 'FAKE', False)
    monkeypatch.setattr(lk, 'API_HOST', 'http://127.0.0.1:9')           # nichts lauscht → Fehler
    vorher = metrik(lokal().get('/api/metriken').text, 'agora_livekit_fehler_total{aufruf="metadaten"}') or 0
    assert asyncio.run(lk.metadaten('abc-defg-hij', {})) is None
    t = lokal().get('/api/metriken').text
    assert metrik(t, 'agora_livekit_fehler_total{aufruf="metadaten"}') == vorher + 1
    assert metrik(t, 'agora_livekit_erreichbar') == 0
    g = lokal().get('/api/gesund?tief=1')
    assert g.status_code == 503 and g.json()['pruefungen']['livekit']['ok'] is False
    monkeypatch.setattr(lk, 'FAKE', True)
    metriken.hintergrund_gelaufen()
    assert metrik(lokal().get('/api/metriken').text, 'agora_hintergrund_letzter_lauf_zeitstempel_sekunden') > 1e9


def test_gesund_tief(monkeypatch):
    from app import metriken
    g = lokal().get('/api/gesund?tief=1')
    assert g.status_code == 200, g.text
    p = g.json()['pruefungen']
    assert set(p) == {'datenbank', 'livekit', 'platte', 'hintergrund'} and all(x['ok'] for x in p.values())
    assert p['datenbank']['ms'] >= 0 and p['platte']['frei_bytes'] > 0
    monkeypatch.setattr(metriken, 'PLATTE_MIN', 1e18)                     # „Platte voll“
    assert lokal().get('/api/gesund?tief=1').status_code == 503
    monkeypatch.setattr(metriken, 'PLATTE_MIN', 0)
    monkeypatch.setitem(metriken.ZUSTAND, 'hintergrund', 1.0)             # Hintergrund hängt seit 1970
    r = lokal().get('/api/gesund?tief=1')
    assert r.status_code == 503 and r.json()['pruefungen']['hintergrund']['ok'] is False
