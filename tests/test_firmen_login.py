"""Firmen-Login (app/firmen_login.py): OIDC-IdP je Organisation – Einrichtung durch Owner, Anmeldung über die Domain,
automatischer Beitritt, Erzwingen, SSRF-Schutz der Aussteller-URL, verschlüsseltes Geheimnis und Kontoübernahme durch
einen Owner mit eigenem IdP."""
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from idp_fake import FakeIdP
from test_anmeldung import grund, ich, zurueck
from test_api import frisch, konto, link_aus, neu_client  # noqa: F401  (frisch = autouse-Fixture)

from app import anmeldung, db, firmen_login, oidc

ISS = 'https://idp.firma.example'


@pytest.fixture
def idp(monkeypatch):
    monkeypatch.setenv('AGORA_FIRMEN_LOGIN', '1')
    fake = FakeIdP(issuer=ISS, client_id='agora-firma', geheim='firma-geheim')
    monkeypatch.setattr(oidc, '_anfrage', fake)
    oidc.cache_leeren()
    db.verbindung().execute('DELETE FROM org_sso')          # jede Domain nur bei einer Organisation
    yield fake
    oidc.cache_leeren()


def org_mit_sso(domain='firma.example', **sso):
    """Owner mit Adresse in der Domain, Organisation, Firmen-Login eingerichtet → (client, email, pw, slug)."""
    c = neu_client()
    email = f'chef-{secrets.token_hex(4)}@{domain}'
    r = c.post('/api/konto/registrieren', json={'name': 'Chefin', 'email': email, 'passwort': 'chef-passwort-1'})
    assert r.status_code == 200
    c.get(link_aus(email), follow_redirects=False)
    slug = c.post('/api/orgs', json={'name': f'Firma {domain}'}).json()['org']['slug']
    body = {'issuer': ISS, 'client_id': 'agora-firma', 'client_secret': 'firma-geheim', 'domain': domain} | sso
    r = c.put(f'/api/orgs/{slug}/sso', json=body)
    assert r.status_code == 200, r.text
    return c, email, 'chef-passwort-1', slug


def firma_durchlauf(c, idp, email, start=None, **kw):
    r = c.post('/api/anmeldung/firma/start', json={'email': email} | (start or {}))
    assert r.status_code == 200, r.text
    url = r.json()['url']
    assert parse_qs(urlsplit(url).query)['login_hint'] == [email.lower()]
    state, code = idp.autorisieren(url, **kw)
    return zurueck(c, 'firma', state, code)


def test_aus_ohne_schalter(monkeypatch):
    monkeypatch.delenv('AGORA_FIRMEN_LOGIN', raising=False)
    c, _e, _p = konto()
    assert c.get('/api/einstellungen').json()['firmen_login'] is False
    assert c.get('/api/einstellungen').json()['firmen_login_an'] is False
    assert c.post('/api/anmeldung/firma/start', json={'email': 'a@firma.example'}).status_code == 404
    slug = c.post('/api/orgs', json={'name': 'Aus'}).json()['org']['slug']
    assert c.get(f'/api/orgs/{slug}/sso').status_code == 404


