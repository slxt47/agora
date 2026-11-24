"""Q&A im laufenden Meeting (Header X-Teilnahme): Fragen stellen (auf Wunsch anonym), hochstimmen, moderieren.

Anonym heißt: Andere Teilnehmende – auch Host und Co-Hosts – sehen keinen Namen. Der Server speichert die Teilnahme
(identity, Name, Konto) trotzdem, damit Missbrauch nachvollziehbar bleibt (Protokoll ohne Inhalt, Meldungen).

Status: offen → beantwortet (optional mit schriftlicher Antwort) oder verworfen (nur noch für Moderation und
fragende Person sichtbar); jederzeit wieder offen. Hervorheben markiert eine Frage für alle. Eine Stimme je Teilnahme
und Frage (Primärschlüssel), nicht für die eigene Frage.

Ereignisse (Topic `agora`): {typ:"frage", aktion: neu|aktualisiert|geloescht, frage} – `frage` ohne persönliche Felder
(meins/ich_gestimmt rechnet der Client selbst nach). Verworfene Fragen gehen nur an Moderation und fragende Person,
alle anderen bekommen „geloescht“.
Mit dem Meeting gelöscht, außer der Raum hat „chat_speichern“ (dann im Meeting-Verlauf des Besitzers, anonyme ohne Namen).
"""
import sqlite3

from fastapi import APIRouter, Request

from . import db, verlauf
from . import livekit_dienst as lk
from .kern import fehler, grenze, iso, jetzt, json_body, text_feld
from .meeting import protokoll, steuernd, teilnahme
from .raeume import BEIM_ENDE, teilnahmen

router = APIRouter()
MOD = ('host', 'cohost')
MAX_JE_MEETING = 300
AKTIONEN = ('beantwortet', 'offen', 'verworfen', 'hervorheben', 'nicht_hervorheben')


def _beim_ende(r):
    if not r['chat_speichern']:
        db.verbindung().execute('DELETE FROM fragen WHERE raum_id = ?', (r['id'],))


BEIM_ENDE.append(_beim_ende)


def frage_json(f, identity=None):
    """Sicht auf eine Frage; identity → mit meins/ich_gestimmt für diese Teilnahme."""
    c = db.verbindung()
    stimmen = c.execute('SELECT identity FROM fragen_stimmen WHERE frage_id = ?', (f['id'],)).fetchall()
    d = {'id': f['id'], 'text': f['text'], 'anonym': bool(f['anonym']), 'name': None if f['anonym'] else f['name'],
         'status': f['status'], 'hervorgehoben': bool(f['hervorgehoben']), 'stimmen': len(stimmen),
         'antwort': f['antwort'], 'antwort_von': f['antwort_von'], 'erstellt': iso(f['erstellt']), 'beantwortet': iso(f['beantwortet'])}
    if identity is not None:
        d['meins'] = f['identity'] == identity
        d['ich_gestimmt'] = any(s['identity'] == identity for s in stimmen)
    return d


def laden(r, fid):
    f = db.verbindung().execute('SELECT * FROM fragen WHERE id = ? AND raum_id = ? AND sitzung = ?', (fid, r['id'], r['sitzung'])).fetchone()
    if not f:
        fehler(404, 'Frage nicht gefunden')
    return f


def neu_lesen(f):
    return db.verbindung().execute('SELECT * FROM fragen WHERE id = ?', (f['id'],)).fetchone()


async def melden(r, f, aktion):
    sicht = frage_json(f)
    if f['status'] != 'verworfen':
        await lk.senden(r['code'], {'typ': 'frage', 'aktion': aktion, 'frage': sicht})
        return
    alle = teilnahmen(r)
    innen = [t['identity'] for t in alle if t['rolle'] in MOD or t['identity'] == f['identity']]
    aussen = [t['identity'] for t in alle if t['identity'] not in innen]
    await lk.senden(r['code'], {'typ': 'frage', 'aktion': aktion, 'frage': sicht}, an=innen)
    await lk.senden(r['code'], {'typ': 'frage', 'aktion': 'geloescht', 'frage': {'id': f['id']}}, an=aussen)


@router.get('/api/raeume/{code}/fragen')
def liste(code: str, req: Request):
    r, t = teilnahme(req, code)
    rows = db.verbindung().execute(
        "SELECT * FROM fragen WHERE raum_id = ? AND sitzung = ? AND (status != 'verworfen' OR ? OR identity = ?) ORDER BY id",
        (r['id'], r['sitzung'], int(t['rolle'] in MOD), t['identity'])).fetchall()
    return {'fragen': [frage_json(f, t['identity']) for f in rows]}


