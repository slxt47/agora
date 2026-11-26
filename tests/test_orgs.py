"""Teams / Organisationen: anlegen, einladen (Link, annehmen/ablehnen), Rollen, Räume der Organisation, Vorlagen,
Aufnahmen mit eigenem Kontingent, Einstellungen (Rechte, Warteraum für Externe, Domain), Austritt, Löschen,
API-Schlüssel für eine Organisation – und vor allem: keine Organisation kommt an die Daten einer anderen."""
import re

import pytest

from test_api import beitreten, frisch, konto, mails_an, neu_client, raum, zugang  # noqa: F401

from app import db


def org(c, name='Firma Eins', **kw):
    r = c.post('/api/orgs', json={'name': name, **kw})
    assert r.status_code == 200, r.text
    return r.json()['org']


def ich(c):
    return c.get('/api/konto/ich').json()['ich']


def einladen_und_annehmen(owner, slug, gast_c, gast_email, rolle='mitglied'):
    r = owner.post(f'/api/orgs/{slug}/einladungen', json={'emails': [gast_email], 'rolle': rolle})
    assert r.status_code == 200 and r.json()['gesendet'] == 1, r.text
    text = mails_an(gast_email)[-1][2]
    token = re.search(r'einladung=([\w-]+)', text).group(1)
    e = gast_c.post('/api/org-einladungen/link', json={'token': token})
    assert e.status_code == 200, e.text
    r = gast_c.post(f'/api/org-einladungen/{e.json()["einladung"]["id"]}/annehmen')
    assert r.status_code == 200 and r.json()['org']['rolle'] == rolle, r.text
    return token


@pytest.fixture
def team():
    """owner + admin + mitglied in „Firma Eins“ und ein fremdes Konto mit eigener Organisation „Firma Zwei“."""
    owner, oe, _ = konto(name='Olga Owner')
    o = org(owner)
    adm, ae, _ = konto(name='Adam Admin')
    einladen_und_annehmen(owner, o['slug'], adm, ae, 'admin')
    mit, me, _ = konto(name='Mia Mitglied')
    einladen_und_annehmen(owner, o['slug'], mit, me)
    fremd, fe, _ = konto(name='Fritz Fremd')
    o2 = org(fremd, 'Firma Zwei')
    return {'owner': owner, 'admin': adm, 'mitglied': mit, 'fremd': fremd, 'slug': o['slug'], 'slug2': o2['slug'],
            'emails': {'owner': oe, 'admin': ae, 'mitglied': me, 'fremd': fe}}


# ---------------------------------------------------------------- Anlegen, Liste, Kurzname
def test_anlegen_und_liste():
    c, _e, _p = konto()
    o = org(c, 'Müller & Söhne', farbe='#0E8A7A')
    assert o['slug'] == 'mueller-soehne' and o['rolle'] == 'owner' and o['farbe'] == '#0e8a7a'
    assert o['kontingent'] == 20 * 1024 ** 3 and o['belegt'] == 0
    assert o['einstellungen'] == {'rechte': {'chat': True, 'bildschirm': True, 'selbst_unmute': True, 'umbenennen': True,
                                             'mehrfach_freigabe': False}, 'warteraum_extern': False, 'domain': None}
    zweite = org(c, 'Müller & Söhne')                     # gleicher Name → eindeutiger Kurzname
    assert zweite['slug'] != o['slug'] and zweite['slug'].startswith('mueller-soehne-')
    assert c.post('/api/orgs', json={'name': 'X', 'slug': 'abc'}).status_code == 400
    assert c.post('/api/orgs', json={'name': 'Team', 'slug': 'Böse Slug'}).status_code == 400
    assert c.post('/api/orgs', json={'name': 'Team', 'slug': o['slug']}).status_code == 409
    assert c.post('/api/orgs', json={'name': 'Team', 'farbe': 'rot'}).status_code == 400
    l = c.get('/api/orgs').json()
    assert {x['slug'] for x in l['orgs']} == {o['slug'], zweite['slug']} and l['einladungen'] == [] and l['beitretbar'] == []
    assert neu_client().get('/api/orgs').status_code == 401


