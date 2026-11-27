"""Admin-Bereich `/admin` – nur für bestätigte Konten aus AGORA_ADMIN_MAILS, für alle anderen 404 (auch die API).

Übersicht (Konten, laufende Meetings und Teilnehmende live aus LiveKit, Meetings der letzten 7/30 Tage, Speicher der
Aufnahmen), Konten (Suche, sperren/entsperren, 2FA zurücksetzen, löschen), Meldungen (bearbeiten mit Notiz),
Protokoll (Filter), laufende Meetings beenden. Jede Admin-Aktion landet im Protokoll („Admin: …“, 365 Tage).
"""
import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from . import admins, db, geteilt, raeume
from . import livekit_dienst as lk
from .kern import WEB, benutzer_aus_sitzung, fehler, iso, jetzt, json_body, text_feld
from .melden import meldung_json

router = APIRouter()
SEITE_GROESSE = 50
PROTOKOLL_ADMIN_S = 365 * 86400


def admin(req: Request):
    """Angemeldeter Admin – sonst 404 (der Bereich soll für andere nicht sichtbar sein)."""
    b = benutzer_aus_sitzung(req, pflicht=False)
    if not admins.ist_admin(b):
        fehler(404, 'Nicht gefunden')
    return b


def protokoll(a, text, raum_code=None):
    db.protokollieren(f'Admin: {text}', a['id'], raum_code)


def seite_zahl(seite):
    try:
        return max(0, min(10000, int(seite)))
    except (TypeError, ValueError):
        return 0


def ziel_konto(a, bid):
    b = db.verbindung().execute('SELECT * FROM benutzer WHERE id = ?', (bid,)).fetchone()
    if not b:
        fehler(404, 'Konto nicht gefunden')
    if b['id'] == a['id'] or admins.ist_admin(b):
        fehler(403, 'Admin-Konten lassen sich hier nicht ändern (Liste AGORA_ADMIN_MAILS).')
    return b


# ---------------------------------------------------------------- Seite
@router.get('/admin')
def seite(req: Request):
    admin(req)
    pfad = os.path.join(WEB, 'admin.html')
    if not os.path.exists(pfad):
        fehler(404, 'Nicht gefunden')
    return FileResponse(pfad, media_type='text/html')


@router.get('/admin.html')
def seite_direkt():
    fehler(404, 'Nicht gefunden')          # nicht als statische Datei an der Prüfung vorbei


# ---------------------------------------------------------------- Übersicht
@router.get('/api/admin/uebersicht')
async def uebersicht(req: Request):
    admin(req)
    c = db.verbindung()
    t = jetzt()
    eins = lambda q, *p: c.execute(q, p).fetchone()[0]   # noqa: E731
    live = await lk.raeume_liste()
    if live is not None:
        haupt = [r for r in live if '__b' not in r['name']]
        laufend = {'meetings': len(haupt), 'teilnehmer': sum(r['teilnehmer'] for r in live), 'livekit': True}
    else:
        laufend = {'meetings': eins('SELECT COUNT(*) FROM raeume WHERE aktiv = 1'), 'teilnehmer': None, 'livekit': False}
    beendet = lambda s: eins("SELECT COUNT(*) FROM protokoll WHERE ereignis LIKE 'Meeting beendet%' AND zeit > ?", t - s)  # noqa: E731
    aktiv = eins('SELECT COUNT(*) FROM raeume WHERE aktiv = 1')
    return {
        'konten': {'gesamt': eins('SELECT COUNT(*) FROM benutzer'), 'bestaetigt': eins('SELECT COUNT(*) FROM benutzer WHERE bestaetigt = 1'),
                   'gesperrt': eins('SELECT COUNT(*) FROM benutzer WHERE gesperrt = 1'),
                   'zwei_faktor': eins('SELECT COUNT(*) FROM benutzer WHERE totp_an = 1'),
                   'neu_7': eins('SELECT COUNT(*) FROM benutzer WHERE erstellt > ?', t - 7 * 86400)},
        'laufend': laufend,
        'meetings': {'tage_7': beendet(7 * 86400) + aktiv, 'tage_30': beendet(30 * 86400) + aktiv,
                     'raeume': eins('SELECT COUNT(*) FROM raeume')},
        'aufnahmen': {'anzahl': eins('SELECT COUNT(*) FROM aufnahmen'), 'belegt': eins('SELECT COALESCE(SUM(groesse), 0) FROM aufnahmen')},
        'meldungen_offen': eins("SELECT COUNT(*) FROM meldungen WHERE status = 'offen'"),
        'fehler_offen': eins('SELECT COUNT(*) FROM fehler_gruppen WHERE erledigt IS NULL'),
        'online': {'konten': geteilt.anzahl('konten'), 'meeting': geteilt.anzahl('meeting'), 'geteilt': geteilt.aktiv()},
    }


