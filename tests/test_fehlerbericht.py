"""Fehler-Monitoring: Berichte aus dem Browser, Ausnahmen des Servers, Gruppierung, Admin-Reiter, Alarm, Datenschutz."""
import json

from test_api import H, konto, mails_an, neu_client  # noqa: F401

from app import db, fehlerbericht, main
from app.kern import grenze, jetzt

UA_CHROME = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36'
UA_IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1'


def admin_client(monkeypatch):
    monkeypatch.setenv("AGORA_ADMIN_MAILS", "chef-fehler@example.org")
    c = neu_client()
    from test_api import link_aus
    r = c.post('/api/konto/registrieren', json={'name': 'Chef', 'email': 'chef-fehler@example.org', 'passwort': 'geheim-passwort-1'})
    if r.status_code == 200 and mails_an('chef-fehler@example.org'):
        c.get(link_aus('chef-fehler@example.org'), follow_redirects=False)
    r = c.post('/api/konto/anmelden', json={'email': 'chef-fehler@example.org', 'passwort': 'geheim-passwort-1'})
    assert r.status_code == 200, r.text
    return c


def bericht(c, **kw):
    d = {'art': 'fehler', 'nachricht': 'TypeError: x is undefined', 'stapel': 'at f (/assets/raum-AbCdEf12.js:1:200)\nat g (/assets/raum-AbCdEf12.js:1:300)',
         'seite': '/konto', 'version': '1.0.0'} | kw
    return c.post('/api/fehlerbericht', json=d, headers={'User-Agent': UA_CHROME})


def gruppen():
    return db.verbindung().execute('SELECT * FROM fehler_gruppen ORDER BY id').fetchall()


def setup_function():
    db.verbindung().execute('DELETE FROM fehler_gruppen')
    grenze.d.clear()


def test_bericht_gruppiert_zaehlt_browser_version():
    c = neu_client()
    for ua in (UA_CHROME, UA_CHROME, UA_IPHONE):
        r = c.post('/api/fehlerbericht', json={'art': 'fehler', 'nachricht': 'TypeError: kaputt', 'stapel': 'at a (/assets/x-AAAAAAAA.js:1:2)',
                                               'seite': '/konto', 'version': '1.0.0'}, headers={'User-Agent': ua})
        assert r.status_code == 200, r.text
    bericht(c, nachricht='Anderer Fehler')
    g = gruppen()
    assert len(g) == 2
    assert g[0]['anzahl'] == 3 and g[0]['quelle'] == 'browser' and g[0]['art'] == 'fehler'
    assert json.loads(g[0]['browser']) == {'Chrome': 2, 'Safari · mobil': 1}
    assert json.loads(g[0]['versionen']) == {'1.0.0': 3}
    assert g[0]['ort'].startswith('at a (/assets/x-AAAAAAAA.js')
    # gleicher Fehler in einem neuen Build (anderer Hash im Dateinamen) bleibt dieselbe Gruppe
    c.post('/api/fehlerbericht', json={'art': 'fehler', 'nachricht': 'TypeError: kaputt', 'stapel': 'at a (/assets/x-BBBBBBBB.js:1:2)',
                                       'version': '1.0.1'})
    g = gruppen()
    assert len(g) == 2 and g[0]['anzahl'] == 4 and json.loads(g[0]['versionen']) == {'1.0.0': 3, '1.0.1': 1}


def test_e2ee_fragment_und_personendaten_nie_gespeichert():
    c = neu_client()
    geheim = 'GEHEIMERSCHLUESSEL0123456789abcdef'
    bericht(c, art='promise',
            nachricht=f'Fehler bei https://agora.slxt.dev/r/qrs-tuvw-xyz?pc=1234#k={geheim} für ada@example.org von 203.0.113.9',
            stapel=f'at x (https://agora.slxt.dev/r/qrs-tuvw-xyz#k={geheim}:1:2)\nat y (/assets/a.js?v=1#k={geheim}:3:4)\n'
                   f'k={geheim}\n%23k%3D{geheim}',
            seite=f'/r/qrs-tuvw-xyz#k={geheim}')
    bericht(c, art='api', nachricht=f'GET /api/aufnahmen/AbCdEfGhIjKlMnOpQrStUv/datei?token={geheim} → 503', seite=f'/a/{geheim}')
    dump = '\n'.join(db.verbindung().iterdump())
    for verboten in (geheim, 'qrs-tuvw-xyz', 'ada@example.org', '203.0.113.9', 'agora.slxt.dev', 'pc=1234', 'AbCdEfGhIjKlMnOpQrStUv'):
        assert verboten not in dump, verboten
    g = gruppen()
    assert g[0]['seite'] == '/r/‹code›' and '‹e-mail›' in g[0]['nachricht'] and '‹ip›' in g[0]['nachricht']
    assert g[1]['nachricht'] == 'GET /api/aufnahmen/‹id›/datei → 503' and g[1]['seite'] == '/a/‹code›'
    for x in g:
        assert '#' not in x['nachricht'] + x['stapel'] + x['seite'] and '?' not in x['stapel'] + x['seite']


