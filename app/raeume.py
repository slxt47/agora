"""Räume: Sofort-Meetings, geplante (auch wiederkehrende) Meetings, persönlicher Raum, Einladungen, .ics,
Beitreten mit Passcode und Warteraum, Lebenszyklus (aktiv solange jemand da ist, beendet durch Host oder Leerlauf).

Ein Raum (Code) kann nacheinander viele Meetings haben: `sitzung` zählt sie hoch. Teilnahmen, Warteraum-Anfragen,
Sperrliste und Chat gehören immer zu genau einer Sitzung und verschwinden mit ihrem Ende.
"""
import json
import re
import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from . import db, ics, mail
from . import livekit_dienst as lk
from .kern import (MAX_TEILNEHMER, SEITE, benutzer_aus_sitzung, client_ip, fehler, grenze, h, hash_pw, iso, jetzt,
                   json_body, pw_ok, text_feld)

router = APIRouter()
ALPHABET = 'abcdefghijkmnpqrstuvwxyz'          # ohne l und o (keine Verwechslung mit 1/0)
CODE_RE = re.compile(r'^[a-z]{3}-[a-z]{4}-[a-z]{3}$')
RECHTE_STANDARD = {'chat': True, 'bildschirm': True, 'selbst_unmute': True, 'umbenennen': True, 'mehrfach_freigabe': False}
WIEDERHOLUNG = ('keine', 'taeglich', 'woechentlich')
LEER_S = 600                  # Meeting endet nach 10 min ohne Teilnehmer
ANFRAGE_S = 1800              # Warteraum-Anfragen verfallen nach 30 min
FARBEN = ['#a993ff', '#5b8def', '#2fb67c', '#f0a04b', '#e5677d', '#41b8c4', '#c873d6', '#8fb339']
RANG = {'host': 3, 'cohost': 2, 'teilnehmer': 1, 'gast': 1}
BEIM_ENDE = []                # Module (Umfragen, Breakout) hängen sich hier ein: f(raum) räumt beim Meeting-Ende auf


# ---------------------------------------------------------------- Hilfen
def code_neu():
    c = db.verbindung()
    while True:
        z = ''.join(secrets.choice(ALPHABET) for _ in range(10))
        code = f'{z[:3]}-{z[3:7]}-{z[7:]}'
        if not c.execute('SELECT 1 FROM raeume WHERE code = ?', (code,)).fetchone():
            return code


def raum_laden(code):
    r = db.verbindung().execute('SELECT * FROM raeume WHERE code = ?', (code,)).fetchone() if CODE_RE.match(code or '') else None
    if not r:
        fehler(404, 'Dieses Meeting gibt es nicht.')
    return r


def neu_laden(r):
    return db.verbindung().execute('SELECT * FROM raeume WHERE id = ?', (r['id'],)).fetchone()


def rechte(r):
    return RECHTE_STANDARD | json.loads(r['rechte'] or '{}')


def meta(r):
    """Raum-Metadaten für LiveKit (nur der Server schreibt sie)."""
    return {'rechte': rechte(r), 'gesperrt': bool(r['gesperrt']), 'aufnahme': {'von_name': r['aufnahme_von']} if r['aufnahme_von'] else False}


def link(code):
    return f'{SEITE}/r/{code}'


def termin(r, t=None):
    if r['art'] != 'geplant':
        return None
    return ics.naechster_termin(r['start'], r['dauer_min'], r['wiederholung'], r['zeitzone'], r['bis'], t or jetzt())


def raum_json(r, b=None):
    c = db.verbindung()
    besitzer = c.execute('SELECT name FROM benutzer WHERE id = ?', (r['besitzer_id'],)).fetchone()
    geplant = None
    if r['art'] == 'geplant':
        geplant = {'start': iso(r['start']), 'dauer_min': r['dauer_min'], 'zeitzone': r['zeitzone'],
                   'beschreibung': r['beschreibung'] or '', 'wiederholung': r['wiederholung'], 'bis': iso(r['bis'])}
    return {'code': r['code'], 'titel': r['titel'], 'besitzer_name': besitzer['name'] if besitzer else '',
            'ist_host': bool(b) and b['id'] == r['besitzer_id'], 'passcode_noetig': bool(r['passcode_hash']),
            'warteraum': bool(r['warteraum']), 'gesperrt': bool(r['gesperrt']), 'aktiv': bool(r['aktiv']),
            'persoenlich': r['art'] == 'persoenlich', 'chat_speichern': bool(r['chat_speichern']),
            'beendet': bool(r['beendet']), 'geplant': geplant, 'naechster_termin': iso(termin(r)), 'link': link(r['code']),
            'kalender': kalender_links(r)}


