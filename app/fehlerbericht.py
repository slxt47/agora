"""Fehler-Monitoring, selbst gehostet (statt Sentry): Fehler aus Browsern und Ausnahmen des Servers, gruppiert.

- POST /api/fehlerbericht (ohne Anmeldung, auch Gäste): window.onerror, unhandledrejection, LiveKit-Trennungsgründe,
  fehlgeschlagene API-Aufrufe ≥ 500 (web/src/fehlerbericht.js). Höchstens 16 KB je Bericht, 30 Berichte/10 min je IP
  (IPv6 als /64), 600/10 min insgesamt, höchstens MAX_GRUPPEN Gruppen aus Browsern.
- Server: Ausnahmen in Routen (Middleware in main.py → 500 „Interner Fehler“) und in der Hintergrund-Schleife.
- Gruppiert nach Fingerabdruck (Quelle + Art + Nachricht + oberster Frame) mit Anzahl, erstem/letztem Auftreten,
  Version (Release) und Browser-Familie (aus dem User-Agent – der User-Agent selbst wird nicht gespeichert).
- Datenschutz: Keine Namen, E-Mail-Adressen, IPs, Cookies. Alles, was nach `?` oder `#` kommt, wird entfernt – so landet
  der E2EE-Schlüssel aus dem Fragment (`#k=…`) nie in der Datenbank. Meeting-Codes, lange Token/IDs, E-Mail-Adressen und
  IPv4-Adressen werden durch Platzhalter ersetzt, Stacks gekürzt (20 Zeilen, 4000 Zeichen). Aufbewahrung 30 Tage nach
  dem letzten Auftreten.
- Admin-Reiter „Fehler“ (/admin#fehler): Liste, Details, erledigt/wieder öffnen. Eine erledigte Gruppe ist wieder offen,
  sobald der Fehler erneut auftritt.
- Alarm: neue (oder wieder aufgetretene) Gruppe → Mail an die Admins, höchstens 1 je Gruppe und Tag (dazu 20/h je Admin);
  optional zusätzlich ein ntfy-Thema (AGORA_FEHLER_NTFY = volle URL, z. B. https://ntfy.example/agora-fehler).
"""
import hashlib
import json
import os
import re
import threading
import traceback
import urllib.request

from fastapi import APIRouter, Request

from . import admins, db, mail
from .kern import SEITE, VERSION, client_ip, fehler, grenze, iso, jetzt, json_body

router = APIRouter()
AUFBEWAHRUNG_S = 30 * 86400
ALARM_ABSTAND_S = 86400
MAX_BERICHT = 16 * 1024            # Bytes JSON je Bericht
MAX_NACHRICHT = 500
MAX_STAPEL = 4000
MAX_STAPEL_ZEILEN = 20
MAX_GRUPPEN = 2000                 # neue Gruppen aus Browsern darüber hinaus werden verworfen (Server-Ausnahmen nie)
MAX_SCHLUESSEL = 20                # Versionen/Browser je Gruppe
ARTEN_BROWSER = ('fehler', 'promise', 'livekit', 'api')
NTFY = os.environ.get('AGORA_FEHLER_NTFY', '').strip()

# ---------------------------------------------------------------- Säubern (keine personenbezogenen Daten, kein Fragment)
_HERKUNFT = re.compile(r'\b(?:https?|wss?|blob:https?)://[^/\s\'"<>()]+', re.I)
_QUERY_FRAGMENT = re.compile(r'(?:[?#]|%23|%3[Ff])[^\s\'"<>()\[\]{}]*')
_SCHLUESSEL = re.compile(r'\bk=[A-Za-z0-9_-]{8,}')        # E2EE-Schlüssel auch ohne „#“ davor
_EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
_CODE = re.compile(r'\b[a-z]{3}-[a-z]{4}-[a-z]{3}\b')
_TOKEN = re.compile(r'(?=[A-Za-z_-]*\d)[A-Za-z0-9_-]{20,}')
_IPV4 = re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}\b')
_PFAD_ID = re.compile(r'/(?:\d+|[A-Za-z0-9_-]{16,})(?=[/\s:]|$)')
_SEITE = re.compile(r'^/(r|w|a|e)/[^/]+')
_STEUER = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_FRAME = re.compile(r':\d+(?::\d+)?\)?\s*$|^\s*at\s|^\s*File "|@')


