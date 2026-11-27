"""Tests Android-TWA: /.well-known/assetlinks.json nur mit gültigem AGORA_ANDROID_SHA256, sonst 404."""
from test_api import neu_client  # noqa: F401

A = 'AB:' * 31 + 'CD'
B_ROH = 'ef' * 32


def test_ohne_variable_404(monkeypatch):
    monkeypatch.delenv('AGORA_ANDROID_SHA256', raising=False)
    assert neu_client().get('/.well-known/assetlinks.json').status_code == 404


def test_mit_fingerabdruecken(monkeypatch):
    monkeypatch.setenv('AGORA_ANDROID_SHA256', f'{A}, {B_ROH},{A.lower()}')
    monkeypatch.delenv('AGORA_ANDROID_PAKET', raising=False)
    r = neu_client().get('/.well-known/assetlinks.json')
    assert r.status_code == 200 and r.headers['content-type'] == 'application/json'
    [eintrag] = r.json()
    assert eintrag['relation'] == ['delegate_permission/common.handle_all_urls']
    ziel = eintrag['target']
    assert ziel['namespace'] == 'android_app' and ziel['package_name'] == 'dev.slxt.agora.twa'
    assert ziel['sha256_cert_fingerprints'] == [A, ':'.join(['EF'] * 32)]      # normiert, doppelte raus
    assert neu_client().head('/.well-known/assetlinks.json').status_code == 200


def test_eigener_paketname(monkeypatch):
    monkeypatch.setenv('AGORA_ANDROID_SHA256', A)
    monkeypatch.setenv('AGORA_ANDROID_PAKET', 'org.beispiel.meet')
    assert neu_client().get('/.well-known/assetlinks.json').json()[0]['target']['package_name'] == 'org.beispiel.meet'


def test_ungueltig_404(monkeypatch):
    for wert, paket in [('kein-abdruck', 'dev.slxt.agora.twa'), (A + ',zz', 'dev.slxt.agora.twa'), (A[:-3], 'dev.slxt.agora.twa'),
                        (A, 'ohnepunkt'), (A, 'dev.slxt/../x'), (','.join([A] * 6), 'dev.slxt.agora.twa')]:
        monkeypatch.setenv('AGORA_ANDROID_SHA256', wert)
        monkeypatch.setenv('AGORA_ANDROID_PAKET', paket)
        assert neu_client().get('/.well-known/assetlinks.json').status_code == 404, (wert, paket)
