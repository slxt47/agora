"""Gemeinsames Whiteboard: Operationen, Reihenfolge (seq), Grenzen, Sperre/Leeren nur Moderation, Ende des Meetings."""
import asyncio

from test_api import T, konto, meeting, neu_client, raum, steuern, zugang  # noqa: F401

from app import db, raeume, whiteboard
from app import livekit_dienst as lk


def strich(i='strich-1', **kw):
    return {'id': i, 'art': 'stift', 'farbe': '#1d1d1f', 'breite': 3, 'punkte': [0, 0, 10, 10, 20, 5]} | kw


def form(i, art='rechteck', **kw):
    return {'id': i, 'art': art, 'farbe': '#2f80ed', 'breite': 2, 'x': 10, 'y': 20, 'w': 100, 'h': 50} | kw


def ops(c, code, z, *liste):
    return c.post(f'/api/raeume/{code}/whiteboard/ops', json={'ops': list(liste)}, headers=T(z))


def wb_pakete():
    return [kw for n, kw in lk.AUFRUFE if n == 'senden' and kw['topic'] == 'whiteboard']


def test_whiteboard_ops_reihenfolge_und_stand():
    host, code, hz, [(g1, z1)] = meeting(1)
    leer = g1.get(f'/api/raeume/{code}/whiteboard', headers=T(z1)).json()
    assert leer == {'seq': 0, 'objekte': [], 'gesperrt': False, 'darf_zeichnen': True}
    r = ops(host, code, hz, {'art': 'neu', 'objekt': strich()}, {'art': 'neu', 'objekt': form('form-1', 'ellipse')})
    assert r.status_code == 200 and r.json() == {'seq': 2, 'angenommen': 2}
    p = wb_pakete()[-1]
    assert p['an'] is None and p['daten']['typ'] == 'ops' and p['daten']['von_seq'] == 1 and p['daten']['seq'] == 2
    assert p['daten']['von'] == hz['identity'] and len(p['daten']['ops']) == 2
    # Gast verschiebt die Ellipse, Text + Notiz dazu, Radierer löscht den Strich
    moved = form('form-1', 'ellipse', x=50.04)
    assert ops(g1, code, z1, {'art': 'aendern', 'objekt': moved}).json()['seq'] == 3
    assert ops(g1, code, z1, {'art': 'neu', 'objekt': form('notiz-1', 'notiz', farbe='#ffe58a', text='Idee\nzwei')}).status_code == 200
    assert ops(g1, code, z1, {'art': 'loeschen', 'id': 'strich-1'}).json()['seq'] == 5
    # Unbekannte id beim Ändern/Löschen: still übergangen (gleichzeitig gelöscht), kein Paket
    n = len(wb_pakete())
    assert ops(g1, code, z1, {'art': 'loeschen', 'id': 'gibts-nicht'}).json() == {'seq': 5, 'angenommen': 0}
    assert len(wb_pakete()) == n
    s = host.get(f'/api/raeume/{code}/whiteboard', headers=T(hz)).json()
    assert s['seq'] == 5 and [o['id'] for o in s['objekte']] == ['form-1', 'notiz-1']
    assert s['objekte'][0]['x'] == 50.0 and s['objekte'][1]['text'] == 'Idee\nzwei'
    # Stand ohne Zwischenspeicher (Neustart) ist derselbe
    whiteboard._cache.clear()
    assert host.get(f'/api/raeume/{code}/whiteboard', headers=T(hz)).json() == s
    # Doppelte id → 409, nichts übernommen
    assert ops(host, code, hz, {'art': 'neu', 'objekt': form('neu-a1')}, {'art': 'neu', 'objekt': form('notiz-1')}).status_code == 409
    assert len(host.get(f'/api/raeume/{code}/whiteboard', headers=T(hz)).json()['objekte']) == 2
    # Fremde kommen nicht heran
    assert neu_client().get(f'/api/raeume/{code}/whiteboard', headers={'X-Teilnahme': 'falsch'}).status_code == 403


def test_whiteboard_pruefung():
    host, code, hz, _g = meeting(0)
    falsch = [
        {'art': 'neu', 'objekt': strich(id='x')},                       # id zu kurz
        {'art': 'neu', 'objekt': strich(art='kreis')},
        {'art': 'neu', 'objekt': strich(farbe='red')},
        {'art': 'neu', 'objekt': strich(farbe='#FFFFFF')},
        {'art': 'neu', 'objekt': strich(punkte=[1, 2, 3])},
        {'art': 'neu', 'objekt': strich(punkte=[0, 0] * 2001)},
        {'art': 'neu', 'objekt': strich(punkte=[0, 'a'])},
        {'art': 'neu', 'objekt': strich(punkte=[0, True])},
        {'art': 'neu', 'objekt': strich(breite=99)},
        {'art': 'neu', 'objekt': form('form-x', x=10 ** 7)},
        {'art': 'neu', 'objekt': form('form-x', y=None)},
        {'art': 'neu', 'objekt': form('text-x', 'text', text='x' * 2001)},
        {'art': 'malen', 'objekt': strich()},
        {'art': 'loeschen', 'id': 5},
        'neu',
    ]
    for op in falsch:
        assert ops(host, code, hz, op).status_code == 400, op
    assert host.post(f'/api/raeume/{code}/whiteboard/ops', json={'ops': []}, headers=T(hz)).status_code == 400
    assert ops(host, code, hz, *[{'art': 'neu', 'objekt': strich(f'strich-{i}')} for i in range(21)]).status_code == 400
    # Unbekannte Felder werden verworfen, Steuerzeichen aus Text entfernt
    ops(host, code, hz, {'art': 'neu', 'objekt': form('text-1', 'text', text='a\x00b', boese='<script>')})
    o = host.get(f'/api/raeume/{code}/whiteboard', headers=T(hz)).json()['objekte'][0]
    assert o == {'id': 'text-1', 'art': 'text', 'farbe': '#2f80ed', 'breite': 2.0, 'x': 10.0, 'y': 20.0, 'w': 100.0, 'h': 50.0, 'text': 'ab'}


