"""API-Tests für Planung und Komfort: Kalender-Links und Kalender-Abo, Meeting-Vorlagen, Erinnerungen,
Meeting-Verlauf, Dauer-Chat im persönlichen Raum. Hilfen kommen aus test_api (gleiche Testumgebung)."""
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from test_api import T, beitreten, ereignisse, konto, mails_an, neu_client, raum, steuern, zugang  # noqa: F401

from app import db, ics, mail, raeume
from app import livekit_dienst as lk


def bloecke_ok(text):
    """Grobe RFC-5545-Prüfung: CRLF, ≤ 75 Oktette je Zeile, BEGIN/END sauber verschachtelt."""
    assert text.endswith('\r\n') and '\n' not in text.replace('\r\n', '')
    zeilen = text.split('\r\n')[:-1]
    assert all(len(z.encode()) <= 75 for z in zeilen)
    stapel = []
    for z in zeilen:
        if z.startswith('BEGIN:'):
            stapel.append(z[6:])
        elif z.startswith('END:'):
            assert stapel and stapel.pop() == z[4:], z
    assert not stapel


def entfaltet(text):
    return text.replace('\r\n ', '')


# ---------------------------------------------------------------- Kalender
def test_kalender_links_einmalig_und_wiederkehrend():
    host, _e, _p = konto(name='Planer')
    r = raum(host, titel='Jour fixe', geplant={'start': '2030-05-01T08:00:00Z', 'dauer_min': 30, 'beschreibung': 'Agenda'})
    k = r['kalender']
    g = parse_qs(urlparse(k['google']).query)
    assert k['google'].startswith('https://calendar.google.com/calendar/render?')
    assert g['action'] == ['TEMPLATE'] and g['text'] == ['Jour fixe'] and g['dates'] == ['20300501T080000Z/20300501T083000Z']
    assert 'recur' not in g and r['link'] in g['details'][0] and 'Agenda' in g['details'][0] and g['location'] == [r['link']]
    for schluessel, host_ in (('outlook', 'outlook.live.com'), ('office365', 'outlook.office.com')):
        u = urlparse(k[schluessel])
        o = parse_qs(u.query)
        assert u.netloc == host_ and u.path == '/calendar/0/action/compose' and o['rru'] == ['addevent']
        assert o['startdt'] == ['2030-05-01T08:00:00Z'] and o['enddt'] == ['2030-05-01T08:30:00Z'] and o['subject'] == ['Jour fixe']
        assert '+' not in u.query          # Leerzeichen als %20
    assert k['ics'] == f'https://agora.slxt.dev/api/raeume/{r["code"]}/ics'
    assert raum(host)['kalender'] is None                          # Sofort-Meeting: kein Termin

    # Wöchentlich in Berlin, Serie hat schon begonnen → Google: Serie ab erstem Termin + RRULE, Outlook: nächster Termin
    berlin = ics.zone('Europe/Berlin')
    erster = (datetime.now(berlin) - timedelta(days=15)).replace(hour=9, minute=0, second=0, microsecond=0)
    r2 = raum(host, titel='Woche', geplant={'start': erster.replace(tzinfo=None).isoformat(), 'zeitzone': 'Europe/Berlin',
                                            'dauer_min': 60, 'wiederholung': 'woechentlich', 'bis': '2031-01-31'})
    k2 = r2['kalender']
    g2 = parse_qs(urlparse(k2['google']).query)
    start_utc = erster.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    assert g2['dates'][0].startswith(start_utc + '/') and g2['ctz'] == ['Europe/Berlin']
    assert g2['recur'][0].startswith('RRULE:FREQ=WEEKLY;UNTIL=20310131T')
    o2 = parse_qs(urlparse(k2['outlook']).query)
    assert o2['startdt'] == [r2['naechster_termin']] and r2['naechster_termin'] > datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    assert 'wöchentlich' in o2['body'][0] and k2['ics'] in o2['body'][0]
    # Einladung enthält die Links
    host.post(f'/api/raeume/{r2["code"]}/einladen', json={'emails': ['kal@example.org']})
    _an, _b, text, html_, _anh = mails_an('kal@example.org')[-1]
    assert 'Zu Google Kalender: https://calendar.google.com/' in text and 'outlook.office.com' in html_


