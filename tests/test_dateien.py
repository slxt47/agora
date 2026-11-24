"""Dateien im Meeting: Hochladen (Grenzen, Endungen), sichere Auslieferung, nur für Teilnehmende, Löschen, Ende des
Meetings / 30 Tage im Meeting-Verlauf, Virenprüfung über clamd INSTREAM (gegen einen Fake-Server)."""
import asyncio
import os
import socket
import struct
import threading
from urllib.parse import quote

import pytest
from test_api import T, ereignisse, meeting, neu_client, steuern, zugang  # noqa: F401

from app import dateien, db, raeume


def hoch(c, code, z, name='bericht.pdf', inhalt=b'%PDF-1.4 test', **header):
    return c.post(f'/api/raeume/{code}/dateien', content=inhalt, headers=T(z) | {'X-Dateiname': quote(name), 'Content-Type': 'application/octet-stream'} | header)


def test_dateien_hochladen_laden_loeschen():
    host, code, hz, [(g1, z1), (g2, z2)] = meeting(2)
    url = f'/api/raeume/{code}/dateien'
    leer = g1.get(url, headers=T(z1)).json()
    assert leer['dateien'] == [] and leer['virenpruefung'] is False and 'pdf' in leer['endungen'] and 'exe' not in leer['endungen']
    r = hoch(host, code, hz, name='Bericht März.pdf')
    assert r.status_code == 200, r.text
    d = r.json()['datei']
    assert d['name'] == 'Bericht März.pdf' and d['groesse'] == 13 and d['typ'] == 'application/pdf' and d['meins'] and not d['geprueft']
    e = ereignisse('datei')[-1]
    assert e['an'] is None and e['daten']['aktion'] == 'neu' and e['daten']['datei']['id'] == d['id'] and 'meins' not in e['daten']['datei']
    # Gast lädt herunter: Anhang, nosniff, CSP sandbox, kein Cache
    r = g1.get(f'{url}/{d["id"]}', headers=T(z1))
    assert r.status_code == 200 and r.content == b'%PDF-1.4 test'
    assert r.headers['content-disposition'].startswith('attachment') and "filename*=utf-8''Bericht%20M%C3%A4rz.pdf" in r.headers['content-disposition']
    assert r.headers['x-content-type-options'] == 'nosniff' and r.headers['content-security-policy'].startswith('sandbox')
    assert r.headers['cache-control'] == 'no-store'
    # Ohne Teilnahme / fremdes Meeting: nichts
    assert neu_client().get(f'{url}/{d["id"]}').status_code == 403
    host2, code2, hz2, _g = meeting(0)
    assert host2.get(f'/api/raeume/{code2}/dateien/{d["id"]}', headers=T(hz2)).status_code == 404
    # Bild: Vorschau inline, sonst Anhang
    bild = hoch(g1, code, z1, name='foto.PNG', inhalt=b'\x89PNG\r\n').json()['datei']
    assert bild['bild'] and bild['typ'] == 'image/png'
    v = g2.get(f'{url}/{bild["id"]}?vorschau=1', headers=T(z2))
    assert v.headers['content-type'] == 'image/png' and v.headers['content-disposition'].startswith('inline')
    # Löschen: nur eigene oder Moderation
    assert g2.delete(f'{url}/{bild["id"]}', headers=T(z2)).status_code == 403
    assert g1.delete(f'{url}/{bild["id"]}', headers=T(z1)).json() == {'ok': True}
    assert ereignisse('datei')[-1]['daten'] == {'typ': 'datei', 'aktion': 'geloescht', 'datei': {'id': bild['id']}}
    assert not os.path.exists(dateien.pfad(bild['id']))
    assert host.delete(f'{url}/{d["id"]}', headers=T(hz)).status_code == 200
    assert g1.get(url, headers=T(z1)).json()['dateien'] == []


