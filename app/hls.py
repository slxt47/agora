"""Öffentliche Übertragung (HLS) eines Webinars für sehr viele Zuschauer – nur mit AGORA_EGRESS=1 und AGORA_HLS=1.

Host oder Co-Host eines Webinars startet „Öffentliche Übertragung (HLS)“: je Qualitätsstufe ein LiveKit-Egress
(RoomComposite, Layout galerie/sprecher) mit SegmentedFileOutput (HLS, MPEG-TS-Segmente zu 4 s). 720p immer, 360p auf
Wunsch – jede Stufe ist ein eigener Egress (eigener Chrome + Encoder, siehe docs/BETRIEB.md), weil ein Egress nur eine
Kodierung kennt. Der Egress schreibt nach AGORA_HLS_ZIEL/<code>/<kennung>/ (seine Sicht, Standard /hls); derselbe
Ordner ist bei der App AGORA_HLS_AUSGABE (Standard /daten/hls).

Auslieferung durch die App unter /hls/<code>/<kennung>/<datei>:
- `index.m3u8` (Master-Playlist, von der App aus den Stufen erzeugt), `<stufe>-live.m3u8` (gleitendes Fenster vom
  Egress), `<stufe>_<nr>.ts` (Segmente). Andere Namen gibt es nicht (feste Muster, kein Pfad aus der Anfrage).
- `<kennung>` ist je Übertragung zufällig (128 bit) und damit eine Fähigkeits-URL: Wer sie kennt, sieht zu. Deshalb
  dürfen Cloudflare & Co. Segmente lange und Playlists kurz zwischenspeichern (`public`) – auch bei „nur Angemeldete“.
  Öffentlich: /api/hls/<code> nennt die Quelle jedem. Nur Angemeldete: die Quelle gibt es erst gegen den persönlichen
  Anmelde-Token des Webinars (POST /api/hls/<code>/zugang).
- Zuschauerseite /h/<code> (ohne WebRTC): Safari spielt HLS selbst, sonst hls.js (selbst gehostet, fest gepinnt).
  Zuschauerzahl ungefähr: jede offene Seite meldet sich alle 30 s (zufällige ID, Präsenz aus app/geteilt.py – mit
  AGORA_REDIS_URL über alle Worker, sonst im Arbeitsspeicher; 75 s gültig).

Beenden: Egress stoppen, Dateien bleiben noch 1 h (Nachzügler, Zwischenspeicher), dann löscht der Hintergrund den
Ordner. Ende des Meetings beendet die Übertragung ebenso. Verwaiste Ordner (ohne Eintrag) verschwinden nach 1 Tag.
"""
import json
import os
import re
import secrets
import shutil
import time

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from . import db, egress, geteilt, meeting, raeume, webinar
from . import livekit_dienst as lk
from .kern import client_ip, fehler, grenze, h, iso, jetzt, json_body

router = APIRouter()
AN = egress.AN and os.environ.get('AGORA_HLS') == '1'
AUSGABE = os.environ.get('AGORA_HLS_AUSGABE') or os.path.join(db.DATEN, 'hls')
ZIEL = (os.environ.get('AGORA_HLS_ZIEL') or '/hls').rstrip('/')
SEGMENT_S = 4                     # Segmentlänge → Verzögerung ≈ 3 Segmente + Kodierung ≈ 10–20 s
NACHLAUF_S = 3600                 # nach dem Ende: Dateien noch 1 h ausliefern, dann löschen
VERWAIST_S = 86400
ZUSCHAUER_S = 75                  # so lange zählt eine Meldung der Zuschauerseite (sie meldet sich alle 30 s)
ZUGAENGE = ('oeffentlich', 'anmeldung')
# Stufe → Kodierung und Angaben für die Master-Playlist (BANDWIDTH = Spitze aus Video + Audio)
STUFEN = {
    '720p': {'breite': 1280, 'hoehe': 720, 'bandbreite': 3_300_000},      # Preset H264_720P_30 (3 Mbit/s + Opus→AAC 128k)
    '360p': {'breite': 640, 'hoehe': 360, 'bandbreite': 1_000_000},       # eigene Kodierung 900 kbit/s + 96k
}
LAEUFT = ('startet', 'laeuft')
KENNUNG_RE = re.compile(r'^[A-Za-z0-9_-]{22}$')
# Nur diese Dateinamen werden ausgeliefert (der Name kommt nie als Pfad auf die Platte, sondern nur nach diesem Muster)
DATEI_RE = re.compile(r'^(?:index\.m3u8|(720p|360p)-live\.m3u8|(720p|360p)_\d{1,8}\.ts)$')
TYPEN = {'.m3u8': 'application/vnd.apple.mpegurl', '.ts': 'video/mp2t'}


