"""Anmerkungen auf geteilten Bildschirmen: das Recht „Anmerkungen erlaubt“ je laufendem Meeting.

Die Striche selbst laufen direkt zwischen den Browsern über den LiveKit-Datenkanal (Topic `anmerkung`, zuverlässig,
im Browser begrenzt) – der Server speichert sie nicht (in E2EE-Meetings sind sie verschlüsselt). Hier liegt nur das
Recht, damit es fälschungssicher in den Raum-Metadaten steht: ändern dürfen Host, Co-Host und wer gerade den
Bildschirm teilt (laut LiveKit). Ohne das Recht zeichnen nur Host, Co-Hosts und die teilende Person selbst.
Beim Ende des Meetings gilt wieder „erlaubt“.
"""
from fastapi import APIRouter, Request

from . import db, raeume
from . import livekit_dienst as lk
from .kern import fehler, grenze, json_body
from .meeting import metadaten_aktualisieren, protokoll, teilnahme

router = APIRouter()


def _meta(r):
    return {'anmerkungen': bool(r['anmerkungen'])}


def _zuruecksetzen(r):
    db.verbindung().execute('UPDATE raeume SET anmerkungen = 1 WHERE id = ?', (r['id'],))


raeume.META_EXTRA.append(_meta)
raeume.BEIM_ENDE.append(_zuruecksetzen)


async def teilt_bildschirm(code, identity):
    liste = await lk.teilnehmer(code)
    if liste is None:
        fehler(503, 'Der Medienserver antwortet gerade nicht – bitte gleich noch einmal.')
    return any(p['identity'] == identity and any(x['quelle'] == 'bildschirm' for x in p['tracks']) for p in liste)


@router.post('/api/raeume/{code}/anmerkungen')
async def anmerkungen_setzen(code: str, req: Request):
    r, t = teilnahme(req, code)
    d = await json_body(req, 1024)
    if not isinstance(d.get('erlaubt'), bool):
        fehler(400, 'Bitte „erlaubt“ (true/false) angeben.')
    grenze.pruefen('anmerkungen:' + t['identity'], 30, 60)
    if t['rolle'] not in ('host', 'cohost') and not await teilt_bildschirm(r['code'], t['identity']):
        fehler(403, 'Nur Host, Co-Host oder wer gerade den Bildschirm teilt, darf das.')
    db.verbindung().execute('UPDATE raeume SET anmerkungen = ? WHERE id = ?', (int(d['erlaubt']), r['id']))
    await metadaten_aktualisieren(r)
    await lk.senden(r['code'], {'typ': 'anmerkungen', 'erlaubt': d['erlaubt'], 'von_name': t['name']})
    protokoll(t, r, 'Anmerkungen erlaubt' if d['erlaubt'] else 'Anmerkungen gesperrt')
    return {'ok': True, 'erlaubt': d['erlaubt']}
