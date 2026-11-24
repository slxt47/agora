"""Dateien teilen im laufenden Meeting (Header X-Teilnahme).

Hochladen als roher Body (wie Aufnahmen) mit Header `X-Dateiname` (URL-kodiert). Grenzen: 25 MB je Datei, 200 MB je
Meeting, 20 Uploads je 10 min und Person. Erlaubte Endungen: `AGORA_DATEI_ENDUNGEN` (Komma-Liste) oder die Vorgabe
ENDUNGEN. Der Typ ergibt sich nur aus der Endung (nie aus dem Header des Browsers).

Ausliefern nur an Teilnehmende genau dieses Meetings (X-Teilnahme), immer mit `nosniff` und
`Content-Security-Policy: sandbox`. Gefährliche Typen (ausführbar, HTML, Skripte, SVG …) gehen als
`application/octet-stream` und immer als Anhang; nur Bilder (png/jpg/gif/webp) gibt es mit `?vorschau=1` inline.

Virenprüfung (optional): Ist `AGORA_CLAMAV=host:port` gesetzt, prüft clamd jede Datei vor der Annahme über das
INSTREAM-Protokoll (hier selbst umgesetzt: `zINSTREAM\\0`, Blöcke mit 4-Byte-Länge, 0-Block, Antwort bis `\\0`).
Fund → 422, clamd nicht erreichbar → 503 (es wird nichts ungeprüft angenommen). Ohne die Variable zeigt die Oberfläche
„nicht auf Viren geprüft“.

Aufbewahrung: beim Meeting-Ende gelöscht; mit „chat_speichern“ bleiben sie noch 30 Tage für den Besitzer (Meeting-Verlauf).
"""
import os
import re
import secrets
import socket
import struct
import time
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import db, verlauf
from . import livekit_dienst as lk
from .kern import benutzer_aus_sitzung, fehler, grenze, iso, jetzt
from .meeting import protokoll, teilnahme
from .raeume import BEIM_ENDE, rechte

router = APIRouter()
MOD = ('host', 'cohost')
MAX_DATEI = 25 * 1024 ** 2
MAX_MEETING = 200 * 1024 ** 2
NACH_ENDE_S = 30 * 86400
ID_RE = re.compile(r'^[A-Za-z0-9_-]{22}$')
ENDUNGEN = ('pdf,png,jpg,jpeg,gif,webp,heic,txt,md,csv,json,rtf,doc,docx,xls,xlsx,ppt,pptx,odt,ods,odp,key,pages,numbers,'
            'zip,7z,tar,gz,mp3,m4a,wav,ogg,mp4,mov,webm,ics,vcf')
GEFAEHRLICH = {'exe', 'msi', 'bat', 'cmd', 'com', 'scr', 'pif', 'cpl', 'ps1', 'psm1', 'vbs', 'vbe', 'js', 'mjs', 'cjs', 'jse', 'wsf', 'wsh',
               'hta', 'jar', 'apk', 'dll', 'sys', 'sh', 'bash', 'zsh', 'py', 'pl', 'rb', 'php', 'html', 'htm', 'xhtml', 'shtml', 'svg',
               'svgz', 'xml', 'xsl', 'swf', 'lnk', 'reg', 'app', 'dmg', 'pkg', 'deb', 'rpm', 'iso', 'img', 'msc', 'appimage', 'desktop'}
BILDER = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp'}
TYPEN = BILDER | {'pdf': 'application/pdf', 'txt': 'text/plain', 'md': 'text/markdown', 'csv': 'text/csv', 'json': 'application/json',
                  'zip': 'application/zip', 'mp3': 'audio/mpeg', 'mp4': 'video/mp4', 'webm': 'video/webm', 'wav': 'audio/wav',
                  'ogg': 'audio/ogg', 'mov': 'video/quicktime', 'heic': 'image/heic'}


def endungen():
    roh = os.environ.get('AGORA_DATEI_ENDUNGEN') or ENDUNGEN
    return sorted({e.strip().lower().lstrip('.') for e in roh.split(',') if e.strip()})


def clamav_adresse():
    """(host, port) aus AGORA_CLAMAV oder None."""
    roh = os.environ.get('AGORA_CLAMAV', '').strip()
    if not roh:
        return None
    host, _, port = roh.rpartition(':')
    if not host or not port.isdigit():
        raise RuntimeError('AGORA_CLAMAV muss host:port sein')
    return host.strip('[]'), int(port)


