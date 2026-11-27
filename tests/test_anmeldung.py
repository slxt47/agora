"""Anmelden mit Google/Microsoft/GitHub (app/anmeldung.py, app/oidc.py) gegen einen nachgebauten Anbieter (tests/idp_fake.py).

Schwerpunkt Sicherheit: id_token-Prüfung (aud/iss/exp/nonce/Signatur/alg none/HS256), state/PKCE/Browser-Bindung,
offene Umleitungen und Kontoübernahme (unbestätigte Adresse, bestehendes Konto nur mit Passwort, vorab angelegte Konten)."""
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from idp_fake import FakeIdP, jwk, schluessel
from test_api import frisch, konto, neu_client  # noqa: F401  (frisch = autouse-Fixture)
from test_zwei_faktor import Uhr, einschalten, totp

from app import anmeldung, db, oidc

ANB = {'AGORA_GOOGLE_CLIENT_ID': 'agora-client', 'AGORA_GOOGLE_CLIENT_SECRET': 'agora-geheim',
       'AGORA_GOOGLE_DISCOVERY': 'https://idp.example.org/.well-known/openid-configuration', 'AGORA_GOOGLE_ISSUER': 'https://idp.example.org'}


@pytest.fixture
def idp(monkeypatch):
    for k, v in ANB.items():
        monkeypatch.setenv(k, v)
    for k in ('AGORA_MICROSOFT_CLIENT_ID', 'AGORA_GITHUB_CLIENT_ID'):
        monkeypatch.delenv(k, raising=False)
    fake = FakeIdP()
    monkeypatch.setattr(oidc, '_anfrage', fake)
    oidc.cache_leeren()
    yield fake
    oidc.cache_leeren()


def starten(c, anbieter='google', **kw):
    r = c.post(f'/api/anmeldung/{anbieter}/start', json=kw)
    assert r.status_code == 200, r.text
    return r.json()['url']


def zurueck(c, anbieter, state, code, **extra):
    return c.get(f'/api/anmeldung/{anbieter}/zurueck', params={'state': state, 'code': code} | extra, follow_redirects=False)


def durchlauf(c, idp, anbieter='google', start=None, **kw):
    """Start → Anbieter → zurück. Gibt die Antwort der Rückkehr (303) zurück."""
    state, code = idp.autorisieren(starten(c, anbieter, **(start or {})), **kw)
    return zurueck(c, anbieter, state, code)


def ich(c):
    return c.get('/api/konto/ich').json()


def grund(r):
    assert r.status_code == 303, r.text
    q = parse_qs(urlsplit(r.headers['location']).query)
    return q.get('grund', [None])[0]


# ---------------------------------------------------------------- aus / an
def test_ohne_zugangsdaten_aus(monkeypatch):
    for k in ('AGORA_GOOGLE_CLIENT_ID', 'AGORA_MICROSOFT_CLIENT_ID', 'AGORA_GITHUB_CLIENT_ID'):
        monkeypatch.delenv(k, raising=False)
    c = neu_client()
    assert c.get('/api/einstellungen').json()['anmeldung'] == []
    assert c.post('/api/anmeldung/google/start', json={}).status_code == 404
    assert c.post('/api/anmeldung/gibtsnicht/start', json={}).status_code == 404
    # nur ID ohne Geheimnis: weiterhin aus
    monkeypatch.setenv('AGORA_GITHUB_CLIENT_ID', 'x')
    assert c.get('/api/einstellungen').json()['anmeldung'] == []


