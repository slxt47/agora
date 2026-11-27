"""KI-Zusammenfassung + Kapitelmarken (gefälschter OpenAI-kompatibler Server), Stichwort-Kapitel ohne KI, Löschen mit
dem Transkript bzw. der Aufnahme. Hilfen aus test_api."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_api import T, hochladen, konto, neu_client, raum, zugang  # noqa: F401

from app import db, untertitel
from app import zusammenfassung as zf


class FakeKI:
    """Antwortet wie /v1/chat/completions; merkt sich die Anfragen."""
    def __init__(self):
        self.anfragen = []
        self.status = 200
        ki = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):     # noqa: N802
                d = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                ki.anfragen.append({'pfad': self.path, 'auth': self.headers.get('Authorization'), 'body': d})
                if ki.status != 200:
                    self.send_response(ki.status)
                    self.end_headers()
                    return
                text = d['messages'][-1]['content']
                if d['messages'][0]['content'] not in zf.ZUSAMMEN.values():      # ein Teil (nicht der Schluss-Aufruf)
                    erste = text.splitlines()[1]
                    zeit = erste[1:erste.index(']')]
                    inhalt = {'zusammenfassung': f'Teil-Zusammenfassung ({len(ki.anfragen)})',
                              'kapitel': [{'zeit': zeit, 'titel': f'Thema ab {zeit}'}, {'zeit': '99:99', 'titel': 'kaputt'}],
                              'aufgaben': [{'text': 'Protokoll verschicken', 'wer': 'Gabi'}, 'Folien hochladen']}
                    antwort = 'Hier das Ergebnis:\n```json\n' + json.dumps(inhalt, ensure_ascii=False) + '\n```'
                else:
                    antwort = json.dumps({'zusammenfassung': 'Gesamte Zusammenfassung.'})
                body = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': antwort}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'


@pytest.fixture
def ki(monkeypatch):
    k = FakeKI()
    monkeypatch.setattr(zf, 'KI_URL', k.url)
    monkeypatch.setattr(zf, 'KI_MODELL', 'fake-modell')
    monkeypatch.setenv('AGORA_KI_SCHLUESSEL', 'nur-test')
    yield k
    k.server.shutdown()


def warten(c, pfad):
    for _ in range(200):
        z = c.get(pfad).json()['zusammenfassung']
        if z and z['status'] != 'laeuft':
            return z
        time.sleep(0.05)
    raise AssertionError('Zusammenfassung wird nicht fertig')


def transkript(host, code, saetze):
    """Gespeichertes Transkript mit festen Zeiten (direkt in die Tabelle)."""
    host.patch(f'/api/raeume/{code}/transkript', json={'speichern': True})
    z = zugang(host, code)
    r = db.verbindung().execute('SELECT id, sitzung FROM raeume WHERE code = ?', (code,)).fetchone()
    t0 = 1_700_000_000_000
    for sek, name, text in saetze:
        db.verbindung().execute('INSERT INTO transkript (raum_id, sitzung, identity, name, text, beginn_ms, ende_ms, zeit) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                                (r['id'], r['sitzung'], z['identity'], name, text, t0 + sek * 1000, t0 + sek * 1000 + 3000, int(time.time())))
    return r['sitzung']


SAETZE = [(0, 'Gabi', 'Willkommen zur Budgetplanung, heute geht es um das Budget für Marketing.'),
          (20, 'Hans', 'Das Marketing Budget steigt, Kampagnen im Frühjahr.'),
          (45, 'Gabi', 'Budget Marketing Kampagnen sind abgestimmt.'),
          (300, 'Hans', 'Nächstes Thema: Personalplanung und neue Stellen im Vertrieb.'),
          (320, 'Gabi', 'Personalplanung Vertrieb zwei Stellen ausschreiben.'),
          (360, 'Hans', 'Die Stellen im Vertrieb schreiben wir nächste Woche aus.')]


def test_stichwort_kapitel_ohne_ki():
    host, _e, _p = konto()
    code = raum(host, titel='Planung')['code']
    s = transkript(host, code, SAETZE)
    r = host.get(f'/api/raeume/{code}/zusammenfassung').json()
    assert r == {'zusammenfassung': None, 'ki_verfuegbar': False, 'ki_ziel': None}
    assert host.post(f'/api/raeume/{code}/zusammenfassung', json={'art': 'ki', 'zustimmung': True}).status_code == 404   # ohne Modell
    assert host.post(f'/api/raeume/{code}/zusammenfassung', json={'art': 'quatsch'}).status_code == 400
    assert neu_client().post(f'/api/raeume/{code}/zusammenfassung', json={'art': 'stichwort'}).status_code == 401
    z = host.post(f'/api/raeume/{code}/zusammenfassung?sitzung={s}', json={'art': 'stichwort'}).json()['zusammenfassung']
    assert z['art'] == 'stichwort' and z['status'] == 'fertig' and z['ergebnis']['zusammenfassung'] is None
    k = z['ergebnis']['kapitel']
    assert [x['start_ms'] for x in k] == [0, 300000]                  # lange Pause → neues Kapitel
    assert 'Budget' in k[0]['titel'] and 'Marketing' in k[0]['titel']
    assert 'Vertrieb' in k[1]['titel'] and set(k[1]['sprecher']) == {'Hans', 'Gabi'}
    vtt = host.get(f'/api/raeume/{code}/kapitel.vtt')
    assert vtt.status_code == 200 and vtt.text.startswith('WEBVTT') and '00:00:00.000 --> 00:05:00.000' in vtt.text
    # Transkript löschen → Zusammenfassung weg
    host.delete(f'/api/raeume/{code}/transkript?sitzung={s}')
    assert host.get(f'/api/raeume/{code}/zusammenfassung?sitzung={s}').json()['zusammenfassung'] is None
    assert host.get(f'/api/raeume/{code}/kapitel.vtt?sitzung={s}').status_code == 404


def test_heuristik_und_hilfen():
    a = [{'ms': i * 10000, 'ende_ms': i * 10000 + 5000, 'name': 'A' if i < 30 else 'B',
          'text': 'Server Datenbank Umzug' if i < 30 else 'Urlaub Planung Sommer'} for i in range(60)]
    k = zf.stichwort_kapitel(a)
    assert k[0]['start_ms'] == 0 and any(x['start_ms'] == 300000 for x in k)      # Sprecherwechsel nach ≥ 3 min
    assert zf.stichwort_kapitel([]) == []
    assert zf.zeit_ms('01:02:03.5') == 3723500 and zf.zeit_ms('02:03') == 123000 and zf.zeit_ms('x') is None
    vtt = 'WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\n<v Gabi>Hallo <b>Welt</b>\n\n2\n00:01:00.000 --> 00:01:02.500\nohne Sprecher\n'
    assert zf.abschnitte_vtt(vtt) == [{'ms': 1000, 'ende_ms': 4000, 'name': 'Gabi', 'text': 'Hallo Welt'},
                                      {'ms': 60000, 'ende_ms': 62500, 'name': '', 'text': 'ohne Sprecher'}]
    assert [len(t) for t in zf.teilen(['a' * 10, 'b' * 10, 'c' * 10], 23)] == [2, 1]
    assert zf.json_aus('bla ```json\n{"a": 1}\n``` bla') == {'a': 1}


def test_ki_mit_zustimmung_und_teilen(ki, monkeypatch):
    monkeypatch.setattr(zf, 'KI_ZEICHEN', 2000)
    host, _e, _p = konto(name='Hosta')
    code = raum(host, titel='Planung')['code']
    langer = SAETZE + [(400 + i * 5, 'Hans', f'Punkt {i}: ' + 'Details zum Vertrieb ' * 5) for i in range(40)]
    s = transkript(host, code, langer)
    r = host.get(f'/api/raeume/{code}/zusammenfassung').json()
    assert r['ki_verfuegbar'] is True and r['ki_ziel'] == ki.url.split('//')[1].split('/')[0]
    assert host.post(f'/api/raeume/{code}/zusammenfassung', json={'art': 'ki'}).status_code == 400        # ohne Zustimmung
    assert not ki.anfragen
    r = host.post(f'/api/raeume/{code}/zusammenfassung?sitzung={s}', json={'art': 'ki', 'zustimmung': True})
    assert r.status_code == 200 and r.json()['zusammenfassung']['status'] == 'laeuft'
    z = warten(host, f'/api/raeume/{code}/zusammenfassung?sitzung={s}')
    assert z['status'] == 'fertig', z
    teile = [a for a in ki.anfragen if a['body']['messages'][0]['content'] not in zf.ZUSAMMEN.values()]
    assert len(teile) >= 2 and len(ki.anfragen) == len(teile) + 1
    assert ki.anfragen[0]['pfad'] == '/v1/chat/completions' and ki.anfragen[0]['auth'] == 'Bearer nur-test'
    assert ki.anfragen[0]['body']['model'] == 'fake-modell' and 'Gabi: Willkommen' in ki.anfragen[0]['body']['messages'][1]['content']
    assert all(len(a['body']['messages'][-1]['content']) < 2200 for a in teile)
    e = z['ergebnis']
    assert e['zusammenfassung'] == 'Gesamte Zusammenfassung.' and e['teile'] == len(teile)
    assert e['kapitel'][0] == {'start_ms': 0, 'titel': 'Thema ab 00:00'} and all(k['titel'] != 'kaputt' for k in e['kapitel'])
    assert e['aufgaben'] == [{'text': 'Protokoll verschicken', 'wer': 'Gabi'}, {'text': 'Folien hochladen', 'wer': ''}]   # ohne Dubletten
    assert z['modell'] == 'fake-modell'
    assert db.verbindung().execute("SELECT COUNT(*) FROM protokoll WHERE ereignis LIKE 'KI-Zusammenfassung angefordert (127.0.0.1:%'").fetchone()[0]
    # Fehler des Dienstes → Status fehler
    ki.status = 500
    host.post(f'/api/raeume/{code}/zusammenfassung?sitzung={s}', json={'art': 'ki', 'zustimmung': True})
    z = warten(host, f'/api/raeume/{code}/zusammenfassung?sitzung={s}')
    assert z['status'] == 'fehler' and z['fehler'] == 'HTTP 500'
    # andere dürfen nicht
    other, _e, _p = konto()
    assert other.get(f'/api/raeume/{code}/zusammenfassung').status_code == 403


def test_aufnahme_whisper_transkript_kapitel_und_loeschen(ki):
    c, _e, _p = konto()
    a = hochladen(c, b'\x1aE\xdf\xa3' + b'x' * 2000).json()['aufnahme']
    assert c.post(f'/api/aufnahmen/{a["id"]}/zusammenfassung', json={'art': 'stichwort'}).status_code == 404     # noch kein Transkript
    with open(untertitel.vtt_pfad(a['id']), 'w', encoding='utf-8') as f:
        f.write('WEBVTT\n\n1\n00:00:00.000 --> 00:00:05.000\nBudget Marketing Kampagnen\n\n2\n00:02:00.000 --> 00:02:05.000\nVertrieb Stellen\n')
    z = c.post(f'/api/aufnahmen/{a["id"]}/zusammenfassung', json={'art': 'ki', 'zustimmung': True}).json()['zusammenfassung']
    assert z['status'] == 'laeuft'
    z = warten(c, f'/api/aufnahmen/{a["id"]}/zusammenfassung')
    assert z['status'] == 'fertig' and z['ergebnis']['kapitel']
    assert c.get('/api/aufnahmen').json()['aufnahmen'][0]['zusammenfassung'] == {'art': 'ki', 'status': 'fertig'}
    vtt = c.get(f'/api/aufnahmen/{a["id"]}/kapitel.vtt')
    assert vtt.status_code == 200 and 'Thema ab 00:00' in vtt.text
    c.delete(f'/api/aufnahmen/{a["id"]}')
    assert db.verbindung().execute('SELECT COUNT(*) FROM zusammenfassungen WHERE aufnahme_id = ?', (a['id'],)).fetchone()[0] == 0
