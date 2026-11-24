"""Tests Webinar-Modus und Registrierung. Hilfen und Umgebung aus test_api."""
import re
from datetime import datetime, timedelta, timezone

import jwt

from test_api import T, beitreten, ereignisse, frisch, konto, mails_an, neu_client, raum, zugang, aufrufe  # noqa: F401

from app import db, main, webinar
from app import livekit_dienst as lk
from app.kern import jetzt


def w(c, code, **kw):
    r = c.patch(f'/api/raeume/{code}/webinar', json=kw)
    assert r.status_code == 200, r.text
    return r.json()['webinar']


def grants(z):
    return jwt.decode(z['token'], options={'verify_signature': False})['video']


def webinar_raum(**einst):
    host, email, _p = konto(name='Hosta')
    r = raum(host, titel='Großes Webinar')
    w(host, r['code'], an=True, **einst)
    return host, r['code']


def test_webinar_rollen_token_und_podium():
    host, code = webinar_raum()
    assert host.get(f'/api/raeume/{code}').json()['raum']['webinar'] == {'registrierung': False, 'anmelde_link': None}
    hz = zugang(host, code)
    assert hz['rolle'] == 'host' and grants(hz)['canPublish'] is True
    # Panel-Link: eigene Rolle, darf senden
    p = host.post(f'/api/raeume/{code}/webinar/panel', json={'name': 'Referentin'}).json()['panel']
    tok = re.search(r'panel=(\w+)', p['link']).group(1)
    pc = neu_client()
    pz = zugang(pc, code, name='Rita', panel=tok)
    assert pz['rolle'] == 'panelist' and grants(pz)['canPublish'] is True
    assert beitreten(neu_client(), code, name='X', panel='f' * 40).status_code == 403
    # Zuschauer: kein Senden
    zc = neu_client()
    zz = zugang(zc, code, name='Zara')
    g = grants(zz)
    assert zz['rolle'] == 'zuschauer' and g['canPublish'] is False and not g.get('canPublishSources')
    # Metadaten zeigen den Webinar-Modus
    assert aufrufe('raum_anlegen')[-1]['metadaten']['webinar'] is True
    # Zuschauer darf nicht steuern, nicht aufs Podium holen
    assert zc.post(f'/api/raeume/{code}/webinar/rolle', json={'ziel': pz['identity'], 'rolle': 'zuschauer'}, headers=T(zz)).status_code == 403
    # Host holt Zuschauerin aufs Podium → LiveKit-Rechte mit Senden, Ereignis an alle
    r = host.post(f'/api/raeume/{code}/webinar/rolle', json={'ziel': zz['identity'], 'rolle': 'panelist'}, headers=T(hz))
    assert r.status_code == 200, r.text
    a = aufrufe('aktualisieren')[-1]
    assert a['identity'] == zz['identity'] and a['rolle'] == 'panelist'
    assert lk.berechtigung('panelist', {}).can_publish and not lk.berechtigung('zuschauer', {}).can_publish
    assert ereignisse('rolle')[-1]['daten'] == {'typ': 'rolle', 'identity': zz['identity'], 'rolle': 'panelist'}
    # … und zurück
    assert host.post(f'/api/raeume/{code}/webinar/rolle', json={'ziel': zz['identity'], 'rolle': 'zuschauer'}, headers=T(hz)).status_code == 200
    assert aufrufe('aktualisieren')[-1]['rolle'] == 'zuschauer'
    # Host und Co-Host lassen sich so nicht verschieben
    assert host.post(f'/api/raeume/{code}/webinar/rolle', json={'ziel': hz['identity'], 'rolle': 'zuschauer'}, headers=T(hz)).status_code == 400
    # Co-Host wieder abgeben → Panelist (nicht Teilnehmer)
    host.post(f'/api/raeume/{code}/steuerung', json={'aktion': 'rolle', 'ziel': pz['identity'], 'rolle': 'cohost'}, headers=T(hz))
    host.post(f'/api/raeume/{code}/steuerung', json={'aktion': 'rolle', 'ziel': pz['identity'], 'rolle': 'teilnehmer'}, headers=T(hz))
    assert ereignisse('rolle')[-1]['daten']['rolle'] == 'panelist'
    # Zuschauerzahl für das Panel (aus LiveKit + Rollen)
    lk.FAKE_TEILNEHMER[code] = [{'identity': x['identity'], 'name': '', 'tracks': []} for x in (hz, pz, zz)]
    s = host.get(f'/api/raeume/{code}/webinar/zustand', headers=T(hz)).json()
    assert s == {'webinar': True, 'chat': 'alle', 'fragen_an': True, 'zuschauer': 1, 'panel': 2}
    assert 'zuschauer' not in zc.get(f'/api/raeume/{code}/webinar/zustand', headers=T(zz)).json()
    # Panel-Link löschen → ungültig
    assert host.delete(f'/api/raeume/{code}/webinar/panel/{p["id"]}').status_code == 200
    assert beitreten(neu_client(), code, name='Rita2', panel=tok).status_code == 403


