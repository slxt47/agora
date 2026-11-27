"""Tests Fernsteuerung (Server-Teil): bereit nur mit Bildschirmspur, Anfrage nur an die teilende Person, Freigabe nur
durch sie (mit Kennung aus dem Dialog der Desktop-App) und nur über die Raum-Metadaten, Ende auf allen Wegen."""
import asyncio

from test_api import T, aufrufe, ereignisse, frisch, konto, neu_client, raum, zugang  # noqa: F401

from app import db, fernsteuerung
from app import livekit_dienst as lk

KENNUNG = 'Kk_' + 'a' * 29


def runde():
    host, _e, _p = konto(name='Hosta')
    code = raum(host, titel='Support')['code']
    hz = zugang(host, code)
    a = neu_client()
    az = zugang(a, code, name='Anna')
    b = neu_client()
    bz = zugang(b, code, name='Boris')
    return host, code, hz, a, az, b, bz


def fs(c, code, z, **kw):
    return c.post(f'/api/raeume/{code}/fernsteuerung', json=kw, headers=T(z))


def teilt(code, *identities):
    lk.FAKE_TEILNEHMER[code] = [{'identity': i, 'name': i, 'tracks': [{'sid': f'TR_{i}', 'quelle': 'bildschirm', 'stumm': False}]}
                                for i in identities]


def anzahl(code):
    return db.verbindung().execute('SELECT COUNT(*) FROM fernsteuerung f JOIN raeume r ON r.id = f.raum_id WHERE r.code = ?', (code,)).fetchone()[0]


def meta():
    return aufrufe('metadaten')[-1]['daten']


def test_bereit_nur_mit_bildschirmspur():
    host, code, hz, a, az, _b, _bz = runde()
    r = fs(host, code, hz, aktion='bereit', an=True)                              # teilt (noch) nicht → kein Eintrag
    assert r.status_code == 200 and r.json() == {'ok': False, 'grund': 'keine-bildschirmspur'}
    assert fernsteuerung._zeilen(db.verbindung().execute('SELECT * FROM raeume WHERE code = ?', (code,)).fetchone()) == []
    assert fs(host, code, hz, aktion='bereit', an='ja').status_code == 400
    teilt(code, hz['identity'])
    assert fs(host, code, hz, aktion='bereit', an=True).status_code == 200
    assert meta()['fernsteuerung_bereit'] == [hz['identity']] and meta()['fernsteuerung'] == []
    assert fs(a, code, az, aktion='bereit', an=True).json()['ok'] is False          # Anna teilt nicht
    assert neu_client().post(f'/api/raeume/{code}/fernsteuerung', json={'aktion': 'bereit', 'an': True}).status_code == 403
    assert fs(host, code, hz, aktion='quatsch').status_code == 400


