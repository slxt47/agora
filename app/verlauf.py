"""Meeting-Verlauf: je Meeting (Raum + Sitzung) Beginn, Ende, Spitzenzahl gleichzeitig Verbundener und Zahl der Beitritte.

Erfasst über Haken in raeume.py: BEIM_START legt die Zeile an, BEIM_BEITRITT zählt Beitritte, BEI_ZAEHLUNG (die minütliche
Leerlauf-Prüfung kennt die Verbundenen aller Räume des Meetings, inkl. Breakout) hebt die Spitze an, BEIM_ENDE trägt das Ende
ein. Die Spitze ist damit auf die Minute genau gemessen, mindestens 1, sobald jemand beigetreten ist.

Gespeichert werden nur Zahlen, Titel und Code – keine Namen. Einträge verschwinden nach 90 Tagen, mit dem Konto oder auf
Wunsch einzeln. Wurde der Chat gespeichert (chat_speichern), verlinkt der Eintrag auf Chat und Umfragen dieser Sitzung.
"""
from fastapi import APIRouter, Request

from . import db, konto, raeume
from .kern import benutzer_aus_sitzung, fehler, iso, jetzt

router = APIRouter()
AUFBEWAHRUNG_S = 90 * 86400
INHALT = []           # f(raum_id, sitzung) → dict: weitere gespeicherte Inhalte (Whiteboard, Notizen, Fragen, Dateien)


def _sicherstellen(r):
    db.verbindung().execute(
        'INSERT OR IGNORE INTO meeting_verlauf (raum_id, sitzung, besitzer_id, code, titel, start) VALUES (?, ?, ?, ?, ?, ?)',
        (r['id'], r['sitzung'], r['besitzer_id'], r['code'], r['titel'], r['gestartet'] or jetzt()))


def beim_start(r):
    _sicherstellen(r)


def beim_beitritt(r):
    _sicherstellen(r)
    db.verbindung().execute('UPDATE meeting_verlauf SET teilnahmen = teilnahmen + 1, spitze = MAX(spitze, 1) '
                            'WHERE raum_id = ? AND sitzung = ?', (r['id'], r['sitzung']))


def bei_zaehlung(r, anzahl):
    db.verbindung().execute('UPDATE meeting_verlauf SET spitze = MAX(spitze, ?) WHERE raum_id = ? AND sitzung = ?',
                            (anzahl, r['id'], r['sitzung']))


def beim_ende(r):
    _sicherstellen(r)
    db.verbindung().execute('UPDATE meeting_verlauf SET ende = ?, titel = ?, chat = ? WHERE raum_id = ? AND sitzung = ? AND ende IS NULL',
                            (jetzt(), r['titel'], int(bool(r['chat_speichern'])), r['id'], r['sitzung']))


raeume.BEIM_START.append(beim_start)
raeume.BEIM_BEITRITT.append(beim_beitritt)
raeume.BEI_ZAEHLUNG.append(bei_zaehlung)
raeume.BEIM_ENDE.append(beim_ende)


def aufraeumen(t=None):
    db.verbindung().execute('DELETE FROM meeting_verlauf WHERE start < ?', ((t or jetzt()) - AUFBEWAHRUNG_S,))


def eintrag_json(v):
    ende = v['ende']
    return {'id': v['id'], 'code': v['code'], 'titel': v['titel'], 'start': iso(v['start']), 'ende': iso(ende),
            'dauer_s': (ende - v['start']) if ende else None, 'spitze': v['spitze'], 'teilnahmen': v['teilnahmen'],
            'chat': bool(v['chat']) and v['raum_id'] is not None, 'sitzung': v['sitzung']}


@router.get('/api/verlauf')
def liste(req: Request):
    b = benutzer_aus_sitzung(req)
    rows = db.verbindung().execute('SELECT * FROM meeting_verlauf WHERE besitzer_id = ? AND ende IS NOT NULL ORDER BY start DESC, id DESC LIMIT 100',
                                   (b['id'],)).fetchall()
    return {'meetings': [eintrag_json(v) for v in rows]}


def laden(b, vid):
    v = db.verbindung().execute('SELECT * FROM meeting_verlauf WHERE id = ? AND besitzer_id = ?', (vid, b['id'])).fetchone()
    if not v:
        fehler(404, 'Eintrag nicht gefunden')
    return v


@router.delete('/api/verlauf/{vid}')
def loeschen(vid: int, req: Request):
    b = benutzer_aus_sitzung(req)
    laden(b, vid)
    db.verbindung().execute('DELETE FROM meeting_verlauf WHERE id = ?', (vid,))
    return {'ok': True}


@router.get('/api/verlauf/{vid}/inhalt')
def inhalt(vid: int, req: Request):
    """Gespeicherter Chat (öffentlich + eigene private Nachrichten) und Umfragen genau dieser Sitzung."""
    from . import umfragen
    b = benutzer_aus_sitzung(req)
    v = laden(b, vid)
    if not v['chat'] or v['raum_id'] is None:
        fehler(404, 'Für dieses Meeting wurde nichts gespeichert.')
    c = db.verbindung()
    n = c.execute('SELECT * FROM nachrichten WHERE raum_id = ? AND sitzung = ? AND (an IS NULL OR von_benutzer = ? OR an_benutzer = ?) ORDER BY id',
                  (v['raum_id'], v['sitzung'], b['id'], b['id'])).fetchall()
    u = c.execute('SELECT * FROM umfragen WHERE raum_id = ? AND sitzung = ? ORDER BY id', (v['raum_id'], v['sitzung'])).fetchall()
    erg = {'meeting': eintrag_json(v), 'nachrichten': [raeume.nachricht_json(x) for x in n],
           'umfragen': [umfragen.umfrage_json(x, mod=True) for x in u]}
    for f in INHALT:
        erg |= f(v['raum_id'], v['sitzung'])
    return erg


konto.EXPORT.append(lambda b: {'meeting_verlauf': [eintrag_json(v) for v in db.verbindung().execute(
    'SELECT * FROM meeting_verlauf WHERE besitzer_id = ? ORDER BY start', (b['id'],)).fetchall()]})