def test_kuerzen_und_grenzen():
    c = neu_client()
    r = bericht(c, nachricht='x' * 5000, stapel='\n'.join(f'at f{i} (/assets/a.js:{i}:1)' for i in range(200)))
    assert r.status_code == 200
    g = gruppen()[-1]
    assert len(g['nachricht']) == fehlerbericht.MAX_NACHRICHT
    assert len(g['stapel'].splitlines()) <= 40 and len(g['stapel']) <= fehlerbericht.MAX_STAPEL
    # zu groß, falsche Art, falsche Typen
    assert c.post('/api/fehlerbericht', content=b'{"art":"fehler","nachricht":"' + b'a' * 20000 + b'"}',
                  headers={'Content-Type': 'application/json'}).status_code == 413
    assert bericht(c, art='boese').status_code == 400
    assert bericht(c, nachricht={'a': 1}).status_code == 400
    # CSRF: ohne X-Agora abgelehnt
    from fastapi.testclient import TestClient
    assert TestClient(main.app).post('/api/fehlerbericht', json={'art': 'fehler', 'nachricht': 'x'}).status_code == 403


def test_begrenzer_je_ip():
    grenze.d.clear()
    c = neu_client()
    stati = [bericht(c, nachricht=f'Flut {i % 3}').status_code for i in range(35)]
    assert stati[:30] == [200] * 30 and stati[30:] == [429] * 5
    assert bericht(neu_client(), nachricht='andere IP').status_code == 200


def test_neue_gruppen_aus_browsern_gedeckelt(monkeypatch):
    monkeypatch.setattr(fehlerbericht, 'MAX_GRUPPEN', 3)
    c = neu_client()
    for i in range(5):
        assert bericht(c, nachricht=f'Gruppe {i}', stapel=f'at f (/x{i}.js:1:1)').status_code == 200
    assert len(gruppen()) == 3
    bericht(c, nachricht='Gruppe 0', stapel='at f (/x0.js:1:1)')          # vorhandene zählt weiter
    assert gruppen()[0]['anzahl'] == 2
    fehlerbericht.server_ausnahme(RuntimeError('Server immer'))           # Server-Ausnahmen werden nie verworfen
    assert len(gruppen()) == 4


def test_server_ausnahme_middleware():
    @main.app.get('/api/test/kaputt')
    def kaputt():
        raise ZeroDivisionError('geteilt durch null bei ada@example.org')
    try:
        c = neu_client()
        r = c.get('/api/test/kaputt', headers={'Accept-Language': 'en'})
        assert r.status_code == 500 and r.json() == {'fehler': 'Internal error'}
        assert r.headers['x-content-type-options'] == 'nosniff' and 'content-security-policy' in r.headers
        c.get('/api/test/kaputt')
        g = gruppen()[-1]
        assert g['quelle'] == 'server' and g['art'] == 'ausnahme' and g['anzahl'] == 2
        assert g['nachricht'] == 'ZeroDivisionError: geteilt durch null bei ‹e-mail›'
        assert g['seite'] == 'GET /api/test/kaputt' and g['ort'] == g['stapel'].splitlines()[0]
        assert g['ort'].startswith('File "test_fehlerbericht.py", line ') and g['ort'].endswith('in kaputt')
        assert '/src/' not in g['stapel'] and '/home/' not in g['stapel'] and 'site-packages' not in g['stapel']
    finally:
        main.app.router.routes[:] = [r for r in main.app.router.routes if getattr(r, 'path', '') != '/api/test/kaputt']


