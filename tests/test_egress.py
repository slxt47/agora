"""Tests Server-Aufnahme + Livestream (LiveKit Egress, gefälscht). Hilfen und Umgebung aus test_api."""
import asyncio
import os

import pytest

from test_api import T, aufrufe, ereignisse, frisch, konto, neu_client, raum, zugang  # noqa: F401

from app import db, egress
from app import livekit_dienst as lk


@pytest.fixture
def egress_an(monkeypatch):
    monkeypatch.setattr(egress, 'AN', True)


def runde():
    host, _e, _p = konto(name='Hosta')
    code = raum(host, titel='Vortrag')['code']
    hz = zugang(host, code)
    g = neu_client()
    gz = zugang(g, code, name='Gast')
    return host, code, hz, g, gz


def test_ohne_feature_flag_versteckt():
    host, code, hz, _g, _gz = runde()
    assert host.get(f'/api/raeume/{code}/egress', headers=T(hz)).json() == {'verfuegbar': False}
    assert host.post(f'/api/raeume/{code}/egress/aufnahme', json={'aktion': 'start'}, headers=T(hz)).status_code == 404


def test_server_aufnahme_start_stopp_bibliothek(egress_an):
    host, code, hz, g, gz = runde()
    p = lambda c, z, **kw: c.post(f'/api/raeume/{code}/egress/aufnahme', json=kw, headers=T(z))
    assert p(g, gz, aktion='start').status_code == 403                    # nur Host/Co-Host
    assert p(host, hz, aktion='start', layout='quer').status_code == 400
    r = p(host, hz, aktion='start', layout='sprecher')
    assert r.status_code == 200, r.text
    eid = r.json()['id']
    start = aufrufe('egress_datei')[-1]
    assert start['layout'] == 'speaker' and start['datei'].startswith('/out/') and start['datei'].endswith('.mp4')
    # Anzeige für alle: Metadaten (aufnahme mit server) + Ereignis
    md = aufrufe('metadaten')[-1]['daten']
    assert md['aufnahme'] == {'von_name': 'Hosta', 'server': True}
    assert ereignisse('aufnahme')[-1]['daten']['server'] is True
    assert p(host, hz, aktion='start').status_code == 409                 # nur eine gleichzeitig
    # Browser-Aufnahme parallel verhindert (UI) – Zustand für den Host
    z = host.get(f'/api/raeume/{code}/egress', headers=T(hz)).json()
    assert z['verfuegbar'] and z['aufnahme']['status'] == 'laeuft' and z['aufnahme']['layout'] == 'sprecher' and z['stream'] is None
    assert g.get(f'/api/raeume/{code}/egress', headers=T(gz)).json() == {'verfuegbar': True}
    # Stoppen → Egress schreibt die Datei → beim Abgleich in die Bibliothek des Besitzers
    assert p(host, hz, aktion='stopp').status_code == 200
    assert aufrufe('egress_stoppen')[-1] == {'egress_id': eid}
    datei = os.path.basename(start['datei'])
    os.makedirs(egress.AUSGABE, exist_ok=True)
    with open(os.path.join(egress.AUSGABE, datei), 'wb') as f:
        f.write(b'\x00\x00\x00\x18ftypmp42' + b'x' * 100)
    lk.FAKE_EGRESS[eid] = {'status': 'fertig', 'datei': datei, 'dauer': 42, 'groesse': 112}
    z = host.get(f'/api/raeume/{code}/egress', headers=T(hz)).json()
    assert z['aufnahme']['status'] == 'fertig' and z['aufnahme']['aufnahme_id']
    liste = host.get('/api/aufnahmen').json()['aufnahmen']
    assert liste[0]['typ'] == 'video/mp4' and liste[0]['dauer'] == 42 and liste[0]['raum_code'] == code and 'Server-Aufnahme' in liste[0]['titel']
    assert host.get(f'/api/aufnahmen/{liste[0]["id"]}/datei').content.startswith(b'\x00\x00\x00\x18ftyp')
    assert not os.path.exists(os.path.join(egress.AUSGABE, datei))
    assert 'aufnahme' not in {k for k, v in aufrufe('metadaten')[-1]['daten'].items() if v}     # Hinweis weg