def saeubern(text, maximal=MAX_NACHRICHT):
    """Text ohne Query/Fragment, Herkunft, E-Mail-Adressen, Meeting-Codes, Token, IPs; Steuerzeichen raus, gekürzt."""
    if not isinstance(text, str):
        text = '' if text is None else str(text)
    text = text[:maximal * 4]                       # erst grob kürzen (Regex-Aufwand begrenzen)
    text = _STEUER.sub(' ', text)
    text = _HERKUNFT.sub('', text)
    text = _QUERY_FRAGMENT.sub('', text)            # zuerst: #k=… (E2EE-Schlüssel) und ?token=… verschwinden ganz
    text = _SCHLUESSEL.sub('', text)
    text = _EMAIL.sub('‹e-mail›', text)
    text = _CODE.sub('‹code›', text)
    text = _TOKEN.sub('‹token›', text)
    text = _IPV4.sub('‹ip›', text)
    text = _PFAD_ID.sub('/‹id›', text)
    return text.strip()[:maximal]


def stapel_saeubern(stapel):
    zeilen = [saeubern(z, 300) for z in (stapel or '').splitlines()[:MAX_STAPEL_ZEILEN * 2]]
    return '\n'.join(z for z in zeilen if z)[:MAX_STAPEL] if isinstance(stapel, str) else ''


def seite_saeubern(seite):
    if not isinstance(seite, str) or not seite.startswith('/'):
        return ''
    seite = _QUERY_FRAGMENT.sub('', seite[:400])
    seite = _SEITE.sub(lambda m: f'/{m.group(1)}/‹code›', seite)
    return saeubern(seite, 200)


def oberster_frame(stapel):
    """Erste Zeile des (gesäuberten) Stacks, die wie ein Frame aussieht – Teil des Fingerabdrucks."""
    for z in (stapel or '').splitlines():
        if _FRAME.search(z):
            return z.strip()[:200]
    return ''


def browser_familie(ua):
    """Nur die Familie (+ „mobil“), nie der ganze User-Agent."""
    ua = ua or ''
    if 'Electron/' in ua:
        name = 'Agora Desktop'
    elif 'Edg/' in ua or 'EdgA/' in ua or 'EdgiOS/' in ua:
        name = 'Edge'
    elif 'OPR/' in ua or 'Opera' in ua:
        name = 'Opera'
    elif 'SamsungBrowser/' in ua:
        name = 'Samsung Internet'
    elif 'Firefox/' in ua or 'FxiOS/' in ua:
        name = 'Firefox'
    elif 'CriOS/' in ua or 'Chrome/' in ua or 'Chromium/' in ua:
        name = 'Chrome'
    elif 'Safari/' in ua or 'AppleWebKit/' in ua:
        name = 'Safari'
    elif not ua:
        name = 'unbekannt'
    else:
        name = 'andere'
    return name + (' · mobil' if re.search(r'Mobile|Android|iPhone|iPad', ua) else '')


def fingerabdruck(quelle, art, nachricht, ort):
    roh = '\0'.join((quelle, art, re.sub(r'\d{4,}', 'N', nachricht), re.sub(r'-[A-Za-z0-9_]{8}\.js', '.js', ort)))
    return hashlib.sha256(roh.encode()).hexdigest()


def _zaehlen(roh, schluessel):
    try:
        d = json.loads(roh or '{}')
    except ValueError:
        d = {}
    if schluessel:
        if schluessel not in d and len(d) >= MAX_SCHLUESSEL:
            schluessel = 'andere'
        d[schluessel] = d.get(schluessel, 0) + 1
    return json.dumps(d, ensure_ascii=False, separators=(',', ':'))