def test_kalender_abo_feed_und_token_wechsel():
    host, _e, _p = konto(name='Abo Nutzer')
    c = host
    assert c.get('/api/konto/kalender').json()['abo'] == {'aktiv': False, 'url': None, 'webcal': None, 'erstellt': None, 'abgerufen': None}
    morgen = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0, second=0)
    einmalig = raum(c, titel='Einmalig, mit Komma', passcode='geheim99', geplant={'start': morgen.strftime('%Y-%m-%dT%H:%M:%SZ'), 'dauer_min': 45})
    serie = raum(c, titel='Täglich', geplant={'start': '2026-01-05T09:00', 'zeitzone': 'America/New_York', 'wiederholung': 'taeglich'})
    vorbei = raum(c, titel='Vorbei', geplant={'start': (morgen - timedelta(days=3)).strftime('%Y-%m-%dT%H:%M:%SZ'), 'dauer_min': 30})
    sofort = raum(c, titel='Sofort')
    fremd_c, _e2, _p2 = konto()
    fremd = raum(fremd_c, titel='Fremd', geplant={'start': morgen.strftime('%Y-%m-%dT%H:%M:%SZ')})

    abo = c.post('/api/konto/kalender').json()['abo']
    assert abo['aktiv'] and abo['url'].startswith('https://agora.slxt.dev/kalender/') and abo['url'].endswith('.ics')
    assert abo['webcal'] == 'webcal://' + abo['url'][8:]
    assert c.get('/api/konto/kalender').json()['abo']['url'] == abo['url']            # bleibt gleich, bis neu erzeugt
    pfad = urlparse(abo['url']).path
    f = neu_client().get(pfad)                                                         # ohne Anmeldung, nur mit Token
    assert f.status_code == 200 and f.headers['content-type'].startswith('text/calendar')
    text = f.text
    bloecke_ok(text)
    voll = entfaltet(text)
    assert voll.count('BEGIN:VEVENT') == 2 and 'METHOD:' not in voll and 'X-WR-CALNAME:Agora – Abo Nutzer' in voll
    assert f'UID:{einmalig["code"]}@agora.slxt.dev' in voll and f'UID:{serie["code"]}@agora.slxt.dev' in voll
    for nicht in (vorbei['code'], sofort['code'], fremd['code'], 'geheim99'):
        assert nicht not in voll
    assert f'DTSTART:{morgen.strftime("%Y%m%dT%H%M%SZ")}' in voll and 'SUMMARY:Einmalig\\, mit Komma' in voll
    assert 'BEGIN:VTIMEZONE\r\nTZID:America/New_York' in voll and 'DTSTART;TZID=America/New_York:20260105T090000' in voll
    assert 'RRULE:FREQ=DAILY' in voll and 'REFRESH-INTERVAL;VALUE=DURATION:PT1H' in voll
    assert c.get('/api/konto/kalender').json()['abo']['abgerufen'] is not None
    # Neues Token: alter Link sofort ungültig
    neu = c.post('/api/konto/kalender').json()['abo']
    assert neu['url'] != abo['url']
    assert neu_client().get(pfad).status_code == 404
    assert neu_client().get(urlparse(neu['url']).path).status_code == 200
    # Falsche/kaputte Token, ohne Anmeldung keine Verwaltung, CSRF
    assert neu_client().get('/kalender/' + 'x' * 43 + '.ics').status_code == 404
    assert neu_client().get('/kalender/kurz.ics').status_code == 404
    assert neu_client().get('/api/konto/kalender').status_code == 401
    from fastapi.testclient import TestClient
    from app import main
    assert TestClient(main.app).post('/api/konto/kalender').status_code == 403
    # Ausschalten → 404; Konto löschen räumt das Abo mit ab
    assert c.request('DELETE', '/api/konto/kalender').json()['abo']['aktiv'] is False
    assert neu_client().get(urlparse(neu['url']).path).status_code == 404
    letzt = c.post('/api/konto/kalender').json()['abo']
    c.request('DELETE', '/api/konto', json={'passwort': 'geheim-passwort-1'})
    assert neu_client().get(urlparse(letzt['url']).path).status_code == 404
    assert db.verbindung().execute('SELECT COUNT(*) FROM kalender_abo WHERE token_hash IS NOT NULL AND benutzer_id NOT IN '
                                   '(SELECT id FROM benutzer)').fetchone()[0] == 0


def test_ics_bleibt_gueltig():
    text = ics.kalender('abc-defg-hij', 'Ä' * 80, 'x\n' * 50, 'https://agora.slxt.dev/r/abc-defg-hij', 1900000000, 30,
                        'Europe/Vienna', 'woechentlich', 1910000000, 1890000000)
    bloecke_ok(text)
    assert 'RRULE:FREQ=WEEKLY;UNTIL=' in text and 'METHOD:PUBLISH' in text