@router.post('/api/raeume/{code}/fragen')
async def stellen(code: str, req: Request):
    r, t = teilnahme(req, code)
    d = await json_body(req)
    grenze.pruefen('frage:' + t['identity'], 10, 60)
    if r['webinar'] and t['rolle'] == 'zuschauer':      # Webinar: Zuschauer dürfen nur fragen, wenn das Panel es erlaubt
        from .webinar import einstellungen
        if not einstellungen(r)['fragen_an']:
            fehler(403, 'In diesem Webinar sind Fragen ausgeschaltet.')
    text = text_feld(d, 'text', 500, pflicht=True)
    if 'anonym' in d and not isinstance(d['anonym'], bool):
        fehler(400, 'Ungültige Anfrage')
    c = db.verbindung()
    if c.execute('SELECT COUNT(*) FROM fragen WHERE raum_id = ? AND sitzung = ?', (r['id'], r['sitzung'])).fetchone()[0] >= MAX_JE_MEETING:
        fehler(400, f'Höchstens {MAX_JE_MEETING} Fragen je Meeting.')
    fid = c.execute('INSERT INTO fragen (raum_id, sitzung, identity, name, benutzer_id, anonym, text, erstellt) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (r['id'], r['sitzung'], t['identity'], t['name'], t['benutzer_id'], int(d.get('anonym', False)), text, jetzt())).lastrowid
    f = c.execute('SELECT * FROM fragen WHERE id = ?', (fid,)).fetchone()
    await melden(r, f, 'neu')
    protokoll(t, r, 'Q&A: Frage gestellt' + (' (anonym)' if f['anonym'] else ''))
    return {'frage': frage_json(f, t['identity'])}


@router.post('/api/raeume/{code}/fragen/{fid}/stimme')
async def stimme(code: str, fid: int, req: Request):
    r, t = teilnahme(req, code)
    d = await json_body(req)
    grenze.pruefen('frage-stimme:' + t['identity'], 60, 60)
    if not isinstance(d.get('an'), bool):
        fehler(400, 'Bitte „an“ (true/false) angeben.')
    f = laden(r, fid)
    if f['identity'] == t['identity']:
        fehler(400, 'Für die eigene Frage kannst du nicht stimmen.')
    if f['status'] == 'verworfen':
        fehler(409, 'Diese Frage wurde verworfen.')
    c = db.verbindung()
    if d['an']:
        try:
            c.execute('INSERT INTO fragen_stimmen VALUES (?, ?)', (f['id'], t['identity']))
        except sqlite3.IntegrityError:
            pass
    else:
        c.execute('DELETE FROM fragen_stimmen WHERE frage_id = ? AND identity = ?', (f['id'], t['identity']))
    await melden(r, f, 'aktualisiert')
    return {'frage': frage_json(f, t['identity'])}


@router.post('/api/raeume/{code}/fragen/{fid}/moderieren')
async def moderieren(code: str, fid: int, req: Request):
    r, t = steuernd(req, code)
    d = await json_body(req)
    aktion = d.get('aktion')
    if aktion not in AKTIONEN:
        fehler(400, 'Aktion: ' + ', '.join(AKTIONEN) + '.')
    f = laden(r, fid)
    c = db.verbindung()
    if aktion in ('hervorheben', 'nicht_hervorheben'):
        c.execute('UPDATE fragen SET hervorgehoben = ? WHERE id = ?', (int(aktion == 'hervorheben'), f['id']))
    elif aktion == 'beantwortet':
        antwort = text_feld(d, 'antwort', 1000) or None
        c.execute("UPDATE fragen SET status = 'beantwortet', hervorgehoben = 0, antwort = ?, antwort_von = ?, beantwortet = ? WHERE id = ?",
                  (antwort, t['name'] if antwort else None, jetzt(), f['id']))
    else:
        c.execute('UPDATE fragen SET status = ?, hervorgehoben = CASE WHEN ? THEN 0 ELSE hervorgehoben END WHERE id = ?',
                  (aktion, int(aktion == 'verworfen'), f['id']))
    war_verworfen = f['status'] == 'verworfen'
    f = neu_lesen(f)
    await melden(r, f, 'neu' if war_verworfen and f['status'] != 'verworfen' else 'aktualisiert')
    protokoll(t, r, f'Q&A: {aktion}')
    return {'frage': frage_json(f, t['identity'])}


@router.delete('/api/raeume/{code}/fragen/{fid}')
async def loeschen(code: str, fid: int, req: Request):
    """Eigene Frage zurückziehen (solange offen) oder als Moderation löschen."""
    r, t = teilnahme(req, code)
    f = laden(r, fid)
    if t['rolle'] not in MOD and (f['identity'] != t['identity'] or f['status'] != 'offen'):
        fehler(403, 'Das darfst du bei dieser Frage nicht.')
    db.verbindung().execute('DELETE FROM fragen WHERE id = ?', (f['id'],))
    await lk.senden(r['code'], {'typ': 'frage', 'aktion': 'geloescht', 'frage': {'id': f['id']}})
    return {'ok': True}


def verlauf_inhalt(raum_id, sitzung):
    rows = db.verbindung().execute("SELECT * FROM fragen WHERE raum_id = ? AND sitzung = ? AND status != 'verworfen' ORDER BY id",
                                   (raum_id, sitzung)).fetchall()
    return {'fragen': [frage_json(f) for f in rows]}


verlauf.INHALT.append(verlauf_inhalt)