def an():
    return AN


def _ordner(code, kennung):
    return os.path.join(AUSGABE, code, kennung)


def _aktiv(r):
    return db.verbindung().execute(f'SELECT * FROM hls_laeufe WHERE raum_id = ? AND status IN {LAEUFT} ORDER BY id DESC LIMIT 1',
                                   (r['id'],)).fetchone()


def _sichtbar(code):
    """Letzte Übertragung des Raums, deren Dateien noch da sind (läuft oder im Nachlauf)."""
    return db.verbindung().execute('SELECT * FROM hls_laeufe WHERE raum_code = ? AND (status IN (?, ?) OR loeschen_ab > ?) '
                                   'ORDER BY id DESC LIMIT 1', (code, *LAEUFT, jetzt())).fetchone()


def zuschauer(code):
    """Ungefähre Zahl offener Zuschauerseiten (Meldungen der letzten 75 s)."""
    return geteilt.anzahl(f'hls:{code}', ZUSCHAUER_S)


def lauf_json(e, mit_zahl=True):
    if not e:
        return None
    return {'id': e['id'], 'status': e['status'], 'fehler': e['fehler'], 'von_name': e['von_name'], 'zugang': e['zugang'],
            'stufen': json.loads(e['stufen']), 'layout': 'sprecher' if e['layout'] == 'speaker' else 'galerie',
            'gestartet': iso(e['gestartet']), 'link': f'{raeume.SEITE}/h/{e["raum_code"]}',
            **({'zuschauer': zuschauer(e['raum_code'])} if mit_zahl else {})}


# ---------------------------------------------------------------- Haken: Metadaten, Ende, Zustand für Host/Co-Host
def _meta(r):
    if not AN:
        return {}
    e = _aktiv(r)
    return {'hls': {'von_name': e['von_name']}} if e else {}


def _beim_ende(r):
    """Meeting vorbei: LiveKit löscht den Raum, die Egress enden von selbst. Ab jetzt läuft der Nachlauf (1 h)."""
    db.verbindung().execute(f"UPDATE hls_laeufe SET status = 'beendet', beendet = ?, loeschen_ab = ? WHERE raum_id = ? AND status IN {LAEUFT}",
                            (jetzt(), jetzt() + NACHLAUF_S, r['id']))


async def _zustand(r, t):
    if not AN or t['rolle'] not in ('host', 'cohost'):
        return {}
    e = _aktiv(r)
    if e:
        e = await abgleichen(e)
    else:
        e = db.verbindung().execute('SELECT * FROM hls_laeufe WHERE raum_id = ? AND sitzung = ? ORDER BY id DESC LIMIT 1',
                                    (r['id'], r['sitzung'])).fetchone()
    return {'hls_verfuegbar': webinar.ist_webinar(r) and not r['e2ee'], 'hls': lauf_json(e),
            'hls_anmeldung': bool(webinar.einstellungen(r)['registrierung'])}


raeume.META_EXTRA.append(_meta)
raeume.BEIM_ENDE.append(_beim_ende)
egress.ZUSTAND_EXTRA.append(_zustand)


