"""Aufzeichnungen: Der Browser des Hosts nimmt lokal auf und lädt die Datei hier hoch (roher Body, gestreamt).
Bibliothek je Konto (500 MB/Datei, 5 GB/Konto, 90 Tage), Abspielen mit Range, Freigabe-Links (1/7/30 Tage, optional Passwort).
"""
import os
import re
import secrets
import time
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import db
from .kern import SEITE, benutzer_aus_sitzung, client_ip, fehler, grenze, hash_pw, iso, jetzt, json_body, pw_ok
from .raeume import CODE_RE

router = APIRouter()
MAX_DATEI = 500 * 1024 ** 2
KONTINGENT = 5 * 1024 ** 3
AUFBEWAHRUNG_S = 90 * 86400
TYPEN = {'video/webm': 'webm', 'video/mp4': 'mp4'}
TAGE = (1, 7, 30)
ID_RE = re.compile(r'^[A-Za-z0-9_-]{22}$')
BEIM_ENTFERNEN = []        # f(id) – andere Module räumen eigene Dateien zur Aufnahme weg (Transkript .vtt)
JSON_EXTRA = []            # f(zeile) → dict, wird in aufnahme_json gemischt (Transkript-Status)
_freigabe_ok = {}          # Cache: sha256(token + passwort) → gültig bis (sonst bei jedem Range-Abruf argon2)


def pfad(i):
    return os.path.join(db.DATEN, 'aufnahmen', i)


def belegt(benutzer_id):
    return db.verbindung().execute('SELECT COALESCE(SUM(groesse), 0) FROM aufnahmen WHERE benutzer_id = ?', (benutzer_id,)).fetchone()[0]


def freigabe_link(token):
    return f'{SEITE}/a/{token}'


def aufnahme_json(a):
    fg = None
    if a['freigabe_token'] and a['freigabe_bis'] and a['freigabe_bis'] > jetzt():
        fg = {'link': freigabe_link(a['freigabe_token']), 'bis': iso(a['freigabe_bis']), 'passwort': bool(a['freigabe_passwort'])}
    return {'id': a['id'], 'titel': a['titel'], 'raum_code': a['raum_code'], 'groesse': a['groesse'], 'typ': a['typ'],
            'dauer': a['dauer'], 'erstellt': iso(a['erstellt']), 'ablauf': iso(a['ablauf']), 'freigabe': fg} | {
        k: v for f in JSON_EXTRA for k, v in f(a).items()}


def aufnahme_entfernen(i):
    try:
        os.remove(pfad(i))
    except FileNotFoundError:
        pass
    for f in BEIM_ENTFERNEN:
        f(i)
    db.verbindung().execute('DELETE FROM aufnahmen WHERE id = ?', (i,))


def eigene(req, i):
    b = benutzer_aus_sitzung(req)
    a = db.verbindung().execute('SELECT * FROM aufnahmen WHERE id = ? AND benutzer_id = ?', (i, b['id'])).fetchone() if ID_RE.match(i) else None
    if not a:
        fehler(404, 'Aufnahme nicht gefunden')
    return b, a


def datei_antwort(a, herunterladen=False):
    endung = TYPEN.get(a['typ'], 'webm')
    name = re.sub(r'[^A-Za-z0-9 ._-]', '_', a['titel'])[:80] or 'aufnahme'
    return FileResponse(pfad(a['id']), media_type=a['typ'], filename=f'{name}.{endung}',
                        content_disposition_type='attachment' if herunterladen else 'inline')