def test_start_adresse(idp):
    c = neu_client()
    assert c.get('/api/einstellungen').json()['anmeldung'] == [{'id': 'google', 'name': 'Google'}]
    url = starten(c)
    u = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert f'{u.scheme}://{u.netloc}{u.path}' == 'https://idp.example.org/auth'
    assert q['redirect_uri'] == 'https://agora.slxt.dev/api/anmeldung/google/zurueck'
    assert q['scope'] == 'openid email profile' and q['code_challenge_method'] == 'S256' and len(q['code_challenge']) == 43
    assert len(q['state']) >= 40 and len(q['nonce']) >= 40 and q['state'] != q['nonce']
    assert 'agora-anmeldung' in c.cookies
    # state nur als Hash gespeichert
    v = db.verbindung().execute('SELECT * FROM anmelde_vorgaenge ORDER BY gueltig_bis DESC LIMIT 1').fetchone()
    assert v['state_hash'] != q['state'] and q['state'] not in str(dict(v))
    # CSRF: ohne X-Agora kein Start
    r = neu_client().post('/api/anmeldung/google/start', json={}, headers={'X-Agora': ''})
    assert r.status_code == 403


# ---------------------------------------------------------------- neue Konten, wiederkehrend
def test_neues_konto_und_wiederkehr(idp):
    c = neu_client()
    r = durchlauf(c, idp, claims={'sub': 's-neu', 'email': 'Nora@Example.org', 'name': 'Nora Neu'})
    assert r.headers['location'] == '/'
    j = ich(c)
    assert j['angemeldet'] and j['ich']['email'] == 'nora@example.org' and j['ich']['name'] == 'Nora Neu'
    assert j['ich']['passwort_gesetzt'] is False and j['ich']['persoenlicher_raum']
    a = c.get('/api/konto/anmeldungen').json()
    assert [x['anbieter'] for x in a['verknuepft']] == ['google'] and a['passwort_gesetzt'] is False
    # Passwort-Login gibt es für das Konto nicht (leerer Hash)
    assert neu_client().post('/api/konto/anmelden', json={'email': 'nora@example.org', 'passwort': ''}).status_code == 400
    # wieder anmelden: gleiche Kennung (sub), auch wenn sich die Adresse beim Anbieter geändert hat
    c2 = neu_client()
    r = durchlauf(c2, idp, claims={'sub': 's-neu', 'email': 'nora.neu@example.org'})
    assert r.headers['location'] == '/' and ich(c2)['ich']['id'] == j['ich']['id']
    ereignisse = [e['art'] for e in c2.get('/api/konto/sicherheit').json()['ereignisse']]
    assert 'anmeldung_extern' in ereignisse and 'extern_verknuepft' in ereignisse
    assert c2.get('/api/konto/export').json()['anmeldungen'][0]['anbieter'] == 'google'


def test_weiter_nur_eigene_pfade(idp):
    for boese in ('//evil.example', 'https://evil.example', '/\\evil.example', '/%2F%2Fevil', 'javascript:alert(1)', '/a b', '/' + 'x' * 400):
        c = neu_client()
        r = durchlauf(c, idp, start={'weiter': boese}, claims={'sub': f's-{len(boese)}', 'email': f'w{len(boese)}@example.org'})
        assert r.headers['location'] == '/', boese
    c = neu_client()
    r = durchlauf(c, idp, start={'weiter': '/konto#teams'}, claims={'sub': 's-weiter', 'email': 'weiter@example.org'})
    assert r.headers['location'] == '/konto#teams'


# ---------------------------------------------------------------- Kontoübernahme
def test_unbestaetigte_adresse_kein_konto(idp):
    c = neu_client()
    r = durchlauf(c, idp, claims={'sub': 's-unb', 'email': 'unb@example.org', 'email_verified': False})
    assert grund(r) == 'email_unbestaetigt' and not ich(c)['angemeldet']
    assert not db.verbindung().execute("SELECT 1 FROM benutzer WHERE email = 'unb@example.org'").fetchone()
    r = durchlauf(neu_client(), idp, claims={'sub': 's-unb2', 'email': 'unb2@example.org', 'email_verified': 'false'})
    assert grund(r) == 'email_unbestaetigt'
    r = durchlauf(neu_client(), idp, claims={'sub': 's-unb3', 'email': None})
    assert grund(r) == 'email_unbestaetigt'