def test_fremde_sehen_nichts(team):
    f = team['fremd']
    s = team['slug']
    for p in (f'/api/orgs/{s}', f'/api/orgs/{s}/raeume', f'/api/orgs/{s}/vorlagen', f'/api/orgs/{s}/aufnahmen', f'/api/orgs/{s}/einladungen',
              '/api/orgs/gibt-es-nicht'):
        r = f.get(p)
        assert r.status_code == 404 and r.json()['fehler'] == 'Diese Organisation gibt es nicht.', p
    for m, p, j in (('PATCH', f'/api/orgs/{s}', {'name': 'Übernommen'}), ('DELETE', f'/api/orgs/{s}', {'bestaetigung': s}),
                    ('POST', f'/api/orgs/{s}/einladungen', {'emails': ['x@example.org']}), ('POST', f'/api/orgs/{s}/raeume', {}),
                    ('POST', f'/api/orgs/{s}/vorlagen', {'name': 'x'}), ('POST', f'/api/orgs/{s}/verlassen', None),
                    ('POST', f'/api/orgs/{s}/beitreten', None), ('PATCH', f'/api/orgs/{s}/mitglieder/1', {'rolle': 'owner'}),
                    ('DELETE', f'/api/orgs/{s}/mitglieder/1', None)):
        r = f.request(m, p, json=j)
        assert r.status_code == 404, (m, p, r.status_code)
    assert team['owner'].get(f'/api/orgs/{s}').json()['org']['name'] == 'Firma Eins'


# ---------------------------------------------------------------- Einladungen
def test_einladung_link_nur_fuer_eingeladene_adresse():
    owner, _e, _p = konto(name='Olga')
    o = org(owner)
    gast, ge, _ = konto(name='Gerd')
    andere, _ae, _ = konto(name='Andere')
    r = owner.post(f'/api/orgs/{o["slug"]}/einladungen', json={'emails': [ge, ge.upper()]})
    assert r.json() == {'ok': True, 'gesendet': 1, 'schon_mitglied': 0}
    m = mails_an(ge.lower())[-1]
    assert 'Firma Eins' in m[1] and '/konto?einladung=' in m[2]
    token = re.search(r'einladung=([\w-]+)', m[2]).group(1)
    r = andere.post('/api/org-einladungen/link', json={'token': token})
    assert r.status_code == 403 and 'andere E-Mail-Adresse' in r.json()['fehler']
    assert andere.post('/api/org-einladungen/link', json={'token': 'x' * 40}).status_code == 404
    e = gast.get('/api/orgs').json()['einladungen']
    assert len(e) == 1 and e[0]['org']['slug'] == o['slug'] and e[0]['rolle'] == 'mitglied' and e[0]['von_name'] == 'Olga'
    assert andere.post(f'/api/org-einladungen/{e[0]["id"]}/annehmen').status_code == 404      # fremde ID
    assert gast.post(f'/api/org-einladungen/{e[0]["id"]}/ablehnen').json() == {'ok': True}
    assert gast.get('/api/orgs').json()['einladungen'] == []
    assert gast.get(f'/api/orgs/{o["slug"]}').status_code == 404
    # offene Einladungen sieht der admin, zurückziehen
    owner.post(f'/api/orgs/{o["slug"]}/einladungen', json={'emails': ['neu@example.org']})
    liste = owner.get(f'/api/orgs/{o["slug"]}/einladungen').json()['einladungen']
    assert [x['email'] for x in liste] == ['neu@example.org']
    assert owner.request('DELETE', f'/api/orgs/{o["slug"]}/einladungen/{liste[0]["id"]}').json() == {'ok': True}
    assert owner.get(f'/api/orgs/{o["slug"]}/einladungen').json()['einladungen'] == []