@router.post('/api/aufnahmen')
async def hochladen(req: Request):
    b = benutzer_aus_sitzung(req)
    grenze.pruefen(f'upload:{b["id"]}', 30, 3600)
    typ = req.headers.get('content-type', '').split(';')[0].strip().lower()
    if typ not in TYPEN:
        fehler(415, 'Nur WebM- oder MP4-Videos.')
    code = req.headers.get('x-raum', '').strip()
    if code and not CODE_RE.match(code):
        fehler(400, 'Ungültiger Raum-Code')
    titel = ''.join(ch for ch in unquote(req.headers.get('x-titel', '')) if ord(ch) >= 32).strip()[:200]
    titel = titel or time.strftime('Aufnahme %d.%m.%Y %H:%M', time.gmtime())
    dauer = req.headers.get('x-dauer', '')
    dauer = int(dauer) if dauer.isdigit() and int(dauer) <= 86400 else None
    try:
        laenge = int(req.headers.get('content-length') or 0)
    except ValueError:
        laenge = 0
    if laenge > MAX_DATEI:
        fehler(413, 'Die Aufnahme ist größer als 500 MB.')
    if belegt(b['id']) + max(laenge, 1) > KONTINGENT:
        fehler(507, 'Dein Speicher für Aufnahmen ist voll (5 GB). Lösche alte Aufnahmen.')
    i = secrets.token_urlsafe(16)
    tmp = os.path.join(db.DATEN, 'tmp', i)
    n = 0
    try:
        with open(tmp, 'wb') as f:
            async for teil in req.stream():
                n += len(teil)
                if n > MAX_DATEI:
                    fehler(413, 'Die Aufnahme ist größer als 500 MB.')
                f.write(teil)
        if n == 0:
            fehler(400, 'Leere Datei')
        c = db.verbindung()
        c.execute('BEGIN IMMEDIATE')
        if belegt(b['id']) + n > KONTINGENT:          # nochmal unter Sperre (parallele Uploads)
            c.execute('ROLLBACK')
            fehler(507, 'Dein Speicher für Aufnahmen ist voll (5 GB). Lösche alte Aufnahmen.')
        c.execute('INSERT INTO aufnahmen (id, benutzer_id, titel, raum_code, groesse, typ, dauer, erstellt, ablauf) '
                  'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (i, b['id'], titel, code or None, n, typ, dauer, jetzt(), jetzt() + AUFBEWAHRUNG_S))
        os.replace(tmp, pfad(i))
        c.execute('COMMIT')
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    db.protokollieren('Aufnahme hochgeladen', b['id'], code or None)
    return {'aufnahme': aufnahme_json(db.verbindung().execute('SELECT * FROM aufnahmen WHERE id = ?', (i,)).fetchone())}


@router.get('/api/aufnahmen')
def liste(req: Request):
    b = benutzer_aus_sitzung(req)
    rows = db.verbindung().execute('SELECT * FROM aufnahmen WHERE benutzer_id = ? ORDER BY erstellt DESC', (b['id'],)).fetchall()
    return {'aufnahmen': [aufnahme_json(a) for a in rows], 'belegt': belegt(b['id']), 'kontingent': KONTINGENT}


@router.get('/api/aufnahmen/{i}/datei')
def datei(i: str, req: Request, download: int = 0):
    _b, a = eigene(req, i)
    return datei_antwort(a, bool(download))


@router.delete('/api/aufnahmen/{i}')
def loeschen(i: str, req: Request):
    b, a = eigene(req, i)
    aufnahme_entfernen(a['id'])
    db.protokollieren('Aufnahme gelöscht', b['id'])
    return {'ok': True}


@router.post('/api/aufnahmen/{i}/freigabe')
async def freigeben(i: str, req: Request):
    b, a = eigene(req, i)
    d = await json_body(req)
    if d.get('tage') not in TAGE or isinstance(d.get('tage'), bool):
        fehler(400, 'Gültigkeit: 1, 7 oder 30 Tage.')
    pw = d.get('passwort')
    if pw in (None, ''):
        pw_hash = None
    elif not isinstance(pw, str) or not 4 <= len(pw) <= 100:
        fehler(400, 'Passwort: 4 bis 100 Zeichen.')
    else:
        pw_hash = await run_in_threadpool(hash_pw, pw)
    token = secrets.token_urlsafe(16)
    bis = min(jetzt() + d['tage'] * 86400, a['ablauf'])
    db.verbindung().execute('UPDATE aufnahmen SET freigabe_token = ?, freigabe_bis = ?, freigabe_passwort = ? WHERE id = ?',
                            (token, bis, pw_hash, a['id']))
    db.protokollieren(f'Aufnahme freigegeben ({d["tage"]} Tage)', b['id'])
    return {'link': freigabe_link(token)}


@router.delete('/api/aufnahmen/{i}/freigabe')
def freigabe_aufheben(i: str, req: Request):
    _b, a = eigene(req, i)
    db.verbindung().execute('UPDATE aufnahmen SET freigabe_token = NULL, freigabe_bis = NULL, freigabe_passwort = NULL WHERE id = ?', (a['id'],))
    return {'ok': True}


def freigabe(token):
    a = db.verbindung().execute('SELECT * FROM aufnahmen WHERE freigabe_token = ? AND freigabe_bis > ? AND ablauf > ?',
                                (token, jetzt(), jetzt())).fetchone() if ID_RE.match(token) else None
    if not a:
        fehler(404, 'Diese Freigabe gibt es nicht (mehr).')
    return a


@router.get('/api/freigaben/{token}')
def freigabe_info(token: str, req: Request):
    grenze.pruefen('freigabe:' + client_ip(req), 120, 60)
    a = freigabe(token)
    return {'titel': a['titel'], 'dauer': a['dauer'], 'groesse': a['groesse'], 'typ': a['typ'], 'passwort_noetig': bool(a['freigabe_passwort'])}


@router.get('/api/freigaben/{token}/datei')
def freigabe_datei(token: str, req: Request, p: str = ''):
    grenze.pruefen('freigabe-datei:' + client_ip(req), 600, 600)
    a = freigabe(token)
    if a['freigabe_passwort']:
        from .kern import h
        schluessel = h(a['freigabe_token'] + '\0' + p + '\0' + a['freigabe_passwort'])
        if _freigabe_ok.get(schluessel, 0) < time.time():
            grenze.pruefen('freigabe-pw:' + client_ip(req), 20, 600)
            if len(p) > 100 or not pw_ok(a['freigabe_passwort'], p):
                fehler(403, 'Falsches Passwort')
            if len(_freigabe_ok) > 10000:
                _freigabe_ok.clear()
            _freigabe_ok[schluessel] = time.time() + 3600
    return datei_antwort(a)


def aufraeumen(t=None):
    t = t or jetzt()
    c = db.verbindung()
    for a in c.execute('SELECT id FROM aufnahmen WHERE ablauf < ?', (t,)).fetchall():
        aufnahme_entfernen(a['id'])
    c.execute('UPDATE aufnahmen SET freigabe_token = NULL, freigabe_bis = NULL, freigabe_passwort = NULL WHERE freigabe_bis < ?', (t,))
