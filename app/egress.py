"""Server-Aufnahme und Livestream über LiveKit Egress (nur mit AGORA_EGRESS=1).

- „Aufnahme auf dem Server“: Host oder Co-Host startet eine RoomComposite-Aufnahme (Layout grid oder speaker, 720p30
  H.264) als MP4. Der Egress-Container schreibt nach AGORA_EGRESS_ZIEL (seine Sicht, Standard /out); derselbe Ordner
  ist bei der App AGORA_EGRESS_AUSGABE (Standard /daten/egress). Nach dem Abschluss wandert die Datei in die
  Bibliothek des Raum-Besitzers (/daten/aufnahmen, 90 Tage wie jede Aufnahme, zählt zum 5-GB-Kontingent).
- „Livestream“: RTMP(S)-URL + Stream-Schlüssel (YouTube, Twitch …). Beides wird nie protokolliert; gespeichert nur
  verschlüsselt (AGORA_GEHEIM) und nur solange der Stream läuft. Ziele im eigenen Netz (localhost, private IPs) sind
  gesperrt, damit niemand über den Egress-Container interne Dienste anspricht.
- Anzeige für alle über die Raum-Metadaten: `aufnahme` (wie die Browser-Aufnahme, von_name + server: true) und
  `livestream` (von_name). Fehler gehen als Ereignis {typ: 'egress', …} an Host und Co-Hosts.
Der Zustand wird abgeglichen, wenn Host/Co-Host den Status abfragt (alle paar Sekunden, solange etwas läuft) und
im Hintergrund jede Minute.
"""
import ipaddress
import os
import re
import secrets
import shutil
import socket
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from . import aufnahmen, db, meeting, raeume
from . import livekit_dienst as lk
from .kern import fehler, grenze, iso, jetzt, json_body, text_feld
from .zwei_faktor import entschluesseln, verschluesseln

router = APIRouter()
AN = os.environ.get('AGORA_EGRESS') == '1'
AUSGABE = os.environ.get('AGORA_EGRESS_AUSGABE') or os.path.join(db.DATEN, 'egress')
ZIEL = (os.environ.get('AGORA_EGRESS_ZIEL') or '/out').rstrip('/')
LAYOUTS = {'galerie': 'grid', 'sprecher': 'speaker'}
LAEUFT = ('startet', 'laeuft', 'endet')
FERTIG_BEHALTEN_S = 7 * 86400


def an():
    return AN


def _aktiv(r, art, stati=LAEUFT):
    return db.verbindung().execute(f"SELECT * FROM egress_laeufe WHERE raum_id = ? AND art = ? AND status IN {stati}",
                                   (r['id'], art)).fetchone()


def _meta(r):
    """Raum-Metadaten: Livestream-Hinweis und „Server“-Kennzeichen an der Aufnahme."""
    if not AN:
        return {}
    m = {}
    s = _aktiv(r, 'stream')
    if s:
        m['livestream'] = {'von_name': s['von_name']}
    a = _aktiv(r, 'aufnahme')
    if a:
        m['aufnahme'] = {'von_name': a['von_name'], 'server': True}
    return m


def _beim_ende(r):
    """Meeting vorbei: LiveKit löscht den Raum, Egress endet von selbst. Stream-Ziel sofort vergessen."""
    db.verbindung().execute('UPDATE egress_laeufe SET ziel_verschluesselt = NULL WHERE raum_id = ?', (r['id'],))


raeume.META_EXTRA.append(_meta)
raeume.BEIM_ENDE.append(_beim_ende)


def lauf_json(e):
    if not e:
        return None
    return {'id': e['egress_id'], 'art': e['art'], 'status': e['status'], 'fehler': e['fehler'], 'von_name': e['von_name'],
            'layout': 'sprecher' if e['layout'] == 'speaker' else 'galerie', 'gestartet': iso(e['gestartet']),
            'aufnahme_id': e['aufnahme_id']}