def test_einladen_rechte(team):
    s = team['slug']
    assert team['mitglied'].post(f'/api/orgs/{s}/einladungen', json={'emails': ['a@example.org']}).status_code == 403
    assert team['admin'].post(f'/api/orgs/{s}/einladungen', json={'emails': ['a@example.org'], 'rolle': 'admin'}).status_code == 403
    assert team['admin'].post(f'/api/orgs/{s}/einladungen', json={'emails': ['a@example.org']}).status_code == 200
    assert team['owner'].post(f'/api/orgs/{s}/einladungen', json={'emails': ['a@example.org'], 'rolle': 'owner'}).status_code == 400
    r = team['owner'].post(f'/api/orgs/{s}/einladungen', json={'emails': [team['emails']['mitglied']]})
    assert r.json()['schon_mitglied'] == 1 and r.json()['gesendet'] == 0
    assert team['owner'].post(f'/api/orgs/{s}/einladungen', json={'emails': ['kaputt']}).status_code == 400
    # Einladung einer anderen Organisation lässt sich nicht über diese zurückziehen
    team['fremd'].post(f'/api/orgs/{team["slug2"]}/einladungen', json={'emails': ['z@example.org']})
    eid = team['fremd'].get(f'/api/orgs/{team["slug2"]}/einladungen').json()['einladungen'][0]['id']
    assert team['owner'].request('DELETE', f'/api/orgs/{s}/einladungen/{eid}').status_code == 404


def test_mitglieder_sichtbarkeit(team):
    s = team['slug']
    als_mitglied = team['mitglied'].get(f'/api/orgs/{s}').json()['org']
    assert {m['rolle'] for m in als_mitglied['mitglieder']} == {'owner', 'admin', 'mitglied'}
    assert all('email' not in m for m in als_mitglied['mitglieder'])           # E-Mails nur für owner/admin
    assert all('email' in m for m in team['admin'].get(f'/api/orgs/{s}').json()['org']['mitglieder'])


# ---------------------------------------------------------------- Räume der Organisation
def test_org_raeume_verwalten(team):
    s = team['slug']
    assert team['mitglied'].post(f'/api/orgs/{s}/raeume', json={'titel': 'x'}).status_code == 403
    r = team['owner'].post(f'/api/orgs/{s}/raeume', json={'titel': 'Teamrunde', 'warteraum': True})
    assert r.status_code == 200, r.text
    rm = r.json()['raum']
    assert rm['org'] == {'slug': s, 'name': 'Firma Eins', 'farbe': rm['org']['farbe'], 'rolle': 'owner'} and rm['ist_host']
    code = rm['code']
    # nicht in der eigenen Liste, aber in der Liste der Organisation – für alle Mitglieder
    assert all(x['code'] != code for x in team['owner'].get('/api/raeume').json()['geplant'])
    for wer in ('owner', 'admin', 'mitglied'):
        liste = team[wer].get(f'/api/orgs/{s}/raeume').json()['geplant']
        assert [x['code'] for x in liste] == [code]
        assert liste[0]['ist_host'] is (wer != 'mitglied')
    # jeder admin verwaltet, Mitglieder und Fremde nicht
    assert team['admin'].patch(f'/api/raeume/{code}', json={'titel': 'Neu'}).json()['raum']['titel'] == 'Neu'
    for wer in ('mitglied', 'fremd'):
        assert team[wer].patch(f'/api/raeume/{code}', json={'titel': 'x'}).status_code == 403
        assert team[wer].request('DELETE', f'/api/raeume/{code}').status_code == 403
        assert team[wer].post(f'/api/raeume/{code}/einladen', json={'emails': ['a@example.org']}).status_code == 403
    # admin, der den Raum nicht angelegt hat, ist Host; Mitglied ist Teilnehmer (ohne Warteraum? nein: Raum hat Warteraum)
    assert zugang(team['admin'], code)['rolle'] == 'host'
    r = beitreten(team['mitglied'], code)
    assert r.json()['status'] == 'wartet'
    # fremder admin einer anderen Organisation ist hier nur Gast
    assert beitreten(team['fremd'], code).json()['status'] == 'wartet'
    assert team['admin'].request('DELETE', f'/api/raeume/{code}').json() == {'ok': True}