def test_bestehendes_konto_nur_mit_passwort(idp):
    opfer, email, pw = konto(name='Opfer')
    raum_vorher = ich(opfer)['ich']['persoenlicher_raum']
    c = neu_client()
    r = durchlauf(c, idp, claims={'sub': 's-opfer', 'email': email})
    assert r.status_code == 303 and r.headers['location'].startswith('/#verknuepfen=')
    assert not ich(c)['angemeldet']            # NICHT angemeldet
    token = r.headers['location'].split('=', 1)[1]
    info = c.post('/api/anmeldung/offen', json={'token': token}).json()
    assert info == {'anbieter': 'google', 'name': 'Google', 'email': email, 'zwei_faktor': False}
    # anderer Browser (ohne Bindungs-Cookie) kann das Token nicht benutzen
    fremd = neu_client()
    assert fremd.post('/api/anmeldung/verknuepfen', json={'token': token, 'passwort': pw}).status_code == 400
    assert c.post('/api/anmeldung/verknuepfen', json={'token': token, 'passwort': 'falsch-falsch-1'}).status_code == 400
    assert not ich(c)['angemeldet']
    r = c.post('/api/anmeldung/verknuepfen', json={'token': token, 'passwort': pw})
    assert r.status_code == 200 and r.json()['ich']['email'] == email
    assert ich(c)['ich']['persoenlicher_raum'] == raum_vorher
    # Token verbraucht; danach direkt über die Verknüpfung
    assert c.post('/api/anmeldung/verknuepfen', json={'token': token, 'passwort': pw}).status_code == 400
    c2 = neu_client()
    assert durchlauf(c2, idp, claims={'sub': 's-opfer', 'email': email}).headers['location'] == '/'
    assert ich(c2)['ich']['email'] == email


def test_verknuepfen_hoechstens_fuenf_versuche(idp):
    _o, email, pw = konto()
    c = neu_client()
    token = durchlauf(c, idp, claims={'sub': 's-v5', 'email': email}).headers['location'].split('=', 1)[1]
    for _ in range(5):
        assert c.post('/api/anmeldung/verknuepfen', json={'token': token, 'passwort': 'falsch-falsch-1'}).status_code == 400
    r = c.post('/api/anmeldung/verknuepfen', json={'token': token, 'passwort': pw})
    assert r.status_code == 400 and 'abgelaufen' in r.json()['fehler']


def test_bestehendes_konto_mit_2fa(idp, monkeypatch):
    uhr = Uhr(monkeypatch)
    oc, email, pw = konto()
    geheim, _codes = einschalten(oc, pw, uhr)
    c = neu_client()
    token = durchlauf(c, idp, claims={'sub': 's-2fa', 'email': email}).headers['location'].split('=', 1)[1]
    assert c.post('/api/anmeldung/offen', json={'token': token}).json()['zwei_faktor'] is True
    r = c.post('/api/anmeldung/verknuepfen', json={'token': token, 'passwort': pw})
    assert r.json() == {'zwei_faktor': True} and not ich(c)['angemeldet']
    assert c.post('/api/anmeldung/verknuepfen', json={'token': token, 'passwort': pw, 'code': '000000'}).status_code == 400
    uhr.weiter()
    r = c.post('/api/anmeldung/verknuepfen', json={'token': token, 'passwort': pw, 'code': totp(geheim, uhr.t)})
    assert r.status_code == 200 and ich(c)['angemeldet']
    # später über die Verknüpfung: 2FA-Schritt bleibt Pflicht
    c2 = neu_client()
    r = durchlauf(c2, idp, claims={'sub': 's-2fa', 'email': email})
    assert r.headers['location'].startswith('/#zf=') and not ich(c2)['angemeldet']
    schritt = r.headers['location'].split('=', 1)[1]
    uhr.weiter()
    r = c2.post('/api/konto/anmelden/code', json={'schritt': schritt, 'code': totp(geheim, uhr.t)})
    assert r.status_code == 200 and ich(c2)['angemeldet']