# ---------------------------------------------------------------- Erfassen
def erfassen(quelle, art, nachricht, stapel='', seite='', version='', browser=''):
    """Einen Fehler zählen (neue Gruppe oder vorhandene). → (gruppen_id, neu_oder_wieder) oder (None, False)."""
    nachricht = saeubern(nachricht) or '(ohne Text)'
    stapel = stapel_saeubern(stapel)
    seite = seite_saeubern(seite) if quelle == 'browser' else saeubern(seite, 200)
    version = version if isinstance(version, str) and re.fullmatch(r'[\w.+-]{1,40}', version) else ''
    ort = oberster_frame(stapel)
    fp = fingerabdruck(quelle, art, nachricht, ort)
    t = jetzt()
    c = db.verbindung()
    c.execute('BEGIN IMMEDIATE')
    try:
        g = c.execute('SELECT * FROM fehler_gruppen WHERE fingerabdruck = ?', (fp,)).fetchone()
        if g:
            wieder = g['erledigt'] is not None
            c.execute('UPDATE fehler_gruppen SET anzahl = anzahl + 1, zuletzt = ?, stapel = ?, seite = ?, version = ?, versionen = ?, '
                      'browser = ?, erledigt = NULL WHERE id = ?',
                      (t, stapel or g['stapel'], seite or g['seite'], version or g['version'], _zaehlen(g['versionen'], version),
                       _zaehlen(g['browser'], browser), g['id']))
            gid = g['id']
        else:
            wieder = True
            if quelle == 'browser' and c.execute("SELECT COUNT(*) FROM fehler_gruppen WHERE quelle = 'browser'").fetchone()[0] >= MAX_GRUPPEN:
                c.execute('ROLLBACK')
                return None, False
            gid = c.execute('INSERT INTO fehler_gruppen (fingerabdruck, quelle, art, nachricht, ort, stapel, seite, anzahl, erstmals, zuletzt, '
                            'version, versionen, browser) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)',
                            (fp, quelle, art, nachricht, ort, stapel, seite, t, t, version, _zaehlen('{}', version),
                             _zaehlen('{}', browser))).lastrowid
        c.execute('COMMIT')
    except BaseException:
        if c.in_transaction:
            c.execute('ROLLBACK')
        raise
    from .metriken import zaehlen
    zaehlen('agora_fehlerberichte_total', quelle=quelle)
    if wieder:
        alarm(gid)
    return gid, wieder


def server_ausnahme(e, ort='', methode=''):
    """Ausnahme des Servers erfassen (nie selbst eine Ausnahme werfen – das Monitoring darf nichts kaputt machen)."""
    try:
        rahmen = traceback.extract_tb(e.__traceback__)
        # Eigener Code zuerst: Bibliotheken (site-packages) und die Middleware selbst gehören nicht zum Fingerabdruck
        eigene = [f for f in rahmen if 'site-packages' not in f.filename and f.name != 'ausnahmen'] or rahmen
        zeilen = [f'File "{"app/" if "/app/" in f.filename.replace(chr(92), "/") else ""}{os.path.basename(f.filename)}", '
                  f'line {f.lineno}, in {f.name}' for f in reversed(eigene[-MAX_STAPEL_ZEILEN:])]   # innerster Frame zuerst, nur Dateiname
        erfassen('server', 'ausnahme', f'{type(e).__name__}: {e}', '\n'.join(zeilen), f'{methode} {ort}'.strip(), VERSION, '')
    except Exception as x:   # noqa: BLE001
        print(f'fehlerbericht-fehler {type(x).__name__}', flush=True)


# ---------------------------------------------------------------- Alarm (Mail an Admins, optional ntfy)
def _mail(an, g, link):
    zeilen = [('Quelle', g['quelle']), ('Art', g['art']), ('Nachricht', g['nachricht'])]
    zeilen += [(k, v) for k, v in (('Ort', g['ort']), ('Seite', g['seite']), ('Version', g['version'])) if v]
    satz = ('Ein neuer Fehler ist aufgetreten.' if g['anzahl'] <= 1 else f'Ein erledigter Fehler ist wieder aufgetreten ({g["anzahl"]}×).')
    text = satz + '\n\n' + '\n'.join(f'{k}: {v}' for k, v in zeilen) + f'\n\nAdmin-Bereich: {link}\n'
    mail.senden(an, f'Agora: Fehler ({g["quelle"]}) – {g["nachricht"][:60]}', text,
                mail.html_mail('Fehler', [satz], knopf=('Fehler ansehen', link), details=zeilen, vorschau='Fehler in Agora'))


def _ntfy(g):
    try:
        anfrage = urllib.request.Request(NTFY, data=f'{g["quelle"]}/{g["art"]}: {g["nachricht"][:300]}'.encode(), method='POST',
                                         headers={'Title': 'Agora: Fehler', 'Tags': 'warning', 'Click': f'{SEITE}/admin#fehler'})
        urllib.request.urlopen(anfrage, timeout=10).close()     # noqa: S310 (Adresse vom Betreiber)
    except Exception as e:   # noqa: BLE001
        print(f'fehler-ntfy {type(e).__name__}', flush=True)


