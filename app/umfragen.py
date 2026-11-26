"""Umfragen im laufenden Meeting (Header X-Teilnahme).

Host/Co-Host legen eine Umfrage an (Frage, 2–10 Antworten, Einfach- oder Mehrfachwahl, anonym oder namentlich),
starten sie, sehen das Ergebnis live, beenden sie und können das Ergebnis mit allen teilen. Jede Teilnahme (identity)
stimmt genau einmal ab – das erzwingt der Primärschlüssel der Tabelle `stimmen`.

Sichtbarkeit: Entwürfe und Live-Ergebnisse nur für Host/Co-Host. Teilnehmende sehen laufende und beendete Umfragen,
das Ergebnis (nur Zahlen, nie Namen) erst, wenn der Host es teilt. Namen sieht bei namentlichen Umfragen nur die
Moderation. Ereignisse gehen über Topic `agora` ({typ:"umfrage", aktion, umfrage}).

Umfragen gehören zu einer Sitzung und verschwinden mit ihrem Ende – außer der Raum hat „chat_speichern“; dann sieht
der Besitzer sie im Konto (GET …/umfragen/verlauf).
"""
import json
import sqlite3

from fastapi import APIRouter, Request

from . import db
from . import livekit_dienst as lk
from .kern import benutzer_aus_sitzung, fehler, grenze, iso, jetzt, json_body, text_feld
from .meeting import protokoll, steuernd, teilnahme
from .raeume import BEIM_ENDE, darf_verwalten, raum_laden, teilnahmen

router = APIRouter()
MAX_JE_MEETING = 50
MOD = ('host', 'cohost')


def _beim_ende(r):
    if not r['chat_speichern']:
        db.verbindung().execute('DELETE FROM umfragen WHERE raum_id = ?', (r['id'],))


BEIM_ENDE.append(_beim_ende)


def umfrage_json(u, mod=False, identity=None):
    """Sicht auf eine Umfrage. mod: mit Live-Ergebnis (und Namen, wenn nicht anonym). identity: eigene Auswahl dazu."""
    c = db.verbindung()
    optionen = json.loads(u['optionen'])
    stimmen = c.execute('SELECT * FROM stimmen WHERE umfrage_id = ? ORDER BY zeit', (u['id'],)).fetchall()
    d = {'id': u['id'], 'frage': u['frage'], 'optionen': optionen, 'mehrfach': bool(u['mehrfach']), 'anonym': bool(u['anonym']),
         'status': u['status'], 'geteilt': bool(u['geteilt']), 'von_name': u['von_name'], 'erstellt': iso(u['erstellt']),
         'gestartet': iso(u['gestartet']), 'beendet': iso(u['beendet']), 'ergebnis': None}
    if mod or u['geteilt']:
        zaehler = [0] * len(optionen)
        namen = [[] for _ in optionen]
        for s in stimmen:
            for i in json.loads(s['auswahl']):
                zaehler[i] += 1
                namen[i].append(s['name'])
        d['ergebnis'] = {'stimmen': len(stimmen), 'zaehler': zaehler}
        if mod and not u['anonym']:
            d['ergebnis']['namen'] = namen
    if identity is not None:
        eigene = next((s for s in stimmen if s['identity'] == identity), None)
        d['meine_auswahl'] = json.loads(eigene['auswahl']) if eigene else None
    return d


def laden(r, uid):
    u = db.verbindung().execute('SELECT * FROM umfragen WHERE id = ? AND raum_id = ? AND sitzung = ?',
                                (uid, r['id'], r['sitzung'])).fetchone()
    if not u:
        fehler(404, 'Umfrage nicht gefunden')
    return u


def neu_lesen(u):
    return db.verbindung().execute('SELECT * FROM umfragen WHERE id = ?', (u['id'],)).fetchone()


async def melden(r, u, aktion):
    """Öffentliches Ereignis an alle (ohne Live-Ergebnis) + Moderationssicht an Host/Co-Hosts."""
    await lk.senden(r['code'], {'typ': 'umfrage', 'aktion': aktion, 'umfrage': umfrage_json(u)})
    await mods_melden(r, u, aktion)