# ---------------------------------------------------------------- Konten
def konto_json(b, c):
    return {'id': b['id'], 'name': b['name'], 'email': b['email'], 'bestaetigt': bool(b['bestaetigt']), 'gesperrt': bool(b['gesperrt']),
            'zwei_faktor': bool(b['totp_an']), 'admin': admins.ist_admin(b), 'erstellt': iso(b['erstellt']),
            'letzte_anmeldung': iso(b['letzte_anmeldung']),
            'raeume': c.execute('SELECT COUNT(*) FROM raeume WHERE besitzer_id = ?', (b['id'],)).fetchone()[0],
            'aufnahmen_belegt': c.execute('SELECT COALESCE(SUM(groesse), 0) FROM aufnahmen WHERE benutzer_id = ?', (b['id'],)).fetchone()[0]}


@router.get('/api/admin/benutzer')
def benutzer(req: Request, q: str = '', seite: str = '0'):
    admin(req)
    c = db.verbindung()
    q = q.strip()[:100]
    muster = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    wo = "WHERE name LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\'" if q else ''
    p = (muster, muster) if q else ()
    gesamt = c.execute(f'SELECT COUNT(*) FROM benutzer {wo}', p).fetchone()[0]
    rows = c.execute(f'SELECT * FROM benutzer {wo} ORDER BY erstellt DESC LIMIT ? OFFSET ?',
                     (*p, SEITE_GROESSE, seite_zahl(seite) * SEITE_GROESSE)).fetchall()
    return {'benutzer': [konto_json(b, c) for b in rows], 'gesamt': gesamt, 'seite_groesse': SEITE_GROESSE}


@router.post('/api/admin/benutzer/{bid}/sperren')
async def sperren(bid: int, req: Request):
    a = admin(req)
    d = await json_body(req)
    if not isinstance(d.get('an'), bool):
        fehler(400, 'Bitte „an“ (true/false) angeben.')
    b = ziel_konto(a, bid)
    c = db.verbindung()
    c.execute('UPDATE benutzer SET gesperrt = ? WHERE id = ?', (int(d['an']), b['id']))
    if d['an']:
        c.execute('DELETE FROM sitzungen WHERE benutzer_id = ?', (b['id'],))        # überall abmelden
        c.execute('DELETE FROM anmeldeschritte WHERE benutzer_id = ?', (b['id'],))
        for r in c.execute('SELECT * FROM raeume WHERE besitzer_id = ? AND aktiv = 1', (b['id'],)).fetchall():
            await raeume.meeting_beenden(r, 'Konto gesperrt')
    protokoll(a, f'Konto {"gesperrt" if d["an"] else "entsperrt"} (#{b["id"]} {b["email"]})')
    db.protokollieren('Konto gesperrt (Admin)' if d['an'] else 'Konto entsperrt (Admin)', b['id'])
    return {'benutzer': konto_json(c.execute('SELECT * FROM benutzer WHERE id = ?', (b['id'],)).fetchone(), c)}


@router.post('/api/admin/benutzer/{bid}/2fa-zuruecksetzen')
def zwei_faktor_zuruecksetzen(bid: int, req: Request):
    a = admin(req)
    b = ziel_konto(a, bid)
    c = db.verbindung()
    c.execute('UPDATE benutzer SET totp_an = 0, totp_geheim = NULL, totp_schritt = 0 WHERE id = ?', (b['id'],))
    c.execute('DELETE FROM wiederherstellung WHERE benutzer_id = ?', (b['id'],))
    protokoll(a, f'2FA zurückgesetzt (#{b["id"]} {b["email"]})')
    db.protokollieren('2FA zurückgesetzt (Admin)', b['id'])
    return {'benutzer': konto_json(c.execute('SELECT * FROM benutzer WHERE id = ?', (b['id'],)).fetchone(), c)}


@router.delete('/api/admin/benutzer/{bid}')
async def loeschen(bid: int, req: Request):
    a = admin(req)
    b = ziel_konto(a, bid)
    from .konto import konto_entfernen
    await konto_entfernen(b)
    protokoll(a, f'Konto gelöscht (#{b["id"]} {b["email"]})')
    return {'ok': True}


# ---------------------------------------------------------------- Meldungen
@router.get('/api/admin/meldungen')
def meldungen(req: Request, status: str = 'offen'):
    admin(req)
    c = db.verbindung()
    if status in ('offen', 'erledigt'):
        rows = c.execute('SELECT * FROM meldungen WHERE status = ? ORDER BY zeit DESC LIMIT 200', (status,)).fetchall()
    else:
        rows = c.execute('SELECT * FROM meldungen ORDER BY zeit DESC LIMIT 200').fetchall()
    konten = {}
    for m in rows:
        for k in (m['von_benutzer'], m['ziel_benutzer']):
            if k and k not in konten:
                x = c.execute('SELECT id, email, gesperrt FROM benutzer WHERE id = ?', (k,)).fetchone()
                konten[k] = {'id': x['id'], 'email': x['email'], 'gesperrt': bool(x['gesperrt'])} if x else None
    return {'meldungen': [meldung_json(m) | {'von_konto': konten.get(m['von_benutzer']), 'ziel_konto': konten.get(m['ziel_benutzer'])}
                          for m in rows]}


