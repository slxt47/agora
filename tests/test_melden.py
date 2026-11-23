"""Tests Melden und Blockieren. Hilfen und Umgebung aus test_api."""
from test_api import T, beitreten, ereignisse, frisch, konto, mails_an, meeting, neu_client, raum, zugang  # noqa: F401

from app import db, mail


def melden(c, code, z, **kw):
    return c.post(f'/api/raeume/{code}/melden', json=kw, headers=T(z))


def test_melden_speichert_mailt_admins_und_hinweis_an_host(monkeypatch):
    admin, admin_mail, _p = konto(name='Admin')
    unbestaetigt = 'offen@example.org'
    neu_client().post('/api/konto/registrieren', json={'name': 'X', 'email': unbestaetigt, 'passwort': 'passwort-123'})
    monkeypatch.setenv('AGORA_ADMIN_MAILS', f' {admin_mail.upper()} , {unbestaetigt},niemand@example.org')
    host, code, hz, gaeste = meeting(2, titel='Runde')
    (g1, z1), (g2, z2) = gaeste
    assert melden(g1, code, z1, ziel=z2['identity'], grund='quatsch').status_code == 400
    assert melden(g1, code, z1, ziel=z1['identity'], grund='spam').status_code == 400
    assert melden(g1, code, z1, ziel='gibts-nicht', grund='spam').status_code == 404
    assert melden(g1, code, z1, ziel=z2['identity'], grund='spam', text='x' * 1001).status_code == 400
    assert neu_client().post(f'/api/raeume/{code}/melden', json={'ziel': z2['identity'], 'grund': 'spam'}).status_code == 403
    r = melden(g1, code, z1, ziel=z2['identity'], grund='belaestigung', text='Beleidigt alle im Chat.')
    assert r.status_code == 200 and r.json() == {'ok': True}
    assert melden(g1, code, z1, ziel=z2['identity'], grund='spam').status_code == 409          # einmal je Person und Meeting
    m = db.verbindung().execute('SELECT * FROM meldungen WHERE raum_code = ?', (code,)).fetchone()
    assert (m['von_name'], m['ziel_name'], m['grund'], m['text'], m['status']) == ('Gast 1', 'Gast 2', 'belaestigung', 'Beleidigt alle im Chat.', 'offen')
    # Mail nur an bestätigte Admin-Konten
    an_admin = [x for x in mails_an(admin_mail) if 'Meldung' in x[1]]
    assert len(an_admin) == 1 and code in an_admin[0][1] and 'Belästigung' in an_admin[0][2] and '/admin#meldungen' in an_admin[0][2]
    assert 'Gast 2' in an_admin[0][2] and 'Beleidigt alle' in an_admin[0][2]
    assert not any('Meldung' in x[1] for x in mails_an(unbestaetigt))
    # Hinweis an Host (nicht an die meldende Person, ohne deren Namen)
    e = ereignisse('meldung')
    assert len(e) == 1 and e[0]['an'] == [hz['identity']]
    assert e[0]['daten'] == {'typ': 'meldung', 'ziel': z2['identity'], 'ziel_name': 'Gast 2', 'grund': 'belaestigung'}
    # Gast meldet den Host → Hinweis an niemanden (kein anderer Host/Co-Host)
    assert melden(g2, code, z2, ziel=hz['identity'], grund='stoerung').status_code == 200
    assert len(ereignisse('meldung')) == 1


def test_melden_ohne_admins_und_grenze(monkeypatch):
    monkeypatch.delenv('AGORA_ADMIN_MAILS', raising=False)
    _host, code, hz, gaeste = meeting(1)
    g, z = gaeste[0]
    vorher = len(mail.POSTEINGANG_TEST)
    assert melden(g, code, z, ziel=hz['identity'], grund='sonstiges').status_code == 200
    assert len(mail.POSTEINGANG_TEST) == vorher
    weitere = [zugang(neu_client(), code, name=f'N{i}') for i in range(5)]
    codes = [melden(g, code, z, ziel=w['identity'], grund='spam').status_code for w in weitere]
    assert codes == [200] * 4 + [429]           # 5 Meldungen je Person in 10 min


