"""Öffentliche API /api/v1 (Bearer-API-Schlüssel, siehe app/api_schluessel.py, docs/API-V1.md, docs/openapi.yaml).

Meetings (auch geplant/wiederkehrend) auflisten, anlegen, ändern, löschen; Beitrittslink; Teilnehmende eines laufenden
Meetings; Aufnahmen auflisten und über kurzlebige, signierte Links herunterladen. Keine Cookies, kein CSRF-Header,
kein CORS. Antworten wie die übrige API: JSON, Fehler `{"fehler": "…"}`.
"""
import hashlib
import hmac
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import aufnahmen, db, raeume
from . import livekit_dienst as lk
from .api_schluessel import pruefen
from .kern import GEHEIM, SEITE, client_ip, fehler, grenze, iso, jetzt, json_body

router = APIRouter()
FELDER = {'titel', 'passcode', 'warteraum', 'chat_speichern', 'geplant'}
DOWNLOAD_S = 900              # signierte Download-Links gelten 15 min


def meeting_json(r):
    j = raeume.raum_json(r)
    return {'code': r['code'], 'titel': r['titel'], 'art': r['art'], 'link': j['link'], 'aktiv': bool(r['aktiv']),
            'beendet': bool(r['beendet']), 'passcode_noetig': j['passcode_noetig'], 'warteraum': j['warteraum'],
            'chat_speichern': j['chat_speichern'], 'e2ee': bool(r['e2ee']), 'geplant': j['geplant'],
            'naechster_termin': j['naechster_termin'], 'erstellt': iso(r['erstellt'])}


def eigenes_meeting(b, code):
    r = raeume.raum_laden(code)
    if r['besitzer_id'] != b['id']:
        fehler(404, 'Dieses Meeting gibt es nicht.')        # fremde Räume sehen wie nicht vorhanden aus
    return r


async def felder(req, b):
    d = await json_body(req)
    fremd = sorted(set(d) - FELDER)
    if fremd:
        fehler(400, f'Unbekanntes Feld: {fremd[0]}')
    return await raeume.einstellungen_lesen(d, b, True)


# ---------------------------------------------------------------- Meetings
@router.get('/api/v1/meetings')
def meetings(req: Request, art: str = ''):
    b, _k = pruefen(req, 'meetings:lesen')
    if art and art not in ('sofort', 'geplant', 'persoenlich'):
        fehler(400, 'art: sofort, geplant oder persoenlich.')
    q, p = 'SELECT * FROM raeume WHERE besitzer_id = ?', [b['id']]
    if art:
        q, p = q + ' AND art = ?', p + [art]
    rows = db.verbindung().execute(q + ' ORDER BY erstellt DESC, id DESC LIMIT 500', p).fetchall()
    return {'meetings': [meeting_json(r) for r in rows]}


@router.post('/api/v1/meetings', status_code=201)
async def meeting_neu(req: Request):
    b, _k = pruefen(req, 'meetings:schreiben')
    f = await felder(req, b)
    grenze.pruefen(f'raum:{b["id"]}', 60, 3600)       # gleiche Grenze wie im Konto
    art = 'geplant' if 'start' in f else 'sofort'
    titel = f.pop('titel', '') or (f'Meeting von {b["name"]}' if art == 'sofort' else 'Geplantes Meeting')
    r = raeume.raum_anlegen(b['id'], art, titel[:120], f)
    db.protokollieren(f'Raum angelegt ({art}, API)', b['id'], r['code'])
    return {'meeting': meeting_json(r)}


@router.get('/api/v1/meetings/{code}')
def meeting_info(code: str, req: Request):
    b, _k = pruefen(req, 'meetings:lesen')
    return {'meeting': meeting_json(eigenes_meeting(b, code))}


@router.patch('/api/v1/meetings/{code}')
async def meeting_aendern(code: str, req: Request):
    b, _k = pruefen(req, 'meetings:schreiben')
    r = eigenes_meeting(b, code)
    f = await felder(req, b)
    if 'start' in f and r['art'] != 'geplant':
        fehler(400, 'Nur geplante Meetings haben einen Termin.')
    if 'titel' in f and not f['titel']:
        del f['titel']
    c = db.verbindung()
    for k, v in f.items():
        c.execute(f'UPDATE raeume SET {k} = ? WHERE id = ?', (v, r['id']))
    return {'meeting': meeting_json(raeume.neu_laden(r))}


@router.delete('/api/v1/meetings/{code}')
async def meeting_loeschen(code: str, req: Request):
    b, _k = pruefen(req, 'meetings:schreiben')
    r = eigenes_meeting(b, code)
    if r['art'] == 'persoenlich':
        fehler(400, 'Der persönliche Raum kann nicht gelöscht werden.')
    await raeume.meeting_beenden(r, 'gelöscht (API)')
    db.verbindung().execute('DELETE FROM raeume WHERE id = ?', (r['id'],))
    db.protokollieren('Raum gelöscht (API)', b['id'], code)
    return {'ok': True}


