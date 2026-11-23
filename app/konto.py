"""Konten: Registrieren mit Bestätigungslink, Anmelden (Sitzungs-Cookie), Passwort vergessen/ändern, Profil,
DSGVO-Export und Löschen. Mails gehen höchstens 3× pro Stunde an dieselbe Adresse."""
import json
import re
import secrets
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from . import aufnahmen, db, mail, raeume
from .kern import (TEST, SEITE, benutzer_aus_sitzung, client_ip, cookie_loeschen, fehler, grenze, h, hash_pw, iso, jetzt,
                   json_body, pw_ok, sitzung_setzen, text_feld)

router = APIRouter()
MAIL_RE = re.compile(r'^[^@\s<>"]{1,64}@[^@\s<>"]{1,190}\.[a-z]{2,24}$', re.I)
# Reservierte Namen (RFC 2606/6761): Mails dorthin kommen nie an und erzeugen nur Unzustellbar-Meldungen beim Absender
RESERVIERT_RE = re.compile(r'@(?:[^@]+\.)?(?:example\.(?:com|net|org)|[^@]+\.(?:invalid|test|localhost|example|local))$', re.I)


def zustellbar(e):
    return bool(MAIL_RE.match(e)) and (TEST or not RESERVIERT_RE.search(e))   # Tests nutzen example.org
FARBE_RE = re.compile(r'^#[0-9a-f]{6}$')
FARBEN = ['#a993ff', '#5b8def', '#2fb67c', '#f0a04b', '#e5677d', '#41b8c4', '#c873d6', '#8fb339']
BESTAETIGEN_S = 48 * 3600
PASSWORT_S = 3600


def ich_json(b):
    r = db.verbindung().execute("SELECT code FROM raeume WHERE besitzer_id = ? AND art = 'persoenlich'", (b['id'],)).fetchone()
    return {'id': b['id'], 'name': b['name'], 'email': b['email'], 'zeitzone': b['zeitzone'], 'sprache': b['sprache'],
            'farbe': b['farbe'], 'persoenlicher_raum': r['code'] if r else None}


def email_feld(d):
    e = str(d.get('email', '')).strip()
    if not zustellbar(e):
        fehler(400, 'Bitte eine gültige E-Mail-Adresse eingeben.')
    return e


def passwort_feld(d, feld):
    pw = d.get(feld)
    if not isinstance(pw, str) or len(pw) < 10:
        fehler(400, 'Das Passwort muss mindestens 10 Zeichen lang sein.')
    if len(pw) > 200:
        fehler(400, 'Das Passwort ist zu lang (höchstens 200 Zeichen).')
    return pw


def token_erstellen(benutzer_id, art, sekunden):
    t = secrets.token_urlsafe(32)
    c = db.verbindung()
    c.execute('DELETE FROM token WHERE benutzer_id = ? AND art = ?', (benutzer_id, art))
    c.execute('INSERT INTO token VALUES (?, ?, ?, ?)', (h(t), benutzer_id, art, jetzt() + sekunden))
    return t


def token_einloesen(t, art):
    if not isinstance(t, str) or not 20 <= len(t) <= 100:
        return None
    c = db.verbindung()
    row = c.execute('SELECT * FROM token WHERE token_hash = ? AND art = ? AND gueltig_bis > ?', (h(t), art, jetzt())).fetchone()
    if row:
        c.execute('DELETE FROM token WHERE token_hash = ?', (row['token_hash'],))
    return row


def mail_erlaubt(email):
    return grenze.ok('mail:' + email.lower(), 3, 3600)


def bestaetigung_schicken(b):
    if mail_erlaubt(b['email']):
        link = f'{SEITE}/api/konto/bestaetigen?t={token_erstellen(b["id"], "bestaetigen", BESTAETIGEN_S)}'
        mail.im_hintergrund(mail.bestaetigen_mail, b['email'], b['name'], link)