def test_whiteboard_grenzen_und_verdichten(monkeypatch):
    host, code, hz, _g = meeting(0)
    monkeypatch.setattr(whiteboard, 'MAX_OBJEKTE', 5)
    assert ops(host, code, hz, *[{'art': 'neu', 'objekt': strich(f'strich-{i}')} for i in range(5)]).status_code == 200
    r = ops(host, code, hz, {'art': 'neu', 'objekt': strich('strich-x')})
    assert r.status_code == 413 and 'voll' in r.json()['fehler']
    # Verdichten: Log wird zum Schnappschuss, Stand und seq bleiben
    monkeypatch.setattr(whiteboard, 'VERDICHTEN_AB', 8)
    for i in range(4):
        ops(host, code, hz, {'art': 'aendern', 'objekt': strich('strich-0', punkte=[i, i, 5, 5])})
    c = db.verbindung()
    rid = raeume.raum_laden(code)['id']
    assert c.execute('SELECT COUNT(*) FROM whiteboard_ops WHERE raum_id = ?', (rid,)).fetchone()[0] == 5
    whiteboard._cache.clear()
    s = host.get(f'/api/raeume/{code}/whiteboard', headers=T(hz)).json()
    assert s['seq'] == 9 and len(s['objekte']) == 5 and s['objekte'][0]['punkte'] == [3.0, 3.0, 5.0, 5.0]
    # Großes Paket → nur „neu_laden“ über den Datenkanal
    monkeypatch.setattr(whiteboard, 'MAX_OBJEKTE', 50)
    ops(host, code, hz, {'art': 'neu', 'objekt': strich('strich-gross', punkte=[1234.5] * 3000)})
    assert wb_pakete()[-1]['daten'] == {'typ': 'neu_laden', 'seq': 10, 'von': hz['identity'], 'von_name': 'Ada'}


def test_whiteboard_sperren_leeren_zeigen_nur_moderation():
    host, code, hz, [(g1, z1)] = meeting(1)
    url = f'/api/raeume/{code}/whiteboard'
    ops(g1, code, z1, {'art': 'neu', 'objekt': strich()})
    for pfad, body in (('/sperren', {'an': True}), ('/leeren', None), ('/zeigen', None)):
        assert g1.post(url + pfad, json=body, headers=T(z1)).status_code == 403
    assert host.post(url + '/sperren', json={'an': 'ja'}, headers=T(hz)).status_code == 400
    assert host.post(url + '/sperren', json={'an': True}, headers=T(hz)).json() == {'ok': True, 'gesperrt': True}
    assert wb_pakete()[-1]['daten'] == {'typ': 'gesperrt', 'an': True, 'von_name': 'Ada'}
    assert g1.get(url, headers=T(z1)).json()['darf_zeichnen'] is False
    r = ops(g1, code, z1, {'art': 'neu', 'objekt': strich('strich-2')})
    assert r.status_code == 403 and 'gesperrt' in r.json()['fehler']
    assert ops(host, code, hz, {'art': 'neu', 'objekt': strich('strich-3')}).status_code == 200     # Moderation darf weiter
    # Co-Host darf leeren
    steuern(host, code, hz, aktion='rolle', ziel=z1['identity'], rolle='cohost')
    r = g1.post(url + '/leeren', headers=T(z1))
    assert r.status_code == 200 and r.json()['seq'] == 3
    assert wb_pakete()[-1]['daten'] == {'typ': 'geleert', 'seq': 3, 'von_name': 'Gast 1'}
    assert host.get(url, headers=T(hz)).json()['objekte'] == []
    assert host.post(url + '/zeigen', headers=T(hz)).status_code == 200
    assert wb_pakete()[-1]['daten']['typ'] == 'zeigen'


def test_whiteboard_mit_meeting_weg_oder_im_verlauf():
    # ohne chat_speichern: beim Ende gelöscht
    host, code, hz, _g = meeting(0)
    ops(host, code, hz, {'art': 'neu', 'objekt': strich()})
    r = raeume.raum_laden(code)
    asyncio.run(raeume.meeting_beenden(r))
    c = db.verbindung()
    assert c.execute('SELECT COUNT(*) FROM whiteboard_ops WHERE raum_id = ?', (r['id'],)).fetchone()[0] == 0
    assert c.execute('SELECT COUNT(*) FROM whiteboard WHERE raum_id = ?', (r['id'],)).fetchone()[0] == 0
    # mit chat_speichern: bleibt, Besitzer sieht es im Meeting-Verlauf; neues Meeting beginnt leer
    host, code, hz, _g = meeting(0, chat_speichern=True)
    ops(host, code, hz, {'art': 'neu', 'objekt': form('form-1')})
    asyncio.run(raeume.meeting_beenden(raeume.raum_laden(code)))
    v = host.get('/api/verlauf').json()['meetings'][0]
    inhalt = host.get(f'/api/verlauf/{v["id"]}/inhalt').json()
    assert [o['id'] for o in inhalt['whiteboard']] == ['form-1']
    hz2 = zugang(host, code)
    assert host.get(f'/api/raeume/{code}/whiteboard', headers=T(hz2)).json()['objekte'] == []