def test_standard_rechte_und_warteraum_fuer_externe(team):
    s = team['slug']
    r = team['admin'].patch(f'/api/orgs/{s}', json={'einstellungen': {'rechte': {'chat': False, 'bildschirm': False}, 'warteraum_extern': True}})
    assert r.status_code == 200 and r.json()['org']['einstellungen']['rechte']['chat'] is False
    assert team['mitglied'].patch(f'/api/orgs/{s}', json={'name': 'x'}).status_code == 403
    assert team['admin'].patch(f'/api/orgs/{s}', json={'einstellungen': {'rechte': {'gibts': True}}}).status_code == 400
    code = team['admin'].post(f'/api/orgs/{s}/raeume', json={'titel': 'Mit Vorgaben'}).json()['raum']['code']
    zeile = db.verbindung().execute('SELECT rechte, rechte_standard FROM raeume WHERE code = ?', (code,)).fetchone()
    assert '"chat": false' in zeile['rechte'] and zeile['rechte'] == zeile['rechte_standard']
    # Raum ohne eigenen Warteraum: Mitglieder direkt, Gäste und Fremde warten
    assert zugang(team['mitglied'], code)['rolle'] == 'teilnehmer'
    assert beitreten(neu_client(), code, name='Gast').json()['status'] == 'wartet'
    assert beitreten(team['fremd'], code).json()['status'] == 'wartet'
    team['admin'].patch(f'/api/orgs/{s}', json={'einstellungen': {'warteraum_extern': False}})
    assert beitreten(neu_client(), code, name='Gast 2').json()['status'] == 'zugelassen'


def test_domain_selbst_beitritt(team):
    s = team['slug']
    dom = team['emails']['owner'].split('@')[1]          # example.org (Testadressen)
    assert team['owner'].patch(f'/api/orgs/{s}', json={'einstellungen': {'domain': 'firma.de'}}).status_code == 400   # nicht die eigene
    assert team['owner'].patch(f'/api/orgs/{s}', json={'einstellungen': {'domain': 'gmail.com'}}).status_code == 400
    assert team['owner'].patch(f'/api/orgs/{s}', json={'einstellungen': {'domain': 'nicht gültig'}}).status_code == 400
    assert team['owner'].patch(f'/api/orgs/{s}', json={'einstellungen': {'domain': dom.upper()}}).json()['org']['einstellungen']['domain'] == dom
    neu, _e, _p = konto(name='Neu')
    l = neu.get('/api/orgs').json()['beitretbar']
    assert [x['slug'] for x in l] == [s]
    r = neu.post(f'/api/orgs/{s}/beitreten')
    assert r.status_code == 200 and r.json()['org']['rolle'] == 'mitglied'
    assert neu.get('/api/orgs').json()['beitretbar'] == []
    # Fremde Organisation ohne Domain: kein Selbst-Beitritt
    assert neu.post(f'/api/orgs/{team["slug2"]}/beitreten').status_code == 404