def kalender_links(r):
    """„Zu Google/Outlook/Office 365 hinzufügen“ + .ics – nur für geplante Meetings."""
    if r['art'] != 'geplant':
        return None
    return ics.kalender_links(r['code'], r['titel'], r['beschreibung'], link(r['code']), r['start'], r['dauer_min'], r['zeitzone'],
                              r['wiederholung'], r['bis'], termin(r), f'{SEITE}/api/raeume/{r["code"]}/ics')


def nachricht_json(n):
    return {'id': n['id'], 'von': n['von'], 'von_name': n['von_name'], 'an': n['an'], 'an_name': n['an_name'],
            'text': n['text'], 'zeit': iso(n['zeit'])}


def zeit_lesen(wert, zeitzone, feld):
    """ISO-Zeit lesen; ohne Zeitzonen-Angabe gilt die Zeitzone des Meetings. Ein reines Datum (bis) = Tagesende."""
    if not isinstance(wert, str) or len(wert) > 40:
        fehler(400, f'Ungültige Zeitangabe ({feld}).')
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', wert):
            d = datetime.fromisoformat(wert + 'T23:59:59')
        else:
            d = datetime.fromisoformat(wert.replace('Z', '+00:00'))
    except ValueError:
        fehler(400, f'Ungültige Zeitangabe ({feld}).')
    if d.tzinfo is None:
        d = d.replace(tzinfo=ZoneInfo(zeitzone))
    return int(d.timestamp())


def geplant_lesen(g, b):
    if not isinstance(g, dict):
        fehler(400, 'Ungültige Angaben zum Termin.')
    zz = g.get('zeitzone') or b['zeitzone']
    try:
        if not isinstance(zz, str) or len(zz) > 64:
            raise ValueError
        ZoneInfo(zz)
    except Exception:   # noqa: BLE001
        fehler(400, 'Unbekannte Zeitzone.')
    start = zeit_lesen(g.get('start'), zz, 'start')
    dauer = g.get('dauer_min', 60)
    if not isinstance(dauer, int) or isinstance(dauer, bool) or not 5 <= dauer <= 1440:
        fehler(400, 'Dauer: 5 bis 1440 Minuten.')
    w = g.get('wiederholung') or 'keine'
    if w not in WIEDERHOLUNG:
        fehler(400, 'Wiederholung: keine, taeglich oder woechentlich.')
    bis = zeit_lesen(g['bis'], zz, 'bis') if g.get('bis') and w != 'keine' else None
    if bis is not None and bis < start:
        fehler(400, 'Das Ende der Wiederholung liegt vor dem Start.')
    if start < jetzt() - 86400 * 366 or start > jetzt() + 86400 * 366 * 5:
        fehler(400, 'Der Termin liegt zu weit in der Vergangenheit oder Zukunft.')
    return {'start': start, 'dauer_min': dauer, 'zeitzone': zz, 'wiederholung': w, 'bis': bis,
            'beschreibung': text_feld(g, 'beschreibung', 2000)}


async def einstellungen_lesen(d, b, neu):
    """Felder aus POST/PATCH (titel, passcode, warteraum, chat_speichern, geplant) → Spalten."""
    f = {}
    if 'titel' in d:
        f['titel'] = text_feld(d, 'titel', 120)
    if 'passcode' in d:
        pc = d['passcode']
        if pc in (None, ''):
            f['passcode_hash'] = None
        elif not isinstance(pc, str) or not 4 <= len(pc) <= 32:
            fehler(400, 'Passcode: 4 bis 32 Zeichen.')
        else:
            f['passcode_hash'] = await run_in_threadpool(hash_pw, pc)
    for k in ('warteraum', 'chat_speichern'):
        if k in d:
            if not isinstance(d[k], bool):
                fehler(400, 'Ungültige Anfrage')
            f[k] = int(d[k])
    if d.get('geplant') is not None:
        f.update(geplant_lesen(d['geplant'], b))
    return f


def raum_anlegen(besitzer_id, art, titel, felder=None):
    c = db.verbindung()
    code = code_neu()
    t = jetzt()
    c.execute('INSERT INTO raeume (code, besitzer_id, art, titel, rechte, erstellt, letzte_aktivitaet) VALUES (?, ?, ?, ?, ?, ?, ?)',
              (code, besitzer_id, art, titel, json.dumps(RECHTE_STANDARD), t, t))
    for k, v in (felder or {}).items():
        c.execute(f'UPDATE raeume SET {k} = ? WHERE code = ?', (v, code))
    return c.execute('SELECT * FROM raeume WHERE code = ?', (code,)).fetchone()