def test_webinar_umschalten_nur_ohne_laufendes_meeting_und_nur_besitzer():
    host, _e, _p = konto()
    code = raum(host)['code']
    fremd, _e2, _p2 = konto()
    assert fremd.patch(f'/api/raeume/{code}/webinar', json={'an': True}).status_code == 403
    assert host.patch(f'/api/raeume/{code}/webinar', json={'chat': 'quatsch'}).status_code == 400
    assert host.patch(f'/api/raeume/{code}/webinar', json={'kapazitaet': 0}).status_code == 400
    assert host.patch(f'/api/raeume/{code}/webinar', json={'fragen': ['a'] * 6}).status_code == 400
    zugang(host, code)
    assert host.patch(f'/api/raeume/{code}/webinar', json={'an': True}).status_code == 409
    # Normales Meeting: Gäste bleiben Gäste mit Senderecht
    gz = zugang(neu_client(), code, name='G')
    assert gz['rolle'] == 'gast' and grants(gz)['canPublish'] is True


def test_webinar_chat_einstellungen():
    host, code = webinar_raum(chat='panel')
    hz = zugang(host, code)
    zc = neu_client()
    zz = zugang(zc, code, name='Zuschauer')
    z2 = zugang(neu_client(), code, name='Andere')
    chat = lambda **kw: zc.post(f'/api/raeume/{code}/chat', json=kw, headers=T(zz))
    assert chat(text='Hallo alle').status_code == 403
    assert chat(text='Privat an Zuschauer', an=z2['identity']).status_code == 403
    assert chat(text='Frage an den Host', an=hz['identity']).status_code == 200
    w(host, code, chat='aus')
    assert chat(text='Frage an den Host', an=hz['identity']).status_code == 403
    assert host.post(f'/api/raeume/{code}/chat', json={'text': 'Willkommen'}, headers=T(hz)).status_code == 200
    w(host, code, chat='alle')
    assert chat(text='Hallo alle').status_code == 200