# ---------------------------------------------------------------- Rollen, Austritt, Entfernen
def test_rollen_und_entfernen(team):
    s = team['slug']
    ids = {m['name']: m['id'] for m in team['owner'].get(f'/api/orgs/{s}').json()['org']['mitglieder']}
    admin_id, mitglied_id, owner_id = ids['Adam Admin'], ids['Mia Mitglied'], ids['Olga Owner']
    # nur owner ändern Rollen
    assert team['admin'].patch(f'/api/orgs/{s}/mitglieder/{mitglied_id}', json={'rolle': 'admin'}).status_code == 403
    assert team['owner'].patch(f'/api/orgs/{s}/mitglieder/{owner_id}', json={'rolle': 'admin'}).status_code == 409   # letzter owner
    assert team['owner'].patch(f'/api/orgs/{s}/mitglieder/{mitglied_id}', json={'rolle': 'chef'}).status_code == 400
    # admin entfernt keine admins/owner, aber Mitglieder
    assert team['admin'].request('DELETE', f'/api/orgs/{s}/mitglieder/{owner_id}').status_code == 403
    assert team['admin'].request('DELETE', f'/api/orgs/{s}/mitglieder/{admin_id}').status_code == 400       # sich selbst
    # ID eines Kontos aus einer anderen Organisation
    fremd_id = ich(team['fremd'])['id']
    assert team['owner'].request('DELETE', f'/api/orgs/{s}/mitglieder/{fremd_id}').status_code == 404
    assert team['owner'].patch(f'/api/orgs/{s}/mitglieder/{fremd_id}', json={'rolle': 'mitglied'}).status_code == 404
    # das Mitglied hat einen eigenen Raum – der bleibt nach dem Entfernen bei ihm
    eigen = raum(team['mitglied'], titel='Privat')
    r = team['admin'].request('DELETE', f'/api/orgs/{s}/mitglieder/{mitglied_id}')
    assert r.status_code == 200 and all(m['id'] != mitglied_id for m in r.json()['org']['mitglieder'])
    assert team['mitglied'].get(f'/api/orgs/{s}').status_code == 404
    assert team['mitglied'].get(f'/api/raeume/{eigen["code"]}').json()['raum']['ist_host']
    assert team['mitglied'].get('/api/orgs').json()['orgs'] == []


def test_verlassen_uebergibt_daten_der_organisation(team):
    s = team['slug']
    code = team['admin'].post(f'/api/orgs/{s}/raeume', json={'titel': 'Vom Admin'}).json()['raum']['code']
    team['admin'].post(f'/api/orgs/{s}/vorlagen', json={'name': 'Standup'})
    eigen = raum(team['admin'], titel='Admins eigener Raum')
    assert team['owner'].post(f'/api/orgs/{s}/verlassen').status_code == 409          # letzter owner
    assert team['admin'].post(f'/api/orgs/{s}/verlassen').json() == {'ok': True}
    owner_id = ich(team['owner'])['id']
    zeile = db.verbindung().execute('SELECT besitzer_id, org_id FROM raeume WHERE code = ?', (code,)).fetchone()
    assert zeile['besitzer_id'] == owner_id and zeile['org_id']
    assert db.verbindung().execute("SELECT benutzer_id FROM vorlagen WHERE name = 'Standup'").fetchone()[0] == owner_id
    # der eigene Raum bleibt beim ehemaligen admin; den Raum der Organisation verwaltet er nicht mehr
    assert team['admin'].get(f'/api/raeume/{eigen["code"]}').json()['raum']['ist_host']
    assert team['admin'].patch(f'/api/raeume/{code}', json={'titel': 'x'}).status_code == 403
    assert team['owner'].patch(f'/api/raeume/{code}', json={'titel': 'Bleibt'}).status_code == 200


def test_konto_loeschen_uebergibt_oder_loescht():
    owner, _e, pw = konto(name='Olga')
    o = org(owner)
    mit, me, _ = konto(name='Mia')
    einladen_und_annehmen(owner, o['slug'], mit, me)
    code = owner.post(f'/api/orgs/{o["slug"]}/raeume', json={'titel': 'Bleibt'}).json()['raum']['code']
    allein = org(owner, 'Nur ich')
    allein_code = owner.post(f'/api/orgs/{allein["slug"]}/raeume', json={'titel': 'Weg'}).json()['raum']['code']
    assert owner.request('DELETE', '/api/konto', json={'passwort': pw}).json() == {'ok': True}
    # Mia ist jetzt owner, der Raum der Organisation bleibt
    info = mit.get(f'/api/orgs/{o["slug"]}').json()['org']
    assert info['rolle'] == 'owner' and [m['name'] for m in info['mitglieder']] == ['Mia']
    assert mit.patch(f'/api/raeume/{code}', json={'titel': 'Weiter'}).status_code == 200
    # die Organisation, in der nur Olga war, ist weg – samt Raum
    assert db.verbindung().execute('SELECT 1 FROM orgs WHERE slug = ?', (allein['slug'],)).fetchone() is None
    assert neu_client().get(f'/api/raeume/{allein_code}').status_code == 404