def test_server_aufnahme_fehler_wird_gemeldet(egress_an):
    host, code, hz, _g, _gz = runde()
    eid = host.post(f'/api/raeume/{code}/egress/aufnahme', json={'aktion': 'start'}, headers=T(hz)).json()['id']
    lk.FAKE_EGRESS[eid] = {'status': 'fehler', 'fehler': 'Chrome abgestürzt'}
    asyncio.run(egress.alle_abgleichen())
    e = [x for x in ereignisse('egress')][-1]
    assert e['daten'] == {'typ': 'egress', 'art': 'aufnahme', 'status': 'fehler', 'fehler': 'Chrome abgestürzt'} and e['an'] == [hz['identity']]
    assert host.get('/api/aufnahmen').json()['aufnahmen'] == []


def test_livestream_schluessel_geheim_und_ziel_geprueft(egress_an, capsys):
    host, code, hz, g, gz = runde()
    p = lambda **kw: host.post(f'/api/raeume/{code}/egress/stream', json=kw, headers=T(hz))
    for url in ('http://1.1.1.1/live', 'rtmp://127.0.0.1/live', 'rtmp://10.1.2.3/live', 'rtmp://[::1]/x', 'rtmp://user:pw@1.1.1.1/x'):
        assert p(aktion='start', url=url, schluessel='abc').status_code == 400, url
    assert p(aktion='start', url='rtmps://1.1.1.1:443/live2', schluessel='mein geheim').status_code == 400   # Leerzeichen
    schluessel = 'sk_live_GEHEIM_123456'
    r = p(aktion='start', url='rtmps://1.1.1.1:443/live2/', schluessel=schluessel)
    assert r.status_code == 200, r.text
    eid = r.json()['id']
    # Schlüssel nie im Klartext: nicht in der Datenbank, nicht im Protokoll, nicht im Fälschungs-Mitschnitt, nicht in der Ausgabe
    zeile = db.verbindung().execute('SELECT * FROM egress_laeufe WHERE egress_id = ?', (eid,)).fetchone()
    assert schluessel not in str(dict(zeile)) and zeile['ziel_verschluesselt'].startswith('v1.')
    assert schluessel not in str(db.verbindung().execute('SELECT * FROM protokoll').fetchall())
    assert schluessel not in str(lk.AUFRUFE)
    assert aufrufe('metadaten')[-1]['daten']['livestream'] == {'von_name': 'Hosta'}
    # Fehler mit Schlüssel im Text → geschwärzt an Host/Co-Hosts
    lk.FAKE_EGRESS[eid] = {'status': 'fehler', 'fehler': lk.schwaerzen(f'rtmps://1.1.1.1/live2/{schluessel}: connection refused', schluessel)}
    z = host.get(f'/api/raeume/{code}/egress', headers=T(hz)).json()
    assert z['stream']['status'] == 'fehler' and schluessel not in z['stream']['fehler'] and '***' in z['stream']['fehler']
    assert db.verbindung().execute('SELECT ziel_verschluesselt FROM egress_laeufe WHERE egress_id = ?', (eid,)).fetchone()[0] is None
    assert schluessel not in capsys.readouterr().out
    # Neu starten und stoppen; Gäste dürfen nicht
    eid2 = p(aktion='start', url='rtmp://1.1.1.1/live', schluessel='k2').json()['id']
    assert g.post(f'/api/raeume/{code}/egress/stream', json={'aktion': 'stopp'}, headers=T(gz)).status_code == 403
    assert p(aktion='stopp').status_code == 200 and aufrufe('egress_stoppen')[-1] == {'egress_id': eid2}
    assert p(aktion='stopp').status_code == 404


def test_meeting_ende_vergisst_stream_ziel(egress_an):
    host, code, hz, _g, _gz = runde()
    eid = host.post(f'/api/raeume/{code}/egress/stream', json={'aktion': 'start', 'url': 'rtmp://1.1.1.1/live', 'schluessel': 'k'},
                    headers=T(hz)).json()['id']
    host.post(f'/api/raeume/{code}/steuerung', json={'aktion': 'beenden'}, headers=T(hz))
    assert db.verbindung().execute('SELECT ziel_verschluesselt FROM egress_laeufe WHERE egress_id = ?', (eid,)).fetchone()[0] is None


def test_e2ee_meeting_ohne_server_aufnahme(egress_an):
    host, code, hz, g, gz = runde()
    db.verbindung().execute('UPDATE raeume SET e2ee = 1 WHERE code = ?', (code,))
    for pfad in ('aufnahme', 'stream'):
        r = host.post(f'/api/raeume/{code}/egress/{pfad}', json={'aktion': 'start', 'layout': 'raster', 'url': 'rtmps://a.example.org/live', 'schluessel': 'x'}, headers=T(hz))
        assert r.status_code == 409, r.text