def ziel_pruefen(url):
    """rtmp:// oder rtmps://, Host im Internet (keine Loopback/privaten Adressen)."""
    try:
        u = urlsplit(url)
    except ValueError:
        fehler(400, 'Ungültige Stream-Adresse.')
    if u.scheme not in ('rtmp', 'rtmps') or not u.hostname or u.username or u.password:
        fehler(400, 'Die Stream-Adresse muss mit rtmp:// oder rtmps:// beginnen.')
    try:
        adressen = {a[4][0] for a in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == 'rtmps' else 1935), proto=socket.IPPROTO_TCP)}
    except (socket.gaierror, UnicodeError):
        fehler(400, 'Der Stream-Server ist nicht erreichbar (Name unbekannt).')
    for a in adressen:
        ip = ipaddress.ip_address(a.split('%')[0])
        if not ip.is_global:
            fehler(400, 'Stream-Ziele im eigenen Netz sind nicht erlaubt.')


async def melden(r, e, text=None):
    """Host und Co-Hosts über Status/Fehler informieren und Metadaten auffrischen."""
    mods = [t['identity'] for t in raeume.teilnahmen(r, ('host', 'cohost'))]
    await lk.senden(r['code'], {'typ': 'egress', 'art': e['art'], 'status': e['status'], 'fehler': text or e['fehler']}, an=mods)
    await lk.metadaten(r['code'], raeume.meta(raeume.neu_laden(r)))


def registrieren(e, info):
    """Fertige MP4 aus dem Ausgabeordner in die Bibliothek des Besitzers übernehmen → aufnahme_id oder None."""
    quelle = os.path.join(AUSGABE, e['datei'] or '')
    if not e['datei'] or not os.path.isfile(quelle) or not e['benutzer_id']:
        return None
    i = secrets.token_urlsafe(16)
    groesse = os.path.getsize(quelle)
    c = db.verbindung()
    c.execute('INSERT INTO aufnahmen (id, benutzer_id, titel, raum_code, groesse, typ, dauer, erstellt, ablauf) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
              (i, e['benutzer_id'], e['titel'] or 'Server-Aufnahme', e['raum_code'], groesse, 'video/mp4', info.get('dauer'), jetzt(),
               jetzt() + aufnahmen.AUFBEWAHRUNG_S))
    shutil.move(quelle, aufnahmen.pfad(i))       # gleiches Dateisystem: umbenennen, sonst kopieren + löschen
    db.protokollieren('Server-Aufnahme in der Bibliothek', e['benutzer_id'], e['raum_code'])
    return i


async def abgleichen(e):
    """Status eines Laufs bei LiveKit nachfragen; Abschluss/Fehler verarbeiten. Gibt die aktuelle Zeile zurück."""
    c = db.verbindung()
    geheim = ()
    if e['ziel_verschluesselt']:
        ziel = entschluesseln(e['ziel_verschluesselt'])
        geheim = (ziel, ziel.rsplit('/', 1)[-1]) if ziel else ()
    info = await lk.egress_info(e['egress_id'], geheim)
    if info is None or (info['status'] == e['status'] and info['status'] in LAEUFT):
        return e
    status, text = info['status'], info.get('fehler') or None
    aufnahme_id = None
    if status == 'fertig' and e['art'] == 'aufnahme':
        aufnahme_id = registrieren(e, info)
        if not aufnahme_id:
            status, text = 'fehler', text or 'Die Aufnahme-Datei fehlt.'
    if status in ('fertig', 'fehler'):
        c.execute('UPDATE egress_laeufe SET status = ?, fehler = ?, aufnahme_id = ?, ziel_verschluesselt = NULL, beendet = ? WHERE egress_id = ?',
                  (status, text, aufnahme_id, jetzt(), e['egress_id']))
        if status == 'fehler' and e['datei']:
            try:
                os.remove(os.path.join(AUSGABE, e['datei']))
            except OSError:
                pass
    else:
        c.execute('UPDATE egress_laeufe SET status = ? WHERE egress_id = ?', (status, e['egress_id']))
    neu = c.execute('SELECT * FROM egress_laeufe WHERE egress_id = ?', (e['egress_id'],)).fetchone()
    r = c.execute('SELECT * FROM raeume WHERE id = ?', (e['raum_id'],)).fetchone() if e['raum_id'] else None
    if r and r['aktiv']:
        await melden(r, neu)
    return neu


