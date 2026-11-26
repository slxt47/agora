"""Öffentliche Status-Seite: Prüfungen → 5-Minuten-Fenster, Verfügbarkeit/Balken/Antwortzeit, Gesamtzustand,
Meldungen (nur Admins schreiben), keine internen Angaben nach außen, Aufbewahrung 90 Tage."""
import json

import pytest

from test_api import frisch, konto, neu_client  # noqa: F401

from app import db, status
from app.kern import grenze


@pytest.fixture(autouse=True)
def leer():
    c = db.verbindung()
    c.execute('DELETE FROM status_proben')
    c.execute('DELETE FROM status_meldungen')
    status.LETZTE.clear()
    status._cache['daten'] = None
    yield


@pytest.fixture
def admin(monkeypatch):
    c, email, _pw = konto(name='Status-Admin')
    monkeypatch.setenv('AGORA_ADMIN_MAILS', email)
    return c


T0 = 1_800_000_000 - 1_800_000_000 % 86400      # Mitternacht UTC


def test_fenster_zusammenfassen():
    for i in range(5):                            # fünf Minuten im selben Fenster
        status.speichern('app', True, 10 + i, T0 + i * 60)
    status.speichern('app', False, 999, T0 + 299)
    status.speichern('app', True, 7, T0 + 300)     # nächstes Fenster
    rows = db.verbindung().execute("SELECT * FROM status_proben WHERE dienst = 'app' ORDER BY fenster").fetchall()
    assert [(r['fenster'], r['proben'], r['ok'], r['ms_summe'], r['ms_max']) for r in rows] == [
        (T0, 6, 5, 60.0, 14.0), (T0 + 300, 1, 1, 7.0, 7.0)]


def test_pruefen_mit_fehlern(monkeypatch):
    monkeypatch.setitem(status.PRUEFUNGEN, 'app', lambda: 3.5)
    monkeypatch.setitem(status.PRUEFUNGEN, 'medien', lambda: (_ for _ in ()).throw(RuntimeError('livekit kaputt')))
    monkeypatch.setitem(status.PRUEFUNGEN, 'mail', lambda: 120.0)
    status.pruefen_und_speichern(T0)
    status.pruefen_und_speichern(T0 + 60)          # Mail nur alle 5 Minuten
    zaehl = {r['dienst']: (r['proben'], r['ok']) for r in db.verbindung().execute('SELECT * FROM status_proben').fetchall()}
    assert zaehl == {'app': (2, 2), 'medien': (2, 0), 'mail': (1, 1)}
    d = status.status_daten(T0 + 90)
    z = {x['id']: x['zustand'] for x in d['dienste']}
    assert z == {'app': 'ok', 'medien': 'ausfall', 'mail': 'ok'} and d['gesamt'] == 'stoerung'


