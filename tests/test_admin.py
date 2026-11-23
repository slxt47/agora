"""Tests Admin-Bereich: nur Admins (sonst 404), Übersicht, Konten, Meldungen, Protokoll, Meetings beenden."""
import pytest

from test_api import T, aufrufe, ereignisse, frisch, konto, meeting, neu_client, raum, zugang  # noqa: F401

from app import db
from app import livekit_dienst as lk


@pytest.fixture
def admin(monkeypatch):
    c, email, pw = konto(name='Admina')
    monkeypatch.setenv('AGORA_ADMIN_MAILS', f'andere@example.org, {email.upper()}')
    return c, email, pw


PFADE_GET = ['/admin', '/admin.html', '/api/admin/uebersicht', '/api/admin/benutzer', '/api/admin/meldungen', '/api/admin/protokoll',
             '/api/admin/meetings']


def admin_protokoll(c, **kw):
    return c.get('/api/admin/protokoll', params=kw).json()['eintraege']


def test_nicht_admin_bekommt_404(admin):
    normal, _e, _p = konto(name='Normal')
    anonym = neu_client()
    for c in (normal, anonym):
        for p in PFADE_GET:
            r = c.get(p)
            assert r.status_code == 404 and r.json() == {'fehler': 'Nicht gefunden'}, (p, r.status_code)
        assert c.post('/api/admin/benutzer/1/sperren', json={'an': True}).status_code == 404
        assert c.request('DELETE', '/api/admin/benutzer/1').status_code == 404
        assert c.post('/api/admin/meetings/abc-defg-hij/beenden').status_code == 404
        assert c.post('/api/admin/meldungen/1', json={'status': 'erledigt'}).status_code == 404
    assert normal.get('/api/konto/ich').json()['ich']['admin'] is False
    a, _e, _p = admin
    assert a.get('/api/konto/ich').json()['ich']['admin'] is True
    assert a.get('/api/admin/uebersicht').status_code == 200
    assert a.get('/admin.html').status_code == 404            # nie als statische Datei
    assert a.get('/admin').status_code == 404                  # ohne Build (Tests) gibt es keine Seite


def test_unbestaetigte_admin_adresse_zaehlt_nicht(monkeypatch):
    c = neu_client()
    c.post('/api/konto/registrieren', json={'name': 'X', 'email': 'nichtbest@example.org', 'passwort': 'passwort-123'})
    monkeypatch.setenv('AGORA_ADMIN_MAILS', 'nichtbest@example.org')
    assert c.get('/api/admin/uebersicht').status_code == 404


def test_uebersicht_live_aus_livekit(admin):
    a, _e, _p = admin
    _host, code, hz, gaeste = meeting(2)
    lk.FAKE_TEILNEHMER[code] = [{'identity': hz['identity'], 'name': 'H', 'tracks': []}] + \
        [{'identity': z['identity'], 'name': 'G', 'tracks': []} for _g, z in gaeste]
    lk.FAKE_TEILNEHMER[code + '__b1'] = [{'identity': 'x', 'name': 'B', 'tracks': []}]
    u = a.get('/api/admin/uebersicht').json()
    assert u['laufend'] == {'meetings': 1, 'teilnehmer': 4, 'livekit': True}
    assert u['meetings']['tage_7'] >= 1 and u['konten']['gesamt'] >= 2 and 'belegt' in u['aufnahmen']
    assert aufrufe('raeume_liste')
    m = a.get('/api/admin/meetings').json()['meetings']
    eintrag = next(x for x in m if x['code'] == code)
    assert eintrag['teilnehmer'] == 4 and eintrag['besitzer_name'] == 'Ada'


def test_konten_suchen_sperren_entsperren(admin):
    a, admin_mail, _p = admin
    opfer, email, pw = konto(name='Zielperson Zorro')
    zweit = neu_client()
    zweit.post('/api/konto/anmelden', json={'email': email, 'passwort': pw})
    r = raum(opfer)
    zugang(opfer, r['code'])
    liste = a.get('/api/admin/benutzer', params={'q': 'Zorro'}).json()
    assert liste['gesamt'] == 1 and liste['benutzer'][0]['email'] == email and liste['benutzer'][0]['gesperrt'] is False
    assert a.get('/api/admin/benutzer', params={'q': '%'}).json()['gesamt'] == 0        # Platzhalter werden escaped
    bid = liste['benutzer'][0]['id']
    # sich selbst bzw. andere Admins nicht
    ich = a.get('/api/konto/ich').json()['ich']['id']
    assert a.post(f'/api/admin/benutzer/{ich}/sperren', json={'an': True}).status_code == 403
    assert a.post('/api/admin/benutzer/999999/sperren', json={'an': True}).status_code == 404
    assert a.post(f'/api/admin/benutzer/{bid}/sperren', json={'an': 'ja'}).status_code == 400
    res = a.post(f'/api/admin/benutzer/{bid}/sperren', json={'an': True})
    assert res.status_code == 200 and res.json()['benutzer']['gesperrt'] is True
    # alle Sitzungen weg, Anmelden geht nicht, laufendes Meeting beendet
    assert opfer.get('/api/konto/ich').json() == {'angemeldet': False}
    assert zweit.get('/api/konto/ich').json() == {'angemeldet': False}
    r2 = neu_client().post('/api/konto/anmelden', json={'email': email, 'passwort': pw})
    assert r2.status_code == 403 and 'gesperrt' in r2.json()['fehler']
    assert ereignisse('beendet')
    assert db.verbindung().execute('SELECT aktiv FROM raeume WHERE code = ?', (r['code'],)).fetchone()[0] == 0
    p = admin_protokoll(a, nur_admin='1')
    assert p[0]['ereignis'] == f'Admin: Konto gesperrt (#{bid} {email})' and p[0]['email'] == admin_mail
    # entsperren
    assert a.post(f'/api/admin/benutzer/{bid}/sperren', json={'an': False}).json()['benutzer']['gesperrt'] is False
    assert neu_client().post('/api/konto/anmelden', json={'email': email, 'passwort': pw}).status_code == 200
    assert admin_protokoll(a, q='entsperrt', nur_admin='1')[0]['ereignis'].startswith('Admin: Konto entsperrt')