# ---------------------------------------------------------------- Vorlagen
def test_vorlagen_anlegen_pruefen_aendern_loeschen():
    c, _e, _p = konto()
    assert c.get('/api/vorlagen').json() == {'vorlagen': []}
    v = c.post('/api/vorlagen', json={'name': 'Kurz-Standup', 'einstellungen': {
        'titel': 'Standup', 'dauer_min': 15, 'passcode': True, 'warteraum': True, 'beschreibung': 'Was gestern, was heute?',
        'rechte': {'bildschirm': False, 'selbst_unmute': False}, 'stumm_beitritt': True}}).json()['vorlage']
    e = v['einstellungen']
    assert v['name'] == 'Kurz-Standup' and e['dauer_min'] == 15 and e['passcode'] and e['warteraum'] and not e['chat_speichern']
    assert e['rechte'] == {'chat': True, 'bildschirm': False, 'selbst_unmute': False, 'umbenennen': True, 'mehrfach_freigabe': False}
    for falsch in ({'dauer_min': 2}, {'rechte': {'gibts': True}}, {'rechte': {'chat': 'ja'}}, {'warteraum': 1}, []):
        assert c.post('/api/vorlagen', json={'name': 'X', 'einstellungen': falsch}).status_code == 400, falsch
    assert c.post('/api/vorlagen', json={'name': ' ', 'einstellungen': {}}).status_code == 400
    neu = c.put(f'/api/vorlagen/{v["id"]}', json={'name': 'Standup', 'einstellungen': {'dauer_min': 20}}).json()['vorlage']
    assert neu['name'] == 'Standup' and neu['einstellungen']['dauer_min'] == 20 and neu['einstellungen']['passcode'] is False
    andere, _e2, _p2 = konto()
    assert andere.put(f'/api/vorlagen/{v["id"]}', json={'name': 'Fremd'}).status_code == 404
    assert andere.request('DELETE', f'/api/vorlagen/{v["id"]}').status_code == 404
    assert andere.post(f'/api/vorlagen/{v["id"]}/raum', json={}).status_code == 404
    assert neu_client().get('/api/vorlagen').status_code == 401
    # Aus bestehendem Raum übernehmen (Passcode nur an/aus), fremder Raum verboten
    r = raum(c, titel='Planung', passcode='abcd1234', chat_speichern=True, geplant={'start': '2030-01-01T10:00:00Z', 'dauer_min': 90})
    aus = c.post('/api/vorlagen', json={'name': 'Wie Planung', 'aus_raum': r['code']}).json()['vorlage']['einstellungen']
    assert aus['titel'] == 'Planung' and aus['dauer_min'] == 90 and aus['passcode'] is True and aus['chat_speichern'] is True
    assert 'abcd1234' not in str(aus)
    assert andere.post('/api/vorlagen', json={'name': 'Klau', 'aus_raum': r['code']}).status_code == 403
    # Export enthält die Vorlagen; Grenze 20
    assert {x['name'] for x in c.get('/api/konto/export').json()['vorlagen']} == {'Standup', 'Wie Planung'}
    for i in range(18):
        c.post('/api/vorlagen', json={'name': f'V{i}', 'einstellungen': {}})
    assert c.post('/api/vorlagen', json={'name': 'Zu viel', 'einstellungen': {}}).status_code == 409
    assert c.request('DELETE', f'/api/vorlagen/{v["id"]}').json() == {'ok': True}
    assert len(c.get('/api/vorlagen').json()['vorlagen']) == 19