def persoenlichen_raum_anlegen(benutzer_id, name):
    return raum_anlegen(benutzer_id, 'persoenlich', f'Persönlicher Raum von {name}'[:120])


def besitzer_raum(req, code):
    b = benutzer_aus_sitzung(req)
    r = raum_laden(code)
    if r['besitzer_id'] != b['id']:
        fehler(403, 'Nur der Besitzer darf das.')
    return b, r


# ---------------------------------------------------------------- Meeting-Lebenszyklus
def meeting_starten(r):
    """Startet ein neues Meeting im Raum, falls gerade keins läuft."""
    if r['aktiv']:
        return r
    c = db.verbindung()
    c.execute('UPDATE raeume SET aktiv = 1, sitzung = sitzung + 1, gestartet = ?, leer_seit = ?, gesperrt = 0, aufnahme_von = NULL, '
              'beendet = 0, letzte_aktivitaet = ? WHERE id = ? AND aktiv = 0', (jetzt(), jetzt(), jetzt(), r['id']))
    return neu_laden(r)


async def meeting_beenden(r, grund='host'):
    """Beendet das laufende Meeting: alle raus, Teilnahmen/Warteraum/Sperrliste weg, Chat weg (außer chat_speichern)."""
    r = neu_laden(r)
    if not r or not r['aktiv']:
        return
    await lk.senden(r['code'], {'typ': 'beendet'})
    await lk.raum_loeschen(r['code'])
    c = db.verbindung()
    c.execute('DELETE FROM teilnahmen WHERE raum_id = ?', (r['id'],))
    c.execute("UPDATE anfragen SET status = 'beendet' WHERE raum_id = ? AND status IN ('wartet', 'zugelassen')", (r['id'],))
    c.execute('DELETE FROM sperren WHERE raum_id = ?', (r['id'],))
    if not r['chat_speichern']:
        c.execute('DELETE FROM nachrichten WHERE raum_id = ?', (r['id'],))
    for f in BEIM_ENDE:
        f(r)
    c.execute('UPDATE raeume SET aktiv = 0, leer_seit = NULL, gesperrt = 0, aufnahme_von = NULL, beendet = ?, letzte_aktivitaet = ? '
              'WHERE id = ?', (1 if r['art'] == 'sofort' else 0, jetzt(), r['id']))
    db.protokollieren(f'Meeting beendet ({grund})', None, r['code'])


def teilnahmen(r, rollen=None):
    q = 'SELECT * FROM teilnahmen WHERE raum_id = ? AND sitzung = ?'
    rows = db.verbindung().execute(q, (r['id'], r['sitzung'])).fetchall()
    return [t for t in rows if rollen is None or t['rolle'] in rollen]


def wartend(r):
    return db.verbindung().execute("SELECT COUNT(*) FROM anfragen WHERE raum_id = ? AND sitzung = ? AND status = 'wartet'",
                                   (r['id'], r['sitzung'])).fetchone()[0]


async def warteraum_melden(r):
    hosts = [t['identity'] for t in teilnahmen(r, ('host', 'cohost'))]
    await lk.senden(r['code'], {'typ': 'warteraum', 'wartend': wartend(r)}, an=hosts)


def gesperrt_fuer(r, b, ip):
    c = db.verbindung()
    if b:
        return bool(c.execute("SELECT 1 FROM sperren WHERE raum_id = ? AND sitzung = ? AND art = 'benutzer' AND wert = ?",
                              (r['id'], r['sitzung'], str(b['id']))).fetchone())
    return bool(c.execute("SELECT 1 FROM sperren WHERE raum_id = ? AND sitzung = ? AND art = 'ip' AND wert = ?",
                          (r['id'], r['sitzung'], ip)).fetchone())


async def voll(r):
    liste = await lk.teilnehmer(r['code'])
    return liste is not None and len(liste) >= MAX_TEILNEHMER