def reset_schicken(b, schon_registriert=False):
    if mail_erlaubt(b['email']):
        link = f'{SEITE}/passwort?t={token_erstellen(b["id"], "passwort", PASSWORT_S)}'
        f = mail.schon_registriert_mail if schon_registriert else mail.passwort_mail
        mail.im_hintergrund(f, b['email'], b['name'], link)


@router.post('/api/konto/registrieren')
async def registrieren(req: Request):
    d = await json_body(req)
    grenze.pruefen('reg:' + client_ip(req), 5, 3600)
    name = text_feld(d, 'name', 60)
    if not name:
        fehler(400, 'Bitte einen Namen eingeben.')
    email = email_feld(d)
    pw = passwort_feld(d, 'passwort')
    pw_hash = await run_in_threadpool(hash_pw, pw)
    c = db.verbindung()
    alt = c.execute('SELECT * FROM benutzer WHERE email = ?', (email,)).fetchone()
    if alt and alt['bestaetigt']:
        # Kein Hinweis in der Antwort, ob es die Adresse schon gibt – stattdessen eine Mail an die Adresse
        reset_schicken(alt, schon_registriert=True)
        return {'ok': True}
    if alt:      # unbestätigtes Konto: neu belegen
        c.execute('UPDATE benutzer SET name = ?, passwort_hash = ? WHERE id = ?', (name, pw_hash, alt['id']))
        bid = alt['id']
    else:
        bid = c.execute('INSERT INTO benutzer (name, email, passwort_hash, farbe, erstellt) VALUES (?, ?, ?, ?, ?)',
                        (name, email, pw_hash, secrets.choice(FARBEN), jetzt())).lastrowid
        raeume.persoenlichen_raum_anlegen(bid, name)
    db.protokollieren('registriert (unbestätigt)', bid)
    bestaetigung_schicken(c.execute('SELECT * FROM benutzer WHERE id = ?', (bid,)).fetchone())
    return {'ok': True}


@router.get('/api/konto/bestaetigen')
def bestaetigen(req: Request, t: str = ''):
    if not grenze.ok('best:' + client_ip(req), 30, 600):
        return RedirectResponse('/?bestaetigt=0', status_code=303)
    row = token_einloesen(t, 'bestaetigen')
    if not row:
        return RedirectResponse('/?bestaetigt=0', status_code=303)
    db.verbindung().execute('UPDATE benutzer SET bestaetigt = 1 WHERE id = ?', (row['benutzer_id'],))
    db.protokollieren('E-Mail bestätigt', row['benutzer_id'])
    resp = RedirectResponse('/?bestaetigt=1', status_code=303)
    sitzung_setzen(resp, row['benutzer_id'])      # gleich angemeldet
    return resp


@router.post('/api/konto/bestaetigung-erneut')
async def bestaetigung_erneut(req: Request):
    d = await json_body(req)
    grenze.pruefen('erneut:' + client_ip(req), 10, 3600)
    e = str(d.get('email', '')).strip()
    b = db.verbindung().execute('SELECT * FROM benutzer WHERE email = ?', (e,)).fetchone() if MAIL_RE.match(e) else None
    if b and not b['bestaetigt']:
        bestaetigung_schicken(b)
    return {'ok': True}


@router.post('/api/konto/anmelden')
async def anmelden(req: Request):
    d = await json_body(req)
    ip = client_ip(req)
    e, pw = str(d.get('email', '')).strip().lower(), d.get('passwort')
    grenze.pruefen('login:' + ip, 50, 600)
    grenze.pruefen(f'login:{ip}:{e}', 10, 600)
    if not MAIL_RE.match(e) or not isinstance(pw, str) or len(pw) > 200:
        fehler(400, 'E-Mail oder Passwort falsch.')
    c = db.verbindung()
    b = c.execute('SELECT * FROM benutzer WHERE email = ?', (e,)).fetchone()
    if not await run_in_threadpool(pw_ok, b['passwort_hash'] if b else None, pw) or not b:
        db.protokollieren('Anmeldung: falsches Passwort', b['id'] if b else None)
        fehler(400, 'E-Mail oder Passwort falsch.')
    if not b['bestaetigt']:
        fehler(403, 'E-Mail-Adresse noch nicht bestätigt – bitte den Link in der Mail öffnen.')
    c.execute('UPDATE benutzer SET letzte_anmeldung = ? WHERE id = ?', (jetzt(), b['id']))
    db.protokollieren('angemeldet', b['id'])
    resp = JSONResponse({'ok': True, 'ich': ich_json(b)})
    sitzung_setzen(resp, b['id'])
    return resp


