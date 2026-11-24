"""Tests Einbetten: Herkünfte prüfen, /e/<code> mit frame-ancestors genau dieser Herkünfte, alle anderen Seiten 'none'."""
import pytest

from test_api import frisch, konto, neu_client, raum  # noqa: F401

from app import einbetten


def einstellen(c, code, erlaubt=True, herkuenfte=('https://beispiel.de',)):
    return c.put(f'/api/raeume/{code}/einbetten', json={'erlaubt': erlaubt, 'herkuenfte': list(herkuenfte)})


def test_aus_404_und_none_ueberall():
    c, _e, _p = konto()
    code = raum(c)['code']
    r = neu_client().get(f'/e/{code}')
    assert r.status_code == 404 and "frame-ancestors 'none'" in r.headers['content-security-policy']
    assert r.headers['x-frame-options'] == 'DENY'
    assert neu_client().get('/e/kein-code').status_code == 404
    assert neu_client().get(f'/api/einbetten/{code}').status_code == 404


def test_an_genau_diese_herkuenfte():
    c, _e, _p = konto()
    code = raum(c)['code']
    r = einstellen(c, code, herkuenfte=['https://Beispiel.de/', 'https://app.beispiel.de:8443', 'http://localhost:5173', 'https://beispiel.de'])
    assert r.status_code == 200, r.text
    e = r.json()['einbetten']
    assert e['erlaubt'] and e['herkuenfte'] == ['https://beispiel.de', 'https://app.beispiel.de:8443', 'http://localhost:5173']
    assert e['link'] == f'https://agora.slxt.dev/e/{code}'
    r = neu_client().get(f'/e/{code}')
    assert r.status_code == 200
    csp = r.headers['content-security-policy']
    assert 'frame-ancestors https://beispiel.de https://app.beispiel.de:8443 http://localhost:5173' in csp and "'none'" not in csp.split('frame-ancestors')[1].split(';')[0]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp                  # Rest der CSP unverändert
    assert 'x-frame-options' not in r.headers and einbetten.MARKE not in r.headers
    assert r.headers['cross-origin-resource-policy'] == 'cross-origin'
    # alle anderen Seiten bleiben gesperrt
    for pfad in ('/api/gesund', f'/api/raeume/{code}', f'/r/{code}', '/konto'):
        r = neu_client().get(pfad)
        assert "frame-ancestors 'none'" in r.headers['content-security-policy'] and r.headers['x-frame-options'] == 'DENY', pfad
    # öffentlich: Liste für postMessage
    assert neu_client().get(f'/api/einbetten/{code}').json() == {'herkuenfte': e['herkuenfte']}
    # Besitzer sieht die Einstellung im Raum, andere nicht
    assert c.get(f'/api/raeume/{code}').json()['raum']['einbetten']['erlaubt'] is True
    assert 'einbetten' not in neu_client().get(f'/api/raeume/{code}').json()['raum']
    # ausschalten → 404
    assert einstellen(c, code, erlaubt=False).status_code == 200
    assert neu_client().get(f'/e/{code}').status_code == 404


@pytest.mark.parametrize('herkunft', ['beispiel.de', 'http://beispiel.de', 'https://beispiel.de/pfad', 'https://*.beispiel.de',
                                      'https://beispiel.de?x=1', 'https://a@beispiel.de', 'javascript:alert(1)', "https://x.de 'unsafe-inline'",
                                      'https://x.de;script-src *', 'data:text/html,x', 'https://', "'self'", '*'])
def test_ungueltige_herkuenfte(herkunft):
    c, _e, _p = konto()
    code = raum(c)['code']
    assert einstellen(c, code, herkuenfte=[herkunft]).status_code == 400, herkunft


def test_nur_besitzer_und_grenzen():
    c, _e, _p = konto()
    code = raum(c)['code']
    andere, _e2, _p2 = konto(name='Bea')
    assert einstellen(andere, code).status_code == 403
    assert neu_client().put(f'/api/raeume/{code}/einbetten', json={'erlaubt': True, 'herkuenfte': ['https://a.de']}).status_code == 401
    assert einstellen(c, code, herkuenfte=[]).status_code == 400                       # an ohne Herkunft
    assert einstellen(c, code, herkuenfte=[f'https://a{i}.de' for i in range(11)]).status_code == 400
    assert c.put(f'/api/raeume/{code}/einbetten', json={'erlaubt': 'ja'}).status_code == 400


def test_embed_js_von_fremden_seiten_ladbar(tmp_path, monkeypatch):
    """/embed.js kommt aus dem Frontend-Build (StaticFiles); der Kopf-Haken setzt CORP cross-origin + kurzes Caching."""
    from starlette.responses import Response

    class Req:
        class url:
            path = '/embed.js'
    r = Response('x', media_type='text/javascript', headers={'cross-origin-resource-policy': 'same-origin', 'cache-control': 'no-store'})
    einbetten.kopf_anpassen(Req, r)
    assert r.headers['cross-origin-resource-policy'] == 'cross-origin' and r.headers['cache-control'] == 'public, max-age=3600'
