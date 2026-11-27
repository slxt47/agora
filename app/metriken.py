"""Metriken für das Monitoring (Prometheus-Textformat) und die tiefe Gesundheitsprüfung.

- GET /api/metriken: Zähler (Anmeldungen, Registrierungen, Mails, LiveKit-Fehler, Anfragen), Histogramm der
  Antwortzeit je Routengruppe und Momentwerte (Meetings, Teilnehmende, Konten, Aufnahmen, Platte, Hintergrund).
- GET /api/gesund?tief=1: Datenbank schreibt, LiveKit antwortet, genug Platz unter daten/, Hintergrund läuft
  → 200 oder 503 mit Einzelergebnissen.

Beides nur von 127.0.0.1 ohne Cloudflare-Header (der Tunnel kommt auch von 127.0.0.1, setzt aber immer
CF-Connecting-IP) oder mit `Authorization: Bearer <AGORA_METRIKEN_TOKEN>`. Sonst 404, als gäbe es die Seiten nicht.
Alle Zähler liegen im Arbeitsspeicher (ein uvicorn-Worker) und beginnen beim Start bei 0.
"""
import hmac
import os
import shutil
import threading
import time
from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import db
from . import livekit_dienst as lk
from .kern import TEST, VERSION, fehler

router = APIRouter()
TOKEN = os.environ.get('AGORA_METRIKEN_TOKEN', '')
PLATTE_MIN = float(os.environ.get('AGORA_PLATTE_MIN_GB', '2')) * 1024 ** 3
HINTERGRUND_MAX_S = 180            # die Schleife läuft jede Minute
START = time.time()
CF_HEADER = ('cf-connecting-ip', 'cf-ray', 'x-forwarded-for', 'cf-ipcountry')

_lock = threading.Lock()
ZAEHLER = defaultdict(float)       # (name, ((label, wert), …)) → Wert
GRENZEN = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
HISTO = {}                         # gruppe → {'b': [Anzahl je Grenze], 'summe', 'anzahl'}
ZUSTAND = {'hintergrund': 0.0}     # Unix-Zeit des letzten erfolgreichen Hintergrund-Laufs
HILFE = {
    'agora_anmeldungen_total': ('counter', 'Anmeldeversuche nach Ergebnis (ok, fehlgeschlagen, begrenzt)'),
    'agora_registrierungen_total': ('counter', 'Angenommene Registrierungen (inkl. schon vorhandener Adressen)'),
    'agora_mails_total': ('counter', 'Verschickte Mails nach Ergebnis (ok, fehler)'),
    'agora_livekit_fehler_total': ('counter', 'Fehlgeschlagene Aufrufe der LiveKit-Server-API je Aufruf'),
    'agora_hintergrund_fehler_total': ('counter', 'Fehler in der Hintergrund-Schleife'),
    'agora_http_anfragen_total': ('counter', 'HTTP-Anfragen je Routengruppe und Statusklasse'),
    'agora_fehlerberichte_total': ('counter', 'Erfasste Fehler (Fehler-Monitoring) je Quelle (browser, server)'),
}


# ---------------------------------------------------------------- Erfassen (aus anderen Modulen aufgerufen)
def zaehlen(name, wert=1, **labels):
    with _lock:
        ZAEHLER[(name, tuple(sorted(labels.items())))] += wert


def hintergrund_gelaufen():
    ZUSTAND['hintergrund'] = time.time()


def gruppe(pfad):
    """Routengruppe für das Histogramm (feste, kleine Menge – keine Codes oder IDs als Label)."""
    if pfad.startswith('/api/konto'):
        return 'konto'
    if pfad.startswith('/api/raeume'):
        return 'meeting' if pfad.count('/') >= 4 else 'raeume'
    for praefix, name in (('/api/aufnahmen', 'aufnahmen'), ('/api/freigaben', 'freigaben'), ('/api/gesund', 'betrieb'),
                          ('/api/metriken', 'betrieb'), ('/api/', 'api'), ('/assets/', 'statisch'), ('/vendor/', 'statisch'),
                          ('/icons/', 'statisch')):
        if pfad.startswith(praefix):
            return name
    return 'seiten'


def _anfrage(methode, pfad, status, dauer):
    g = gruppe(pfad)
    with _lock:
        h = HISTO.setdefault(g, {'b': [0] * len(GRENZEN), 'summe': 0.0, 'anzahl': 0})
        for i, grenze in enumerate(GRENZEN):
            if dauer <= grenze:
                h['b'][i] += 1
        h['summe'] += dauer
        h['anzahl'] += 1
        ZAEHLER[('agora_http_anfragen_total', (('gruppe', g), ('status', f'{status // 100}xx')))] += 1
    if methode == 'POST' and pfad == '/api/konto/anmelden':
        zaehlen('agora_anmeldungen_total', ergebnis='ok' if status == 200 else ('begrenzt' if status == 429 else 'fehlgeschlagen'))
    elif methode == 'POST' and pfad == '/api/konto/registrieren' and status == 200:
        zaehlen('agora_registrierungen_total')