@router.post('/api/admin/meldungen/{mid}')
async def meldung_bearbeiten(mid: int, req: Request):
    a = admin(req)
    d = await json_body(req)
    if d.get('status') not in ('offen', 'erledigt'):
        fehler(400, 'Status: offen oder erledigt.')
    notiz = text_feld(d, 'notiz', 2000)
    c = db.verbindung()
    if not c.execute('UPDATE meldungen SET status = ?, notiz = ?, bearbeitet_von = ?, bearbeitet = ? WHERE id = ?',
                     (d['status'], notiz, a['id'], jetzt(), mid)).rowcount:
        fehler(404, 'Meldung nicht gefunden')
    m = c.execute('SELECT * FROM meldungen WHERE id = ?', (mid,)).fetchone()
    protokoll(a, f'Meldung #{mid} {d["status"]}', m['raum_code'])
    return {'meldung': meldung_json(m)}


# ---------------------------------------------------------------- Protokoll
@router.get('/api/admin/protokoll')
def protokoll_lesen(req: Request, q: str = '', benutzer: str = '', raum: str = '', nur_admin: str = '', seite: str = '0'):
    admin(req)
    wo, p = [], []
    if q.strip():
        wo.append("p.ereignis LIKE ? ESCAPE '\\'")
        p.append('%' + q.strip()[:100].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
    if benutzer.strip():
        if benutzer.strip().isdigit():
            wo.append('p.benutzer_id = ?')
            p.append(int(benutzer.strip()))
        else:
            wo.append('b.email = ?')
            p.append(benutzer.strip()[:200])
    if raum.strip():
        wo.append('p.raum_code = ?')
        p.append(raum.strip()[:20])
    if nur_admin in ('1', 'true'):
        wo.append("p.ereignis LIKE 'Admin:%'")
    sql = ('SELECT p.*, b.email, b.name FROM protokoll p LEFT JOIN benutzer b ON b.id = p.benutzer_id '
           + ('WHERE ' + ' AND '.join(wo) if wo else '') + ' ORDER BY p.zeit DESC, p.rowid DESC LIMIT ? OFFSET ?')
    rows = db.verbindung().execute(sql, (*p, SEITE_GROESSE + 1, seite_zahl(seite) * SEITE_GROESSE)).fetchall()
    return {'eintraege': [{'zeit': iso(x['zeit']), 'benutzer_id': x['benutzer_id'], 'email': x['email'], 'name': x['name'],
                           'raum_code': x['raum_code'], 'ereignis': x['ereignis']} for x in rows[:SEITE_GROESSE]],
            'weitere': len(rows) > SEITE_GROESSE}


# ---------------------------------------------------------------- Laufende Meetings
@router.get('/api/admin/meetings')
async def meetings(req: Request):
    admin(req)
    c = db.verbindung()
    live = {r['name']: r for r in (await lk.raeume_liste() or [])}
    out = []
    for r in c.execute('SELECT r.*, b.name AS besitzer, b.email FROM raeume r JOIN benutzer b ON b.id = r.besitzer_id '
                       'WHERE r.aktiv = 1 ORDER BY r.gestartet DESC').fetchall():
        teilnehmer = sum(x['teilnehmer'] for n, x in live.items() if n == r['code'] or n.startswith(r['code'] + '__b'))
        out.append({'code': r['code'], 'titel': r['titel'], 'besitzer_name': r['besitzer'], 'besitzer_email': r['email'],
                    'gestartet': iso(r['gestartet']), 'teilnehmer': teilnehmer, 'aufnahme': bool(r['aufnahme_von'])})
    return {'meetings': out}


@router.post('/api/admin/meetings/{code}/beenden')
async def meeting_beenden(code: str, req: Request):
    a = admin(req)
    r = raeume.raum_laden(code)
    if not r['aktiv']:
        fehler(409, 'Dieses Meeting läuft gerade nicht.')
    protokoll(a, 'Meeting beendet', code)
    await raeume.meeting_beenden(r, 'Admin')
    return {'ok': True}


def aufraeumen(t):
    """Admin-Einträge bleiben 365 Tage (der Rest des Protokolls 30 Tage, siehe main.aufraeumen)."""
    db.verbindung().execute("DELETE FROM protokoll WHERE ereignis LIKE 'Admin:%' AND zeit < ?", (t - PROTOKOLL_ADMIN_S,))