def test_organisation_loeschen(team):
    s = team['slug']
    code = team['owner'].post(f'/api/orgs/{s}/raeume', json={'titel': 'x'}).json()['raum']['code']
    zugang(team['owner'], code)
    assert team['admin'].request('DELETE', f'/api/orgs/{s}', json={'bestaetigung': s}).status_code == 403
    assert team['owner'].request('DELETE', f'/api/orgs/{s}', json={'bestaetigung': 'falsch'}).status_code == 400
    assert team['owner'].request('DELETE', f'/api/orgs/{s}', json={'bestaetigung': s}).json() == {'ok': True}
    assert neu_client().get(f'/api/raeume/{code}').status_code == 404
    assert team['mitglied'].get('/api/orgs').json()['orgs'] == []
    assert team['fremd'].get(f'/api/orgs/{team["slug2"]}').status_code == 200            # andere Organisation unberührt


# ---------------------------------------------------------------- Vorlagen
def test_gemeinsame_vorlagen(team):
    s = team['slug']
    assert team['mitglied'].post(f'/api/orgs/{s}/vorlagen', json={'name': 'x'}).status_code == 403
    v = team['admin'].post(f'/api/orgs/{s}/vorlagen', json={'name': 'Weekly', 'einstellungen': {'warteraum': True, 'titel': 'Weekly'}})
    assert v.status_code == 200, v.text
    vid = v.json()['vorlage']['id']
    assert team['admin'].get('/api/vorlagen').json()['vorlagen'] == []                     # nicht in den eigenen
    assert [x['name'] for x in team['mitglied'].get(f'/api/orgs/{s}/vorlagen').json()['vorlagen']] == ['Weekly']
    # Mitglied: eigenes Meeting aus der Vorlage ja, Raum der Organisation nein
    assert team['mitglied'].post(f'/api/orgs/{s}/vorlagen/{vid}/raum', json={'ziel': 'org'}).status_code == 403
    r = team['mitglied'].post(f'/api/orgs/{s}/vorlagen/{vid}/raum', json={'ziel': 'konto'}).json()['raum']
    assert r['titel'] == 'Weekly' and r['warteraum'] and 'org' not in r and r['ist_host']
    r = team['admin'].post(f'/api/orgs/{s}/vorlagen/{vid}/raum', json={}).json()['raum']
    assert r['org']['slug'] == s
    # persönliche Vorlagen-Endpunkte erreichen gemeinsame Vorlagen nicht
    for m, p in (('PUT', f'/api/vorlagen/{vid}'), ('DELETE', f'/api/vorlagen/{vid}'), ('POST', f'/api/vorlagen/{vid}/raum')):
        assert team['admin'].request(m, p, json={'name': 'x'}).status_code == 404
    # fremde Organisation kommt nicht an die Vorlage (auch nicht über die eigene Organisation)
    for m, p in (('PUT', f'/api/orgs/{team["slug2"]}/vorlagen/{vid}'), ('DELETE', f'/api/orgs/{team["slug2"]}/vorlagen/{vid}'),
                 ('POST', f'/api/orgs/{team["slug2"]}/vorlagen/{vid}/raum')):
        assert team['fremd'].request(m, p, json={'name': 'x'}).status_code == 404
    assert team['admin'].put(f'/api/orgs/{s}/vorlagen/{vid}', json={'name': 'Wöchentlich'}).json()['vorlage']['name'] == 'Wöchentlich'
    assert team['mitglied'].request('DELETE', f'/api/orgs/{s}/vorlagen/{vid}').status_code == 403
    assert team['admin'].request('DELETE', f'/api/orgs/{s}/vorlagen/{vid}').json() == {'ok': True}