@router.post('/api/konto/abmelden')
async def abmelden(req: Request):
    from .kern import COOKIE
    t = req.cookies.get(COOKIE)
    if t:
        db.verbindung().execute('DELETE FROM sitzungen WHERE token_hash = ?', (h(t),))
    resp = JSONResponse({'ok': True})
    cookie_loeschen(resp)
    return resp


@router.get('/api/konto/ich')
def ich(req: Request):
    b = benutzer_aus_sitzung(req, pflicht=False)
    return {'angemeldet': True, 'ich': ich_json(b)} if b else {'angemeldet': False}


@router.patch('/api/konto/ich')
async def ich_aendern(req: Request):
    b = benutzer_aus_sitzung(req)
    d = await json_body(req)
    felder = {}
    if 'name' in d:
        felder['name'] = text_feld(d, 'name', 60)
        if not felder['name']:
            fehler(400, 'Bitte einen Namen eingeben.')
    if 'zeitzone' in d:
        z = d['zeitzone']
        try:
            if not isinstance(z, str) or len(z) > 64:
                raise ValueError
            ZoneInfo(z)
        except Exception:   # noqa: BLE001
            fehler(400, 'Unbekannte Zeitzone.')
        felder['zeitzone'] = z
    if 'sprache' in d:
        if d['sprache'] not in ('de', 'en'):
            fehler(400, 'Sprache: de oder en.')
        felder['sprache'] = d['sprache']
    if 'farbe' in d:
        if not isinstance(d['farbe'], str) or not FARBE_RE.match(d['farbe'].lower()):
            fehler(400, 'Farbe bitte als #rrggbb.')
        felder['farbe'] = d['farbe'].lower()
    c = db.verbindung()
    for k, v in felder.items():
        c.execute(f'UPDATE benutzer SET {k} = ? WHERE id = ?', (v, b['id']))
    return {'ich': ich_json(c.execute('SELECT * FROM benutzer WHERE id = ?', (b['id'],)).fetchone())}


@router.post('/api/konto/passwort-vergessen')
async def passwort_vergessen(req: Request):
    d = await json_body(req)
    grenze.pruefen('vergessen:' + client_ip(req), 10, 3600)
    e = str(d.get('email', '')).strip()
    b = db.verbindung().execute('SELECT * FROM benutzer WHERE email = ?', (e,)).fetchone() if MAIL_RE.match(e) else None
    if b:
        reset_schicken(b)
    return {'ok': True}       # gleiche Antwort, ob es das Konto gibt oder nicht


@router.post('/api/konto/passwort-setzen')
async def passwort_setzen(req: Request):
    d = await json_body(req)
    grenze.pruefen('setzen:' + client_ip(req), 20, 600)
    pw = passwort_feld(d, 'passwort')
    row = token_einloesen(d.get('token'), 'passwort')
    if not row:
        fehler(400, 'Der Link ist ungültig oder abgelaufen – bitte neu anfordern.')
    pw_hash = await run_in_threadpool(hash_pw, pw)
    c = db.verbindung()
    c.execute('UPDATE benutzer SET passwort_hash = ?, bestaetigt = 1 WHERE id = ?', (pw_hash, row['benutzer_id']))
    c.execute('DELETE FROM sitzungen WHERE benutzer_id = ?', (row['benutzer_id'],))
    db.protokollieren('Passwort zurückgesetzt', row['benutzer_id'])
    return {'ok': True}