def test_dateien_grenzen_und_typen(monkeypatch):
    host, code, hz, [(g1, z1)] = meeting(1)
    assert hoch(host, code, hz, name='').status_code == 400
    assert hoch(host, code, hz, name='virus.exe').status_code == 415
    assert hoch(host, code, hz, name='ohne-endung').status_code == 415
    assert hoch(host, code, hz, name='seite.html').status_code == 415
    assert hoch(host, code, hz, inhalt=b'').status_code == 400
    monkeypatch.setattr(dateien, 'MAX_DATEI', 10)
    assert hoch(host, code, hz, inhalt=b'x' * 11).status_code == 413
    monkeypatch.setattr(dateien, 'MAX_DATEI', 100)
    monkeypatch.setattr(dateien, 'MAX_MEETING', 25)
    assert hoch(host, code, hz, inhalt=b'x' * 20).status_code == 200
    assert hoch(g1, code, z1, inhalt=b'x' * 10).status_code == 507
    # Pfadteile und Steuerzeichen im Namen fallen weg
    monkeypatch.setattr(dateien, 'MAX_MEETING', 10 ** 6)
    d = hoch(host, code, hz, name='../../etc/pa\x00sswd.txt', inhalt=b'x').json()['datei']
    assert d['name'] == 'etcpasswd.txt'
    # Gefährliche Typen nur, wenn erlaubt – dann als octet-stream-Anhang, auch mit ?vorschau=1
    monkeypatch.setenv('AGORA_DATEI_ENDUNGEN', 'html, .JS ,svg')
    assert hoch(host, code, hz, name='a.pdf').status_code == 415
    for name in ('seite.html', 'skript.js', 'bild.svg'):
        d = hoch(host, code, hz, name=name, inhalt=b'<script>alert(1)</script>').json()['datei']
        assert d['gefaehrlich'] and not d['bild']
        r = g1.get(f'/api/raeume/{code}/dateien/{d["id"]}?vorschau=1', headers=T(z1))
        assert r.headers['content-type'] == 'application/octet-stream' and r.headers['content-disposition'].startswith('attachment')
        assert 'sandbox' in r.headers['content-security-policy'] and r.headers['x-content-type-options'] == 'nosniff'
    # Chat aus → Teilnehmende teilen keine Dateien, Moderation schon
    monkeypatch.delenv('AGORA_DATEI_ENDUNGEN')
    steuern(host, code, hz, aktion='rechte', chat=False)
    assert hoch(g1, code, z1).status_code == 403
    assert hoch(host, code, hz).status_code == 200


def test_dateien_ende_und_verlauf():
    host, code, hz, [(g1, z1)] = meeting(1)
    d = hoch(g1, code, z1).json()['datei']
    asyncio.run(raeume.meeting_beenden(raeume.raum_laden(code)))
    assert not os.path.exists(dateien.pfad(d['id']))
    assert db.verbindung().execute('SELECT COUNT(*) FROM dateien WHERE id = ?', (d['id'],)).fetchone()[0] == 0
    # chat_speichern: 30 Tage für den Besitzer, nicht für andere
    host, code, hz, [(g1, z1)] = meeting(1, chat_speichern=True)
    d = hoch(g1, code, z1, name='folien.pdf').json()['datei']
    asyncio.run(raeume.meeting_beenden(raeume.raum_laden(code)))
    v = host.get('/api/verlauf').json()['meetings'][0]
    liste = host.get(f'/api/verlauf/{v["id"]}/inhalt').json()['dateien']
    assert [x['name'] for x in liste] == ['folien.pdf'] and liste[0]['ablauf'].endswith('Z')
    r = host.get(f'/api/verlauf/{v["id"]}/dateien/{d["id"]}')
    assert r.status_code == 200 and r.content == b'%PDF-1.4 test' and r.headers['content-disposition'].startswith('attachment')
    fremd = neu_client()
    from test_api import konto
    konto(fremd)
    assert fremd.get(f'/api/verlauf/{v["id"]}/dateien/{d["id"]}').status_code == 404
    assert g1.get(f'/api/raeume/{code}/dateien/{d["id"]}', headers=T(z1)).status_code == 403      # Meeting vorbei
    # nach Ablauf: Aufräumen löscht Zeile und Datei; verwaiste Dateien (Raum gelöscht) ebenso
    dateien.aufraeumen(dateien.jetzt() + 31 * 86400)
    assert not os.path.exists(dateien.pfad(d['id']))
    assert host.get(f'/api/verlauf/{v["id"]}/dateien/{d["id"]}').status_code == 404
    waise = dateien.pfad('W' * 22)
    open(waise, 'wb').close()
    os.utime(waise, (0, 0))
    dateien.aufraeumen()
    assert not os.path.exists(waise)