async def mods_melden(r, u, aktion):
    mods = [t['identity'] for t in teilnahmen(r, MOD)]
    await lk.senden(r['code'], {'typ': 'umfrage', 'aktion': aktion, 'umfrage': umfrage_json(u, mod=True)}, an=mods)


# ---------------------------------------------------------------- Lesen
@router.get('/api/raeume/{code}/umfragen')
def liste(code: str, req: Request):
    r, t = teilnahme(req, code)
    mod = t['rolle'] in MOD
    rows = db.verbindung().execute('SELECT * FROM umfragen WHERE raum_id = ? AND sitzung = ? ' + ('' if mod else "AND status != 'entwurf' ")
                                   + 'ORDER BY id', (r['id'], r['sitzung'])).fetchall()
    return {'umfragen': [umfrage_json(u, mod, t['identity']) for u in rows]}


@router.get('/api/raeume/{code}/umfragen/verlauf')
def verlauf(code: str, req: Request):
    """Gespeicherte Umfragen (Raum mit chat_speichern) – nur für den Besitzer, mit Ergebnis."""
    b = benutzer_aus_sitzung(req)
    r = raum_laden(code)
    if not darf_verwalten(r, b):
        fehler(403, 'Nur der Besitzer darf das.')
    rows = db.verbindung().execute("SELECT * FROM umfragen WHERE raum_id = ? AND status != 'entwurf' ORDER BY id", (r['id'],)).fetchall()
    return {'umfragen': [umfrage_json(u, mod=True) | {'sitzung': u['sitzung']} for u in rows]}


# ---------------------------------------------------------------- Anlegen, starten, beenden, teilen, löschen
@router.post('/api/raeume/{code}/umfragen')
async def anlegen(code: str, req: Request):
    r, t = steuernd(req, code)
    d = await json_body(req)
    grenze.pruefen('umfrage:' + t['identity'], 30, 600)
    frage = text_feld(d, 'frage', 300, pflicht=True).replace('\n', ' ')
    optionen = d.get('optionen')
    if not isinstance(optionen, list) or not 2 <= len(optionen) <= 10:
        fehler(400, 'Bitte 2 bis 10 Antworten angeben.')
    texte = [text_feld({'o': o}, 'o', 200).replace('\n', ' ') for o in optionen]
    if any(not x for x in texte):
        fehler(400, 'Antworten dürfen nicht leer sein.')
    if len({x.casefold() for x in texte}) != len(texte):
        fehler(400, 'Jede Antwort darf nur einmal vorkommen.')
    for k in ('mehrfach', 'anonym'):
        if k in d and not isinstance(d[k], bool):
            fehler(400, 'Ungültige Anfrage')
    c = db.verbindung()
    if c.execute('SELECT COUNT(*) FROM umfragen WHERE raum_id = ? AND sitzung = ?', (r['id'], r['sitzung'])).fetchone()[0] >= MAX_JE_MEETING:
        fehler(400, f'Höchstens {MAX_JE_MEETING} Umfragen je Meeting.')
    uid = c.execute('INSERT INTO umfragen (raum_id, sitzung, frage, optionen, mehrfach, anonym, von, von_name, erstellt) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (r['id'], r['sitzung'], frage, json.dumps(texte, ensure_ascii=False), int(d.get('mehrfach', False)),
                     int(d.get('anonym', True)), t['identity'], t['name'], jetzt())).lastrowid
    u = c.execute('SELECT * FROM umfragen WHERE id = ?', (uid,)).fetchone()
    await mods_melden(r, u, 'angelegt')
    protokoll(t, r, 'Umfrage angelegt')
    return {'umfrage': umfrage_json(u, True, t['identity'])}