def test_verfuegbarkeit_und_balken():
    # 3 Tage: Tag 1 alles ok, Tag 2 ein Drittel Ausfall, Tag 3 ohne Daten bis auf jetzt
    for m in range(0, 86400, 300):
        status.speichern('medien', True, 20, T0 + m)
    for m in range(0, 86400, 300):
        status.speichern('medien', (m // 300) % 3 != 0, 40, T0 + 86400 + m)
    jetzt = T0 + 2 * 86400 + 600
    status.speichern('medien', True, 30, jetzt - 60)
    d = next(x for x in status.status_daten(jetzt)['dienste'] if x['id'] == 'medien')
    assert len(d['balken']['90d']) == 90 and len(d['balken']['24h']) == 24 and len(d['balken']['7d']) == 28
    tage = d['balken']['90d']
    assert tage[-1]['uptime'] == 100.0 and tage[-2]['uptime'] == pytest.approx(66.667, abs=0.01) and tage[-3]['uptime'] == 100.0
    assert tage[-4]['uptime'] is None and tage[-2]['ausfaelle'] == 96 and tage[-3]['ms'] == 20.0
    assert d['uptime']['90d'] == pytest.approx((288 + 192 + 1) / (288 * 2 + 1) * 100, abs=0.001)
    assert d['uptime']['24h'] < 100 and d['zustand'] == 'ok'
    assert d['ms'] is not None


def test_zustand_nach_neustart_aus_db():
    status.speichern('app', False, 0, T0)
    status.speichern('app', True, 5, T0 + 60)
    status.LETZTE.clear()                          # Neustart: nichts im Speicher
    assert status.zustand('app', T0 + 120) == 'gestoert'
    assert status.zustand('app', T0 + 3600) == 'unbekannt'


def test_api_oeffentlich_ohne_interna(monkeypatch):
    monkeypatch.setitem(status.PRUEFUNGEN, 'medien', lambda: 12.0)
    status.pruefen_und_speichern()
    r = neu_client().get('/api/status')
    assert r.status_code == 200
    d = r.json()
    assert {x['id'] for x in d['dienste']} == {'app', 'medien', 'mail'} and d['gesamt'] == 'ok'
    text = json.dumps(d).lower()
    for verboten in ('livekit', '127.0.0.1', 'localhost', 'smtp', 'version', 'sqlite', '7880', 'slxt', 'fehler'):
        assert verboten not in text, verboten
    assert set(d) == {'stand', 'gesamt', 'dienste', 'meldungen', 'vergangen', 'fenster_s'}
    # Begrenzer je IP
    c = neu_client()
    grenze.d.clear()
    assert all(c.get('/api/status').status_code == 200 for _ in range(120))
    assert c.get('/api/status').status_code == 429


def test_meldungen_nur_admin(admin):
    normal, _e, _p = konto(name='Normal')
    for c in (normal, neu_client()):
        assert c.get('/api/admin/status/meldungen').status_code == 404
        assert c.post('/api/admin/status/meldungen', json={'titel': 'x'}).status_code == 404
        assert c.patch('/api/admin/status/meldungen/1', json={'titel': 'x'}).status_code == 404
        assert c.request('DELETE', '/api/admin/status/meldungen/1').status_code == 404
    r = admin.post('/api/admin/status/meldungen', json={'art': 'stoerung', 'titel': 'Video stockt', 'text': 'Wir prüfen das.\nZeile 2',
                                                        'dienste': ['medien']})
    assert r.status_code == 200, r.text
    m = r.json()['meldung']
    assert m['zustand'] == 'untersuchung' and m['dienste'] == ['medien'] and m['ende'] is None
    d = neu_client().get('/api/status').json()
    assert [x['titel'] for x in d['meldungen']] == ['Video stockt'] and d['gesamt'] == 'stoerung'
    # ungültige Eingaben
    for body in ({'art': 'boese', 'titel': 'x'}, {'art': 'wartung', 'titel': 'x', 'zustand': 'behoben'}, {'titel': ''},
                 {'titel': 'x', 'dienste': ['datenbank']}, {'titel': 'x', 'beginn': '2030-01-02T10:00Z', 'ende': '2030-01-01T10:00Z'},
                 {'titel': 'x' * 141}):
        assert admin.post('/api/admin/status/meldungen', json=body).status_code == 400, body
    # behoben → Ende gesetzt, wandert in „vergangen“
    r = admin.patch(f'/api/admin/status/meldungen/{m["id"]}', json={'zustand': 'behoben', 'text': 'Behoben.'})
    assert r.json()['meldung']['ende'] is not None
    d = neu_client().get('/api/status').json()
    assert d['meldungen'] == [] and [x['zustand'] for x in d['vergangen']] == ['behoben']
    # Wartung in der Zukunft: sichtbar als geplant, Gesamt nicht „Wartung“
    w = admin.post('/api/admin/status/meldungen', json={'art': 'wartung', 'titel': 'Update', 'beginn': '2099-01-01T02:00Z',
                                                        'ende': '2099-01-01T03:00Z'}).json()['meldung']
    assert w['zustand'] == 'geplant'
    status._cache['daten'] = None
    d = neu_client().get('/api/status').json()
    assert [x['titel'] for x in d['meldungen']] == ['Update'] and d['gesamt'] != 'wartung'
    assert admin.request('DELETE', f'/api/admin/status/meldungen/{w["id"]}').json() == {'ok': True}
    assert admin.patch(f'/api/admin/status/meldungen/{w["id"]}', json={'titel': 'x'}).status_code == 404
    assert len(admin.get('/api/admin/status/meldungen').json()['meldungen']) == 1
    eintraege = admin.get('/api/admin/protokoll', params={'q': 'Status-Meldung'}).json()['eintraege']
    assert len(eintraege) >= 3


def test_aufraeumen():
    status.speichern('app', True, 1, T0)
    status.speichern('app', True, 1, T0 + 91 * 86400)
    c = db.verbindung()
    c.execute("INSERT INTO status_meldungen (art, titel, text, zustand, dienste, beginn, ende, erstellt, geaendert) "
              "VALUES ('stoerung', 'alt', '', 'behoben', '[]', ?, ?, ?, ?)", (T0, T0 + 60, T0, T0))
    status.aufraeumen(T0 + 400 * 86400)
    assert c.execute('SELECT COUNT(*) FROM status_proben').fetchone()[0] == 0
    assert c.execute('SELECT COUNT(*) FROM status_meldungen').fetchone()[0] == 0
    status.speichern('app', True, 1, T0)
    status.aufraeumen(T0 + 30 * 86400)
    assert c.execute('SELECT COUNT(*) FROM status_proben').fetchone()[0] == 1


def test_seite_route():
    from app import main
    assert '/status' in main.SEITEN