def test_meeting_aus_vorlage_rechte_und_stumm_beitritt():
    c, _e, _p = konto(name='Vorlagen Host')
    v = c.post('/api/vorlagen', json={'name': 'Vortrag', 'einstellungen': {
        'titel': 'Vortrag', 'dauer_min': 45, 'passcode': True, 'warteraum': False, 'chat_speichern': True, 'beschreibung': 'Folien',
        'rechte': {'bildschirm': False, 'chat': False, 'selbst_unmute': False}, 'stumm_beitritt': True}}).json()['vorlage']
    # Sofort-Meeting: Passcode wird erzeugt und einmal zurückgegeben
    a = c.post(f'/api/vorlagen/{v["id"]}/raum', json={}).json()
    r, pc = a['raum'], a['passcode']
    assert r['titel'] == 'Vortrag' and r['passcode_noetig'] and r['chat_speichern'] and r['geplant'] is None and len(pc) == 8
    code = r['code']
    hz = zugang(c, code)
    assert hz['stumm_beitritt'] is False                            # Host nicht
    g = neu_client()
    assert beitreten(g, code, name='Gast').status_code == 403      # Passcode nötig
    gz = zugang(g, code, name='Gast', passcode=pc)
    assert gz['stumm_beitritt'] is True
    zu = g.get(f'/api/raeume/{code}/zustand', headers=T(gz)).json()
    assert zu['rechte']['bildschirm'] is False and zu['rechte']['chat'] is False and zu['rechte']['selbst_unmute'] is False
    import jwt
    quellen = jwt.decode(gz['token'], options={'verify_signature': False})['video']['canPublishSources']
    assert 'screen_share' not in quellen and 'microphone' in quellen
    # Host ändert Rechte im Meeting → nach dem Ende wieder Vorgaben der Vorlage
    steuern(c, code, hz, aktion='rechte', chat=True, bildschirm=True)
    assert g.get(f'/api/raeume/{code}/zustand', headers=T(gz)).json()['rechte']['chat'] is True
    steuern(c, code, hz, aktion='beenden')
    hz2 = zugang(c, code)
    assert c.get(f'/api/raeume/{code}/zustand', headers=T(hz2)).json()['rechte']['chat'] is False
    # Geplant aus Vorlage: Dauer/Beschreibung aus der Vorlage, Body überschreibt Titel und Passcode
    b = c.post(f'/api/vorlagen/{v["id"]}/raum', json={'titel': 'Vortrag Mai', 'passcode': 'eigen123',
                                                      'geplant': {'start': '2030-05-01T08:00:00Z'}}).json()
    g2 = b['raum']['geplant']
    assert b['raum']['titel'] == 'Vortrag Mai' and g2['dauer_min'] == 45 and g2['beschreibung'] == 'Folien' and b['passcode'] == 'eigen123'
    assert b['raum']['kalender'] is not None
    assert c.post(f'/api/vorlagen/{v["id"]}/raum', json={'geplant': {'start': 'morgen'}}).status_code == 400
    # Ohne Vorlage: kein stumm_beitritt
    r3 = raum(c)
    zugang(c, r3['code'])
    assert zugang(neu_client(), r3['code'], name='X')['stumm_beitritt'] is False


# ---------------------------------------------------------------- Erinnerungen (Uhr wird an pruefen(t) übergeben)
def betreffe(email):
    return [m[1] for m in mails_an(email)]