def test_ablauf_anfrage_erteilen_ereignisse_beenden():
    host, code, hz, a, az, b, bz = runde()
    teilt(code, hz['identity'])
    fs(host, code, hz, aktion='bereit', an=True)
    # Anfrage geht nur an die teilende Person
    assert fs(a, code, az, aktion='anfragen', ziel='g-gibtsnicht').status_code == 404
    assert fs(host, code, hz, aktion='anfragen', ziel=hz['identity']).status_code == 400
    assert fs(a, code, az, aktion='anfragen', ziel=hz['identity']).status_code == 200
    e = ereignisse('fernsteuerung')[-1]
    assert e['daten'] == {'typ': 'fernsteuerung', 'art': 'anfrage', 'von': az['identity'], 'name': 'Anna'} and e['an'] == [hz['identity']]
    # Nur die teilende Person erteilt – mit gültiger Kennung
    assert fs(a, code, az, aktion='erteilen', an=az['identity'], kennung=KENNUNG).status_code == 403       # sich selbst
    assert fs(b, code, bz, aktion='erteilen', an=bz['identity'], kennung=KENNUNG).status_code == 403
    assert fs(host, code, hz, aktion='erteilen', an=az['identity']).status_code == 400                     # ohne Kennung
    assert fs(host, code, hz, aktion='erteilen', an=az['identity'], kennung='kurz').status_code == 400
    assert fs(host, code, hz, aktion='erteilen', an='g-unbekannt', kennung=KENNUNG).status_code == 404
    assert fs(host, code, hz, aktion='erteilen', an=az['identity'], kennung=KENNUNG).status_code == 200
    assert meta()['fernsteuerung'] == [{'von': hz['identity'], 'an': az['identity'], 'an_name': 'Anna', 'id': KENNUNG}]
    assert ereignisse('fernsteuerung')[-1]['an'] == [az['identity']]
    # Zweite Person: belegt
    assert fs(b, code, bz, aktion='anfragen', ziel=hz['identity']).status_code == 409
    assert fs(host, code, hz, aktion='erteilen', an=bz['identity'], kennung=KENNUNG).status_code == 409
    # Boris kann Annas Steuerung nicht beenden, Anna selbst schon
    assert fs(b, code, bz, aktion='beenden').status_code == 404
    assert fs(a, code, az, aktion='beenden').status_code == 200
    assert meta()['fernsteuerung'] == [] and meta()['fernsteuerung_bereit'] == [hz['identity']]
    ende = ereignisse('fernsteuerung')[-1]
    assert ende['daten']['art'] == 'beendet' and set(ende['an']) == {hz['identity'], az['identity']}
    # Ablehnen schickt nur an die anfragende Person
    assert fs(host, code, hz, aktion='ablehnen', an=bz['identity']).status_code == 200
    assert ereignisse('fernsteuerung')[-1]['an'] == [bz['identity']]
    # Teilen beendet → Freigabe weg
    fs(host, code, hz, aktion='erteilen', an=az['identity'], kennung=KENNUNG)
    assert fs(host, code, hz, aktion='bereit', an=False).status_code == 200
    assert meta()['fernsteuerung'] == [] and meta()['fernsteuerung_bereit'] == []
    assert anzahl(code) == 0


def test_host_beendet_fremde_steuerung_und_hintergrund():
    host, code, hz, a, az, b, bz = runde()
    teilt(code, az['identity'])
    fs(a, code, az, aktion='bereit', an=True)
    assert fs(a, code, az, aktion='erteilen', an=bz['identity'], kennung=KENNUNG).status_code == 200
    assert fs(host, code, hz, aktion='beenden', ziel=az['identity']).status_code == 200            # Host greift ein
    assert meta()['fernsteuerung'] == []
    # Hintergrund: Bildschirmspur weg → alles weg
    fs(a, code, az, aktion='erteilen', an=bz['identity'], kennung=KENNUNG)
    lk.FAKE_TEILNEHMER[code] = [{'identity': az['identity'], 'name': 'Anna', 'tracks': []}]
    asyncio.run(fernsteuerung.alle_pruefen())
    assert meta()['fernsteuerung'] == [] and meta()['fernsteuerung_bereit'] == []
    # Meeting-Ende räumt auf
    teilt(code, az['identity'])
    fs(a, code, az, aktion='bereit', an=True)
    host.post(f'/api/raeume/{code}/steuerung', json={'aktion': 'beenden'}, headers=T(hz))
    assert anzahl(code) == 0


def test_webinar_zuschauer_nie():
    host, _e, _p = konto(name='Wanda')
    code = raum(host)['code']
    host.patch(f'/api/raeume/{code}/webinar', json={'an': True})
    hz = zugang(host, code)
    z = neu_client()
    zz = zugang(z, code, name='Zoe')
    teilt(code, hz['identity'])
    fs(host, code, hz, aktion='bereit', an=True)
    assert fs(z, code, zz, aktion='anfragen', ziel=hz['identity']).status_code == 403
    assert fs(host, code, hz, aktion='erteilen', an=zz['identity'], kennung=KENNUNG).status_code == 403