def test_konto_loeschen_und_2fa_zuruecksetzen(admin):
    a, _m, _p = admin
    opfer, email, pw = konto(name='Weg')
    bid = opfer.get('/api/konto/ich').json()['ich']['id']
    db.verbindung().execute("UPDATE benutzer SET totp_an = 1, totp_geheim = 'v1.x' WHERE id = ?", (bid,))
    res = a.post(f'/api/admin/benutzer/{bid}/2fa-zuruecksetzen')
    assert res.status_code == 200 and res.json()['benutzer']['zwei_faktor'] is False
    r = neu_client().post('/api/konto/anmelden', json={'email': email, 'passwort': pw})
    assert r.status_code == 200 and 'zwei_faktor' not in r.json()
    assert a.request('DELETE', f'/api/admin/benutzer/{bid}').json() == {'ok': True}
    assert a.get('/api/admin/benutzer', params={'q': email}).json()['gesamt'] == 0
    assert neu_client().post('/api/konto/anmelden', json={'email': email, 'passwort': pw}).status_code == 400
    assert any(e['ereignis'] == f'Admin: Konto gelöscht (#{bid} {email})' for e in admin_protokoll(a, nur_admin='1'))
    assert a.request('DELETE', f'/api/admin/benutzer/{bid}').status_code == 404


def test_meldungen_bearbeiten(admin):
    a, _m, _p = admin
    _host, code, hz, gaeste = meeting(1)
    g, z = gaeste[0]
    assert g.post(f'/api/raeume/{code}/melden', json={'ziel': hz['identity'], 'grund': 'spam', 'text': 'Werbung'}, headers=T(z)).status_code == 200
    m = a.get('/api/admin/meldungen').json()['meldungen']
    eigene = [x for x in m if x['raum_code'] == code]
    assert len(eigene) == 1 and eigene[0]['grund_text'] == 'Spam oder Werbung' and eigene[0]['ziel_konto']['email']
    mid = eigene[0]['id']
    assert a.post(f'/api/admin/meldungen/{mid}', json={'status': 'weg'}).status_code == 400
    assert a.post('/api/admin/meldungen/999999', json={'status': 'erledigt'}).status_code == 404
    res = a.post(f'/api/admin/meldungen/{mid}', json={'status': 'erledigt', 'notiz': 'Verwarnt.'})
    assert res.status_code == 200 and res.json()['meldung']['status'] == 'erledigt' and res.json()['meldung']['notiz'] == 'Verwarnt.'
    assert not [x for x in a.get('/api/admin/meldungen').json()['meldungen'] if x['id'] == mid]
    assert [x for x in a.get('/api/admin/meldungen', params={'status': 'erledigt'}).json()['meldungen'] if x['id'] == mid]
    assert admin_protokoll(a, raum=code, nur_admin='1')[0]['ereignis'] == f'Admin: Meldung #{mid} erledigt'


def test_meeting_beenden_und_protokoll_filter(admin):
    a, _m, _p = admin
    host, code, hz, _g = meeting(1)
    assert a.post('/api/admin/meetings/abc-defg-hij/beenden').status_code == 404
    assert a.post(f'/api/admin/meetings/{code}/beenden').json() == {'ok': True}
    assert ereignisse('beendet') and aufrufe('raum_loeschen')
    assert a.post(f'/api/admin/meetings/{code}/beenden').status_code == 409
    p = admin_protokoll(a, raum=code)
    assert [e['ereignis'] for e in p[:2]] == ['Meeting beendet (Admin)', 'Admin: Meeting beendet']
    # Filter nach Konto (E-Mail oder ID) und Text
    hid = host.get('/api/konto/ich').json()['ich']['id']
    assert all(e['benutzer_id'] == hid for e in admin_protokoll(a, benutzer=str(hid)))
    assert admin_protokoll(a, q='zzz-gibts-nicht') == []


def test_admin_protokoll_bleibt_laenger():
    from app import main
    t = main.jetzt()
    c = db.verbindung()
    c.execute('INSERT INTO protokoll VALUES (?, NULL, NULL, ?)', (t - 40 * 86400, 'Admin: alt aber behalten'))
    c.execute('INSERT INTO protokoll VALUES (?, NULL, NULL, ?)', (t - 40 * 86400, 'normal alt'))
    c.execute('INSERT INTO protokoll VALUES (?, NULL, NULL, ?)', (t - 400 * 86400, 'Admin: zu alt'))
    main.aufraeumen()
    rest = {x[0] for x in c.execute('SELECT ereignis FROM protokoll').fetchall()}
    assert 'Admin: alt aber behalten' in rest and 'normal alt' not in rest and 'Admin: zu alt' not in rest