# ---------------------------------------------------------------- Abgleich mit LiveKit, Aufräumen
async def _stoppen_alle(e):
    fehlertexte = []
    for eid in json.loads(e['egress']).values():
        _ok, f = await lk.egress_stoppen(eid)
        if f:
            fehlertexte.append(f)
    return fehlertexte


async def abgleichen(e):
    """Status aller Egress einer laufenden Übertragung abfragen. Fehler einer Stufe beendet alle (sonst sähen Zuschauer
    je nach Stufe Verschiedenes)."""
    if e['status'] not in LAEUFT:
        return e
    infos = [await lk.egress_info(eid) for eid in json.loads(e['egress']).values()]
    if any(i is None for i in infos):
        return e                                       # LiveKit gerade nicht erreichbar → beim nächsten Mal
    stati = {i['status'] for i in infos}
    c = db.verbindung()
    if 'fehler' in stati:
        text = next((i.get('fehler') for i in infos if i['status'] == 'fehler' and i.get('fehler')), None) or 'Egress-Fehler'
        await _stoppen_alle(e)
        c.execute("UPDATE hls_laeufe SET status = 'fehler', fehler = ?, beendet = ?, loeschen_ab = ? WHERE id = ?",
                  (text, jetzt(), jetzt() + NACHLAUF_S, e['id']))
    elif stati & {'fertig', 'endet'}:                  # von außen beendet (z. B. Raum weg)
        c.execute("UPDATE hls_laeufe SET status = 'beendet', beendet = ?, loeschen_ab = ? WHERE id = ?",
                  (jetzt(), jetzt() + NACHLAUF_S, e['id']))
    elif stati == {'laeuft'} and e['status'] != 'laeuft':
        c.execute("UPDATE hls_laeufe SET status = 'laeuft' WHERE id = ?", (e['id'],))
    else:
        return e
    neu = c.execute('SELECT * FROM hls_laeufe WHERE id = ?', (e['id'],)).fetchone()
    r = c.execute('SELECT * FROM raeume WHERE id = ?', (e['raum_id'],)).fetchone() if e['raum_id'] else None
    if r and r['aktiv']:
        await melden(r, neu)
    return neu


async def alle_abgleichen():
    if not AN:
        return
    for e in db.verbindung().execute(f'SELECT * FROM hls_laeufe WHERE status IN {LAEUFT}').fetchall():
        await abgleichen(e)


def _sicher_loeschen(pfad):
    """Nur Ordner unterhalb von AUSGABE löschen (Schutz gegen Symlinks/verdrehte Pfade)."""
    basis = os.path.realpath(AUSGABE)
    echt = os.path.realpath(pfad)
    if echt != basis and echt.startswith(basis + os.sep) and os.path.isdir(echt) and not os.path.islink(pfad):
        shutil.rmtree(echt, ignore_errors=True)


def aufraeumen(t=None):
    """Abgelaufene Übertragungen (Nachlauf vorbei) von der Platte, Zeilen nach 7 Tagen, verwaiste Ordner nach 1 Tag."""
    t = t or jetzt()
    c = db.verbindung()
    for e in c.execute(f'SELECT * FROM hls_laeufe WHERE status NOT IN {LAEUFT} AND loeschen_ab < ? AND geloescht = 0', (t,)).fetchall():
        _sicher_loeschen(_ordner(e['raum_code'], e['kennung']))
        c.execute('UPDATE hls_laeufe SET geloescht = 1 WHERE id = ?', (e['id'],))
    c.execute('DELETE FROM hls_laeufe WHERE geloescht = 1 AND beendet < ?', (t - 7 * 86400,))
    if not os.path.isdir(AUSGABE):
        return
    bekannt = {(x['raum_code'], x['kennung']) for x in c.execute('SELECT raum_code, kennung FROM hls_laeufe WHERE geloescht = 0').fetchall()}
    for code in os.listdir(AUSGABE):
        p = os.path.join(AUSGABE, code)
        if not os.path.isdir(p) or os.path.islink(p):
            continue
        for k in os.listdir(p):
            q = os.path.join(p, k)
            if (code, k) not in bekannt and os.path.getmtime(q) < time.time() - VERWAIST_S:
                _sicher_loeschen(q)
        if not os.listdir(p) and os.path.getmtime(p) < time.time() - 3600:
            os.rmdir(p)