def test_admin_liste_details_erledigt_wieder_offen(monkeypatch):
    c = neu_client()
    a = admin_client(monkeypatch)
    bericht(c, nachricht='<img src=x onerror=alert(1)>')
    # Nicht-Admins: 404
    nicht, _e, _p = konto()
    for m, p in (('GET', '/api/admin/fehler'), ('GET', '/api/admin/fehler/1'), ('POST', '/api/admin/fehler/1'), ('DELETE', '/api/admin/fehler/1')):
        assert nicht.request(m, p, json={'erledigt': True} if m == 'POST' else None).status_code == 404
        assert neu_client().request(m, p, json={'erledigt': True} if m == 'POST' else None).status_code == 404
    r = a.get('/api/admin/fehler')
    assert r.status_code == 200 and r.json()['offen'] == 1
    gid = r.json()['gruppen'][0]['id']
    assert r.json()['gruppen'][0]['nachricht'] == '<img src=x onerror=alert(1)>'      # als Text; Escaping macht die Oberfläche
    d = a.get(f'/api/admin/fehler/{gid}').json()['gruppe']
    assert d['stapel'].startswith('at f') and d['browser'] == {'Chrome': 1}
    assert a.post(f'/api/admin/fehler/{gid}', json={'erledigt': 'ja'}).status_code == 400
    assert a.post(f'/api/admin/fehler/{gid}', json={'erledigt': True}).json()['gruppe']['erledigt']
    assert a.get('/api/admin/fehler').json()['gruppen'] == [] and a.get('/api/admin/fehler?status=erledigt').json()['gruppen']
    assert a.get('/api/admin/uebersicht').json()['fehler_offen'] == 0
    bericht(c, nachricht='<img src=x onerror=alert(1)>')                        # tritt wieder auf → wieder offen
    g = a.get('/api/admin/fehler').json()['gruppen']
    assert len(g) == 1 and g[0]['erledigt'] is None and g[0]['anzahl'] == 2
    assert a.get('/api/admin/fehler/99999').status_code == 404
    assert a.delete(f'/api/admin/fehler/{gid}').json() == {'ok': True}
    assert not gruppen()
    eintraege = a.get('/api/admin/protokoll?nur_admin=1').json()['eintraege']
    assert any(f'Fehler #{gid} erledigt' in e['ereignis'] for e in eintraege)


def test_alarm_mail_einmal_je_gruppe_und_tag(monkeypatch):
    from app import mail
    admin_client(monkeypatch)
    mail.POSTEINGANG_TEST.clear()
    c = neu_client()
    for _ in range(3):
        bericht(c, nachricht='Alarm-Test')
    an = mails_an('chef-fehler@example.org')
    assert len(an) == 1 and 'Alarm-Test' in an[0][1] and '/admin#fehler' in an[0][2]
    gid = gruppen()[-1]['id']
    db.verbindung().execute('UPDATE fehler_gruppen SET erledigt = ? WHERE id = ?', (jetzt(), gid))
    bericht(c, nachricht='Alarm-Test')                     # wieder aufgetreten, aber am selben Tag → keine zweite Mail
    assert len(mails_an('chef-fehler@example.org')) == 1
    db.verbindung().execute('UPDATE fehler_gruppen SET erledigt = ?, gemeldet = ? WHERE id = ?', (jetzt(), jetzt() - 90000, gid))
    bericht(c, nachricht='Alarm-Test')
    an = mails_an('chef-fehler@example.org')
    assert len(an) == 2 and 'wieder aufgetreten' in an[1][2]


def test_aufbewahrung_30_tage():
    c = neu_client()
    bericht(c, nachricht='alt')
    bericht(c, nachricht='neu')
    db.verbindung().execute("UPDATE fehler_gruppen SET zuletzt = ? WHERE nachricht = 'alt'", (jetzt() - 31 * 86400,))
    main.aufraeumen()
    assert [g['nachricht'] for g in gruppen()] == ['neu']


def test_browser_familie():
    f = fehlerbericht.browser_familie
    assert f(UA_CHROME) == 'Chrome'
    assert f(UA_IPHONE) == 'Safari · mobil'
    assert f('Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0') == 'Firefox'
    assert f(UA_CHROME + ' Edg/140.0') == 'Edge'
    assert f(UA_CHROME + ' Electron/38.0.0') == 'Agora Desktop'
    assert f('') == 'unbekannt' and f('curl/8.0') == 'andere'