async def alle_abgleichen():
    """Hintergrund: alle offenen Läufe abgleichen, alte fertige Zeilen und verwaiste Dateien entfernen."""
    if not AN:
        return
    c = db.verbindung()
    for e in c.execute(f'SELECT * FROM egress_laeufe WHERE status IN {LAEUFT}').fetchall():
        await abgleichen(e)
    c.execute("DELETE FROM egress_laeufe WHERE status IN ('fertig', 'fehler') AND beendet < ?", (jetzt() - FERTIG_BEHALTEN_S,))
    bekannt = {x['datei'] for x in c.execute(f'SELECT datei FROM egress_laeufe WHERE status IN {LAEUFT}').fetchall()}
    if os.path.isdir(AUSGABE):
        for n in os.listdir(AUSGABE):
            p = os.path.join(AUSGABE, n)
            if n not in bekannt and os.path.getmtime(p) < time.time() - 86400:
                os.remove(p)


# ---------------------------------------------------------------- API (Host/Co-Host im Meeting)
def _verfuegbar():
    if not AN:
        fehler(404, 'Aufnahme und Livestream auf dem Server sind nicht eingerichtet.')


@router.get('/api/raeume/{code}/egress')
async def zustand(code: str, req: Request):
    r, t = meeting.teilnahme(req, code)
    if not AN:
        return {'verfuegbar': False}
    if t['rolle'] not in ('host', 'cohost'):
        return {'verfuegbar': True}
    out = {'verfuegbar': True}
    for art in ('aufnahme', 'stream'):
        e = _aktiv(r, art)
        if e:
            e = await abgleichen(e)
        else:     # zuletzt beendeter Lauf dieses Meetings (zeigt Fehler oder „in der Bibliothek“)
            e = db.verbindung().execute('SELECT * FROM egress_laeufe WHERE raum_id = ? AND sitzung = ? AND art = ? ORDER BY gestartet DESC LIMIT 1',
                                        (r['id'], r['sitzung'], art)).fetchone()
        out[art] = lauf_json(e)
    return out


def _layout(d):
    w = d.get('layout', 'galerie')
    if w not in LAYOUTS:
        fehler(400, 'Layout: galerie oder sprecher.')
    return LAYOUTS[w]


@router.post('/api/raeume/{code}/egress/aufnahme')
async def aufnahme(code: str, req: Request):
    _verfuegbar()
    r, t = meeting.steuernd(req, code)
    d = await json_body(req)
    c = db.verbindung()
    if d.get('aktion') != 'stopp' and r['e2ee']:     # Server sieht in E2EE-Meetings nur verschlüsselte Medien
        fehler(409, 'In Ende-zu-Ende-verschlüsselten Meetings geht das nur im Browser – der Server kann Bild und Ton nicht lesen.')
    if d.get('aktion') == 'stopp':
        e = _aktiv(r, 'aufnahme', ('startet', 'laeuft'))
        if not e:
            fehler(404, 'Es läuft keine Aufnahme auf dem Server.')
        _ok, f = await lk.egress_stoppen(e['egress_id'])
        if f:
            fehler(502, f'Die Aufnahme ließ sich nicht stoppen: {f}')
        c.execute("UPDATE egress_laeufe SET status = 'endet' WHERE egress_id = ?", (e['egress_id'],))
        meeting.protokoll(t, r, 'Server-Aufnahme gestoppt')
        await melden(r, c.execute('SELECT * FROM egress_laeufe WHERE egress_id = ?', (e['egress_id'],)).fetchone())
        return {'ok': True}
    if d.get('aktion') != 'start':
        fehler(400, 'Aktion: start oder stopp.')
    grenze.pruefen('egress:' + t['identity'], 10, 600)
    layout = _layout(d)
    if _aktiv(r, 'aufnahme'):
        fehler(409, 'Auf dem Server wird schon aufgezeichnet.')
    if r['aufnahme_von']:
        fehler(409, 'Es läuft schon eine Aufnahme im Browser.')
    if aufnahmen.belegt(r['besitzer_id']) >= aufnahmen.KONTINGENT:
        fehler(507, 'Der Speicher für Aufnahmen des Besitzers ist voll (5 GB).')
    os.makedirs(AUSGABE, exist_ok=True)
    try:
        os.chmod(AUSGABE, 0o2770)                # Egress schreibt als 1001:1000 (Gruppe der App)
    except OSError:
        pass
    datei = f'{secrets.token_urlsafe(12)}.mp4'
    eid, f = await lk.egress_datei(r['code'], f'{ZIEL}/{datei}', layout)
    if f or not eid:
        fehler(502, f'Die Aufnahme auf dem Server ließ sich nicht starten: {f or "unbekannt"}')
    titel = f'{r["titel"]} – Server-Aufnahme {time.strftime("%d.%m.%Y %H:%M", time.gmtime())} UTC'[:200]
    c.execute('INSERT INTO egress_laeufe (egress_id, raum_id, sitzung, art, benutzer_id, von_name, titel, raum_code, layout, datei, status, gestartet) '
              "VALUES (?, ?, ?, 'aufnahme', ?, ?, ?, ?, ?, ?, 'startet', ?)",
              (eid, r['id'], r['sitzung'], r['besitzer_id'], t['name'], titel, r['code'], layout, datei, jetzt()))
    meeting.protokoll(t, r, 'Server-Aufnahme gestartet')
    await melden(r, c.execute('SELECT * FROM egress_laeufe WHERE egress_id = ?', (eid,)).fetchone())
    await lk.senden(r['code'], {'typ': 'aufnahme', 'aktiv': True, 'von_name': t['name'], 'server': True})
    return {'ok': True, 'id': eid}