async def melden(r, e):
    """Host/Co-Hosts: Zustand (Ereignis wie bei der Server-Aufnahme) + Metadaten (LIVE-Hinweis für alle)."""
    mods = [x['identity'] for x in raeume.teilnahmen(r, ('host', 'cohost'))]
    await lk.senden(r['code'], {'typ': 'egress', 'art': 'hls', 'status': e['status'], 'fehler': e['fehler']}, an=mods)
    await lk.metadaten(r['code'], raeume.meta(raeume.neu_laden(r)))


# ---------------------------------------------------------------- API: Host/Co-Host im Meeting
@router.post('/api/raeume/{code}/egress/hls')
async def steuern(code: str, req: Request):
    if not AN:
        fehler(404, 'Die öffentliche Übertragung (HLS) ist nicht eingerichtet.')
    r, t = meeting.steuernd(req, code)
    d = await json_body(req)
    c = db.verbindung()
    if d.get('aktion') == 'stopp':
        e = _aktiv(r)
        if not e:
            fehler(404, 'Es läuft keine öffentliche Übertragung.')
        fehlertexte = await _stoppen_alle(e)
        if fehlertexte:
            fehler(502, f'Die Übertragung ließ sich nicht stoppen: {fehlertexte[0]}')
        c.execute("UPDATE hls_laeufe SET status = 'beendet', beendet = ?, loeschen_ab = ? WHERE id = ?", (jetzt(), jetzt() + NACHLAUF_S, e['id']))
        meeting.protokoll(t, r, 'Öffentliche Übertragung (HLS) beendet')
        await melden(r, c.execute('SELECT * FROM hls_laeufe WHERE id = ?', (e['id'],)).fetchone())
        return {'ok': True}
    if d.get('aktion') != 'start':
        fehler(400, 'Aktion: start oder stopp.')
    if not webinar.ist_webinar(r):
        fehler(400, 'Die öffentliche Übertragung gibt es nur für Webinare.')
    if r['e2ee']:
        fehler(409, 'In Ende-zu-Ende-verschlüsselten Meetings geht das nur im Browser – der Server kann Bild und Ton nicht lesen.')
    grenze.pruefen('egress:' + t['identity'], 10, 600)
    zugang = d.get('zugang', 'oeffentlich')
    if zugang not in ZUGAENGE:
        fehler(400, 'Zugang: oeffentlich oder anmeldung.')
    if zugang == 'anmeldung' and not webinar.einstellungen(r)['registrierung']:
        fehler(400, 'Für „nur Angemeldete“ muss die Anmeldung zum Webinar eingeschaltet sein.')
    stufen = d.get('stufen', ['720p'])
    if not isinstance(stufen, list) or not stufen or any(s not in STUFEN for s in stufen) or len(set(stufen)) != len(stufen) or '720p' not in stufen:
        fehler(400, 'Stufen: 720p, optional zusätzlich 360p.')
    stufen = [s for s in STUFEN if s in stufen]
    layout = egress._layout(d)
    if _aktiv(r):
        fehler(409, 'Die öffentliche Übertragung läuft schon.')
    kennung = secrets.token_urlsafe(16)
    ordner = _ordner(r['code'], kennung)
    os.makedirs(ordner, exist_ok=True)
    for p in (AUSGABE, os.path.dirname(ordner), ordner):
        try:
            os.chmod(p, 0o2770)                    # Egress schreibt als 1001:1000 (Gruppe der App)
        except OSError:
            pass
    ids = {}
    for s in stufen:
        eid, f = await lk.egress_hls(r['code'], f'{ZIEL}/{r["code"]}/{kennung}/{s}', s, layout, SEGMENT_S)
        if f or not eid:
            for x in ids.values():
                await lk.egress_stoppen(x)
            # Ordner nicht sofort löschen: ein schon gestarteter Egress schreibt evtl. noch kurz und legte ihn sonst
            # selbst (als 1001, 0755) neu an – den könnte die App nicht mehr aufräumen. Das Aufräumen kommt in 5 min.
            c.execute('INSERT INTO hls_laeufe (raum_id, sitzung, raum_code, kennung, egress, stufen, zugang, layout, von_name, status, '
                      "fehler, gestartet, beendet, loeschen_ab) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'fehler', ?, ?, ?, ?)",
                      (r['id'], r['sitzung'], r['code'], kennung, json.dumps(ids), json.dumps(stufen), zugang, layout, t['name'],
                       f or 'unbekannt', jetzt(), jetzt(), jetzt() + 300))
            fehler(502, f'Die Übertragung ließ sich nicht starten: {f or "unbekannt"}')
        ids[s] = eid
    rid = c.execute('INSERT INTO hls_laeufe (raum_id, sitzung, raum_code, kennung, egress, stufen, zugang, layout, von_name, status, gestartet) '
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'startet', ?)",
                    (r['id'], r['sitzung'], r['code'], kennung, json.dumps(ids), json.dumps(stufen), zugang, layout, t['name'], jetzt())).lastrowid
    meeting.protokoll(t, r, f'Öffentliche Übertragung (HLS) gestartet ({zugang}, {"+".join(stufen)})')
    await melden(r, c.execute('SELECT * FROM hls_laeufe WHERE id = ?', (rid,)).fetchone())
    return {'ok': True, 'link': f'{raeume.SEITE}/h/{r["code"]}'}


