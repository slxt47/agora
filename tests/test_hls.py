"""Tests öffentliche Übertragung (HLS): Rechte, Zugang (öffentlich / nur Angemeldete), Auslieferung mit festen
Dateinamen (kein Pfad-Trick), Cache-Kopfzeilen, Zuschauerzahl, Aufräumen. LiveKit/Egress gefälscht (test_api)."""
import asyncio
import json
import os
import re

import pytest

from test_api import T, aufrufe, ereignisse, frisch, konto, mails_an, neu_client, raum, zugang  # noqa: F401

from app import db, egress, hls
from app import livekit_dienst as lk
from app.kern import jetzt


@pytest.fixture
def hls_an(monkeypatch):
    monkeypatch.setattr(egress, 'AN', True)
    monkeypatch.setattr(hls, 'AN', True)


def webinar(**einst):
    host, _e, _p = konto(name='Hosta')
    code = raum(host, titel='Großes Webinar')['code']
    r = host.patch(f'/api/raeume/{code}/webinar', json={'an': True, **einst})
    assert r.status_code == 200, r.text
    hz = zugang(host, code)
    return host, code, hz


def starten(c, code, z, **kw):
    return c.post(f'/api/raeume/{code}/egress/hls', json={'aktion': 'start', **kw}, headers=T(z))


def segmente_schreiben(code, kennung, stufen=('720p',)):
    ordner = os.path.join(hls.AUSGABE, code, kennung)
    os.makedirs(ordner, exist_ok=True)
    for s in stufen:
        with open(os.path.join(ordner, f'{s}-live.m3u8'), 'w') as f:
            f.write(f'#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\n{s}_00000.ts\n')
        with open(os.path.join(ordner, f'{s}_00000.ts'), 'wb') as f:
            f.write(b'\x47' + b'\x00' * 187)
    return ordner


def test_ohne_schalter_nichts():
    host, code, hz = webinar()
    assert starten(host, code, hz).status_code == 404
    assert neu_client().get(f'/api/hls/{code}').status_code == 404
    assert 'hls' not in host.get(f'/api/raeume/{code}/egress', headers=T(hz)).json()


def test_nur_host_cohost_nur_webinar_nicht_e2ee(hls_an):
    host, code, hz = webinar()
    g = neu_client()
    gz = zugang(g, code, name='Zara')
    assert gz['rolle'] == 'zuschauer'
    assert starten(g, code, gz).status_code == 403
    assert neu_client().post(f'/api/raeume/{code}/egress/hls', json={'aktion': 'start'}).status_code == 403   # ohne Teilnahme
    # kein Webinar → 400
    h2, _e, _p = konto(name='Normalo')
    c2 = raum(h2)['code']
    z2 = zugang(h2, c2)
    assert starten(h2, c2, z2).status_code == 400
    # E2EE → 409
    db.verbindung().execute('UPDATE raeume SET e2ee = 1 WHERE code = ?', (code,))
    assert starten(host, code, hz).status_code == 409
    db.verbindung().execute('UPDATE raeume SET e2ee = 0 WHERE code = ?', (code,))
    # ungültige Angaben
    for kw in ({'zugang': 'alle'}, {'stufen': ['1080p']}, {'stufen': ['360p']}, {'stufen': []}, {'stufen': '720p'},
               {'stufen': ['720p', '720p']}, {'layout': 'raster'}, {'zugang': 'anmeldung'}):
        assert starten(host, code, hz, **kw).status_code == 400, kw
    assert host.post(f'/api/raeume/{code}/egress/hls', json={'aktion': 'los'}, headers=T(hz)).status_code == 400


