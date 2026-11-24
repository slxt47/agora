"""Einbetten: Meetings auf fremden Seiten im iframe (siehe docs/EINBETTEN.md).

- Der Besitzer schaltet „Einbetten erlauben“ je Raum ein und nennt die Herkünfte (Origins), die einbetten dürfen
  (höchstens 10, https://; http:// nur für localhost/127.0.0.1/[::1] zum Entwickeln, ohne Platzhalter).
- /e/<code> liefert die Meeting-Seite (raum.html) mit `frame-ancestors <genau diese Herkünfte>` und ohne
  X-Frame-Options; alle anderen Seiten behalten `frame-ancestors 'none'` + `X-Frame-Options: DENY`. Ist Einbetten aus,
  gibt es 404.
- /embed.js (web/public, ohne Abhängigkeiten) baut das iframe mit `allow="camera; microphone; display-capture"` und
  reicht postMessage-Ereignisse (bereit, beigetreten, verlassen) mit Herkunftsprüfung weiter. Die Seite im iframe
  schickt sie nur an erlaubte Herkünfte (GET /api/einbetten/<code>).
- Im iframe gibt es keine Agora-Anmeldung (das Cookie ist SameSite=Lax und kommt im fremden Kontext nicht mit):
  alle treten als Gast bei; Passcode und Warteraum gelten wie sonst.
"""
import json
import os
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import db, raeume
from .kern import SEITE, WEB, client_ip, fehler, grenze, json_body

router = APIRouter()
MAX_HERKUENFTE = 10
LOKAL = ('localhost', '127.0.0.1', '[::1]')
HOST_RE = re.compile(r'^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$|^\[[0-9a-f:.]+\]$')
MARKE = 'x-agora-einbetten'          # interne Kopfzeile Route → Middleware (wird entfernt)


def herkunft_lesen(wert):
    """'https://Beispiel.de/' → 'https://beispiel.de'. Wirft 400 bei allem, was keine reine Herkunft ist."""
    if not isinstance(wert, str) or not 8 <= len(wert.strip()) <= 200:
        fehler(400, 'Ungültige Herkunft.')
    w = wert.strip().lower().rstrip('/')
    try:
        u = urlsplit(w)
        port = u.port
    except ValueError:
        fehler(400, f'Ungültige Herkunft: {wert[:80]}')
    netloc = u.netloc.rsplit(':', 1)[0] if port else u.netloc
    if u.path or u.query or u.fragment or '@' in u.netloc or '*' in w or not HOST_RE.match(netloc or ''):
        fehler(400, f'Nur die Herkunft angeben, z. B. https://beispiel.de (ohne Pfad, ohne *): {wert[:80]}')
    if u.scheme != 'https' and not (u.scheme == 'http' and netloc in LOKAL):
        fehler(400, f'Herkünfte müssen mit https:// beginnen (http:// nur für localhost): {wert[:80]}')
    return f'{u.scheme}://{netloc}' + (f':{port}' if port else '')


def herkuenfte(r):
    try:
        return json.loads(r['einbetten_herkunft'] or '[]')
    except ValueError:
        return []


def erlaubt(r):
    return bool(r['einbetten']) and bool(herkuenfte(r))


def _json(r, b):
    if not b or b['id'] != r['besitzer_id']:
        return {}
    return {'einbetten': {'erlaubt': erlaubt(r), 'herkuenfte': herkuenfte(r), 'link': f'{SEITE}/e/{r["code"]}'}}


raeume.RAUM_JSON_EXTRA.append(_json)


@router.put('/api/raeume/{code}/einbetten')
async def einstellen(code: str, req: Request):
    b, r = raeume.besitzer_raum(req, code)
    d = await json_body(req)
    if not isinstance(d.get('erlaubt'), bool):
        fehler(400, 'Bitte „erlaubt“ (true/false) angeben.')
    liste = d.get('herkuenfte', [])
    if not isinstance(liste, list) or len(liste) > MAX_HERKUENFTE:
        fehler(400, f'Höchstens {MAX_HERKUENFTE} Herkünfte.')
    neu = list(dict.fromkeys(herkunft_lesen(x) for x in liste))
    if d['erlaubt'] and not neu:
        fehler(400, 'Zum Einbetten mindestens eine Herkunft angeben (z. B. https://beispiel.de).')
    db.verbindung().execute('UPDATE raeume SET einbetten = ?, einbetten_herkunft = ? WHERE id = ?',
                            (int(d['erlaubt']), json.dumps(neu), r['id']))
    db.protokollieren(f'Einbetten {"an" if d["erlaubt"] else "aus"} ({len(neu)} Herkünfte)', b['id'], code)
    return {'einbetten': _json(raeume.neu_laden(r), b)['einbetten']}


@router.get('/api/einbetten/{code}')
def oeffentlich(code: str, req: Request):
    """Für die Seite im iframe: an welche Herkünfte sie postMessage schicken darf."""
    grenze.pruefen('einbetten:' + client_ip(req), 120, 60)
    r = raeume.raum_laden(code)
    if not erlaubt(r):
        fehler(404, 'Dieses Meeting darf nicht eingebettet werden.')
    return {'herkuenfte': herkuenfte(r)}


@router.api_route('/e/{code}', methods=['GET', 'HEAD'])
def seite(code: str, req: Request):
    grenze.pruefen('einbetten-seite:' + client_ip(req), 120, 60)
    r = db.verbindung().execute('SELECT * FROM raeume WHERE code = ?', (code,)).fetchone() if raeume.CODE_RE.match(code) else None
    if not r or not erlaubt(r):
        return JSONResponse({'fehler': 'Dieses Meeting darf nicht eingebettet werden.'}, status_code=404)
    datei = os.path.join(WEB, 'raum.html')
    kopf = {MARKE: ' '.join(herkuenfte(r))}
    if os.path.isfile(datei):
        return FileResponse(datei, media_type='text/html', headers=kopf)
    return HTMLResponse('<!doctype html><title>Agora</title>', headers=kopf)      # Tests ohne Frontend-Build


def kopf_anpassen(req: Request, r):
    """Middleware-Haken: /e/<code> darf genau in den erlaubten Herkünften eingebettet werden; /embed.js darf von
    fremden Seiten geladen werden (CORP cross-origin, kurz cachebar)."""
    p = req.url.path
    marke = r.headers.get(MARKE)
    if marke is not None:
        del r.headers[MARKE]
        if p.startswith('/e/') and r.status_code == 200 and marke:
            csp = r.headers.get('content-security-policy', '')
            r.headers['content-security-policy'] = csp.replace("frame-ancestors 'none'", f'frame-ancestors {marke}')
            if 'x-frame-options' in r.headers:
                del r.headers['x-frame-options']
            r.headers['cross-origin-resource-policy'] = 'cross-origin'
    elif p == '/embed.js' and r.status_code == 200:
        r.headers['cross-origin-resource-policy'] = 'cross-origin'
        r.headers['cache-control'] = 'public, max-age=3600'