def test_vorab_angelegtes_konto_wird_verworfen(idp):
    """Angreifer registriert opfer@… mit eigenem Passwort (unbestätigt). Meldet sich das Opfer über den Anbieter an,
    darf das Passwort des Angreifers danach nicht funktionieren."""
    a = neu_client()
    email = 'vorab@example.org'
    assert a.post('/api/konto/registrieren', json={'name': 'Böse', 'email': email, 'passwort': 'angreifer-pw-123'}).status_code == 200
    c = neu_client()
    assert durchlauf(c, idp, claims={'sub': 's-vorab', 'email': email, 'name': 'Echt'}).headers['location'] == '/'
    j = ich(c)['ich']
    assert j['name'] == 'Echt' and j['passwort_gesetzt'] is False     # neu angelegt (die id kann SQLite wiederverwenden)
    assert neu_client().post('/api/konto/anmelden', json={'email': email, 'passwort': 'angreifer-pw-123'}).status_code == 400


def test_konto_ohne_passwort_nicht_ueber_anderen_anbieter(idp, monkeypatch):
    c = neu_client()
    durchlauf(c, idp, claims={'sub': 's-ohnepw', 'email': 'ohnepw@example.org'})
    r = durchlauf(neu_client(), idp, claims={'sub': 's-anderer', 'email': 'ohnepw@example.org'})
    assert grund(r) == 'kein_passwort'


# ---------------------------------------------------------------- state, Bindung, PKCE
def test_state_und_bindung(idp):
    c = neu_client()
    url = starten(c)
    state, code = idp.autorisieren(url, claims={'sub': 's-state', 'email': 'state@example.org'})
    # falscher state, leerer state, anderer Anbieter-Pfad
    assert grund(zurueck(c, 'google', 'x' * 43, code)) == 'ungueltig'
    assert grund(zurueck(c, 'google', '', code)) == 'ungueltig'
    # Login-CSRF: Angreifer schiebt seinen Code+state einem anderen Browser unter (ohne Bindungs-Cookie)
    opfer = neu_client()
    assert grund(zurueck(opfer, 'google', state, code)) == 'ungueltig' and not ich(opfer)['angemeldet']
    # der richtige Browser kann abschließen – genau einmal
    assert zurueck(c, 'google', state, code).headers['location'] == '/'
    assert grund(zurueck(c, 'google', state, code)) == 'ungueltig'
    # fremdes Bindungs-Cookie
    c2 = neu_client()
    s2, k2 = idp.autorisieren(starten(c2), claims={'sub': 's-state2', 'email': 'state2@example.org'})
    c3 = neu_client()
    starten(c3)                # eigenes, anderes Cookie
    assert grund(zurueck(c3, 'google', s2, k2)) == 'ungueltig'
    # abgelaufen
    c4 = neu_client()
    s4, k4 = idp.autorisieren(starten(c4), claims={'sub': 's-state4', 'email': 'state4@example.org'})
    db.verbindung().execute('UPDATE anmelde_vorgaenge SET gueltig_bis = 1')
    assert grund(zurueck(c4, 'google', s4, k4)) == 'ungueltig'
    # Abbruch beim Anbieter
    c5 = neu_client()
    s5, _ = idp.autorisieren(starten(c5))
    r = c5.get('/api/anmeldung/google/zurueck', params={'state': s5, 'error': 'access_denied'}, follow_redirects=False)
    assert grund(r) == 'abgebrochen'


def test_pkce_verifier_muss_passen(idp):
    c = neu_client()
    s, k = idp.autorisieren(starten(c), claims={'sub': 's-pkce', 'email': 'pkce@example.org'})
    db.verbindung().execute("UPDATE anmelde_vorgaenge SET verifier = 'falsch-falsch-falsch-falsch-falsch-falsch-falsch'")
    assert grund(zurueck(c, 'google', s, k)) == 'anbieter' and not ich(c)['angemeldet']