@router.post('/api/raeume/{code}/umfragen/{uid}/starten')
async def starten(code: str, uid: int, req: Request):
    r, t = steuernd(req, code)
    u = laden(r, uid)
    if u['status'] != 'entwurf':
        fehler(409, 'Die Umfrage wurde schon gestartet.')
    db.verbindung().execute("UPDATE umfragen SET status = 'laeuft', gestartet = ? WHERE id = ?", (jetzt(), u['id']))
    u = neu_lesen(u)
    await melden(r, u, 'gestartet')
    protokoll(t, r, 'Umfrage gestartet')
    return {'umfrage': umfrage_json(u, True, t['identity'])}


@router.post('/api/raeume/{code}/umfragen/{uid}/beenden')
async def beenden(code: str, uid: int, req: Request):
    r, t = steuernd(req, code)
    u = laden(r, uid)
    if u['status'] != 'laeuft':
        fehler(409, 'Die Umfrage läuft nicht.')
    db.verbindung().execute("UPDATE umfragen SET status = 'beendet', beendet = ? WHERE id = ?", (jetzt(), u['id']))
    u = neu_lesen(u)
    await melden(r, u, 'beendet')
    protokoll(t, r, 'Umfrage beendet')
    return {'umfrage': umfrage_json(u, True, t['identity'])}


@router.post('/api/raeume/{code}/umfragen/{uid}/teilen')
async def teilen(code: str, uid: int, req: Request):
    """Ergebnis (Zahlen, ohne Namen) für alle sichtbar machen. Läuft die Umfrage noch, wird sie dabei beendet."""
    r, t = steuernd(req, code)
    u = laden(r, uid)
    if u['status'] == 'entwurf':
        fehler(409, 'Die Umfrage wurde noch nicht gestartet.')
    db.verbindung().execute("UPDATE umfragen SET geteilt = 1, status = 'beendet', beendet = COALESCE(beendet, ?) WHERE id = ?",
                            (jetzt(), u['id']))
    u = neu_lesen(u)
    await melden(r, u, 'geteilt')
    protokoll(t, r, 'Umfrage-Ergebnis geteilt')
    return {'umfrage': umfrage_json(u, True, t['identity'])}


@router.delete('/api/raeume/{code}/umfragen/{uid}')
async def loeschen(code: str, uid: int, req: Request):
    r, t = steuernd(req, code)
    u = laden(r, uid)
    db.verbindung().execute('DELETE FROM umfragen WHERE id = ?', (u['id'],))
    await lk.senden(r['code'], {'typ': 'umfrage', 'aktion': 'geloescht', 'umfrage': {'id': u['id']}})
    protokoll(t, r, 'Umfrage gelöscht')
    return {'ok': True}


# ---------------------------------------------------------------- Abstimmen
@router.post('/api/raeume/{code}/umfragen/{uid}/abstimmen')
async def abstimmen(code: str, uid: int, req: Request):
    r, t = teilnahme(req, code)
    d = await json_body(req)
    grenze.pruefen('abstimmen:' + t['identity'], 30, 60)
    u = laden(r, uid)
    if u['status'] != 'laeuft':
        fehler(409, 'Die Umfrage ist nicht (mehr) offen.')
    n = len(json.loads(u['optionen']))
    auswahl = d.get('auswahl')
    if (not isinstance(auswahl, list) or not auswahl or any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < n for i in auswahl)
            or len(set(auswahl)) != len(auswahl)):
        fehler(400, 'Bitte eine gültige Auswahl treffen.')
    if not u['mehrfach'] and len(auswahl) != 1:
        fehler(400, 'Bei dieser Umfrage geht nur eine Antwort.')
    try:
        db.verbindung().execute('INSERT INTO stimmen VALUES (?, ?, ?, ?, ?)',
                                (u['id'], t['identity'], t['name'], json.dumps(sorted(auswahl)), jetzt()))
    except sqlite3.IntegrityError:
        fehler(409, 'Du hast schon abgestimmt.')
    await mods_melden(r, u, 'stimme')
    return {'umfrage': umfrage_json(u, t['rolle'] in MOD, t['identity'])}