# ---------------------------------------------------------------- Öffentlich: Zuschauerseite /h/<code>
def _verfuegbar():
    if not AN:
        fehler(404, 'Nicht gefunden')


def _quelle(e):
    return f'/hls/{e["raum_code"]}/{e["kennung"]}/index.m3u8'


@router.get('/api/hls/{code}')
def info(code: str, req: Request):
    _verfuegbar()
    grenze.pruefen('hls-info:' + client_ip(req), 120, 60)
    r = raeume.raum_laden(code)
    if not webinar.ist_webinar(r):
        fehler(404, 'Nicht gefunden')
    e = _sichtbar(code)
    laeuft = bool(e and e['status'] in LAEUFT)
    out = {'code': r['code'], 'titel': r['titel'], 'laeuft': laeuft, 'beendet': bool(e and not laeuft),
           'verzoegerung_s': [10, 20], 'segment_s': SEGMENT_S}
    if laeuft:
        out.update(zugang=e['zugang'], stufen=json.loads(e['stufen']), seit=iso(e['gestartet']))
        if e['zugang'] == 'oeffentlich':
            out['quelle'] = _quelle(e)
        else:
            out['anmelde_link'] = webinar.anmelde_link(r['code'])
    return out


@router.post('/api/hls/{code}/zugang')
async def zugang_holen(code: str, req: Request):
    """Nur Angemeldete: persönlicher Token aus der Bestätigungsmail → Quelle der laufenden Übertragung."""
    _verfuegbar()
    grenze.pruefen('hls-zugang:' + client_ip(req), 20, 600)
    r = raeume.raum_laden(code)
    d = await json_body(req)
    e = _sichtbar(code)
    if not webinar.ist_webinar(r) or not e or e['status'] not in LAEUFT:
        fehler(404, 'Gerade läuft keine Übertragung.')
    if e['zugang'] == 'anmeldung':
        tok = webinar.token_lesen(d, 'anmeldung')
        a = db.verbindung().execute('SELECT id, beigetreten FROM webinar_anmeldungen WHERE raum_id = ? AND token_hash = ?',
                                    (r['id'], h(tok))).fetchone() if tok else None
        if not a:
            fehler(403, 'Für diese Übertragung ist eine Anmeldung nötig. Nutze den Link aus deiner Bestätigungsmail.')
        if not a['beigetreten']:
            db.verbindung().execute('UPDATE webinar_anmeldungen SET beigetreten = ? WHERE id = ?', (jetzt(), a['id']))
    return {'quelle': _quelle(e)}