def clamav_pruefen(pfad, adresse, block=64 * 1024, zeit=60):
    """Datei per clamd INSTREAM prüfen → (sauber: bool, befund: str|None). Fehler (Verbindung, Antwort) → RuntimeError."""
    try:
        with socket.create_connection(adresse, timeout=zeit) as s:
            s.sendall(b'zINSTREAM\0')
            with open(pfad, 'rb') as f:
                while teil := f.read(block):
                    s.sendall(struct.pack('>I', len(teil)) + teil)
            s.sendall(struct.pack('>I', 0))
            antwort = b''
            while not antwort.endswith(b'\0') and len(antwort) < 4096:
                stueck = s.recv(1024)
                if not stueck:
                    break
                antwort += stueck
    except OSError as e:
        raise RuntimeError(f'clamd nicht erreichbar: {type(e).__name__}') from e
    text = antwort.rstrip(b'\0').decode('utf-8', 'replace').strip()
    if text.endswith(' OK') or text == 'stream: OK':
        return True, None
    if text.endswith('FOUND'):
        return False, text.split(':', 1)[-1].removesuffix('FOUND').strip()[:120]
    raise RuntimeError(f'clamd: {text[:120] or "keine Antwort"}')


def pfad(i):
    return os.path.join(db.DATEN, 'dateien', i)


def entfernen(i):
    try:
        os.remove(pfad(i))
    except FileNotFoundError:
        pass
    db.verbindung().execute('DELETE FROM dateien WHERE id = ?', (i,))


def _beim_ende(r):
    c = db.verbindung()
    if r['chat_speichern']:
        c.execute('UPDATE dateien SET ablauf = ? WHERE raum_id = ? AND ablauf IS NULL', (jetzt() + NACH_ENDE_S, r['id']))
    else:
        for d in c.execute('SELECT id FROM dateien WHERE raum_id = ?', (r['id'],)).fetchall():
            entfernen(d['id'])


BEIM_ENDE.append(_beim_ende)


def aufraeumen(t=None):
    """Abgelaufene Dateien löschen und Dateien ohne Eintrag (Raum gelöscht → Zeile per CASCADE weg) von der Platte."""
    t = t or jetzt()
    c = db.verbindung()
    for d in c.execute('SELECT id FROM dateien WHERE ablauf IS NOT NULL AND ablauf < ?', (t,)).fetchall():
        entfernen(d['id'])
    ordner = os.path.join(db.DATEN, 'dateien')
    if os.path.isdir(ordner):
        bekannt = {d['id'] for d in c.execute('SELECT id FROM dateien').fetchall()}
        for n in os.listdir(ordner):
            p = os.path.join(ordner, n)
            if n not in bekannt and os.path.getmtime(p) < time.time() - 3600:
                os.remove(p)


def endung(name):
    return name.rsplit('.', 1)[-1].lower() if '.' in name else ''


def datei_json(d, identity=None):
    e = d['endung']
    j = {'id': d['id'], 'name': d['name'], 'groesse': d['groesse'], 'typ': d['typ'], 'von_name': d['von_name'], 'erstellt': iso(d['erstellt']),
         'geprueft': bool(d['geprueft']), 'bild': e in BILDER, 'gefaehrlich': e in GEFAEHRLICH}
    if identity is not None:
        j['meins'] = d['identity'] == identity
    return j


def belegt(r):
    return db.verbindung().execute('SELECT COALESCE(SUM(groesse), 0) FROM dateien WHERE raum_id = ? AND sitzung = ?',
                                   (r['id'], r['sitzung'])).fetchone()[0]


def antwort(d, vorschau=False):
    """Datei sicher ausliefern: nosniff (global), CSP sandbox, gefährliche Typen nur als Anhang/octet-stream."""
    e = d['endung']
    inline = vorschau and e in BILDER
    typ = 'application/octet-stream' if e in GEFAEHRLICH else d['typ']
    r = FileResponse(pfad(d['id']), media_type=typ, filename=d['name'], content_disposition_type='inline' if inline else 'attachment')
    r.headers['Content-Security-Policy'] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    r.headers['X-Content-Type-Options'] = 'nosniff'
    r.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    return r


def laden(r, i):
    d = db.verbindung().execute('SELECT * FROM dateien WHERE id = ? AND raum_id = ? AND sitzung = ?', (i, r['id'], r['sitzung'])).fetchone() \
        if ID_RE.match(i or '') else None
    if not d:
        fehler(404, 'Datei nicht gefunden')
    return d


# ---------------------------------------------------------------- Endpunkte
@router.get('/api/raeume/{code}/dateien')
def liste(code: str, req: Request):
    r, t = teilnahme(req, code)
    rows = db.verbindung().execute('SELECT * FROM dateien WHERE raum_id = ? AND sitzung = ? ORDER BY erstellt, id', (r['id'], r['sitzung'])).fetchall()
    return {'dateien': [datei_json(d, t['identity']) for d in rows], 'belegt': belegt(r), 'grenze_meeting': MAX_MEETING, 'grenze_datei': MAX_DATEI,
            'virenpruefung': bool(os.environ.get('AGORA_CLAMAV', '').strip()), 'endungen': endungen()}