async def zugang_erstellen(r, name, benutzer_id, ip):
    """Neue Teilnahme + LiveKit-Token. Der Besitzer wird Host (ein anderer Host wird dann Co-Host)."""
    c = db.verbindung()
    b = c.execute('SELECT * FROM benutzer WHERE id = ?', (benutzer_id,)).fetchone() if benutzer_id else None
    if b and b['id'] == r['besitzer_id']:
        rolle = 'host'
        for t in teilnahmen(r, ('host',)):
            if t['benutzer_id'] == b['id']:
                continue                 # eigene ältere Teilnahme (z. B. nach Neuladen) bleibt Host
            c.execute("UPDATE teilnahmen SET rolle = 'cohost' WHERE identity = ?", (t['identity'],))
            await lk.aktualisieren(r['code'], t['identity'], rolle='cohost', farbe=t['farbe'], rechte=rechte(r))
            await lk.senden(r['code'], {'typ': 'rolle', 'identity': t['identity'], 'rolle': 'cohost'})
    else:
        rolle = 'teilnehmer' if b else 'gast'
    farbe = b['farbe'] if b else secrets.choice(FARBEN)
    identity = f'u{b["id"]}-{secrets.token_hex(4)}' if b else f'g-{secrets.token_hex(5)}'
    geheim = secrets.token_urlsafe(24)
    c.execute('INSERT INTO teilnahmen VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
              (h(geheim), r['id'], r['sitzung'], identity, name, rolle, b['id'] if b else None, ip, farbe, jetzt()))
    c.execute('UPDATE raeume SET letzte_aktivitaet = ? WHERE id = ?', (jetzt(), r['id']))
    await lk.raum_anlegen(r['code'], meta(r), MAX_TEILNEHMER)
    return {'token': lk.token(r['code'], identity, name, rolle, farbe, rechte(r)), 'url': lk_url(), 'identity': identity,
            'name': name, 'rolle': rolle, 'teilnahme': geheim}


def lk_url():
    from .kern import LIVEKIT_URL
    return LIVEKIT_URL


async def leere_pruefen(t=None):
    """Hintergrund: aktive Meetings ohne Teilnehmer (und ohne Wartende) nach LEER_S beenden."""
    t = t or jetzt()
    c = db.verbindung()
    for r in c.execute('SELECT * FROM raeume WHERE aktiv = 1').fetchall():
        liste = await lk.teilnehmer(r['code'])
        if liste is None:
            continue                     # LiveKit nicht erreichbar → nichts entscheiden
        if liste or c.execute("SELECT 1 FROM anfragen WHERE raum_id = ? AND sitzung = ? AND status = 'wartet' AND erstellt > ?",
                              (r['id'], r['sitzung'], t - ANFRAGE_S)).fetchone():
            c.execute('UPDATE raeume SET leer_seit = NULL, letzte_aktivitaet = ? WHERE id = ?', (t, r['id']))
        elif r['leer_seit'] is None:
            c.execute('UPDATE raeume SET leer_seit = ? WHERE id = ?', (t, r['id']))
        elif t - r['leer_seit'] >= LEER_S:
            await meeting_beenden(r, 'leer')


def aufraeumen(t=None):
    """Hintergrund: alte Sofort-Meetings und vergangene geplante Meetings löschen, alte Anfragen entfernen."""
    t = t or jetzt()
    c = db.verbindung()
    c.execute('DELETE FROM anfragen WHERE erstellt < ?', (t - ANFRAGE_S,))
    c.execute("DELETE FROM raeume WHERE art = 'sofort' AND aktiv = 0 AND letzte_aktivitaet < ?", (t - 30 * 86400,))
    for r in c.execute("SELECT * FROM raeume WHERE art = 'geplant' AND aktiv = 0 AND letzte_aktivitaet < ?", (t - 90 * 86400,)).fetchall():
        if termin(r, t) is None:
            c.execute('DELETE FROM raeume WHERE id = ?', (r['id'],))


# ---------------------------------------------------------------- Verwaltung
@router.post('/api/raeume')
async def raum_neu(req: Request):
    b = benutzer_aus_sitzung(req)
    d = await json_body(req)
    grenze.pruefen(f'raum:{b["id"]}', 60, 3600)
    f = await einstellungen_lesen(d, b, True)
    art = 'geplant' if 'start' in f else 'sofort'
    titel = f.pop('titel', '') or (f'Meeting von {b["name"]}' if art == 'sofort' else 'Geplantes Meeting')
    r = raum_anlegen(b['id'], art, titel[:120], f)
    db.protokollieren(f'Raum angelegt ({art})', b['id'], r['code'])
    return {'raum': raum_json(r, b)}