# ---------------------------------------------------------------- id_token
FALSCH = [
    ('aud', {'claims': {'aud': 'fremde-app'}}),
    ('aud-liste-ohne-azp', {'claims': {'aud': ['agora-client', 'fremde-app']}}),
    ('aud-liste-falsches-azp', {'claims': {'aud': ['agora-client', 'fremde-app'], 'azp': 'fremde-app'}}),
    ('iss', {'claims': {'iss': 'https://boese.example.org'}}),
    ('iss-leer', {'claims': {'iss': None}}),
    ('exp', {'claims': {'exp': int(time.time()) - 3600, 'iat': int(time.time()) - 4000}}),
    ('iat-zukunft', {'claims': {'iat': int(time.time()) + 3600, 'exp': int(time.time()) + 7200}}),
    ('iat-alt', {'claims': {'iat': int(time.time()) - 3 * 86400, 'exp': int(time.time()) + 600}}),
    ('nonce', {'claims': {'nonce': 'fremde-nonce'}}),
    ('nonce-fehlt', {'claims': {'nonce': None}}),
    ('alg-none', {'kopf': {'alg': 'none'}}),
    ('alg-hs256', {'kopf': {'alg': 'HS256'}}),
    ('fremder-schluessel', {'signier_key': 'fremd'}),
    ('unbekannte-kid', {'kopf': {'kid': 'gibts-nicht'}}),
    ('kaputt', {'token': 'abc.def.ghi'}),
    ('kein-jwt', {'token': 'kein-token'}),
]


@pytest.mark.parametrize('name,art', FALSCH, ids=[f[0] for f in FALSCH])
def test_id_token_falsch(idp, name, art):
    kw = dict(art)
    if kw.get('signier_key') == 'fremd':
        kw['signier_key'] = schluessel('fremd')
    kw['claims'] = {'sub': f's-{name}', 'email': f'{name}@example.org'} | kw.get('claims', {})
    c = neu_client()
    r = durchlauf(c, idp, **kw)
    assert grund(r) == 'token', name
    assert not ich(c)['angemeldet']
    assert not db.verbindung().execute('SELECT 1 FROM benutzer WHERE email = ?', (f'{name}@example.org',)).fetchone()


def test_ohne_kid_nur_bei_einem_schluessel(idp):
    r = durchlauf(neu_client(), idp, kopf={'kid': None}, claims={'sub': 's-ohnekid', 'email': 'ohnekid@example.org'})
    assert r.headers['location'] == '/'
    idp.weitere_keys = [jwk(schluessel('fremd'), 'k2')]
    oidc.cache_leeren()
    r = durchlauf(neu_client(), idp, kopf={'kid': None}, claims={'sub': 's-ohnekid2', 'email': 'ohnekid2@example.org'})
    assert grund(r) == 'token'


def test_schluesselwechsel_beim_anbieter(idp):
    durchlauf(neu_client(), idp, claims={'sub': 's-w1', 'email': 'w1@example.org'})      # JWKS im Speicher
    idp.key, idp.kid = schluessel('neu'), 'k-neu'
    r = durchlauf(neu_client(), idp, claims={'sub': 's-w2', 'email': 'w2@example.org'})
    assert r.headers['location'] == '/'


def test_discovery_mit_fremdem_issuer(idp, monkeypatch):
    idp.discovery_issuer = 'https://boese.example.org'
    r = neu_client().post('/api/anmeldung/google/start', json={})
    assert r.status_code == 502


# ---------------------------------------------------------------- Microsoft
@pytest.fixture
def ms(monkeypatch):
    monkeypatch.setenv('AGORA_MICROSOFT_CLIENT_ID', 'agora-client')
    monkeypatch.setenv('AGORA_MICROSOFT_CLIENT_SECRET', 'agora-geheim')
    monkeypatch.delenv('AGORA_MICROSOFT_TENANT', raising=False)
    fake = FakeIdP(issuer='https://login.microsoftonline.com/{tenantid}/v2.0', basis='https://login.microsoftonline.com/common/v2.0')
    monkeypatch.setattr(oidc, '_anfrage', fake)
    oidc.cache_leeren()
    yield fake
    oidc.cache_leeren()