def test_erinnerung_konto_einstellung_und_idempotenz():
    from app import erinnerungen
    c, email, _p = konto(name='Erin')
    assert c.get('/api/konto/erinnerung').json() == {'minuten': 0}
    for falsch in (5, '15', True, None):
        assert c.put('/api/konto/erinnerung', json={'minuten': falsch}).status_code == 400
    assert c.put('/api/konto/erinnerung', json={'minuten': 15}).json() == {'minuten': 15}
    assert c.get('/api/konto/export').json()['erinnerung_minuten'] == 15
    jetzt_ = int(datetime.now(timezone.utc).timestamp())
    start = (jetzt_ // 60 + 120) * 60                                       # in 2 Stunden, volle Minute
    iso = datetime.fromtimestamp(start, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    r = raum(c, titel='Täglich kurz', geplant={'start': iso, 'dauer_min': 30, 'wiederholung': 'taeglich'})
    mail.POSTEINGANG_TEST.clear()
    assert erinnerungen.pruefen(start - 16 * 60) == 0                       # noch zu früh
    assert erinnerungen.pruefen(start - 15 * 60) == 1
    an, betreff, text, html_, _a = mails_an(email)[-1]
    assert betreff == 'Erinnerung: Täglich kurz beginnt in 15 Minuten' and r['link'] in text and 'Meeting starten' in html_
    # Gleiche Minute, nächste Minute und „Neustart“ (neue Verbindung) → nichts doppelt
    assert erinnerungen.pruefen(start - 15 * 60) == 0 and erinnerungen.pruefen(start - 14 * 60) == 0
    db.verbindung().close()
    db._lokal.c = None
    assert erinnerungen.pruefen(start - 5 * 60) == 0
    assert erinnerungen.pruefen(start + 60) == 0                             # nie nach Beginn
    assert len(mails_an(email)) == 1
    # Nächstes Vorkommen (morgen) → wieder genau eine
    assert erinnerungen.pruefen(start + 86400 - 10 * 60) == 1 and erinnerungen.pruefen(start + 86400 - 9 * 60) == 0
    # Ausschalten → keine mehr; Meeting kurzfristig angelegt (innerhalb des Fensters) → keine
    c.put('/api/konto/erinnerung', json={'minuten': 0})
    assert erinnerungen.pruefen(start + 2 * 86400 - 10 * 60) == 0
    c.put('/api/konto/erinnerung', json={'minuten': 60})
    bald = (jetzt_ // 60 + 30) * 60
    raum(c, titel='Kurzfristig', geplant={'start': datetime.fromtimestamp(bald, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})
    assert erinnerungen.pruefen(bald - 20 * 60) == 0
    # Fehlversand → Schlüssel wird freigegeben, nächster Versuch klappt
    r2 = raum(c, titel='Später', geplant={'start': datetime.fromtimestamp(start + 3 * 3600, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})
    alt = mail.senden
    try:
        def kaputt(*a, **k):
            raise OSError('smtp weg')
        mail.senden = kaputt
        assert erinnerungen.pruefen(start + 3 * 3600 - 60 * 60) == 0
    finally:
        mail.senden = alt
    assert erinnerungen.pruefen(start + 3 * 3600 - 59 * 60) == 1
    assert 'Erinnerung: Später beginnt in 60 Minuten' in betreffe(email)
    assert r2['code']


def test_erinnerung_eingeladene_nur_mit_haken_und_danach_geloescht():
    from app import erinnerungen
    c, _email, _p = konto(name='Gastgeberin')
    jetzt_ = int(datetime.now(timezone.utc).timestamp())
    start = (jetzt_ // 60 + 180) * 60
    iso = datetime.fromtimestamp(start, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    r = raum(c, titel='Beirat', passcode='pc123456', geplant={'start': iso, 'dauer_min': 60, 'wiederholung': 'woechentlich'})
    code = r['code']
    zaehlen = lambda: db.verbindung().execute(  # noqa: E731
        'SELECT COUNT(*) FROM erinnerung_gaeste g JOIN raeume r ON r.id = g.raum_id WHERE r.code = ?', (code,)).fetchone()[0]
    # Ohne Haken wird nichts gespeichert
    assert c.post(f'/api/raeume/{code}/einladen', json={'emails': ['ohne@example.org']}).json() == {'ok': True, 'gesendet': 1}
    assert zaehlen() == 0
    assert c.post(f'/api/raeume/{code}/einladen', json={'emails': ['x@example.org'], 'erinnerung': True, 'erinnerung_min': 7}).status_code == 400
    res = c.post(f'/api/raeume/{code}/einladen', json={'emails': ['eva@example.org', 'tom@example.org'], 'erinnerung': True,
                                                       'erinnerung_min': 30}).json()
    assert res == {'ok': True, 'gesendet': 2, 'erinnerung': 2} and zaehlen() == 2
    mail.POSTEINGANG_TEST.clear()
    assert erinnerungen.pruefen(start - 31 * 60) == 0
    assert erinnerungen.pruefen(start - 30 * 60) == 2 and erinnerungen.pruefen(start - 29 * 60) == 0
    an, betreff, text, _h, _a = mails_an('eva@example.org')[-1]
    assert betreff == 'Erinnerung: Beirat beginnt in 30 Minuten' and 'Gastgeberin' in text and 'pc123456' not in text
    assert 'gelöscht' in text
    assert betreffe('ohne@example.org') == []
    assert zaehlen() == 2                                         # bleibt bis zum Ende des Termins
    erinnerungen.pruefen(start + 30 * 60)
    assert zaehlen() == 2
    erinnerungen.pruefen(start + 60 * 60)                          # Termin vorbei → Adressen weg
    assert zaehlen() == 0
    assert erinnerungen.pruefen(start + 7 * 86400 - 30 * 60) == 0  # nächste Woche: keine Erinnerung mehr
    # Raum gelöscht → Adressen weg (CASCADE)
    c.post(f'/api/raeume/{code}/einladen', json={'emails': ['ida@example.org'], 'erinnerung': True})
    assert zaehlen() == 1
    c.request('DELETE', f'/api/raeume/{code}')
    assert db.verbindung().execute("SELECT COUNT(*) FROM erinnerung_gaeste WHERE email = 'ida@example.org'").fetchone()[0] == 0
    # Sofort-Meeting: Haken wird ignoriert (kein Termin)
    s = raum(c)
    assert c.post(f'/api/raeume/{s["code"]}/einladen', json={'emails': ['s@example.org'], 'erinnerung': True}).json() == {'ok': True, 'gesendet': 1}
