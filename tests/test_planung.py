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