@router.get('/api/raeume')
def raeume_liste(req: Request):
    b = benutzer_aus_sitzung(req)
    c = db.verbindung()
    rows = c.execute('SELECT * FROM raeume WHERE besitzer_id = ? ORDER BY erstellt DESC', (b['id'],)).fetchall()
    geplant, vergangen, persoenlich = [], [], None
    for r in rows:
        if r['art'] == 'persoenlich':
            persoenlich = persoenlich or raum_json(r, b)
        elif r['art'] == 'geplant':
            (geplant if termin(r) is not None or r['aktiv'] else vergangen).append(raum_json(r, b))
        else:
            (vergangen if r['beendet'] else geplant).append(raum_json(r, b))
    geplant.sort(key=lambda x: x['naechster_termin'] or '0')      # offene Sofort-Meetings zuerst, dann nach Termin
    if persoenlich is None:
        persoenlich = raum_json(persoenlichen_raum_anlegen(b['id'], b['name']), b)
    return {'geplant': geplant, 'persoenlich': persoenlich, 'vergangen': vergangen[:50]}


@router.get('/api/raeume/{code}')
def raum_info(code: str, req: Request):
    grenze.pruefen('info:' + client_ip(req), 120, 60)
    return {'raum': raum_json(raum_laden(code), benutzer_aus_sitzung(req, pflicht=False))}


@router.patch('/api/raeume/{code}')
async def raum_aendern(code: str, req: Request):
    b, r = besitzer_raum(req, code)
    d = await json_body(req)
    if r['art'] != 'geplant' and d.get('geplant') is not None:
        fehler(400, 'Nur geplante Meetings haben einen Termin.')
    f = await einstellungen_lesen(d, b, False)
    if 'titel' in f and not f['titel']:
        del f['titel']
    c = db.verbindung()
    for k, v in f.items():
        c.execute(f'UPDATE raeume SET {k} = ? WHERE id = ?', (v, r['id']))
    return {'raum': raum_json(neu_laden(r), b)}


@router.delete('/api/raeume/{code}')
async def raum_loeschen(code: str, req: Request):
    b, r = besitzer_raum(req, code)
    if r['art'] == 'persoenlich':
        fehler(400, 'Der persönliche Raum kann nicht gelöscht werden.')
    await meeting_beenden(r, 'gelöscht')
    db.verbindung().execute('DELETE FROM raeume WHERE id = ?', (r['id'],))
    db.protokollieren('Raum gelöscht', b['id'], code)
    return {'ok': True}


def termin_details(r, b):
    """Zeilen für die Einladung (Zeit in der Zeitzone des Meetings)."""
    zeilen = []
    t = termin(r)
    if t is not None:
        tz = ics.zone(r['zeitzone'])
        lokal = datetime.fromtimestamp(t, tz)
        zeilen.append(('Wann', f'{lokal.strftime("%d.%m.%Y, %H:%M")} Uhr ({r["zeitzone"]}), {r["dauer_min"]} min'))
        if r['wiederholung'] != 'keine':
            zeilen.append(('Wiederholung', 'täglich' if r['wiederholung'] == 'taeglich' else 'wöchentlich'))
    zeilen.append(('Meeting-Code', r['code']))
    if r['beschreibung']:
        zeilen.append(('Beschreibung', r['beschreibung']))
    return zeilen


def ics_text(r):
    b = db.verbindung().execute('SELECT name FROM benutzer WHERE id = ?', (r['besitzer_id'],)).fetchone()
    return ics.kalender(r['code'], r['titel'], r['beschreibung'], link(r['code']), r['start'], r['dauer_min'], r['zeitzone'],
                        r['wiederholung'], r['bis'], r['erstellt'], b['name'] if b else 'Agora')


@router.post('/api/raeume/{code}/einladen')
async def einladen(code: str, req: Request):
    b, r = besitzer_raum(req, code)
    d = await json_body(req)
    emails = d.get('emails')
    if not isinstance(emails, list) or not 1 <= len(emails) <= 20:
        fehler(400, 'Bitte 1 bis 20 E-Mail-Adressen angeben.')
    from .konto import zustellbar
    adressen = []
    for e in emails:
        if not isinstance(e, str) or not zustellbar(e.strip()):
            fehler(400, f'Ungültige E-Mail-Adresse: {str(e)[:80]}')
        if e.strip().lower() not in [a.lower() for a in adressen]:
            adressen.append(e.strip())
    passcode = d.get('passcode')
    if passcode is not None:        # optional: Passcode in die Mail schreiben – nur wenn er stimmt
        if not isinstance(passcode, str) or not r['passcode_hash'] or not await run_in_threadpool(pw_ok, r['passcode_hash'], passcode):
            fehler(400, 'Der Passcode stimmt nicht.')
    grenze.pruefen(f'einladen:{b["id"]}', 100, 86400)
    ics_daten = ics_text(r) if r['art'] == 'geplant' else None
    details = termin_details(r, b)
    gesendet = 0
    for a in adressen:
        if grenze.ok('mail:' + a.lower(), 3, 3600):
            mail.im_hintergrund(mail.einladung_mail, a, b['name'], r['titel'], link(code), details, ics_daten, passcode,
                                kalender_links(r))
            gesendet += 1
    db.protokollieren(f'Einladungen verschickt ({gesendet})', b['id'], code)
    return {'ok': True, 'gesendet': gesendet}