def test_blockieren_beitritt_und_privatchat():
    ada, _e, _p = konto(name='Ada')
    bob, _e2, _p2 = konto(name='Bob')
    r = raum(ada)
    az = zugang(ada, r['code'])
    bz = zugang(bob, r['code'])
    gast = neu_client()
    gz = zugang(gast, r['code'], name='Gast')
    # Gäste und man selbst lassen sich nicht blockieren; ohne Anmeldung geht es nicht
    assert ada.post(f'/api/raeume/{r["code"]}/blockieren', json={'ziel': gz['identity']}, headers=T(az)).status_code == 400
    assert ada.post(f'/api/raeume/{r["code"]}/blockieren', json={'ziel': az['identity']}, headers=T(az)).status_code == 400
    assert gast.post(f'/api/raeume/{r["code"]}/blockieren', json={'ziel': bz['identity']}, headers=T(gz)).status_code == 401
    # fremde Teilnahme mit eigenem Cookie → nein
    assert ada.post(f'/api/raeume/{r["code"]}/blockieren', json={'ziel': az['identity']}, headers=T(bz)).status_code == 403
    # Bob schreibt Ada privat – geht noch
    assert bob.post(f'/api/raeume/{r["code"]}/chat', json={'text': 'hi', 'an': az['identity']}, headers=T(bz)).status_code == 200
    res = ada.post(f'/api/raeume/{r["code"]}/blockieren', json={'ziel': bz['identity']}, headers=T(az))
    assert res.status_code == 200 and res.json() == {'ok': True, 'name': 'Bob'}
    assert ada.post(f'/api/raeume/{r["code"]}/blockieren', json={'ziel': bz['identity']}, headers=T(az)).status_code == 200   # doppelt egal
    # Bob kann Ada nicht mehr privat schreiben, aber an alle; Ada kann Bob schreiben
    res = bob.post(f'/api/raeume/{r["code"]}/chat', json={'text': 'hallo?', 'an': az['identity']}, headers=T(bz))
    assert res.status_code == 403 and 'keine privaten' in res.json()['fehler']
    assert bob.post(f'/api/raeume/{r["code"]}/chat', json={'text': 'an alle'}, headers=T(bz)).status_code == 200
    assert ada.post(f'/api/raeume/{r["code"]}/chat', json={'text': 'ok', 'an': bz['identity']}, headers=T(az)).status_code == 200
    # Bob kann Adas Räume nicht mehr betreten (auch nicht den persönlichen); andere schon, Ada Bobs Räume auch
    res = beitreten(bob, r['code'])
    assert res.status_code == 403 and res.json()['fehler'] == 'Du kannst diesem Meeting nicht beitreten.'
    pers = ada.get('/api/konto/ich').json()['ich']['persoenlicher_raum']
    assert beitreten(bob, pers).status_code == 403
    assert beitreten(neu_client(), r['code'], name='Neu').status_code == 200
    br = raum(bob)
    assert beitreten(ada, br['code']).status_code == 200
    # Liste im Konto, aufheben
    liste = ada.get('/api/konto/blockiert').json()['blockiert']
    assert len(liste) == 1 and liste[0]['name'] == 'Bob' and liste[0]['seit'].endswith('Z')
    assert bob.get('/api/konto/blockiert').json() == {'blockiert': []}
    assert bob.request('DELETE', f'/api/konto/blockiert/{liste[0]["id"]}').status_code == 404
    assert ada.request('DELETE', f'/api/konto/blockiert/{liste[0]["id"]}').json() == {'ok': True}
    assert beitreten(bob, r['code']).status_code == 200
    # Löschen des blockierten Kontos räumt die Blockierung weg
    ada.post(f'/api/raeume/{r["code"]}/blockieren', json={'ziel': bz['identity']}, headers=T(az))
    assert bob.request('DELETE', '/api/konto', json={'passwort': _p2}).status_code == 200
    assert ada.get('/api/konto/blockiert').json() == {'blockiert': []}


def test_export_enthaelt_blockierte():
    from app import konto
    assert any('blockiert' in (f({'id': -1}) or {}) for f in konto.EXPORT)
