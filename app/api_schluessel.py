"""Persönliche API-Schlüssel für die öffentliche API (/api/v1, siehe docs/API-V1.md).

- Anlegen, Auflisten und Widerrufen im Konto (Cookie + X-Agora wie jede Konto-Aktion). Der Schlüssel wird genau einmal
  in der Antwort gezeigt; gespeichert werden nur sein SHA-256, ein kurzer Anfang zum Wiedererkennen, die Rechte und
  die letzte Nutzung (Zeit, keine IP).
- `pruefen(req, recht)` liest `Authorization: Bearer agora_…` und gibt (benutzer, schluessel) zurück. Cookies spielen
  dabei keine Rolle – deshalb braucht /api/v1 keinen CSRF-Header (eine fremde Seite kann den Bearer-Kopf nicht setzen,
  ohne den Schlüssel zu kennen, und CORS ist aus).
- Grenzen: 120 Anfragen/min je Schlüssel, 30 falsche Schlüssel/10 min je IP, höchstens 10 Schlüssel je Konto.
"""
import json
import secrets

from fastapi import APIRouter, HTTPException, Request

from . import db, konto
from .kern import benutzer_aus_sitzung, client_ip, fehler, grenze, h, iso, jetzt, json_body, text_feld

router = APIRouter()
RECHTE = ('meetings:lesen', 'meetings:schreiben', 'aufnahmen:lesen', 'webhooks')
PRAEFIX = 'agora_'
MAX_JE_KONTO = 10
TAGE = (None, 30, 90, 365)
PRO_MINUTE = 120


def schluessel_json(k):
    return {'id': k['id'], 'name': k['name'], 'anfang': k['anfang'], 'rechte': json.loads(k['rechte']),
            'erstellt': iso(k['erstellt']), 'gueltig_bis': iso(k['gueltig_bis']), 'benutzt': iso(k['benutzt'])}


def _abgelehnt(code, text, **kopf):
    raise HTTPException(code, text, headers=kopf or None)


def pruefen(req: Request, recht):
    """Bearer-Schlüssel prüfen → (benutzer, schluessel). 401 ohne/mit falschem Schlüssel, 403 ohne das Recht."""
    ip = client_ip(req)
    kopf = req.headers.get('authorization', '')
    if not kopf[:7].lower() == 'bearer ':
        _abgelehnt(401, 'API-Schlüssel fehlt (Authorization: Bearer agora_…).', **{'WWW-Authenticate': 'Bearer'})
    token = kopf[7:].strip()
    c = db.verbindung()
    k = None
    if token.startswith(PRAEFIX) and 20 <= len(token) <= 100:
        k = c.execute('SELECT * FROM api_schluessel WHERE schluessel_hash = ?', (h(token),)).fetchone()
    b = c.execute('SELECT * FROM benutzer WHERE id = ?', (k['benutzer_id'],)).fetchone() if k else None
    if not k or not b or not b['bestaetigt'] or b['gesperrt'] or (k['gueltig_bis'] and k['gueltig_bis'] < jetzt()):
        if not grenze.ok('api-falsch:' + ip, 30, 600):
            _abgelehnt(429, 'Zu viele Anfragen mit ungültigem Schlüssel – bitte später wieder.', **{'Retry-After': '600'})
        _abgelehnt(401, 'API-Schlüssel ungültig, abgelaufen oder widerrufen.', **{'WWW-Authenticate': 'Bearer error="invalid_token"'})
    if not grenze.ok(f'api:{k["id"]}', PRO_MINUTE, 60):
        _abgelehnt(429, f'Zu viele Anfragen – höchstens {PRO_MINUTE} pro Minute je Schlüssel.', **{'Retry-After': '60'})
    if recht not in json.loads(k['rechte']):
        fehler(403, f'Diesem API-Schlüssel fehlt das Recht „{recht}“.')
    if not k['benutzt'] or k['benutzt'] < jetzt() - 60:          # höchstens minütlich schreiben
        c.execute('UPDATE api_schluessel SET benutzt = ? WHERE id = ?', (jetzt(), k['id']))
    return b, k