# ---------------------------------------------------------------- Aufnahmen
def hochladen(c, code, groesse=1000):
    return c.post('/api/aufnahmen', content=b'\x1aE\xdf\xa3' + b'\0' * groesse, headers={'Content-Type': 'video/webm', 'X-Raum': code})


def test_aufnahmen_der_organisation(team):
    s = team['slug']
    code = team['admin'].post(f'/api/orgs/{s}/raeume', json={'titel': 'Aufnahme'}).json()['raum']['code']
    r = hochladen(team['admin'], code)
    assert r.status_code == 200, r.text
    aid = r.json()['aufnahme']['id']
    assert team['admin'].get('/api/aufnahmen').json()['aufnahmen'] == []              # nicht in der eigenen Bibliothek
    assert team['admin'].get(f'/api/aufnahmen/{aid}/datei').status_code == 404
    l = team['mitglied'].get(f'/api/orgs/{s}/aufnahmen').json()
    assert [a['id'] for a in l['aufnahmen']] == [aid] and l['belegt'] == 1004 and l['kontingent'] == 20 * 1024 ** 3
    assert team['mitglied'].get(f'/api/orgs/{s}/aufnahmen/{aid}/datei').status_code == 200
    assert team['mitglied'].request('DELETE', f'/api/orgs/{s}/aufnahmen/{aid}').status_code == 403
    assert team['fremd'].get(f'/api/orgs/{s}/aufnahmen/{aid}/datei').status_code == 404
    assert team['fremd'].get(f'/api/orgs/{team["slug2"]}/aufnahmen/{aid}/datei').status_code == 404
    assert team['fremd'].request('DELETE', f'/api/orgs/{team["slug2"]}/aufnahmen/{aid}').status_code == 404
    # Kontingent der Organisation
    db.verbindung().execute('UPDATE orgs SET kontingent = 1500 WHERE slug = ?', (s,))
    r = hochladen(team['admin'], code)
    assert r.status_code == 507 and 'Organisation' in r.json()['fehler']
    assert hochladen(team['admin'], 'abc-defg-hij').status_code == 200                   # eigene Bibliothek unberührt
    # Ein Fremder, der den Code kennt, lädt nicht in die Bibliothek der Organisation
    r = hochladen(team['fremd'], code)
    assert r.status_code == 200 and team['fremd'].get('/api/aufnahmen').json()['aufnahmen'][0]['id'] == r.json()['aufnahme']['id']
    assert team['owner'].request('DELETE', f'/api/orgs/{s}/aufnahmen/{aid}').json() == {'ok': True}