def test_start_oeffentlich_ausliefern_stoppen_aufraeumen(hls_an):
    host, code, hz = webinar()
    z = host.get(f'/api/raeume/{code}/egress', headers=T(hz)).json()
    assert z['hls_verfuegbar'] is True and z['hls'] is None
    r = starten(host, code, hz, stufen=['720p', '360p'], layout='sprecher')
    assert r.status_code == 200, r.text
    assert r.json()['link'].endswith(f'/h/{code}')
    starts = aufrufe('egress_hls')
    assert [s['stufe'] for s in starts] == ['720p', '360p'] and all(s['layout'] == 'speaker' and s['segment_s'] == 4 for s in starts)
    kennung = starts[0]['praefix'].split('/')[3]
    assert starts[0]['praefix'] == f'/hls/{code}/{kennung}/720p' and re.fullmatch(r'[A-Za-z0-9_-]{22}', kennung)
    assert starten(host, code, hz).status_code == 409
    # alle sehen den LIVE-Hinweis (Metadaten), Host den Zustand
    assert aufrufe('metadaten')[-1]['daten']['hls'] == {'von_name': 'Hosta'}
    zs = host.get(f'/api/raeume/{code}/egress', headers=T(hz)).json()['hls']
    assert zs['status'] == 'laeuft' and zs['stufen'] == ['720p', '360p'] and zs['zugang'] == 'oeffentlich' and zs['zuschauer'] == 0
    # Zuschauerseite: öffentlich → Quelle für jeden
    anon = neu_client()
    i = anon.get(f'/api/hls/{code}').json()
    assert i['laeuft'] and i['quelle'] == f'/hls/{code}/{kennung}/index.m3u8' and i['verzoegerung_s'] == [10, 20]
    # Master-Playlist von der App, Stufen-Playlists und Segmente aus dem Ordner
    m = anon.get(i['quelle'])
    assert m.status_code == 200 and m.headers['content-type'].startswith('application/vnd.apple.mpegurl')
    assert '720p-live.m3u8' in m.text and 'RESOLUTION=640x360' in m.text and m.headers['cache-control'] == 'public, max-age=5'
    assert anon.get(f'/hls/{code}/{kennung}/720p-live.m3u8').status_code == 404       # noch nichts geschrieben
    segmente_schreiben(code, kennung, ('720p', '360p'))
    p = anon.get(f'/hls/{code}/{kennung}/720p-live.m3u8')
    assert p.status_code == 200 and p.headers['cache-control'] == 'public, max-age=2' and '720p_00000.ts' in p.text
    s = anon.get(f'/hls/{code}/{kennung}/360p_00000.ts')
    assert s.status_code == 200 and s.headers['content-type'] == 'video/mp2t' and 'immutable' in s.headers['cache-control']
    assert s.headers['x-content-type-options'] == 'nosniff'
    assert anon.head(f'/hls/{code}/{kennung}/720p_00000.ts').status_code == 200
    # Zuschauer melden sich → Zahl für den Host
    for n in range(3):
        assert anon.post(f'/api/hls/{code}/da', json={'id': f'zuschauer-{n:010d}'}).json() == {'ok': True}
    anon.post(f'/api/hls/{code}/da', json={'id': 'zuschauer-0000000000'})
    assert anon.post(f'/api/hls/{code}/da', json={'id': '../x'}).status_code == 400
    assert host.get(f'/api/raeume/{code}/egress', headers=T(hz)).json()['hls']['zuschauer'] == 3
    # Stoppen: beide Egress, Dateien bleiben im Nachlauf
    ids = [x['egress_id'] for x in aufrufe('egress_stoppen')]
    assert host.post(f'/api/raeume/{code}/egress/hls', json={'aktion': 'stopp'}, headers=T(hz)).status_code == 200
    assert len(aufrufe('egress_stoppen')) == len(ids) + 2
    assert 'hls' not in aufrufe('metadaten')[-1]['daten']
    i = anon.get(f'/api/hls/{code}').json()
    assert not i['laeuft'] and i['beendet'] and 'quelle' not in i
    assert anon.get(f'/hls/{code}/{kennung}/720p_00000.ts').status_code == 200      # Nachzügler
    assert host.post(f'/api/raeume/{code}/egress/hls', json={'aktion': 'stopp'}, headers=T(hz)).status_code == 404
    # nach 1 h: Ordner weg, Auslieferung 404
    hls.aufraeumen(jetzt() + 3601)
    assert not os.path.exists(os.path.join(hls.AUSGABE, code, kennung))
    assert anon.get(f'/hls/{code}/{kennung}/720p_00000.ts').status_code == 404
    assert anon.get(f'/hls/{code}/{kennung}/index.m3u8').status_code == 404


def test_nur_angemeldete(hls_an):
    host, code, hz = webinar(registrierung=True)
    assert starten(host, code, hz, zugang='anmeldung').status_code == 200
    anon = neu_client()
    i = anon.get(f'/api/hls/{code}').json()
    assert i['laeuft'] and i['zugang'] == 'anmeldung' and 'quelle' not in i and i['anmelde_link'].endswith(f'/w/{code}')
    assert anon.post(f'/api/hls/{code}/zugang', json={}).status_code == 403
    assert anon.post(f'/api/hls/{code}/zugang', json={'anmeldung': 'x' * 32}).status_code == 403
    assert anon.post(f'/api/webinare/{code}/anmelden', json={'name': 'Ana', 'email': 'ana@example.org'}).status_code == 200
    tok = re.search(r'anmeldung=([\w-]+)', mails_an('ana@example.org')[-1][2]).group(1)
    r = anon.post(f'/api/hls/{code}/zugang', json={'anmeldung': tok})
    assert r.status_code == 200 and r.json()['quelle'].startswith(f'/hls/{code}/')
    assert db.verbindung().execute('SELECT beigetreten FROM webinar_anmeldungen WHERE email = ?', ('ana@example.org',)).fetchone()[0]
    # Token eines anderen Webinars hilft nicht
    h2, c2, z2 = webinar(registrierung=True)
    assert starten(h2, c2, z2, zugang='anmeldung').status_code == 200
    assert neu_client().post(f'/api/hls/{c2}/zugang', json={'anmeldung': tok}).status_code == 403
    # CSRF: ohne X-Agora abgelehnt
    from fastapi.testclient import TestClient
    from app import main
    assert TestClient(main.app).post(f'/api/hls/{code}/zugang', json={'anmeldung': tok}).status_code == 403