FIRMA_TID = '11111111-2222-3333-4444-555555555555'


def ms_claims(tid, sub, email, **extra):
    return {'iss': f'https://login.microsoftonline.com/{tid}/v2.0', 'tid': tid, 'sub': sub, 'email': email, 'email_verified': None} | extra


def test_microsoft_common(ms):
    # privates Microsoft-Konto: Adresse gilt als bestätigt
    c = neu_client()
    r = durchlauf(c, ms, 'microsoft', claims=ms_claims(anmeldung.MS_PRIVAT, 'ms-1', 'privat@example.org'))
    assert r.headers['location'] == '/' and ich(c)['ich']['email'] == 'privat@example.org'
    # Firmenkonto ohne xms_edov: Adresse NICHT vertrauenswürdig (nOAuth)
    r = durchlauf(neu_client(), ms, 'microsoft', claims=ms_claims(FIRMA_TID, 'ms-2', 'chef@example.org'))
    assert grund(r) == 'email_unbestaetigt'
    # mit xms_edov (Domain im Mandanten verifiziert)
    r = durchlauf(neu_client(), ms, 'microsoft', claims=ms_claims(FIRMA_TID, 'ms-3', 'edov@example.org', xms_edov=True))
    assert r.headers['location'] == '/'
    # iss passt nicht zur tid im Token (anderer Mandant)
    r = durchlauf(neu_client(), ms, 'microsoft', claims=ms_claims(FIRMA_TID, 'ms-4', 'x@example.org', iss=f'https://login.microsoftonline.com/{anmeldung.MS_PRIVAT}/v2.0'))
    assert grund(r) == 'token'
    # tid keine GUID
    r = durchlauf(neu_client(), ms, 'microsoft', claims=ms_claims('boese', 'ms-5', 'y@example.org', iss='https://login.microsoftonline.com/boese/v2.0'))
    assert grund(r) == 'token'


# ---------------------------------------------------------------- GitHub
@pytest.fixture
def gh(monkeypatch):
    monkeypatch.setenv('AGORA_GITHUB_CLIENT_ID', 'agora-client')
    monkeypatch.setenv('AGORA_GITHUB_CLIENT_SECRET', 'agora-geheim')
    fake = FakeIdP()
    monkeypatch.setattr(oidc, '_anfrage', fake)
    yield fake


def test_github(gh):
    c = neu_client()
    url = starten(c, 'github')
    assert url.startswith('https://github.com/login/oauth/authorize?') and 'code_challenge=' in url and 'nonce=' not in url
    s, k = gh.autorisieren(url)
    assert zurueck(c, 'github', s, k).headers['location'] == '/'
    assert ich(c)['ich']['email'] == 'octo@example.org' and ich(c)['ich']['name'] == 'Octo Cat'
    # Haupt-Adresse unbestätigt → nein (auch wenn eine Neben-Adresse bestätigt ist)
    gh.github = {'user': {'id': 77, 'login': 'neu'}, 'emails': [{'email': 'haupt@example.org', 'primary': True, 'verified': False},
                                                                 {'email': 'neben@example.org', 'primary': False, 'verified': True}]}
    c2 = neu_client()
    s, k = gh.autorisieren(starten(c2, 'github'))
    assert grund(zurueck(c2, 'github', s, k)) == 'email_unbestaetigt'