def einrichten(app):
    """Middleware für Antwortzeiten und Anmelde-/Registrierungszähler (am Pfad und Status abgelesen)."""
    @app.middleware('http')
    async def messen(req: Request, call_next):
        t0 = time.perf_counter()
        status = 500
        try:
            r = await call_next(req)
            status = r.status_code
            return r
        finally:
            _anfrage(req.method, req.url.path, status, time.perf_counter() - t0)


# ---------------------------------------------------------------- Zugriff
def erlaubt(req: Request):
    auth = req.headers.get('authorization', '')
    if TOKEN and auth.startswith('Bearer ') and hmac.compare_digest(auth[7:].encode(), TOKEN.encode()):
        return True
    host = req.client.host if req.client else ''
    return host in ('127.0.0.1', '::1') and not any(k in req.headers for k in CF_HEADER)


def pruefen(req: Request):
    if not erlaubt(req):
        fehler(404, 'Nicht gefunden')


# ---------------------------------------------------------------- Momentwerte
async def livekit_lage():
    """(erreichbar, ms, verbundene Teilnehmende, Räume) – eine ListRooms-Abfrage."""
    t0 = time.perf_counter()
    if lk.FAKE:
        return True, 0.0, sum(len(v) for v in lk.FAKE_TEILNEHMER.values()), len(lk.FAKE_TEILNEHMER)
    try:
        async with lk._client() as c:
            r = await c.room.list_rooms(lk.api.ListRoomsRequest())
        return True, (time.perf_counter() - t0) * 1000, sum(x.num_participants for x in r.rooms), len(r.rooms)
    except Exception as e:   # noqa: BLE001
        zaehlen('agora_livekit_fehler_total', aufruf='list_rooms')
        print(f'livekit-fehler list_rooms: {type(e).__name__}', flush=True)
        return False, (time.perf_counter() - t0) * 1000, None, None


def db_schreibtest():
    """Echter Schreibzugriff (Sperre holen, Zeile neu schreiben) – zeigt volle Platte, Rechte- und Sperrprobleme."""
    t0 = time.perf_counter()
    c = db.verbindung()
    c.execute('BEGIN IMMEDIATE')
    try:
        c.execute('UPDATE schema_version SET v = v')
        c.execute('COMMIT')
    except Exception:
        c.execute('ROLLBACK')
        raise
    return (time.perf_counter() - t0) * 1000


def db_werte():
    c = db.verbindung()
    eins = lambda sql: c.execute(sql).fetchone()[0] or 0   # noqa: E731
    return {
        'meetings': eins('SELECT COUNT(*) FROM raeume WHERE aktiv = 1'),
        'teilnahmen': eins('SELECT COUNT(*) FROM teilnahmen t JOIN raeume r ON r.id = t.raum_id AND r.sitzung = t.sitzung WHERE r.aktiv = 1'),
        'wartend': eins("SELECT COUNT(*) FROM anfragen WHERE status = 'wartet'"),
        'konten_bestaetigt': eins('SELECT COUNT(*) FROM benutzer WHERE bestaetigt = 1'),
        'konten_offen': eins('SELECT COUNT(*) FROM benutzer WHERE bestaetigt = 0'),
        'aufnahmen': eins('SELECT COUNT(*) FROM aufnahmen'),
        'aufnahmen_bytes': eins('SELECT SUM(groesse) FROM aufnahmen'),
    }


def _zeile(name, wert, **labels):
    lab = ','.join(f'{k}="{str(v)}"' for k, v in labels.items())
    zahl = str(int(wert)) if float(wert).is_integer() else repr(round(float(wert), 6))   # volle Genauigkeit (Bytes, Zeitstempel)
    return f'{name}{{{lab}}} {zahl}' if lab else f'{name} {zahl}'