def test_pfade_ohne_trick(hls_an):
    host, code, hz = webinar()
    starten(host, code, hz)
    kennung = aufrufe('egress_hls')[-1]['praefix'].split('/')[3]
    ordner = segmente_schreiben(code, kennung)
    # Köder außerhalb und innerhalb des Ordners, die nie ausgeliefert werden dürfen
    with open(os.path.join(db.DATEN, 'geheim.ts'), 'w') as f:
        f.write('GEHEIM')
    with open(os.path.join(ordner, '720p.m3u8'), 'w') as f:      # volle Playlist: nicht in der Liste erlaubter Namen
        f.write('VOLL')
    os.symlink(os.path.join(db.DATEN, 'agora.sqlite3'), os.path.join(ordner, '720p_00009.ts'))
    c = neu_client()
    pfade = [f'/hls/{code}/{kennung}/..%2F..%2F..%2Fagora.sqlite3', f'/hls/{code}/{kennung}/%2e%2e%2fgeheim.ts',
             f'/hls/{code}/{kennung}/../../../geheim.ts', f'/hls/{code}/..%2F..%2Fgeheim.ts/720p_00000.ts',
             f'/hls/{code}/{kennung}/720p.m3u8', f'/hls/{code}/{kennung}/720p_00009.ts', f'/hls/{code}/{kennung}/360p_00000.ts',
             f'/hls/{code}/{kennung}/720p_00000.ts%00.html', f'/hls/{code}/{kennung}/720p_00000.TS', f'/hls/{code}/{"A" * 22}/720p_00000.ts',
             f'/hls/abc-defg-hij/{kennung}/720p_00000.ts', f'/hls/{code}/{kennung}/agora.sqlite3', f'/hls/{code}/{kennung}/',
             f'/hls/{code}/{kennung}/720p_-1.ts', f'/hls/{code}/{kennung}/720p_123456789.ts', f'/hls/..%2F..%2F/{kennung}/index.m3u8',
             f'/hls/{code}/{kennung}/%2e%2e/%2e%2e/agora.sqlite3']
    for p in pfade:
        r = c.get(p)
        assert r.status_code == 404, (p, r.status_code)
        assert b'SQLite' not in r.content and b'GEHEIM' not in r.content and b'VOLL' not in r.content, p
    assert c.get(f'/hls/{code}/{kennung}/720p_00000.ts').status_code == 200            # Gegenprobe
    # Verwaister Ordner (ohne Eintrag) verschwindet nach einem Tag; Symlinks außerhalb werden nie verfolgt
    fremd = os.path.join(hls.AUSGABE, code, 'B' * 22)
    os.makedirs(fremd)
    os.utime(fremd, (0, 0))
    hls.aufraeumen()
    assert not os.path.exists(fremd) and os.path.exists(ordner) and os.path.exists(os.path.join(db.DATEN, 'agora.sqlite3'))
    hls._sicher_loeschen(os.path.join(hls.AUSGABE, '..'))
    assert os.path.exists(db.DATEN) and os.path.exists(os.path.join(db.DATEN, 'geheim.ts'))


def test_fehler_einer_stufe_beendet_alle_und_meeting_ende(hls_an):
    host, code, hz = webinar()
    starten(host, code, hz, stufen=['720p', '360p'])
    e = db.verbindung().execute('SELECT * FROM hls_laeufe WHERE raum_code = ?', (code,)).fetchone()
    ids = json.loads(e['egress'])
    lk.FAKE_EGRESS[ids['360p']] = {'status': 'fehler', 'fehler': 'Chrome abgestürzt'}
    asyncio.run(hls.alle_abgleichen())
    neu = db.verbindung().execute('SELECT * FROM hls_laeufe WHERE id = ?', (e['id'],)).fetchone()
    assert neu['status'] == 'fehler' and neu['fehler'] == 'Chrome abgestürzt'
    assert {'egress_id': ids['720p']} in aufrufe('egress_stoppen')
    ev = [x for x in ereignisse('egress') if x['daten'].get('art') == 'hls'][-1]
    assert ev['daten']['status'] == 'fehler' and ev['an'] == [hz['identity']]
    # neu starten, dann Meeting beenden → Nachlauf
    assert starten(host, code, hz).status_code == 200
    host.post(f'/api/raeume/{code}/steuerung', json={'aktion': 'beenden'}, headers=T(hz))
    letzte = db.verbindung().execute('SELECT * FROM hls_laeufe WHERE raum_code = ? ORDER BY id DESC', (code,)).fetchone()
    assert letzte['status'] == 'beendet' and letzte['loeschen_ab'] > jetzt() + 3500


def test_cache_kopf_der_middleware(hls_an):
    """Die Middleware setzt sonst überall no-store – für /hls/ (200) gelten die eigenen Regeln, 404 bleibt no-store."""
    host, code, hz = webinar()
    starten(host, code, hz)
    kennung = aufrufe('egress_hls')[-1]['praefix'].split('/')[3]
    c = neu_client()
    assert c.get(f'/hls/{code}/{kennung}/720p_00001.ts').headers['cache-control'] == 'no-store'
    assert c.get(f'/hls/{code}/{kennung}/index.m3u8').headers['cache-control'] == 'public, max-age=5'
    assert c.get(f'/api/hls/{code}').headers['cache-control'] == 'no-store'
