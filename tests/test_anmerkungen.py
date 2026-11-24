"""Tests Anmerkungen-Recht: wer darf es ändern, Metadaten, Ereignis, Zurücksetzen beim Ende. Hilfen aus test_api."""
from test_api import T, aufrufe, ereignisse, frisch, meeting, neu_client, steuern  # noqa: F401

from app import db
from app import livekit_dienst as lk


def setzen(c, code, z, **kw):
    return c.post(f'/api/raeume/{code}/anmerkungen', json=kw, headers=T(z))


def test_anmerkungen_recht_host_und_teilende_person():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    assert setzen(host, code, hz, erlaubt='nein').status_code == 400
    assert neu_client().post(f'/api/raeume/{code}/anmerkungen', json={'erlaubt': False}).status_code == 403
    # Gast ohne Bildschirmfreigabe darf nicht
    lk.FAKE_TEILNEHMER[code] = [{'identity': z1['identity'], 'name': 'Gast 1', 'tracks': [{'sid': 'TR_m', 'quelle': 'mikrofon', 'stumm': False}]}]
    r = setzen(g1, code, z1, erlaubt=False)
    assert r.status_code == 403 and 'teilt' in r.json()['fehler']
    # Host darf; Metadaten und Ereignis
    r = setzen(host, code, hz, erlaubt=False)
    assert r.status_code == 200 and r.json() == {'ok': True, 'erlaubt': False}
    assert aufrufe('metadaten')[-1]['daten']['anmerkungen'] is False
    e = ereignisse('anmerkungen')[-1]['daten']
    assert e['erlaubt'] is False and e['von_name']
    # wer gerade teilt, darf (auch als Gast)
    lk.FAKE_TEILNEHMER[code] = [{'identity': z1['identity'], 'name': 'Gast 1', 'tracks': [{'sid': 'TR_s', 'quelle': 'bildschirm', 'stumm': False}]}]
    assert setzen(g1, code, z1, erlaubt=True).status_code == 200
    assert aufrufe('metadaten')[-1]['daten']['anmerkungen'] is True
    assert setzen(g2, code, z2, erlaubt=False).status_code == 403
    # Rechte-Metadaten bleiben vollständig
    md = aufrufe('metadaten')[-1]['daten']
    assert {'rechte', 'gesperrt', 'aufnahme', 'anmerkungen'} <= set(md)


def test_anmerkungen_beim_ende_wieder_erlaubt():
    host, code, hz, _g = meeting(0)
    assert setzen(host, code, hz, erlaubt=False).status_code == 200
    assert db.verbindung().execute('SELECT anmerkungen FROM raeume WHERE code = ?', (code,)).fetchone()[0] == 0
    assert steuern(host, code, hz, aktion='beenden').status_code == 200
    assert db.verbindung().execute('SELECT anmerkungen FROM raeume WHERE code = ?', (code,)).fetchone()[0] == 1


def test_anmerkungen_ohne_livekit_503():
    host, code, hz, [(g1, z1)] = meeting(1)
    orig = lk.teilnehmer

    async def weg(_code):
        return None
    lk.teilnehmer = weg
    try:
        assert setzen(g1, code, z1, erlaubt=False).status_code == 503
        assert setzen(host, code, hz, erlaubt=False).status_code == 200      # Host braucht LiveKit dafür nicht
    finally:
        lk.teilnehmer = orig