@router.post('/api/raeume/{code}/dateien')
async def hochladen(code: str, req: Request):
    r, t = teilnahme(req, code)
    if not rechte(r)['chat'] and t['rolle'] not in MOD:
        fehler(403, 'Dateien teilen ist mit dem Chat ausgeschaltet.')
    grenze.pruefen('datei:' + t['identity'], 20, 600)
    name = ''.join(ch for ch in unquote(req.headers.get('x-dateiname', '')) if ord(ch) >= 32 and ch not in '/\\:*?"<>|').strip().strip('.')[:150]
    if not name:
        fehler(400, 'Dateiname fehlt.')
    e = endung(name)
    if e not in endungen():
        fehler(415, f'Dateien vom Typ „.{e}“ sind hier nicht erlaubt.' if e else 'Dateien ohne Endung sind hier nicht erlaubt.')
    try:
        laenge = int(req.headers.get('content-length') or 0)
    except ValueError:
        laenge = 0
    if laenge > MAX_DATEI:
        fehler(413, 'Die Datei ist größer als 25 MB.')
    if belegt(r) + max(laenge, 1) > MAX_MEETING:
        fehler(507, 'Der Speicher für Dateien in diesem Meeting ist voll (200 MB).')
    i = secrets.token_urlsafe(16)
    tmp = os.path.join(db.DATEN, 'tmp', 'datei-' + i)
    os.makedirs(os.path.join(db.DATEN, 'dateien'), exist_ok=True)
    n = 0
    try:
        with open(tmp, 'wb') as f:
            async for teil in req.stream():
                n += len(teil)
                if n > MAX_DATEI:
                    fehler(413, 'Die Datei ist größer als 25 MB.')
                f.write(teil)
        if n == 0:
            fehler(400, 'Leere Datei')
        try:
            adresse = clamav_adresse()
            sauber, befund = (await run_in_threadpool(clamav_pruefen, tmp, adresse)) if adresse else (True, None)
        except RuntimeError as err:
            print(f'virenpruefung-fehler: {err}', flush=True)
            fehler(503, 'Die Virenprüfung ist gerade nicht möglich – bitte später nochmal versuchen.')
        if not sauber:
            protokoll(t, r, 'Datei abgelehnt: Virus gefunden')
            fehler(422, f'Die Datei wurde abgelehnt: Virenprüfung meldet „{befund}“.')
        c = db.verbindung()
        c.execute('BEGIN IMMEDIATE')
        if belegt(r) + n > MAX_MEETING:          # nochmal unter Sperre (parallele Uploads)
            c.execute('ROLLBACK')
            fehler(507, 'Der Speicher für Dateien in diesem Meeting ist voll (200 MB).')
        c.execute('INSERT INTO dateien (id, raum_id, sitzung, identity, von_name, name, endung, typ, groesse, geprueft, erstellt) '
                  'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                  (i, r['id'], r['sitzung'], t['identity'], t['name'], name, e, TYPEN.get(e, 'application/octet-stream'), n, int(bool(adresse)), jetzt()))
        os.replace(tmp, pfad(i))
        c.execute('COMMIT')
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    d = db.verbindung().execute('SELECT * FROM dateien WHERE id = ?', (i,)).fetchone()
    await lk.senden(r['code'], {'typ': 'datei', 'aktion': 'neu', 'datei': datei_json(d)})
    protokoll(t, r, 'Datei geteilt')
    return {'datei': datei_json(d, t['identity'])}


@router.get('/api/raeume/{code}/dateien/{i}')
def herunterladen(code: str, i: str, req: Request, vorschau: int = 0):
    r, t = teilnahme(req, code)
    grenze.pruefen('datei-laden:' + t['identity'], 120, 60)
    return antwort(laden(r, i), bool(vorschau))


@router.delete('/api/raeume/{code}/dateien/{i}')
async def loeschen(code: str, i: str, req: Request):
    r, t = teilnahme(req, code)
    d = laden(r, i)
    if d['identity'] != t['identity'] and t['rolle'] not in MOD:
        fehler(403, 'Nur wer die Datei geteilt hat oder die Moderation darf sie löschen.')
    entfernen(d['id'])
    await lk.senden(r['code'], {'typ': 'datei', 'aktion': 'geloescht', 'datei': {'id': d['id']}})
    return {'ok': True}


@router.get('/api/verlauf/{vid}/dateien/{i}')
def verlauf_datei(vid: int, i: str, req: Request):
    """Gespeicherte Datei (chat_speichern, höchstens 30 Tage nach dem Meeting) – nur für den Besitzer, immer als Anhang."""
    b = benutzer_aus_sitzung(req)
    v = verlauf.laden(b, vid)
    d = db.verbindung().execute('SELECT * FROM dateien WHERE id = ? AND raum_id = ? AND sitzung = ? AND ablauf > ?',
                                (i, v['raum_id'], v['sitzung'], jetzt())).fetchone() if ID_RE.match(i) and v['chat'] else None
    if not d:
        fehler(404, 'Datei nicht gefunden')
    return antwort(d)


def verlauf_inhalt(raum_id, sitzung):
    rows = db.verbindung().execute('SELECT * FROM dateien WHERE raum_id = ? AND sitzung = ? AND ablauf > ? ORDER BY erstellt, id',
                                   (raum_id, sitzung, jetzt())).fetchall()
    return {'dateien': [datei_json(d) | {'ablauf': iso(d['ablauf'])} for d in rows]}


verlauf.INHALT.append(verlauf_inhalt)