# ---------------------------------------------------------------- Profil
def test_verknuepfen_aus_profil_und_trennen(idp):
    c, email, pw = konto()
    assert c.post('/api/anmeldung/google/start', json={'verknuepfen': True, 'passwort': 'falsch-falsch-1'}).status_code == 400
    assert neu_client().post('/api/anmeldung/google/start', json={'verknuepfen': True}).status_code == 401
    # andere Adresse beim Anbieter ist hier erlaubt: das Konto ist angemeldet und hat das Passwort bestätigt
    r = durchlauf(c, idp, start={'verknuepfen': True, 'passwort': pw}, claims={'sub': 's-profil', 'email': 'anders@example.org'})
    assert r.headers['location'] == '/konto?verknuepft=google#profil'
    liste = c.get('/api/konto/anmeldungen').json()['verknuepft']
    assert [x['email'] for x in liste] == ['anders@example.org']
    # gleiche Kennung an einem zweiten Konto: abgelehnt
    c2, _e2, pw2 = konto()
    r = durchlauf(c2, idp, start={'verknuepfen': True, 'passwort': pw2}, claims={'sub': 's-profil', 'email': 'anders@example.org'})
    assert grund(r) == 'schon_verknuepft'
    # Vorgang aus dem Profil, aber Rückkehr in einer anderen Sitzung → ungültig
    c3, _e3, pw3 = konto()
    s, k = idp.autorisieren(starten(c3, verknuepfen=True, passwort=pw3), claims={'sub': 's-profil3', 'email': 'p3@example.org'})
    c3.post('/api/konto/abmelden')
    assert grund(zurueck(c3, 'google', s, k)) == 'ungueltig'
    # fremde Verknüpfung löschen: 404; eigene trennen
    assert c2.delete(f'/api/konto/anmeldungen/{liste[0]["id"]}').status_code == 404
    assert c.delete(f'/api/konto/anmeldungen/{liste[0]["id"]}').json() == {'ok': True}
    assert c.get('/api/konto/anmeldungen').json()['verknuepft'] == []


def test_trennen_nur_mit_passwort(idp):
    c = neu_client()
    durchlauf(c, idp, claims={'sub': 's-trennen', 'email': 'trennen@example.org'})
    lid = c.get('/api/konto/anmeldungen').json()['verknuepft'][0]['id']
    assert c.delete(f'/api/konto/anmeldungen/{lid}').status_code == 409
    # Konto ohne Passwort löschen: mit der eigenen Adresse bestätigen
    assert c.request('DELETE', '/api/konto', json={'bestaetigung': 'falsch@example.org'}).status_code == 400
    assert c.request('DELETE', '/api/konto', json={'bestaetigung': 'trennen@example.org'}).json() == {'ok': True}
    assert not db.verbindung().execute('SELECT 1 FROM anmelde_konten WHERE sub = ?', ('s-trennen',)).fetchone()


def test_gesperrtes_konto(idp):
    c = neu_client()
    durchlauf(c, idp, claims={'sub': 's-gesperrt', 'email': 'gesperrt@example.org'})
    db.verbindung().execute("UPDATE benutzer SET gesperrt = 1 WHERE email = 'gesperrt@example.org'")
    c2 = neu_client()
    assert grund(durchlauf(c2, idp, claims={'sub': 's-gesperrt', 'email': 'gesperrt@example.org'})) == 'gesperrt'
    assert not ich(c2)['angemeldet']


def test_oidc_bausteine():
    v, ch = oidc.pkce_paar()
    assert 43 <= len(v) <= 128 and len(ch) == 43
    assert oidc.erwarteter_issuer('https://a/{tenantid}/v2.0', {'tid': FIRMA_TID}) == f'https://a/{FIRMA_TID}/v2.0'
    assert oidc.erwarteter_issuer('https://a/{tenantid}/v2.0', {'tid': '../x'}) == '\0'
    assert oidc.ja(True) and oidc.ja('true') and not oidc.ja('false') and not oidc.ja(1)
    assert oidc.gleich('ä', 'ä') and not oidc.gleich('ä', 'a')
    with pytest.raises(oidc.OidcFehler):
        oidc.url_pruefen('http://127.0.0.1/x', True)
    with pytest.raises(oidc.OidcFehler):
        oidc.url_pruefen('https://user:pw@example.org/', False)