@router.get('/api/metriken')
async def metriken(req: Request):
    pruefen(req)
    w = await run_in_threadpool(db_werte)
    erreichbar, ms, verbunden, lk_raeume = await livekit_lage()
    platte = shutil.disk_usage(db.DATEN)
    try:
        db_bytes = os.path.getsize(db.DB)
    except OSError:
        db_bytes = 0
    out = []

    def gauge(name, hilfe, werte):
        out.append(f'# HELP {name} {hilfe}')
        out.append(f'# TYPE {name} gauge')
        for wert, labels in werte:
            out.append(_zeile(name, wert, **labels))

    gauge('agora_info', 'Version der App', [(1, {'version': VERSION})])
    gauge('agora_start_zeitstempel_sekunden', 'Startzeit der App (Unix)', [(START, {})])
    gauge('agora_meetings_aktiv', 'Laufende Meetings', [(w['meetings'], {})])
    gauge('agora_teilnahmen_aktiv', 'Teilnahmen in laufenden Meetings (auch kurz Abwesende)', [(w['teilnahmen'], {})])
    gauge('agora_warteraum', 'Wartende Personen in Warteräumen', [(w['wartend'], {})])
    gauge('agora_livekit_erreichbar', 'LiveKit-Server-API antwortet (1/0)', [(int(erreichbar), {})])
    gauge('agora_livekit_antwort_ms', 'Antwortzeit der LiveKit-Server-API', [(round(ms, 1), {})])
    if verbunden is not None:
        gauge('agora_teilnehmer_verbunden', 'Bei LiveKit verbundene Teilnehmende (alle Räume)', [(verbunden, {})])
        gauge('agora_livekit_raeume', 'Räume bei LiveKit (inkl. Breakout-Räume)', [(lk_raeume, {})])
    gauge('agora_konten', 'Konten nach Status', [(w['konten_bestaetigt'], {'status': 'bestaetigt'}), (w['konten_offen'], {'status': 'unbestaetigt'})])
    gauge('agora_aufnahmen', 'Gespeicherte Aufnahmen', [(w['aufnahmen'], {})])
    gauge('agora_aufnahmen_bytes', 'Größe aller Aufnahmen in Bytes', [(w['aufnahmen_bytes'], {})])
    gauge('agora_db_bytes', 'Größe der SQLite-Datenbank in Bytes', [(db_bytes, {})])
    gauge('agora_daten_frei_bytes', 'Freier Platz unter daten/', [(platte.free, {})])
    gauge('agora_daten_gesamt_bytes', 'Größe des Dateisystems unter daten/', [(platte.total, {})])
    gauge('agora_hintergrund_letzter_lauf_zeitstempel_sekunden', 'Letzter erfolgreicher Lauf der Hintergrund-Schleife (Unix, 0 = noch nie)',
          [(ZUSTAND['hintergrund'], {})])
    with _lock:
        zaehler = dict(ZAEHLER)
        histo = {g: {'b': list(h['b']), 'summe': h['summe'], 'anzahl': h['anzahl']} for g, h in HISTO.items()}
    # Zähler: alle bekannten Namen immer ausgeben (auch mit 0), damit rate() von Anfang an funktioniert
    grund = {('agora_anmeldungen_total', (('ergebnis', e),)): 0 for e in ('ok', 'fehlgeschlagen', 'begrenzt')}
    grund |= {('agora_mails_total', (('ergebnis', e),)): 0 for e in ('ok', 'fehler')}
    grund |= {('agora_registrierungen_total', ()): 0, ('agora_hintergrund_fehler_total', ()): 0}
    grund |= zaehler
    for name in sorted({n for n, _ in grund} | {'agora_livekit_fehler_total'}):
        art, hilfe = HILFE[name]
        out.append(f'# HELP {name} {hilfe}')
        out.append(f'# TYPE {name} {art}')
        for (n, labels), wert in sorted(grund.items()):
            if n == name:
                out.append(_zeile(name, wert, **dict(labels)))
    out.append('# HELP agora_http_dauer_sekunden Antwortzeit je Routengruppe')
    out.append('# TYPE agora_http_dauer_sekunden histogram')
    for g in sorted(histo):
        h = histo[g]
        for grenze, n in zip(GRENZEN, h['b']):
            out.append(_zeile('agora_http_dauer_sekunden_bucket', n, gruppe=g, le=f'{grenze:g}'))
        out.append(_zeile('agora_http_dauer_sekunden_bucket', h['anzahl'], gruppe=g, le='+Inf'))
        out.append(_zeile('agora_http_dauer_sekunden_sum', round(h['summe'], 6), gruppe=g))
        out.append(_zeile('agora_http_dauer_sekunden_count', h['anzahl'], gruppe=g))
    return Response('\n'.join(out) + '\n', media_type='text/plain; version=0.0.4; charset=utf-8')


async def gesund_tief(req: Request):
    """Für /api/gesund?tief=1 (main.py)."""
    pruefen(req)
    p = {}
    try:
        p['datenbank'] = {'ok': True, 'ms': round(await run_in_threadpool(db_schreibtest), 1)}
    except Exception as e:   # noqa: BLE001
        p['datenbank'] = {'ok': False, 'fehler': type(e).__name__}
    erreichbar, ms, verbunden, _r = await livekit_lage()
    p['livekit'] = {'ok': erreichbar, 'ms': round(ms, 1), 'verbunden': verbunden}
    platte = shutil.disk_usage(db.DATEN)
    p['platte'] = {'ok': platte.free >= PLATTE_MIN and platte.free / platte.total >= 0.05, 'frei_bytes': platte.free,
                   'frei_prozent': round(platte.free / platte.total * 100, 1)}
    letzter = ZUSTAND['hintergrund']
    alter = round(time.time() - letzter) if letzter else None
    if TEST and not letzter:
        ok = True                        # Testmodus: keine Hintergrund-Schleife
    else:
        ok = (alter is not None and alter <= HINTERGRUND_MAX_S) or (not letzter and time.time() - START < HINTERGRUND_MAX_S)
    p['hintergrund'] = {'ok': ok, 'alter_s': alter}
    alles = all(x['ok'] for x in p.values())
    return JSONResponse({'ok': alles, 'version': VERSION, 'pruefungen': p}, status_code=200 if alles else 503)