# ---------------------------------------------------------------- Verwaltung im Konto (Cookie)
@router.get('/api/konto/api-schluessel')
def liste(req: Request):
    b = benutzer_aus_sitzung(req)
    rows = db.verbindung().execute('SELECT * FROM api_schluessel WHERE benutzer_id = ? ORDER BY erstellt DESC, id DESC', (b['id'],)).fetchall()
    return {'schluessel': [schluessel_json(k) for k in rows], 'rechte': list(RECHTE), 'maximal': MAX_JE_KONTO}


@router.post('/api/konto/api-schluessel')
async def anlegen(req: Request):
    b = benutzer_aus_sitzung(req)
    d = await json_body(req)
    grenze.pruefen(f'api-schluessel:{b["id"]}', 20, 3600)
    name = text_feld(d, 'name', 60, pflicht=True).replace('\n', ' ')
    rechte = d.get('rechte')
    if not isinstance(rechte, list) or not rechte or any(r not in RECHTE for r in rechte):
        fehler(400, 'Bitte mindestens ein gültiges Recht wählen: ' + ', '.join(RECHTE) + '.')
    tage = d.get('gueltig_tage')
    if tage not in TAGE or isinstance(tage, bool):
        fehler(400, 'Gültigkeit: 30, 90 oder 365 Tage oder ohne Ablauf (null).')
    c = db.verbindung()
    if c.execute('SELECT COUNT(*) FROM api_schluessel WHERE benutzer_id = ?', (b['id'],)).fetchone()[0] >= MAX_JE_KONTO:
        fehler(409, f'Höchstens {MAX_JE_KONTO} API-Schlüssel je Konto – widerrufe zuerst einen alten.')
    token = PRAEFIX + secrets.token_urlsafe(32)
    t = jetzt()
    i = c.execute('INSERT INTO api_schluessel (benutzer_id, name, anfang, schluessel_hash, rechte, erstellt, gueltig_bis) '
                  'VALUES (?, ?, ?, ?, ?, ?, ?)', (b['id'], name, token[:12], h(token), json.dumps([r for r in RECHTE if r in rechte]),
                                                   t, t + tage * 86400 if tage else None)).lastrowid
    db.protokollieren(f'API-Schlüssel erstellt ({token[:12]}…)', b['id'])
    k = c.execute('SELECT * FROM api_schluessel WHERE id = ?', (i,)).fetchone()
    return {'schluessel': schluessel_json(k), 'token': token}      # token nur in dieser Antwort


@router.delete('/api/konto/api-schluessel/{i}')
def widerrufen(i: int, req: Request):
    b = benutzer_aus_sitzung(req)
    c = db.verbindung()
    k = c.execute('SELECT * FROM api_schluessel WHERE id = ? AND benutzer_id = ?', (i, b['id'])).fetchone()
    if not k:
        fehler(404, 'API-Schlüssel nicht gefunden')
    c.execute('DELETE FROM api_schluessel WHERE id = ?', (k['id'],))
    db.protokollieren(f'API-Schlüssel widerrufen ({k["anfang"]}…)', b['id'])
    return {'ok': True}


def aufraeumen(t):
    """Abgelaufene Schlüssel 30 Tage nach dem Ablauf löschen (bis dahin sieht der Besitzer „abgelaufen“)."""
    db.verbindung().execute('DELETE FROM api_schluessel WHERE gueltig_bis IS NOT NULL AND gueltig_bis < ?', (t - 30 * 86400,))


def _export(b):
    rows = db.verbindung().execute('SELECT * FROM api_schluessel WHERE benutzer_id = ? ORDER BY id', (b['id'],)).fetchall()
    return {'api_schluessel': [schluessel_json(k) for k in rows]}      # ohne Hash


konto.EXPORT.append(_export)