def test_registrierung_mail_link_kapazitaet_csv_und_loeschen():
    host, code = webinar_raum(registrierung=True, kapazitaet=2, fragen=['Firma?', 'Was interessiert dich?'])
    info = neu_client().get(f'/api/webinare/{code}').json()
    assert info['titel'] == 'Großes Webinar' and info['fragen'] == ['Firma?', 'Was interessiert dich?'] and info['frei'] == 2 and info['offen']
    c = neu_client()
    assert c.post(f'/api/webinare/{code}/anmelden', json={'name': 'Ana', 'email': 'kaputt'}).status_code == 400
    assert c.post(f'/api/webinare/{code}/anmelden', json={'name': 'Ana', 'email': 'ana@example.org', 'antworten': ['a', 'b', 'c']}).status_code == 400
    r = c.post(f'/api/webinare/{code}/anmelden', json={'name': 'Ana', 'email': 'ana@example.org', 'antworten': ['=SUMME(A1)', 'Video']})
    assert r.status_code == 200 and r.json() == {'ok': True}
    m = mails_an('ana@example.org')[-1]
    assert 'Anmeldung bestätigt' in m[1] and '30 Tage' in m[2]
    link = re.search(r'/r/' + code + r'\?anmeldung=([\w-]+)', m[2]).group(1)
    # Ohne Link kein Zutritt, mit Link als Zuschauer; Beitritt wird vermerkt
    assert beitreten(neu_client(), code, name='Ohne').status_code == 403
    assert beitreten(neu_client(), code, name='Falsch', anmeldung='x' * 32).status_code == 403
    zugang(host, code)
    az = zugang(neu_client(), code, name='Ana', anmeldung=link)
    assert az['rolle'] == 'zuschauer'
    # Panel braucht keine Anmeldung
    p = host.post(f'/api/raeume/{code}/webinar/panel', json={'name': 'P'}).json()['panel']
    assert zugang(neu_client(), code, name='Paul', panel=p['link'].split('panel=')[1])['rolle'] == 'panelist'
    # Zweite Anmeldung; dritte ist zu viel; erneute Anmeldung derselben Adresse → neuer Link, alter ungültig
    assert neu_client().post(f'/api/webinare/{code}/anmelden', json={'name': 'Bo', 'email': 'bo@example.org'}).status_code == 200
    assert neu_client().post(f'/api/webinare/{code}/anmelden', json={'name': 'Cy', 'email': 'cy@example.org'}).status_code == 409
    assert neu_client().get(f'/api/webinare/{code}').json()['offen'] is False
    assert neu_client().post(f'/api/webinare/{code}/anmelden', json={'name': 'Bo B.', 'email': 'BO@example.org'}).status_code == 200
    bo_links = [re.search(r'anmeldung=([\w-]+)', x[2]).group(1) for x in mails_an('bo@example.org') + mails_an('BO@example.org')]
    assert len(bo_links) == 2 and bo_links[0] != bo_links[1]
    assert beitreten(neu_client(), code, name='Bo', anmeldung=bo_links[0]).status_code == 403
    # Liste + CSV nur für den Besitzer
    fremd, _e, _p = konto()
    assert fremd.get(f'/api/raeume/{code}/webinar/anmeldungen').status_code == 403
    liste = host.get(f'/api/raeume/{code}/webinar/anmeldungen').json()
    assert [a['name'] for a in liste['anmeldungen']] == ['Ana', 'Bo B.'] and liste['anmeldungen'][0]['beigetreten']
    csv = host.get(f'/api/raeume/{code}/webinar/anmeldungen.csv')
    assert csv.status_code == 200 and csv.headers['content-type'].startswith('text/csv')
    zeilen = csv.text.lstrip('﻿').splitlines()
    assert zeilen[0] == 'Name;E-Mail;Angemeldet (UTC);Beigetreten (UTC);Firma?;Was interessiert dich?'
    assert "'=SUMME(A1)" in zeilen[1]                       # Formel entschärft
    # Selbst abmelden mit dem Link aus der Mail
    assert neu_client().post(f'/api/webinare/{code}/abmelden', json={'token': bo_links[1]}).status_code == 200
    assert len(host.get(f'/api/raeume/{code}/webinar/anmeldungen').json()['anmeldungen']) == 1
    # Besitzer löscht einzeln
    aid = liste['anmeldungen'][0]['id']
    assert host.delete(f'/api/raeume/{code}/webinar/anmeldungen/{aid}').status_code == 200
    assert host.get(f'/api/raeume/{code}/webinar/anmeldungen').json()['anmeldungen'] == []
    # Ohne Registrierung gibt es keine öffentliche Seite
    w(host, code, registrierung=False)
    assert neu_client().get(f'/api/webinare/{code}').status_code == 404


def test_registrierung_wird_30_tage_nach_dem_webinar_geloescht():
    host, _e, _p = konto()
    start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    code = raum(host, titel='Geplant', geplant={'start': start.isoformat(), 'dauer_min': 60, 'zeitzone': 'Europe/Berlin'})['code']
    w(host, code, an=True, registrierung=True)
    assert neu_client().post(f'/api/webinare/{code}/anmelden', json={'name': 'Ana', 'email': 'ana2@example.org'}).status_code == 200
    a = db.verbindung().execute('SELECT * FROM webinar_anmeldungen').fetchall()[-1]
    assert a['loeschen_ab'] == int(start.timestamp()) + 3600 + 30 * 86400
    assert any(x[4] for x in mails_an('ana2@example.org'))           # .ics angehängt
    # Meeting findet statt und endet → spätestens 30 Tage nach jetzt
    hz = zugang(host, code)
    host.post(f'/api/raeume/{code}/steuerung', json={'aktion': 'beenden'}, headers=T(hz))
    a = db.verbindung().execute('SELECT * FROM webinar_anmeldungen WHERE id = ?', (a['id'],)).fetchone()
    assert a['loeschen_ab'] <= jetzt() + 30 * 86400
    webinar.aufraeumen(a['loeschen_ab'] + 1)
    assert db.verbindung().execute('SELECT 1 FROM webinar_anmeldungen WHERE id = ?', (a['id'],)).fetchone() is None
    assert main.aufraeumen  # Hintergrund ruft webinar.aufraeumen mit auf
