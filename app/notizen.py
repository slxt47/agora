"""Gemeinsame Notizen je Meeting (Header X-Teilnahme): ein Text (Markdown light), den alle bearbeiten.

Verfahren: „letzter Schreiber gewinnt“ mit Versionsprüfung (optimistische Sperre, kein OT). Jeder Speicher-Aufruf
schickt die Version mit, auf der die Änderung beruht. Passt sie nicht mehr (jemand anderes hat inzwischen gespeichert),
antwortet der Server mit 409 und dem aktuellen Stand; der Client führt beide Fassungen zeilenweise zusammen, wenn sie
sich nicht überschneiden, und zeigt sonst einen Konflikt-Hinweis (eigene Fassung behalten oder die neue übernehmen).
So geht nie unbemerkt etwas verloren. Begründung: Notizen werden meist von wenigen Personen nacheinander geschrieben;
OT/CRDT wäre für diesen Fall unverhältnismäßig viel Code.

Ereignis (Topic `agora`): {typ:"notizen", version, von, von_name, text?} – text nur, wenn klein genug fürs Paket,
sonst holen die Clients den Stand per GET.
Mit dem Meeting gelöscht, außer der Raum hat „chat_speichern“ (dann im Meeting-Verlauf des Besitzers).
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import db, texte, verlauf
from . import livekit_dienst as lk
from .kern import fehler, grenze, iso, jetzt, json_body
from .meeting import teilnahme
from .raeume import BEIM_ENDE

router = APIRouter()
MAX_ZEICHEN = 100000
MAX_PAKET = 12000


def _beim_ende(r):
    if not r['chat_speichern']:
        db.verbindung().execute('DELETE FROM notizen WHERE raum_id = ?', (r['id'],))


BEIM_ENDE.append(_beim_ende)


def stand(r):
    n = db.verbindung().execute('SELECT * FROM notizen WHERE raum_id = ? AND sitzung = ?', (r['id'], r['sitzung'])).fetchone()
    if not n:
        return {'text': '', 'version': 0, 'geaendert': None, 'von_name': None}
    return {'text': n['text'], 'version': n['version'], 'geaendert': iso(n['geaendert']), 'von_name': n['von_name']}


@router.get('/api/raeume/{code}/notizen')
def lesen(code: str, req: Request):
    r, _t = teilnahme(req, code)
    return stand(r)


@router.put('/api/raeume/{code}/notizen')
async def speichern(code: str, req: Request):
    r, t = teilnahme(req, code)
    grenze.pruefen('notizen:' + t['identity'], 60, 60)
    d = await json_body(req, 4 * MAX_ZEICHEN + 1024)
    text, version = d.get('text'), d.get('version')
    if not isinstance(text, str) or not isinstance(version, int) or isinstance(version, bool) or version < 0:
        fehler(400, 'Ungültige Anfrage')
    if len(text) > MAX_ZEICHEN:
        fehler(413, f'Die Notizen sind zu lang (höchstens {MAX_ZEICHEN} Zeichen).')
    text = ''.join(ch for ch in text if ch in '\n\t' or ord(ch) >= 32).replace('\r', '')
    c = db.verbindung()
    c.execute('BEGIN IMMEDIATE')
    try:
        jetzt_stand = stand(r)
        if version != jetzt_stand['version']:
            c.execute('ROLLBACK')
            return JSONResponse({'fehler': texte.fehler_text('Inzwischen hat jemand anderes die Notizen geändert.'), 'konflikt': jetzt_stand}, status_code=409)
        neu = version + 1
        c.execute('INSERT OR REPLACE INTO notizen (raum_id, sitzung, text, version, geaendert, von_name) VALUES (?, ?, ?, ?, ?, ?)',
                  (r['id'], r['sitzung'], text, neu, jetzt(), t['name']))
        c.execute('COMMIT')
    except BaseException:
        if c.in_transaction:
            c.execute('ROLLBACK')
        raise
    paket = {'typ': 'notizen', 'version': neu, 'von': t['identity'], 'von_name': t['name'], 'text': text}
    if len(json.dumps(paket)) > MAX_PAKET:
        del paket['text']
    await lk.senden(r['code'], paket)
    return stand(r)


def verlauf_inhalt(raum_id, sitzung):
    n = db.verbindung().execute('SELECT text FROM notizen WHERE raum_id = ? AND sitzung = ?', (raum_id, sitzung)).fetchone()
    return {'notizen': n['text'] if n else ''}


verlauf.INHALT.append(verlauf_inhalt)