@router.post('/api/konto/passwort-aendern')
async def passwort_aendern(req: Request):
    b = benutzer_aus_sitzung(req)
    d = await json_body(req)
    grenze.pruefen(f'aendern:{b["id"]}', 5, 900)
    if not isinstance(d.get('alt'), str) or not await run_in_threadpool(pw_ok, b['passwort_hash'], d['alt']):
        fehler(400, 'Altes Passwort falsch.')
    pw_hash = await run_in_threadpool(hash_pw, passwort_feld(d, 'neu'))
    c = db.verbindung()
    c.execute('UPDATE benutzer SET passwort_hash = ? WHERE id = ?', (pw_hash, b['id']))
    c.execute('DELETE FROM sitzungen WHERE benutzer_id = ?', (b['id'],))     # andere Geräte abmelden
    db.protokollieren('Passwort geändert', b['id'])
    resp = JSONResponse({'ok': True})
    sitzung_setzen(resp, b['id'])
    return resp


@router.get('/api/konto/export')
def export(req: Request):
    """DSGVO: alle eigenen Daten als JSON (Aufnahmen nur als Liste – die Dateien lädt man einzeln)."""
    b = benutzer_aus_sitzung(req)
    c = db.verbindung()
    raum_liste = []
    for r in c.execute('SELECT * FROM raeume WHERE besitzer_id = ?', (b['id'],)).fetchall():
        j = raeume.raum_json(r, b)
        j['chat_verlauf'] = [raeume.nachricht_json(n) | {'sitzung': n['sitzung']} for n in c.execute(
            'SELECT * FROM nachrichten WHERE raum_id = ? AND (an IS NULL OR von_benutzer = ? OR an_benutzer = ?) ORDER BY id',
            (r['id'], b['id'], b['id'])).fetchall()]
        raum_liste.append(j)
    daten = {
        'erstellt': iso(jetzt()),
        'konto': ich_json(b) | {'registriert': iso(b['erstellt']), 'letzte_anmeldung': iso(b['letzte_anmeldung'])},
        'raeume': raum_liste,
        'aufnahmen': [aufnahmen.aufnahme_json(a) for a in c.execute('SELECT * FROM aufnahmen WHERE benutzer_id = ?', (b['id'],)).fetchall()],
        'protokoll': [{'zeit': iso(p['zeit']), 'raum': p['raum_code'], 'ereignis': p['ereignis']} for p in c.execute(
            'SELECT * FROM protokoll WHERE benutzer_id = ? ORDER BY zeit', (b['id'],)).fetchall()],
    }
    return Response(json.dumps(daten, ensure_ascii=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': 'attachment; filename="agora-export.json"'})


@router.delete('/api/konto')
async def konto_loeschen(req: Request):
    b = benutzer_aus_sitzung(req)
    d = await json_body(req)
    grenze.pruefen(f'loeschen:{b["id"]}', 5, 900)
    if not isinstance(d.get('passwort'), str) or not await run_in_threadpool(pw_ok, b['passwort_hash'], d['passwort']):
        fehler(400, 'Passwort falsch.')
    c = db.verbindung()
    for r in c.execute('SELECT * FROM raeume WHERE besitzer_id = ? AND aktiv = 1', (b['id'],)).fetchall():
        await raeume.meeting_beenden(r)
    for a in c.execute('SELECT id FROM aufnahmen WHERE benutzer_id = ?', (b['id'],)).fetchall():
        aufnahmen.aufnahme_entfernen(a['id'])
    c.execute('DELETE FROM benutzer WHERE id = ?', (b['id'],))       # Räume, Sitzungen, Token per CASCADE
    c.execute('DELETE FROM protokoll WHERE benutzer_id = ?', (b['id'],))
    db.protokollieren(f'Konto gelöscht (#{b["id"]})')
    resp = JSONResponse({'ok': True})
    cookie_loeschen(resp)
    return resp
