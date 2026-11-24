"""Q&A: Fragen (anonym), Stimmen, Moderation, Sichtbarkeit verworfener Fragen, Ende des Meetings / Meeting-Verlauf."""
import asyncio

from test_api import T, ereignisse, meeting, steuern, zugang  # noqa: F401

from app import db, raeume


def frage(c, code, z, text='Wie geht es weiter?', **kw):
    return c.post(f'/api/raeume/{code}/fragen', json={'text': text, **kw}, headers=T(z))


def test_fragen_stellen_anonym_und_stimmen():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    url = f'/api/raeume/{code}/fragen'
    assert frage(g1, code, z1, text='').status_code == 400
    assert frage(g1, code, z1, text='x' * 501).status_code == 400
    assert frage(g1, code, z1, anonym='ja').status_code == 400
    r = frage(g1, code, z1, anonym=True)
    assert r.status_code == 200
    f = r.json()['frage']
    assert f['anonym'] and f['name'] is None and f['meins'] and f['stimmen'] == 0 and f['status'] == 'offen'
    e = ereignisse('frage')[-1]
    assert e['an'] is None and e['daten']['aktion'] == 'neu' and e['daten']['frage']['name'] is None and 'meins' not in e['daten']['frage']
    # Auch der Host sieht keinen Namen – der Server kennt ihn trotzdem
    sicht = host.get(url, headers=T(hz)).json()['fragen'][0]
    assert sicht['name'] is None and not sicht['meins']
    assert db.verbindung().execute('SELECT name, identity FROM fragen WHERE id = ?', (f['id'],)).fetchone()['identity'] == z1['identity']
    benannt = frage(g2, code, z2, text='Wann ist Pause?').json()['frage']
    assert benannt['name'] == 'Gast 2' and not benannt['anonym']
    # Stimmen: nicht für die eigene, eine je Person, zurücknehmen
    assert g1.post(f'{url}/{f["id"]}/stimme', json={'an': True}, headers=T(z1)).status_code == 400
    assert g2.post(f'{url}/{f["id"]}/stimme', json={'an': 1}, headers=T(z2)).status_code == 400
    for _ in range(2):
        r = g2.post(f'{url}/{f["id"]}/stimme', json={'an': True}, headers=T(z2))
        assert r.json()['frage']['stimmen'] == 1 and r.json()['frage']['ich_gestimmt']
    assert host.post(f'{url}/{f["id"]}/stimme', json={'an': True}, headers=T(hz)).json()['frage']['stimmen'] == 2
    assert g2.post(f'{url}/{f["id"]}/stimme', json={'an': False}, headers=T(z2)).json()['frage']['stimmen'] == 1
    assert ereignisse('frage')[-1]['daten']['frage']['stimmen'] == 1
    assert host.post(f'{url}/9999/stimme', json={'an': True}, headers=T(hz)).status_code == 404


def test_fragen_moderation_und_sichtbarkeit():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    url = f'/api/raeume/{code}/fragen'
    f = frage(g1, code, z1).json()['frage']
    m = f'{url}/{f["id"]}/moderieren'
    assert g2.post(m, json={'aktion': 'beantwortet'}, headers=T(z2)).status_code == 403
    assert host.post(m, json={'aktion': 'loeschen'}, headers=T(hz)).status_code == 400
    assert host.post(m, json={'aktion': 'hervorheben'}, headers=T(hz)).json()['frage']['hervorgehoben'] is True
    r = host.post(m, json={'aktion': 'beantwortet', 'antwort': 'Nächste Woche.'}, headers=T(hz)).json()['frage']
    assert r['status'] == 'beantwortet' and r['antwort'] == 'Nächste Woche.' and r['antwort_von'] == 'Ada' and not r['hervorgehoben']
    assert r['beantwortet'].endswith('Z')
    # Verwerfen: nur Moderation + fragende Person sehen sie, die anderen bekommen „geloescht“
    host.post(m, json={'aktion': 'verworfen'}, headers=T(hz))
    letzte = ereignisse('frage')[-2:]
    innen = next(e for e in letzte if e['daten']['aktion'] == 'aktualisiert')
    aussen = next(e for e in letzte if e['daten']['aktion'] == 'geloescht')
    assert sorted(innen['an']) == sorted([hz['identity'], z1['identity']]) and aussen['an'] == [z2['identity']]
    assert g2.get(url, headers=T(z2)).json()['fragen'] == []
    assert len(g1.get(url, headers=T(z1)).json()['fragen']) == 1 and len(host.get(url, headers=T(hz)).json()['fragen']) == 1
    assert g2.post(f'{url}/{f["id"]}/stimme', json={'an': True}, headers=T(z2)).status_code == 409
    # Wieder offen → für alle wieder da („neu“)
    host.post(m, json={'aktion': 'offen'}, headers=T(hz))
    assert ereignisse('frage')[-1]['daten']['aktion'] == 'neu' and ereignisse('frage')[-1]['an'] is None
    assert len(g2.get(url, headers=T(z2)).json()['fragen']) == 1
    # Zurückziehen: nur eigene offene; Moderation darf immer
    assert g2.delete(f'{url}/{f["id"]}', headers=T(z2)).status_code == 403
    assert g1.delete(f'{url}/{f["id"]}', headers=T(z1)).json() == {'ok': True}
    assert ereignisse('frage')[-1]['daten'] == {'typ': 'frage', 'aktion': 'geloescht', 'frage': {'id': f['id']}}
    f2 = frage(g1, code, z1).json()['frage']
    host.post(f'{url}/{f2["id"]}/moderieren', json={'aktion': 'beantwortet'}, headers=T(hz))
    assert g1.delete(f'{url}/{f2["id"]}', headers=T(z1)).status_code == 403
    assert host.delete(f'{url}/{f2["id"]}', headers=T(hz)).status_code == 200


def test_fragen_mit_meeting_weg_oder_im_verlauf():
    host, code, hz, [(g1, z1)] = meeting(1)
    frage(g1, code, z1)
    r = raeume.raum_laden(code)
    asyncio.run(raeume.meeting_beenden(r))
    assert db.verbindung().execute('SELECT COUNT(*) FROM fragen WHERE raum_id = ?', (r['id'],)).fetchone()[0] == 0
    host, code, hz, [(g1, z1)] = meeting(1, chat_speichern=True)
    a = frage(g1, code, z1, text='anonym?', anonym=True).json()['frage']
    b = frage(g1, code, z1, text='weg damit').json()['frage']
    host.post(f'/api/raeume/{code}/fragen/{b["id"]}/moderieren', json={'aktion': 'verworfen'}, headers=T(hz))
    host.post(f'/api/raeume/{code}/fragen/{a["id"]}/stimme', json={'an': True}, headers=T(hz))
    asyncio.run(raeume.meeting_beenden(raeume.raum_laden(code)))
    v = host.get('/api/verlauf').json()['meetings'][0]
    fr = host.get(f'/api/verlauf/{v["id"]}/inhalt').json()['fragen']
    assert [(x['text'], x['name'], x['stimmen']) for x in fr] == [('anonym?', None, 1)]
