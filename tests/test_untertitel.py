"""Tests Live-Untertitel, Transkript und Transkript aus Aufnahme (Whisper gefälscht). Hilfen aus test_api."""
import time

from test_api import T, aufrufe, frisch, hochladen, konto, neu_client, raum, zugang  # noqa: F401

from app import db, untertitel


def ut(c, code, z, **kw):
    return c.post(f'/api/raeume/{code}/untertitel', json=kw, headers=T(z))


def test_untertitel_verteilen_und_nur_mit_schalter_speichern():
    host, _e, _p = konto(name='Hosta')
    code = raum(host, titel='Runde')['code']
    hz = zugang(host, code)
    g = neu_client()
    gz = zugang(g, code, name='Gabi')
    assert neu_client().post(f'/api/raeume/{code}/untertitel', json={'text': 'x'}).status_code == 403
    assert ut(g, code, gz, text='   ').status_code == 400
    assert ut(g, code, gz, text='x' * 501).status_code == 400
    assert ut(g, code, gz, text='Hallo zusammen', final=True, dauer_ms=1500, name='Gefälscht', identity='u1-xxx').status_code == 200
    s = aufrufe('senden')[-1]
    assert s['topic'] == 'untertitel' and s['an'] is None
    assert 'sprache' not in s['daten']
    # Sprache der Erkennung geht mit (für die Übersetzung auf dem Gerät), nur gültige Codes
    ut(g, code, gz, text='Hallo', sprache='de')
    assert aufrufe('senden')[-1]['daten']['sprache'] == 'de'
    ut(g, code, gz, text='Hallo', sprache='<script>')
    assert 'sprache' not in aufrufe('senden')[-1]['daten']
    assert s['daten']['name'] == 'Gabi' and s['daten']['identity'] == gz['identity'] and s['daten']['text'] == 'Hallo zusammen'
    # Ohne „Transkript speichern“ nichts gespeichert
    assert db.verbindung().execute('SELECT COUNT(*) FROM transkript').fetchone()[0] == 0
    # Nur der Besitzer schaltet; Metadaten + Ereignis an alle
    assert g.patch(f'/api/raeume/{code}/transkript', json={'speichern': True}).status_code == 401
    assert host.patch(f'/api/raeume/{code}/transkript', json={'speichern': True}).json() == {'speichern': True}
    assert aufrufe('metadaten')[-1]['daten']['transkript'] is True
    assert host.get(f'/api/raeume/{code}').json()['raum']['transkript_speichern'] is True
    ut(g, code, gz, text='Erster Satz', final=True, dauer_ms=1200)
    ut(g, code, gz, text='Zwischenstand', final=False)                  # Zwischenergebnisse nie gespeichert
    time.sleep(0.01)
    ut(host, code, hz, text='Antwort --> mit Pfeil', final=True, dauer_ms=900)
    rows = db.verbindung().execute('SELECT name, text FROM transkript ORDER BY id').fetchall()
    assert [tuple(x) for x in rows] == [('Gabi', 'Erster Satz'), ('Hosta', 'Antwort --> mit Pfeil')]
    # Download für den Besitzer
    assert g.get(f'/api/raeume/{code}/transkript').status_code == 401
    l = host.get(f'/api/raeume/{code}/transkripte').json()
    assert l['speichern'] and l['transkripte'][0]['abschnitte'] == 2
    txt = host.get(f'/api/raeume/{code}/transkript?format=txt')
    assert txt.status_code == 200 and 'Gabi: Erster Satz' in txt.text and txt.headers['content-type'].startswith('text/plain')
    vtt = host.get(f'/api/raeume/{code}/transkript?format=vtt').text
    assert vtt.startswith('WEBVTT') and '<v Gabi>Erster Satz' in vtt and '00:00:00.000 --> ' in vtt and '-->' not in vtt.split('<v Hosta>')[1]
    assert host.get(f'/api/raeume/{code}/transkript?format=pdf').status_code == 400
    # Löschen
    assert host.delete(f'/api/raeume/{code}/transkript').status_code == 200
    assert host.get(f'/api/raeume/{code}/transkript').status_code == 404