# ---------------------------------------------------------------- API-Schlüssel für eine Organisation
def test_api_schluessel_fuer_organisation(team):
    s = team['slug']
    persoenlich_code = raum(team['admin'], titel='Privat')['code']
    org_code = team['owner'].post(f'/api/orgs/{s}/raeume', json={'titel': 'Gehört der Org'}).json()['raum']['code']
    neu = lambda c, **kw: c.post('/api/konto/api-schluessel', json={'name': 'k', 'rechte': ['meetings:lesen', 'meetings:schreiben'],  # noqa: E731
                                                                   'gueltig_tage': 30, **kw})
    assert neu(team['mitglied'], org=s).status_code == 403
    assert neu(team['fremd'], org=s).status_code == 404
    assert team['admin'].post('/api/konto/api-schluessel', json={'name': 'k', 'rechte': ['webhooks'], 'gueltig_tage': 30, 'org': s}).status_code == 400
    r = neu(team['admin'], org=s)
    assert r.status_code == 200 and r.json()['schluessel']['org'] == {'slug': s, 'name': 'Firma Eins'}
    bear = {'Authorization': 'Bearer ' + r.json()['token']}
    api = neu_client()
    codes = [m['code'] for m in api.get('/api/v1/meetings', headers=bear).json()['meetings']]
    assert codes == [org_code]
    assert api.get(f'/api/v1/meetings/{persoenlich_code}', headers=bear).status_code == 404
    m = api.post('/api/v1/meetings', json={'titel': 'Per API'}, headers=bear)
    assert m.status_code == 201
    neu_code = m.json()['meeting']['code']
    assert team['mitglied'].get(f'/api/orgs/{s}/raeume').json()['geplant'][0]['code'] in (neu_code, org_code)
    assert db.verbindung().execute('SELECT org_id FROM raeume WHERE code = ?', (neu_code,)).fetchone()[0]
    # persönlicher Schlüssel sieht die Räume der Organisation nicht
    p = neu(team['owner']).json()['token']
    assert org_code not in [x['code'] for x in api.get('/api/v1/meetings', headers={'Authorization': 'Bearer ' + p}).json()['meetings']]
    assert api.get(f'/api/v1/meetings/{org_code}', headers={'Authorization': 'Bearer ' + p}).status_code == 404
    # Herabstufung zum Mitglied → Schlüssel der Organisation weg
    admin_id = ich(team['admin'])['id']
    team['owner'].patch(f'/api/orgs/{s}/mitglieder/{admin_id}', json={'rolle': 'mitglied'})
    assert api.get('/api/v1/meetings', headers=bear).status_code == 401


def test_api_schluessel_nach_austritt_ungueltig(team):
    s = team['slug']
    token = team['admin'].post('/api/konto/api-schluessel', json={'name': 'k', 'rechte': ['meetings:lesen'], 'gueltig_tage': 30,
                                                                   'org': s}).json()['token']
    team['admin'].post(f'/api/orgs/{s}/verlassen')
    assert neu_client().get('/api/v1/meetings', headers={'Authorization': 'Bearer ' + token}).status_code == 401


def test_schluessel_verliert_rechte_ohne_loeschen(team):
    """Fällt die Rolle weg, ohne dass der Schlüssel gelöscht wurde (z. B. alte Daten), gilt er nicht mehr: 403."""
    s = team['slug']
    token = team['admin'].post('/api/konto/api-schluessel', json={'name': 'k', 'rechte': ['meetings:lesen'], 'gueltig_tage': 30,
                                                                   'org': s}).json()['token']
    db.verbindung().execute("UPDATE org_mitglieder SET rolle = 'mitglied' WHERE benutzer_id = ?", (ich(team['admin'])['id'],))
    r = neu_client().get('/api/v1/meetings', headers={'Authorization': 'Bearer ' + token})
    assert r.status_code == 403


# ---------------------------------------------------------------- Export, Admin
def test_export_und_admin(team, monkeypatch):
    s = team['slug']
    team['owner'].post(f'/api/orgs/{s}/einladungen', json={'emails': [team['emails']['fremd']]})
    ex = team['fremd'].get('/api/konto/export').json()
    assert [o['slug'] for o in ex['organisationen']] == [team['slug2']] and ex['organisationen'][0]['rolle'] == 'owner'
    assert ex['organisationen_einladungen'][0]['organisation'] == 'Firma Eins'
    assert team['owner'].get('/api/admin/orgs').status_code == 404
    monkeypatch.setenv('AGORA_ADMIN_MAILS', team['emails']['owner'])
    l = team['owner'].get('/api/admin/orgs').json()
    eins = next(o for o in l['orgs'] if o['slug'] == s)
    assert eins['mitglieder'] == 3 and eins['owner']['name'] == 'Olga Owner' and l['gesamt'] >= 2
    assert team['owner'].get('/api/admin/orgs', params={'q': team['slug2']}).json()['orgs'][0]['slug'] == team['slug2']