def test_einrichten_und_anmelden(idp):
    chef, _email, _pw, slug = org_mit_sso()
    j = chef.get(f'/api/orgs/{slug}/sso').json()
    assert j['sso']['issuer'] == ISS and j['sso']['geheimnis_gesetzt'] is True and 'client_secret' not in str(j)
    assert j['redirect_uri'] == 'https://agora.slxt.dev/api/anmeldung/firma/zurueck'
    # Geheimnis verschlüsselt, an die Organisation gebunden
    z = db.verbindung().execute('SELECT * FROM org_sso').fetchone()
    assert z['geheim'].startswith('g1.') and 'firma-geheim' not in z['geheim']
    assert firmen_login.entschluesseln(z['geheim'], z['org_id']) == 'firma-geheim'
    assert firmen_login.entschluesseln(z['geheim'], z['org_id'] + 1) is None
    assert neu_client().get('/api/einstellungen').json()['firmen_login'] is True
    # neues Konto über den Firmen-IdP → Mitglied der Organisation
    c = neu_client()
    r = firma_durchlauf(c, idp, 'Bernd@Firma.example', claims={'sub': 'f-bernd', 'email': 'bernd@firma.example', 'name': 'Bernd'})
    assert r.headers['location'] == '/'
    assert ich(c)['ich']['email'] == 'bernd@firma.example'
    orgs = c.get('/api/orgs').json()['orgs']
    assert [(o['slug'], o['rolle']) for o in orgs] == [(slug, 'mitglied')]
    assert c.get('/api/konto/anmeldungen').json()['verknuepft'][0]['anbieter'] == 'firma'
    # unbekannte Domain
    r = neu_client().post('/api/anmeldung/firma/start', json={'email': 'x@andere.example'})
    assert r.status_code == 404 and 'kein Firmen-Login' in r.json()['fehler']


def test_fremde_domain_und_unbestaetigt(idp):
    org_mit_sso()
    c = neu_client()
    r = firma_durchlauf(c, idp, 'mallory@firma.example', claims={'sub': 'f-mallory', 'email': 'mallory@fremd.example'})
    assert grund(r) == 'domain' and not ich(c)['angemeldet']
    r = firma_durchlauf(neu_client(), idp, 'u@firma.example', claims={'sub': 'f-unb', 'email': 'u@firma.example', 'email_verified': False})
    assert grund(r) == 'email_unbestaetigt'
    # ohne email_verified (z. B. Entra ID): der IdP der Organisation gilt für seine Domain
    r = firma_durchlauf(neu_client(), idp, 'ohne@firma.example', claims={'sub': 'f-ohne', 'email': 'ohne@firma.example', 'email_verified': None})
    assert r.headers['location'] == '/'


def test_owner_idp_uebernimmt_kein_fremdes_konto(idp):
    """Jemand mit Adresse in der Domain legt eine Organisation mit eigenem IdP an und behauptet dort, ein Kollege zu sein.
    Das bestehende Konto des Kollegen wird NICHT angemeldet – es fehlt sein Agora-Passwort."""
    kemail, kpw = 'kollege@firma.example', 'kollegen-passwort-1'
    kollege = neu_client()
    kollege.post('/api/konto/registrieren', json={'name': 'Kollege', 'email': kemail, 'passwort': kpw})
    kollege.get(link_aus(kemail), follow_redirects=False)
    org_mit_sso()
    angreifer = neu_client()
    r = firma_durchlauf(angreifer, idp, kemail, claims={'sub': 'f-gefaelscht', 'email': kemail})
    assert r.headers['location'].startswith('/#verknuepfen=') and not ich(angreifer)['angemeldet']
    token = r.headers['location'].split('=', 1)[1]
    assert angreifer.post('/api/anmeldung/verknuepfen', json={'token': token, 'passwort': 'geraten-geraten-1'}).status_code == 400
    assert not ich(angreifer)['angemeldet']


def test_reset_trennt_firmen_verknuepfung(idp):
    """Ein IdP legt ein Konto für eine Adresse an, die er nicht besitzt. Setzt die echte Person per Mail ein Passwort,
    ist der IdP danach draußen."""
    org_mit_sso()
    c = neu_client()
    firma_durchlauf(c, idp, 'vorab@firma.example', claims={'sub': 'f-vorab', 'email': 'vorab@firma.example'})
    echt = neu_client()
    echt.post('/api/konto/passwort-vergessen', json={'email': 'vorab@firma.example'})
    token = parse_qs(urlsplit(link_aus('vorab@firma.example')).query)['t'][0]
    assert echt.post('/api/konto/passwort-setzen', json={'token': token, 'passwort': 'echtes-passwort-1'}).status_code == 200
    assert not ich(c)['angemeldet']                      # alte Sitzung des IdP-Kontos beendet
    c2 = neu_client()
    r = firma_durchlauf(c2, idp, 'vorab@firma.example', claims={'sub': 'f-vorab', 'email': 'vorab@firma.example'})
    assert r.headers['location'].startswith('/#verknuepfen=') and not ich(c2)['angemeldet']