# ---------------------------------------------------------------- Virenprüfung (clamd INSTREAM)
class FakeClamd:
    """Minimaler clamd: liest zINSTREAM + Blöcke, meldet FOUND, wenn die EICAR-Kennung im Strom steht."""
    def __init__(self, antwort=None):
        self.s = socket.socket()
        self.s.bind(('127.0.0.1', 0))
        self.s.listen()
        self.port = self.s.getsockname()[1]
        self.empfangen = []
        self.antwort = antwort
        threading.Thread(target=self.laufen, daemon=True).start()

    def laufen(self):
        while True:
            try:
                k, _ = self.s.accept()
            except OSError:
                return
            with k:
                f = k.makefile('rb')
                assert f.read(10) == b'zINSTREAM\0'
                daten = b''
                while True:
                    n = struct.unpack('>I', f.read(4))[0]
                    if n == 0:
                        break
                    daten += f.read(n)
                self.empfangen.append(daten)
                a = self.antwort or (b'stream: Eicar-Test-Signature FOUND\0' if b'EICAR' in daten else b'stream: OK\0')
                k.sendall(a)


@pytest.fixture
def clamd(monkeypatch):
    fake = FakeClamd()
    monkeypatch.setenv('AGORA_CLAMAV', f'127.0.0.1:{fake.port}')
    yield fake
    fake.s.close()


def test_virenpruefung_instream(clamd):
    host, code, hz, _g = meeting(0)
    assert host.get(f'/api/raeume/{code}/dateien', headers=T(hz)).json()['virenpruefung'] is True
    gross = b'a' * (200 * 1024)                     # mehrere Blöcke
    r = hoch(host, code, hz, name='gross.txt', inhalt=gross)
    assert r.status_code == 200 and r.json()['datei']['geprueft'] is True
    assert clamd.empfangen[-1] == gross
    r = hoch(host, code, hz, name='eicar.txt', inhalt=b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*')
    assert r.status_code == 422 and 'Eicar-Test-Signature' in r.json()['fehler']
    assert len(host.get(f'/api/raeume/{code}/dateien', headers=T(hz)).json()['dateien']) == 1
    assert not [n for n in os.listdir(os.path.join(db.DATEN, 'tmp')) if n.startswith('datei-')]


def test_virenpruefung_fehler_lehnt_ab(monkeypatch):
    host, code, hz, _g = meeting(0)
    frei = socket.socket(); frei.bind(('127.0.0.1', 0)); port = frei.getsockname()[1]; frei.close()
    monkeypatch.setenv('AGORA_CLAMAV', f'127.0.0.1:{port}')            # niemand lauscht
    r = hoch(host, code, hz)
    assert r.status_code == 503 and 'Virenprüfung' in r.json()['fehler']
    fake = FakeClamd(antwort=b'INSTREAM size limit exceeded. ERROR\0')
    monkeypatch.setenv('AGORA_CLAMAV', f'127.0.0.1:{fake.port}')
    assert hoch(host, code, hz).status_code == 503
    fake.s.close()
    assert host.get(f'/api/raeume/{code}/dateien', headers=T(hz)).json()['dateien'] == []


def test_clamav_protokoll_direkt(tmp_path):
    fake = FakeClamd()
    p = tmp_path / 'x.bin'
    p.write_bytes(b'0123456789' * 10)
    assert dateien.clamav_pruefen(str(p), ('127.0.0.1', fake.port), block=7) == (True, None)
    assert fake.empfangen[-1] == b'0123456789' * 10
    p.write_bytes(b'..EICAR..')
    assert dateien.clamav_pruefen(str(p), ('127.0.0.1', fake.port)) == (False, 'Eicar-Test-Signature')
    fake.s.close()
