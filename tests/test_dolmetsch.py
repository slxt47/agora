"""Dolmetscher-Kanäle: Sprachen, Zuweisen (Rechte vom Server), Kanal, Übergabe, Durchsetzen der Spurnamen, Webinar."""
import asyncio

from test_api import T, aufrufe, ereignisse, konto, neu_client, raum, zugang  # noqa: F401

from app import db, raeume
from app import livekit_dienst as lk


def dm(c, code, z, pfad='', methode='post', **kw):
    return getattr(c, methode)(f'/api/raeume/{code}/dolmetsch{pfad}', headers=T(z), **({'json': kw} if methode in ('post', 'put') else {}))


def letzte_rechte(identity):
    return [kw for kw in aufrufe('aktualisieren') if kw['identity'] == identity][-1]


def test_dolmetschen_ablauf():
    host, _e, _p = konto(name='Hanna')
    code = raum(host, titel='Konferenz')['code']
    hz = zugang(host, code)
    anna, bert, gast = neu_client(), neu_client(), neu_client()
    az, bz, gz = zugang(anna, code, name='Anna'), zugang(bert, code, name='Bert'), zugang(gast, code, name='Gast')
    # Sprachen: nur Host/Co-Host, gültige Codes
    assert dm(gast, code, gz, '/sprachen', 'put', sprachen=['de', 'en']).status_code == 403
    assert dm(host, code, hz, '/sprachen', 'put', sprachen=['deutsch']).status_code == 400
    assert dm(host, code, hz, '/sprachen', 'put', sprachen=['de', 'a1']).status_code == 400
    r = dm(host, code, hz, '/sprachen', 'put', sprachen=['de', 'en', 'de'])
    assert r.status_code == 200 and r.json()['sprachen'] == ['de', 'en']
    assert aufrufe('metadaten')[-1]['daten']['dolmetsch']['sprachen'] == ['de', 'en']
    # Zuweisen: nur Mods, Sprachen aus der Liste, verschieden
    assert dm(gast, code, gz, ziel=az['identity'], a='de', b='en').status_code == 403
    assert dm(host, code, hz, ziel=az['identity'], a='de', b='fr').status_code == 400
    assert dm(host, code, hz, ziel=az['identity'], a='de', b='de').status_code == 400
    z = dm(host, code, hz, ziel=az['identity'], a='de', b='en').json()
    assert z['dolmetscher'][az['identity']] == {'name': 'Anna', 'a': 'de', 'b': 'en', 'sprache': 'en', 'aktiv': True}
    assert z['kanaele'] == {'en': az['identity']}
    r = letzte_rechte(az['identity'])
    assert 'mikrofon' in r['quellen'] and r['attribute'] == {'dolmetsch': '1'}
    assert ereignisse('dolmetsch')[-1]['daten']['zustand']['kanaele'] == {'en': az['identity']}
    # Zweite Person im selben Paar (andere Richtung) → Bereitschaft
    z = dm(host, code, hz, ziel=bz['identity'], a='en', b='de').json()
    assert z['dolmetscher'][bz['identity']]['aktiv'] is False and z['kanaele'] == {'en': az['identity']}
    # Kanal wechseln: nur selbst, nur eigene Sprachen
    assert dm(gast, code, gz, '/kanal', sprache='de').status_code == 403
    assert dm(anna, code, az, '/kanal', sprache='fr').status_code == 400
    assert dm(anna, code, az, '/kanal', sprache='de').json()['kanaele'] == {'de': az['identity']}
    # Übergabe: Anna gibt an Bert ab, Kanal bleibt
    z = dm(anna, code, az, '/uebergabe').json()
    assert z['kanaele'] == {'de': bz['identity']} and z['dolmetscher'][az['identity']]['aktiv'] is False
    # Anna übernimmt wieder (aus der Bereitschaft); Host setzt Bert auf Sendung
    assert dm(anna, code, az, '/uebergabe').json()['kanaele'] == {'de': az['identity']}
    assert dm(host, code, hz, '/uebergabe', an=bz['identity']).json()['kanaele'] == {'de': bz['identity']}
    assert dm(gast, code, gz, '/uebergabe', an=az['identity']).status_code == 403
    # Sprache, die noch genutzt wird, lässt sich nicht entfernen
    assert dm(host, code, hz, '/sprachen', 'put', sprachen=['de']).status_code == 400
    # Alle sehen den Zustand
    assert dm(gast, code, gz, methode='get').json()['kanaele'] == {'de': bz['identity']}
    # Entfernen: Bert war auf Sendung → Anna übernimmt; Berts Dolmetsch-Attribut weg
    z = dm(host, code, hz, f'/{bz["identity"]}', 'delete').json()
    assert z['kanaele'] == {'de': az['identity']} and bz['identity'] not in z['dolmetscher']
    assert letzte_rechte(bz['identity'])['attribute'] == {'dolmetsch': ''}
    assert dm(host, code, hz, f'/{bz["identity"]}', 'delete').status_code == 404