@router.post('/api/hls/{code}/da')
async def da(code: str, req: Request):
    """Zuschauerseite meldet sich (alle 30 s) – nur fürs Zählen, ohne IP oder Namen."""
    _verfuegbar()
    if not grenze.ok('hls-da:' + client_ip(req), 20, 60):
        return {'ok': False}
    raeume.raum_laden(code)
    d = await json_body(req, 512)
    kennung = d.get('id')
    if not isinstance(kennung, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,40}', kennung):
        fehler(400, 'Ungültige Anfrage')
    e = _sichtbar(code)
    if e and e['status'] in LAEUFT:
        geteilt.gesehen(f'hls:{code}', kennung)
    return {'ok': True}


def _cache(name):
    if name == 'index.m3u8':
        return 'public, max-age=5'
    if name.endswith('.m3u8'):                     # gleitendes Fenster: höchstens eine halbe Segmentlänge alt
        return f'public, max-age={SEGMENT_S // 2}'
    return 'public, max-age=86400, immutable'      # Segmente ändern sich nie (zufällige Kennung im Pfad)


def master(e):
    zeilen = ['#EXTM3U', '#EXT-X-VERSION:3', '#EXT-X-INDEPENDENT-SEGMENTS']
    for s in json.loads(e['stufen']):
        st = STUFEN[s]
        zeilen += [f'#EXT-X-STREAM-INF:BANDWIDTH={st["bandbreite"]},RESOLUTION={st["breite"]}x{st["hoehe"]},CODECS="avc1.64001f,mp4a.40.2"',
                   f'{s}-live.m3u8']
    return '\n'.join(zeilen) + '\n'


@router.api_route('/hls/{code}/{kennung}/{datei}', methods=['GET', 'HEAD'])
def datei(code: str, kennung: str, datei: str):
    """Playlist oder Segment. Code, Kennung und Dateiname nur nach festen Mustern; die Kennung muss zu einer
    Übertragung dieses Raums gehören, deren Dateien noch nicht gelöscht sind."""
    nicht = Response('Nicht gefunden', status_code=404, media_type='text/plain', headers={'Cache-Control': 'no-store'})
    if not AN or not raeume.CODE_RE.match(code) or not KENNUNG_RE.match(kennung) or not DATEI_RE.match(datei):
        return nicht
    e = db.verbindung().execute('SELECT * FROM hls_laeufe WHERE raum_code = ? AND kennung = ? AND geloescht = 0', (code, kennung)).fetchone()
    if not e:
        return nicht
    kopf = {'Cache-Control': _cache(datei), 'X-Content-Type-Options': 'nosniff'}
    if datei == 'index.m3u8':
        return Response(master(e), media_type=TYPEN['.m3u8'], headers=kopf)
    m = DATEI_RE.match(datei)
    if (m.group(1) or m.group(2)) not in json.loads(e['stufen']):
        return nicht
    pfad = os.path.join(_ordner(code, kennung), datei)
    basis = os.path.realpath(_ordner(code, kennung))
    if os.path.dirname(os.path.realpath(pfad)) != basis or not os.path.isfile(pfad):
        return nicht
    return FileResponse(pfad, media_type=TYPEN[os.path.splitext(datei)[1]], headers=kopf)