def test_erzwingen(idp):
    chef, cemail, cpw, slug = org_mit_sso(erzwingen=True)
    # Mitglied mit Passwort-Konto in der Domain
    m, memail, mpw = neu_client(), 'mitglied@firma.example', 'mitglied-passwort-1'
    m.post('/api/konto/registrieren', json={'name': 'M', 'email': memail, 'passwort': mpw})
    m.get(link_aus(memail), follow_redirects=False)
    db.verbindung().execute("INSERT INTO org_mitglieder SELECT o.id, b.id, 'mitglied', 0 FROM orgs o, benutzer b WHERE o.slug = ? AND b.email = ?",
                            (slug, memail))
    r = neu_client().post('/api/konto/anmelden', json={'email': memail, 'passwort': mpw})
    assert r.status_code == 403 and 'Firmen-Login' in r.json()['fehler']
    # Owner bleibt Notzugang
    assert neu_client().post('/api/konto/anmelden', json={'email': cemail, 'passwort': cpw}).status_code == 200
    # Firmen-Login: bestehendes Konto mit Passwort verknüpfen geht trotz „erzwingen“
    c = neu_client()
    r = firma_durchlauf(c, idp, memail, claims={'sub': 'f-mitglied', 'email': memail})
    token = r.headers['location'].split('=', 1)[1]
    assert c.post('/api/anmeldung/verknuepfen', json={'token': token, 'passwort': mpw}).status_code == 200
    # danach direkt über den Firmen-IdP
    c2 = neu_client()
    assert firma_durchlauf(c2, idp, memail, claims={'sub': 'f-mitglied', 'email': memail}).headers['location'] == '/'
    # Erzwingen aus → Passwort geht wieder
    assert chef.put(f'/api/orgs/{slug}/sso', json={'issuer': ISS, 'client_id': 'agora-firma', 'domain': 'firma.example',
                                                   'erzwingen': False}).status_code == 200
    assert neu_client().post('/api/konto/anmelden', json={'email': memail, 'passwort': mpw}).status_code == 200


def test_erzwingen_sperrt_google(idp, monkeypatch):
    """Erzwungener Firmen-Login gilt auch für Google & Co."""
    _chef, _ce, _cp, slug = org_mit_sso(erzwingen=True)
    c = neu_client()
    firma_durchlauf(c, idp, 'g@firma.example', claims={'sub': 'f-g', 'email': 'g@firma.example'})     # Mitglied (auto)
    assert anmeldung.regel_verstoss(db.verbindung().execute("SELECT * FROM benutzer WHERE email = 'g@firma.example'").fetchone(),
                                    {'name': 'Google'}) == firmen_login.PFLICHT