def alarm(gid):
    """Höchstens 1 Alarm je Gruppe und Tag (gemeldet = Zeit des letzten Alarms)."""
    c = db.verbindung()
    t = jetzt()
    if not c.execute('UPDATE fehler_gruppen SET gemeldet = ? WHERE id = ? AND (gemeldet IS NULL OR gemeldet < ?)',
                     (t, gid, t - ALARM_ABSTAND_S)).rowcount:
        return
    if not grenze.ok('fehleralarm', 60, 3600):          # Flut vieler Gruppen: höchstens 60 Alarme/h insgesamt
        return
    g = c.execute('SELECT * FROM fehler_gruppen WHERE id = ?', (gid,)).fetchone()
    for a in admins.bestaetigte():
        if grenze.ok('fehlermail:' + a['email'].lower(), 20, 3600):
            mail.im_hintergrund(_mail, a['email'], g, f'{SEITE}/admin#fehler')
    if NTFY:
        threading.Thread(target=_ntfy, args=(dict(g),), daemon=True).start()


# ---------------------------------------------------------------- Endpunkte
@router.post('/api/fehlerbericht')
async def bericht(req: Request):
    ip = client_ip(req)
    grenze.pruefen('fehlerbericht:' + ip, 30, 600)
    grenze.pruefen('fehlerbericht-alle', 600, 600)
    d = await json_body(req, MAX_BERICHT)
    art = d.get('art')
    if art not in ARTEN_BROWSER:
        fehler(400, 'Art: fehler, promise, livekit oder api.')
    for feld in ('nachricht', 'stapel', 'seite', 'version'):
        if d.get(feld) is not None and not isinstance(d.get(feld), str):
            fehler(400, 'Ungültige Anfrage')
    erfassen('browser', art, d.get('nachricht') or '', d.get('stapel') or '', d.get('seite') or '', d.get('version') or '',
             browser_familie(req.headers.get('user-agent', '')))
    return {'ok': True}


def gruppe_json(g, details=False):
    j = {'id': g['id'], 'quelle': g['quelle'], 'art': g['art'], 'nachricht': g['nachricht'], 'ort': g['ort'], 'seite': g['seite'],
         'anzahl': g['anzahl'], 'erstmals': iso(g['erstmals']), 'zuletzt': iso(g['zuletzt']), 'version': g['version'],
         'versionen': json.loads(g['versionen'] or '{}'), 'browser': json.loads(g['browser'] or '{}'),
         'erledigt': iso(g['erledigt'])}
    if details:
        j['stapel'] = g['stapel']
    return j


def _admin(req):
    from .admin import admin
    return admin(req)


def _laden(gid):
    g = db.verbindung().execute('SELECT * FROM fehler_gruppen WHERE id = ?', (gid,)).fetchone()
    if not g:
        fehler(404, 'Fehler nicht gefunden')
    return g


def offen():
    return db.verbindung().execute('SELECT COUNT(*) FROM fehler_gruppen WHERE erledigt IS NULL').fetchone()[0]


@router.get('/api/admin/fehler')
def liste(req: Request, status: str = 'offen'):
    _admin(req)
    wo = {'offen': 'WHERE erledigt IS NULL', 'erledigt': 'WHERE erledigt IS NOT NULL'}.get(status, '')
    rows = db.verbindung().execute(f'SELECT * FROM fehler_gruppen {wo} ORDER BY zuletzt DESC, id DESC LIMIT 200').fetchall()
    return {'gruppen': [gruppe_json(g) for g in rows], 'offen': offen()}


@router.get('/api/admin/fehler/{gid}')
def details(gid: int, req: Request):
    _admin(req)
    return {'gruppe': gruppe_json(_laden(gid), details=True)}


@router.post('/api/admin/fehler/{gid}')
async def setzen(gid: int, req: Request):
    a = _admin(req)
    d = await json_body(req)
    if not isinstance(d.get('erledigt'), bool):
        fehler(400, 'Bitte „erledigt“ (true/false) angeben.')
    g = _laden(gid)
    db.verbindung().execute('UPDATE fehler_gruppen SET erledigt = ? WHERE id = ?', (jetzt() if d['erledigt'] else None, g['id']))
    db.protokollieren(f'Admin: Fehler #{g["id"]} {"erledigt" if d["erledigt"] else "wieder offen"}', a['id'])
    return {'gruppe': gruppe_json(_laden(gid), details=True)}


@router.delete('/api/admin/fehler/{gid}')
def loeschen(gid: int, req: Request):
    a = _admin(req)
    g = _laden(gid)
    db.verbindung().execute('DELETE FROM fehler_gruppen WHERE id = ?', (g['id'],))
    db.protokollieren(f'Admin: Fehler #{g["id"]} gelöscht', a['id'])
    return {'ok': True}


def aufraeumen(t=None):
    t = t or jetzt()
    db.verbindung().execute('DELETE FROM fehler_gruppen WHERE zuletzt < ?', (t - AUFBEWAHRUNG_S,))