@router.post('/api/raeume/{code}/egress/stream')
async def stream(code: str, req: Request):
    _verfuegbar()
    r, t = meeting.steuernd(req, code)
    d = await json_body(req)
    c = db.verbindung()
    if d.get('aktion') != 'stopp' and r['e2ee']:     # Server sieht in E2EE-Meetings nur verschlüsselte Medien
        fehler(409, 'In Ende-zu-Ende-verschlüsselten Meetings geht das nur im Browser – der Server kann Bild und Ton nicht lesen.')
    if d.get('aktion') == 'stopp':
        e = _aktiv(r, 'stream', ('startet', 'laeuft'))
        if not e:
            fehler(404, 'Es läuft kein Livestream.')
        _ok, f = await lk.egress_stoppen(e['egress_id'])
        if f:
            fehler(502, f'Der Livestream ließ sich nicht stoppen: {f}')
        c.execute("UPDATE egress_laeufe SET status = 'endet', ziel_verschluesselt = NULL WHERE egress_id = ?", (e['egress_id'],))
        meeting.protokoll(t, r, 'Livestream gestoppt')
        await melden(r, c.execute('SELECT * FROM egress_laeufe WHERE egress_id = ?', (e['egress_id'],)).fetchone())
        return {'ok': True}
    if d.get('aktion') != 'start':
        fehler(400, 'Aktion: start oder stopp.')
    grenze.pruefen('egress:' + t['identity'], 10, 600)
    layout = _layout(d)
    url = text_feld(d, 'url', 500, pflicht=True).rstrip('/')
    schluessel = text_feld(d, 'schluessel', 500)
    if re.search(r'\s', url + schluessel):
        fehler(400, 'Adresse und Schlüssel dürfen keine Leerzeichen enthalten.')
    await run_in_threadpool(ziel_pruefen, url)
    if _aktiv(r, 'stream'):
        fehler(409, 'Es läuft schon ein Livestream.')
    ziel = f'{url}/{schluessel}' if schluessel else url
    eid, f = await lk.egress_stream(r['code'], ziel, layout, (schluessel,))
    if f or not eid:
        fehler(502, f'Der Livestream ließ sich nicht starten: {f or "unbekannt"}')
    c.execute('INSERT INTO egress_laeufe (egress_id, raum_id, sitzung, art, benutzer_id, von_name, raum_code, layout, ziel_verschluesselt, status, gestartet) '
              "VALUES (?, ?, ?, 'stream', ?, ?, ?, ?, ?, 'startet', ?)",
              (eid, r['id'], r['sitzung'], r['besitzer_id'], t['name'], r['code'], layout, verschluesseln(ziel), jetzt()))
    meeting.protokoll(t, r, 'Livestream gestartet')       # ohne Ziel
    await melden(r, c.execute('SELECT * FROM egress_laeufe WHERE egress_id = ?', (eid,)).fetchone())
    return {'ok': True, 'id': eid}
