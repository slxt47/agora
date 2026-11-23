"""Dauer-Chat im persönlichen Raum: öffentliche Chat-Nachrichten bleiben über das Meeting-Ende hinaus erhalten und
erscheinen beim nächsten Meeting wieder (Besitzer schaltet das in den Raum-Einstellungen ein: `dauer_chat`).

- Sichtbar nur im laufenden Meeting (GET …/chat mit X-Teilnahme) – also für alle, die gerade im Raum sind.
- Private Nachrichten gehören nie zum Dauer-Chat (sie verschwinden wie bisher mit dem Meeting).
- Der Besitzer leert ihn mit DELETE /api/raeume/{code}/dauer-chat; Ausschalten löscht ihn ebenfalls.
- Es bleiben höchstens die letzten MAX_NACHRICHTEN Nachrichten (ältere räumt der Hintergrund weg).
"""
from fastapi import APIRouter, Request

from . import db
from . import livekit_dienst as lk
from .raeume import besitzer_raum

router = APIRouter()
MAX_NACHRICHTEN = 1000


@router.delete('/api/raeume/{code}/dauer-chat')
async def leeren(code: str, req: Request):
    b, r = besitzer_raum(req, code)
    if r['art'] != 'persoenlich':
        return {'ok': True, 'geloescht': 0}           # andere Räume haben keinen Dauer-Chat
    n = db.verbindung().execute('DELETE FROM nachrichten WHERE raum_id = ? AND dauer = 1', (r['id'],)).rowcount
    if r['aktiv']:
        await lk.senden(r['code'], {'typ': 'chat_geleert'})
    db.protokollieren('Dauer-Chat geleert', b['id'], code)
    return {'ok': True, 'geloescht': n}


def aufraeumen():
    """Je Raum nur die neuesten MAX_NACHRICHTEN Dauer-Nachrichten behalten."""
    c = db.verbindung()
    for (raum_id,) in c.execute('SELECT raum_id FROM nachrichten WHERE dauer = 1 GROUP BY raum_id HAVING COUNT(*) > ?',
                                (MAX_NACHRICHTEN,)).fetchall():
        c.execute('DELETE FROM nachrichten WHERE raum_id = ? AND dauer = 1 AND id NOT IN '
                  '(SELECT id FROM nachrichten WHERE raum_id = ? AND dauer = 1 ORDER BY id DESC LIMIT ?)',
                  (raum_id, raum_id, MAX_NACHRICHTEN))