def test_zuschauer_senden_keine_untertitel_und_aufraeumen():
    host, _e, _p = konto()
    code = raum(host)['code']
    host.patch(f'/api/raeume/{code}/webinar', json={'an': True})
    host.patch(f'/api/raeume/{code}/transkript', json={'speichern': True})
    hz = zugang(host, code)
    z = neu_client()
    zz = zugang(z, code, name='Zoe')
    assert ut(z, code, zz, text='Ich bin nur Zuschauerin').status_code == 403
    ut(host, code, hz, text='Alt', final=True)
    untertitel.aufraeumen(int(time.time()) + 91 * 86400)
    assert db.verbindung().execute('SELECT COUNT(*) FROM transkript').fetchone()[0] == 0


def test_transkript_aus_aufnahme_mit_whisper(monkeypatch):
    c, _e, _p = konto()
    a = hochladen(c, b'\x1aE\xdf\xa3' + b'x' * 2000).json()['aufnahme']
    assert a['transkript'] is None and a['transkript_moeglich'] is False
    assert c.post(f'/api/aufnahmen/{a["id"]}/transkript').status_code == 404          # ohne AGORA_WHISPER_URL
    monkeypatch.setattr(untertitel, 'WHISPER_URL', 'http://127.0.0.1:9/v1/audio/transcriptions')
    gesehen = {}

    def falsch(pfad, typ):
        gesehen.update(pfad=pfad, typ=typ)
        return 'WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\nHallo <b>Welt</b>\n'
    monkeypatch.setattr(untertitel, 'whisper_anfrage', falsch)
    r = c.post(f'/api/aufnahmen/{a["id"]}/transkript')
    assert r.status_code == 200 and r.json()['transkript']['status'] == 'laeuft'
    for _ in range(50):
        if c.get(f'/api/aufnahmen/{a["id"]}/transkript').json()['transkript']['status'] != 'laeuft':
            break
        time.sleep(0.05)
    assert c.get('/api/aufnahmen').json()['aufnahmen'][0]['transkript'] == {'status': 'fertig', 'fehler': None}
    assert gesehen['typ'] == 'video/webm'
    assert c.get(f'/api/aufnahmen/{a["id"]}/transkript?format=vtt').text.startswith('WEBVTT')
    assert c.get(f'/api/aufnahmen/{a["id"]}/transkript?format=txt').text == 'Hallo Welt\n'
    fremd, _e2, _p2 = konto()
    assert fremd.get(f'/api/aufnahmen/{a["id"]}/transkript?format=vtt').status_code == 404
    # Fehler des Dienstes landet beim Status
    monkeypatch.setattr(untertitel, 'whisper_anfrage', lambda p, t: (_ for _ in ()).throw(OSError('Verbindung abgelehnt')))
    c.post(f'/api/aufnahmen/{a["id"]}/transkript')
    for _ in range(50):
        s = c.get(f'/api/aufnahmen/{a["id"]}/transkript').json()['transkript']
        if s['status'] != 'laeuft':
            break
        time.sleep(0.05)
    assert s['status'] == 'fehler' and 'Verbindung abgelehnt' in s['fehler']
    # Mit der Aufnahme verschwindet die .vtt
    assert c.delete(f'/api/aufnahmen/{a["id"]}').status_code == 200
    import os
    assert not os.path.exists(untertitel.vtt_pfad(a['id']))


def test_multipart_streamt_datei(tmp_path):
    p = tmp_path / 'a.webm'
    p.write_bytes(b'ABC' * 10)
    grenze, laenge, teile = untertitel._multipart({'model': 'whisper-1'}, 'file', 'a.webm', 'video/webm', str(p))
    koerper = b''.join(teile)
    assert len(koerper) == laenge and koerper.count(grenze.encode()) == 3 and b'ABC' * 10 in koerper and b'name="model"' in koerper
