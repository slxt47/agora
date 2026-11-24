"""Gemeinsame Notizen: Versionsprüfung (409 mit Stand), Ereignis, Grenzen, Ende des Meetings / Meeting-Verlauf."""
import asyncio

from test_api import T, ereignisse, meeting, neu_client, zugang  # noqa: F401

from app import db, raeume


def speichern(c, code, z, text, version):
    return c.put(f'/api/raeume/{code}/notizen', json={'text': text, 'version': version}, headers=T(z))


def test_notizen_version_konflikt_und_ereignis():
    host, code, hz, [(g1, z1)] = meeting(1)
    url = f'/api/raeume/{code}/notizen'
    assert g1.get(url, headers=T(z1)).json() == {'text': '', 'version': 0, 'geaendert': None, 'von_name': None}
    r = speichern(host, code, hz, '# Agenda\n- Punkt A', 0)
    assert r.status_code == 200 and r.json()['version'] == 1 and r.json()['von_name'] == 'Ada' and r.json()['geaendert'].endswith('Z')
    e = ereignisse('notizen')[-1]
    assert e['an'] is None and e['daten'] == {'typ': 'notizen', 'version': 1, 'von': hz['identity'], 'von_name': 'Ada', 'text': '# Agenda\n- Punkt A'}
    # Gast speichert auf Version 1 → 2; Host schreibt noch auf Version 1 → 409 mit aktuellem Stand
    assert speichern(g1, code, z1, '# Agenda\n- Punkt A\n- Punkt B', 1).json()['version'] == 2
    r = speichern(host, code, hz, '# Agenda neu\n- Punkt A', 1)
    assert r.status_code == 409
    assert r.json()['konflikt']['version'] == 2 and r.json()['konflikt']['text'] == '# Agenda\n- Punkt A\n- Punkt B'
    assert r.json()['konflikt']['von_name'] == 'Gast 1' and 'jemand anderes' in r.json()['fehler']
    assert g1.get(url, headers=T(z1)).json()['version'] == 2
    # Steuerzeichen raus, \r weg
    assert speichern(host, code, hz, 'a\r\nb\x00c', 2).json()['text'] == 'a\nbc'


def test_notizen_pruefung_und_grenzen():
    host, code, hz, _g = meeting(0)
    for body in ({'text': 5, 'version': 0}, {'text': 'x', 'version': '0'}, {'text': 'x', 'version': -1}, {'text': 'x', 'version': True}, {'text': 'x'}):
        assert host.put(f'/api/raeume/{code}/notizen', json=body, headers=T(hz)).status_code == 400, body
    assert speichern(host, code, hz, 'x' * 100001, 0).status_code == 413
    # Großer Text: Ereignis ohne Text (Clients holen per GET)
    assert speichern(host, code, hz, 'y' * 20000, 0).status_code == 200
    assert 'text' not in ereignisse('notizen')[-1]['daten']
    assert neu_client().get(f'/api/raeume/{code}/notizen', headers={'X-Teilnahme': 'falsch'}).status_code == 403
    # ohne X-Agora (CSRF) abgelehnt
    from fastapi.testclient import TestClient
    from app import main
    assert TestClient(main.app).put(f'/api/raeume/{code}/notizen', json={'text': 'x', 'version': 1}, headers=T(hz)).status_code == 403


def test_notizen_mit_meeting_weg_oder_im_verlauf():
    host, code, hz, _g = meeting(0)
    speichern(host, code, hz, 'geheim', 0)
    r = raeume.raum_laden(code)
    asyncio.run(raeume.meeting_beenden(r))
    assert db.verbindung().execute('SELECT COUNT(*) FROM notizen WHERE raum_id = ?', (r['id'],)).fetchone()[0] == 0
    host, code, hz, _g = meeting(0, chat_speichern=True)
    speichern(host, code, hz, '# Protokoll\n- erledigt', 0)
    asyncio.run(raeume.meeting_beenden(raeume.raum_laden(code)))
    v = host.get('/api/verlauf').json()['meetings'][0]
    assert host.get(f'/api/verlauf/{v["id"]}/inhalt').json()['notizen'] == '# Protokoll\n- erledigt'
    hz2 = zugang(host, code)
    assert host.get(f'/api/raeume/{code}/notizen', headers=T(hz2)).json()['version'] == 0     # neues Meeting, neue Notizen