def test_einrichten_rechte_und_regeln(idp):
    chef, _e, _p, slug = org_mit_sso()
    # Domain nur einmal
    andere, _e2, _p2 = konto()
    s2 = andere.post('/api/orgs', json={'name': 'Andere'}).json()['org']['slug']
    body = {'issuer': ISS, 'client_id': 'x', 'client_secret': 'y', 'domain': 'example.org'}
    assert andere.put(f'/api/orgs/{s2}/sso', json=body).status_code == 200      # eigene Domain (example.org) geht
    # fremde Domain (nicht die eigene Adresse), Freemail, schon vergeben
    assert andere.put(f'/api/orgs/{s2}/sso', json=body | {'domain': 'firma.example'}).status_code == 400
    assert andere.put(f'/api/orgs/{s2}/sso', json=body | {'domain': 'gmail.com'}).status_code == 400
    # Mitglieder/Admins/Fremde dürfen nicht
    m, _me, _mp = konto()
    assert m.get(f'/api/orgs/{slug}/sso').status_code == 404
    assert m.put(f'/api/orgs/{slug}/sso', json=body).status_code == 404
    assert m.delete(f'/api/orgs/{slug}/sso').status_code == 404
    # ohne Geheimnis beim ersten Mal: 400; beim Ändern bleibt das alte
    assert chef.put(f'/api/orgs/{slug}/sso', json={'issuer': ISS, 'client_id': 'agora-firma', 'domain': 'firma.example', 'aktiv': True}).status_code == 200
    z = db.verbindung().execute('SELECT * FROM org_sso WHERE domain = ?', ('firma.example',)).fetchone()
    assert firmen_login.entschluesseln(z['geheim'], z['org_id']) == 'firma-geheim'
    # ungültige Werte
    for falsch in ({'issuer': 'https://idp.firma.example?x=1'}, {'issuer': 'https://u:p@idp.firma.example'}, {'issuer': 'ftp://idp'},
                   {'issuer': 'http://idp.firma.example'}, {'client_id': ''}, {'auto_beitritt': 'ja'}):
        r = chef.put(f'/api/orgs/{slug}/sso', json={'issuer': ISS, 'client_id': 'agora-firma', 'domain': 'firma.example'} | falsch)
        assert r.status_code == 400, falsch
    # Discovery mit anderem Aussteller → abgelehnt
    idp.discovery_issuer = 'https://boese.example'
    oidc.cache_leeren()
    assert chef.put(f'/api/orgs/{slug}/sso', json={'issuer': ISS, 'client_id': 'agora-firma', 'domain': 'firma.example'}).status_code == 400
    # entfernen
    idp.discovery_issuer = ISS
    assert chef.delete(f'/api/orgs/{slug}/sso').json() == {'ok': True}
    assert neu_client().post('/api/anmeldung/firma/start', json={'email': 'a@firma.example'}).status_code == 404
    assert andere.delete(f'/api/orgs/{s2}/sso').json() == {'ok': True}


def test_ssrf_aussteller(monkeypatch):
    """Aussteller-URL ins eigene Netz: wird nicht abgefragt (echte Prüfung, kein Fake)."""
    monkeypatch.setenv('AGORA_FIRMEN_LOGIN', '1')
    oidc.cache_leeren()
    c = neu_client()
    r = c.post('/api/konto/registrieren', json={'name': 'S', 'email': 'ssrf@intern.example', 'passwort': 'ssrf-passwort-1'})
    c.get(link_aus('ssrf@intern.example'), follow_redirects=False)
    slug = c.post('/api/orgs', json={'name': 'SSRF'}).json()['org']['slug']
    for ziel in ('https://127.0.0.1', 'https://localhost:8110', 'https://169.254.169.254', 'https://[::1]', 'https://10.0.0.1',
                 'https://[64:ff9b::7f00:1]'):
        r = c.put(f'/api/orgs/{slug}/sso', json={'issuer': ziel, 'client_id': 'x', 'client_secret': 'y', 'domain': 'intern.example'})
        assert r.status_code == 400, ziel
    assert not db.verbindung().execute('SELECT 1 FROM org_sso').fetchone()


def test_org_loeschen_entfernt_sso(idp):
    chef, _e, _p, slug = org_mit_sso()
    firma_durchlauf(neu_client(), idp, 'weg@firma.example', claims={'sub': 'f-weg', 'email': 'weg@firma.example'})
    oid = db.verbindung().execute('SELECT id FROM orgs WHERE slug = ?', (slug,)).fetchone()['id']
    assert db.verbindung().execute('SELECT 1 FROM anmelde_konten WHERE org_id = ?', (oid,)).fetchone()
    assert chef.request('DELETE', f'/api/orgs/{slug}', json={'bestaetigung': slug}).status_code == 200
    assert not db.verbindung().execute('SELECT 1 FROM org_sso WHERE org_id = ?', (oid,)).fetchone()
    assert not db.verbindung().execute('SELECT 1 FROM anmelde_konten WHERE org_id = ?', (oid,)).fetchone()