@router.get('/api/v1/meetings/{code}/link')
def beitrittslink(code: str, req: Request):
    b, _k = pruefen(req, 'meetings:lesen')
    r = eigenes_meeting(b, code)
    return {'code': r['code'], 'link': raeume.link(r['code']), 'passcode_noetig': bool(r['passcode_hash']), 'e2ee': bool(r['e2ee']),
            'naechster_termin': iso(raeume.termin(r))}


@router.get('/api/v1/meetings/{code}/teilnehmer')
async def teilnehmer(code: str, req: Request):
    b, _k = pruefen(req, 'meetings:lesen')
    r = eigenes_meeting(b, code)
    if not r['aktiv']:
        return {'aktiv': False, 'teilnehmer': []}
    liste = await lk.teilnehmer(r['code'])
    if liste is None:
        fehler(503, 'Der Medienserver antwortet gerade nicht.')
    zugaenge = {t['identity']: t for t in raeume.teilnahmen(r)}
    aus = []
    for p in liste:
        t = zugaenge.get(p['identity'])
        aus.append({'identity': p['identity'], 'name': t['name'] if t else p['name'], 'rolle': t['rolle'] if t else None,
                    'gast': not (t and t['benutzer_id']), 'beigetreten': iso(t['erstellt']) if t else None,
                    'breakout': p.get('raum') != r['code']})
    return {'aktiv': True, 'teilnehmer': aus}


# ---------------------------------------------------------------- Aufnahmen
def aufnahme_json(a):
    return {'id': a['id'], 'titel': a['titel'], 'raum_code': a['raum_code'], 'groesse': a['groesse'], 'typ': a['typ'],
            'dauer': a['dauer'], 'erstellt': iso(a['erstellt']), 'ablauf': iso(a['ablauf'])}


def eigene_aufnahme(b, i):
    a = db.verbindung().execute('SELECT * FROM aufnahmen WHERE id = ? AND benutzer_id = ?', (i, b['id'])).fetchone() \
        if aufnahmen.ID_RE.match(i) else None
    if not a:
        fehler(404, 'Aufnahme nicht gefunden')
    return a


def signatur(i, benutzer_id, bis):
    return hmac.new(GEHEIM, f'v1-download\0{i}\0{benutzer_id}\0{bis}'.encode(), hashlib.sha256).hexdigest()


@router.get('/api/v1/aufnahmen')
def aufnahmen_liste(req: Request):
    b, _k = pruefen(req, 'aufnahmen:lesen')
    rows = db.verbindung().execute('SELECT * FROM aufnahmen WHERE benutzer_id = ? ORDER BY erstellt DESC', (b['id'],)).fetchall()
    return {'aufnahmen': [aufnahme_json(a) for a in rows]}


@router.get('/api/v1/aufnahmen/{aufnahme_id}')
def aufnahme_info(aufnahme_id: str, req: Request):
    b, _k = pruefen(req, 'aufnahmen:lesen')
    return {'aufnahme': aufnahme_json(eigene_aufnahme(b, aufnahme_id))}


@router.get('/api/v1/aufnahmen/{aufnahme_id}/download')
def download_link(aufnahme_id: str, req: Request):
    """Kurzlebiger, signierter Link (15 min) – zum Weitergeben an einen Download-Dienst ohne den API-Schlüssel."""
    b, _k = pruefen(req, 'aufnahmen:lesen')
    a = eigene_aufnahme(b, aufnahme_id)
    bis = jetzt() + DOWNLOAD_S
    return {'url': f'{SEITE}/api/v1/download/{a["id"]}?bis={bis}&sig={signatur(a["id"], b["id"], bis)}', 'gueltig_bis': iso(bis)}


@router.get('/api/v1/download/{aufnahme_id}')
def download(aufnahme_id: str, req: Request, bis: int = 0, sig: str = ''):
    grenze.pruefen('v1-download:' + client_ip(req), 120, 600)
    a = db.verbindung().execute('SELECT * FROM aufnahmen WHERE id = ?', (aufnahme_id,)).fetchone() if aufnahmen.ID_RE.match(aufnahme_id) else None
    if not a or bis < jetzt() or bis > jetzt() + DOWNLOAD_S or len(sig) != 64 \
            or not hmac.compare_digest(sig, signatur(a['id'], a['benutzer_id'], bis)) or not os.path.isfile(aufnahmen.pfad(a['id'])):
        return JSONResponse({'fehler': 'Der Download-Link ist ungültig oder abgelaufen.'}, status_code=403)
    return aufnahmen.datei_antwort(a, herunterladen=True)