@router.get('/api/raeume/{code}/ics')
def ics_download(code: str, req: Request):
    grenze.pruefen('ics:' + client_ip(req), 60, 600)
    r = raum_laden(code)
    if r['art'] != 'geplant':
        fehler(404, 'Für dieses Meeting gibt es keinen Termin.')
    return Response(ics_text(r), media_type='text/calendar; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="agora-{code}.ics"'})


# ---------------------------------------------------------------- Beitreten
@router.post('/api/raeume/{code}/beitreten')
async def beitreten(code: str, req: Request):
    r = raum_laden(code)
    d = await json_body(req)
    b = benutzer_aus_sitzung(req, pflicht=False)
    ip = client_ip(req)
    grenze.pruefen('beitreten:' + ip, 60, 600)
    name = text_feld(d, 'name', 60).replace('\n', ' ') or (b['name'] if b else '')
    if not name:
        fehler(400, 'Bitte einen Namen eingeben.')
    besitzer = bool(b) and b['id'] == r['besitzer_id']
    if not besitzer:
        if r['beendet']:
            fehler(403, 'Meeting ist beendet')
        if r['passcode_hash']:
            grenze.pruefen(f'passcode:{ip}:{code}', 10, 600)
            pc = d.get('passcode')
            if not isinstance(pc, str) or len(pc) > 64 or not await run_in_threadpool(pw_ok, r['passcode_hash'], pc):
                fehler(403, 'Falscher Passcode')
        if r['aktiv'] and r['gesperrt']:
            fehler(403, 'Meeting ist gesperrt')
    r = meeting_starten(r)
    if not besitzer and gesperrt_fuer(r, b, ip):
        fehler(403, 'Du wurdest aus diesem Meeting entfernt.')
    if await voll(r):
        fehler(403, 'Meeting ist voll')
    if r['warteraum'] and not besitzer:
        anfrage, geheim = secrets.token_urlsafe(12), secrets.token_urlsafe(24)
        db.verbindung().execute('INSERT INTO anfragen (id, geheim_hash, raum_id, sitzung, name, benutzer_id, ip, erstellt) '
                                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (anfrage, h(geheim), r['id'], r['sitzung'], name,
                                                                    b['id'] if b else None, ip, jetzt()))
        await warteraum_melden(r)
        return {'status': 'wartet', 'anfrage': anfrage, 'anfrage_geheim': geheim}
    if besitzer:
        db.protokollieren('Meeting betreten (Host)', b['id'], code)
    return {'status': 'zugelassen', 'zugang': await zugang_erstellen(r, name, b['id'] if b else None, ip)}


@router.get('/api/raeume/{code}/warten/{anfrage}')
async def warten(code: str, anfrage: str, req: Request, g: str = ''):
    grenze.pruefen('warten:' + client_ip(req), 240, 60)
    r = raum_laden(code)
    c = db.verbindung()
    a = c.execute('SELECT * FROM anfragen WHERE id = ? AND raum_id = ?', (anfrage, r['id'])).fetchone()
    if not a or len(g) > 100 or a['geheim_hash'] != h(g):
        fehler(404, 'Anfrage nicht gefunden')
    if a['status'] == 'beendet' or a['sitzung'] != r['sitzung'] or not r['aktiv']:
        return {'status': 'abgelehnt', 'grund': 'beendet'}
    if a['status'] == 'abgelehnt':
        return {'status': 'abgelehnt'}
    if a['status'] == 'wartet':
        return {'status': 'wartet'}
    c.execute('DELETE FROM anfragen WHERE id = ?', (a['id'],))     # zugelassen: Zugang genau einmal ausgeben
    return {'status': 'zugelassen', 'zugang': await zugang_erstellen(r, a['name'], a['benutzer_id'], a['ip'])}