def test_fremde_dolmetsch_spuren_werden_stumm_geschaltet():
    host, _e, _p = konto()
    code = raum(host)['code']
    hz = zugang(host, code)
    anna, gast = neu_client(), neu_client()
    az, gz = zugang(anna, code, name='Anna'), zugang(gast, code, name='Gast')
    dm(host, code, hz, '/sprachen', 'put', sprachen=['de', 'en', 'fr'])
    dm(host, code, hz, ziel=az['identity'], a='de', b='en')
    lk.FAKE_TEILNEHMER[code] = [
        {'identity': az['identity'], 'name': 'Anna', 'tracks': [{'sid': 'TR_a1', 'quelle': 'mikrofon', 'stumm': False, 'name': 'dolmetsch:en'},
                                                                {'sid': 'TR_a2', 'quelle': 'mikrofon', 'stumm': False, 'name': 'dolmetsch:fr'}]},
        {'identity': gz['identity'], 'name': 'Gast', 'tracks': [{'sid': 'TR_g1', 'quelle': 'mikrofon', 'stumm': False, 'name': 'dolmetsch:en'},
                                                                {'sid': 'TR_g2', 'quelle': 'mikrofon', 'stumm': False, 'name': ''}]},
    ]
    asyncio.run(raeume.leere_pruefen())
    assert sorted(kw['sid'] for kw in aufrufe('spur_stumm')) == ['TR_a2', 'TR_g1']


def test_webinar_zuschauer_bekommt_nur_als_dolmetscher_das_mikrofon_und_ende_raeumt_auf():
    host, _e, _p = konto()
    code = raum(host)['code']
    host.patch(f'/api/raeume/{code}/webinar', json={'an': True})
    hz = zugang(host, code)
    zu = neu_client()
    zz = zugang(zu, code, name='Zora')
    assert zz['rolle'] == 'zuschauer'
    dm(host, code, hz, '/sprachen', 'put', sprachen=['de', 'en'])
    dm(host, code, hz, ziel=zz['identity'], a='de', b='en')
    r = letzte_rechte(zz['identity'])
    assert r['rolle'] == 'zuschauer' and r['quellen'] == ['mikrofon']
    # Podium und zurück: das Extra bleibt, solange zugewiesen
    host.post(f'/api/raeume/{code}/webinar/rolle', json={'ziel': zz['identity'], 'rolle': 'panelist'}, headers=T(hz))
    host.post(f'/api/raeume/{code}/webinar/rolle', json={'ziel': zz['identity'], 'rolle': 'zuschauer'}, headers=T(hz))
    assert letzte_rechte(zz['identity'])['quellen'] == ['mikrofon']
    dm(host, code, hz, f'/{zz["identity"]}', 'delete')
    assert letzte_rechte(zz['identity'])['quellen'] == []
    dm(host, code, hz, ziel=zz['identity'], a='de', b='en')
    host.post(f'/api/raeume/{code}/steuerung', json={'aktion': 'beenden'}, headers=T(hz))
    n = db.verbindung().execute('SELECT COUNT(*) FROM dolmetscher WHERE raum_id = (SELECT id FROM raeume WHERE code = ?)', (code,)).fetchone()[0]
    assert n == 0
