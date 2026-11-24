"""Kalender-Abo ohne OAuth: ein privater iCal-Feed je Konto (GET /kalender/<token>.ics).

Apple Kalender, Google Kalender und Outlook abonnieren die Adresse und fragen sie selbst regelmäßig ab. Der Feed
enthält alle eigenen geplanten Meetings mit kommendem Termin (wiederkehrende als Serie mit RRULE), nie Passcodes.

Das Token ist ein Geheimnis wie ein Passwort: Es wird nur nach dem Einschalten im Profil erzeugt, aus AGORA_GEHEIM und
einem zufälligen Salz abgeleitet (HMAC-SHA256) und nur als Hash gespeichert. „Neu erzeugen“ wechselt das Salz – der
alte Link liefert dann 404. Ausschalten löscht den Eintrag.
"""
import hashlib
import hmac
import secrets
import base64

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import db, ics, raeume, texte
from .kern import GEHEIM, SEITE, benutzer_aus_sitzung, client_ip, fehler, grenze, h, iso, jetzt

router = APIRouter()


def _token(salz):
    roh = hmac.new(GEHEIM, f'kalender-abo:{salz}'.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(roh).decode().rstrip('=')       # 43 Zeichen


def _url(salz):
    return f'{SEITE}/kalender/{_token(salz)}.ics'


def abo_json(b):
    a = db.verbindung().execute('SELECT * FROM kalender_abo WHERE benutzer_id = ?', (b['id'],)).fetchone()
    if not a:
        return {'aktiv': False, 'url': None, 'webcal': None, 'erstellt': None, 'abgerufen': None}
    url = _url(a['salz'])
    return {'aktiv': True, 'url': url, 'webcal': 'webcal://' + url.split('://', 1)[1], 'erstellt': iso(a['erstellt']),
            'abgerufen': iso(a['abgerufen'])}


@router.get('/api/konto/kalender')
def abo_lesen(req: Request):
    return {'abo': abo_json(benutzer_aus_sitzung(req))}


@router.post('/api/konto/kalender')
def abo_erzeugen(req: Request):
    """Einschalten bzw. neues Token (der alte Link wird sofort ungültig)."""
    b = benutzer_aus_sitzung(req)
    grenze.pruefen(f'kalender-abo:{b["id"]}', 20, 3600)
    salz = secrets.token_urlsafe(24)
    c = db.verbindung()
    c.execute('DELETE FROM kalender_abo WHERE benutzer_id = ?', (b['id'],))
    c.execute('INSERT INTO kalender_abo (benutzer_id, salz, token_hash, erstellt) VALUES (?, ?, ?, ?)',
              (b['id'], salz, h(_token(salz)), jetzt()))
    db.protokollieren('Kalender-Abo: neuer Link', b['id'])
    return {'abo': abo_json(b)}


@router.delete('/api/konto/kalender')
def abo_aus(req: Request):
    b = benutzer_aus_sitzung(req)
    db.verbindung().execute('DELETE FROM kalender_abo WHERE benutzer_id = ?', (b['id'],))
    db.protokollieren('Kalender-Abo: ausgeschaltet', b['id'])
    return {'abo': abo_json(b)}


def feed_text(b, t=None):
    """Alle eigenen geplanten Meetings mit kommendem (oder laufendem) Termin."""
    t = t or jetzt()
    termine = []
    for r in db.verbindung().execute("SELECT * FROM raeume WHERE besitzer_id = ? AND art = 'geplant' ORDER BY start",
                                     (b['id'],)).fetchall():
        if raeume.termin(r, t) is None:
            continue
        termine.append({'code': r['code'], 'titel': r['titel'], 'beschreibung': r['beschreibung'], 'link': raeume.link(r['code']),
                        'start': r['start'], 'dauer_min': r['dauer_min'], 'zeitzone': r['zeitzone'] or 'UTC',
                        'wiederholung': r['wiederholung'], 'bis': r['bis'], 'erstellt': r['erstellt'],
                        'veranstalter': b['name']})
    with texte.mit_sprache(b['sprache']):      # Feed in der Konto-Sprache (Kalender-Apps schicken keine passende Sprache)
        return ics.abo_kalender(f'Agora – {b["name"]}', termine)


@router.get('/kalender/{datei}')
def feed(datei: str, req: Request):
    grenze.pruefen('kalender-feed:' + client_ip(req), 120, 3600)
    token = datei[:-4] if datei.endswith('.ics') else ''
    if not 20 <= len(token) <= 100:
        fehler(404, 'Nicht gefunden')
    c = db.verbindung()
    a = c.execute('SELECT * FROM kalender_abo WHERE token_hash = ?', (h(token),)).fetchone()
    if not a or not hmac.compare_digest(_token(a['salz']), token):
        fehler(404, 'Nicht gefunden')
    b = c.execute('SELECT * FROM benutzer WHERE id = ? AND bestaetigt = 1', (a['benutzer_id'],)).fetchone()
    if not b:
        fehler(404, 'Nicht gefunden')
    c.execute('UPDATE kalender_abo SET abgerufen = ? WHERE benutzer_id = ?', (jetzt(), b['id']))
    return Response(feed_text(b), media_type='text/calendar; charset=utf-8',
                    headers={'Content-Disposition': 'inline; filename="agora.ics"', 'X-Robots-Tag': 'noindex'})
