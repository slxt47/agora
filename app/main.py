"""Agora – Videokonferenzen (wie Zoom) mit LiveKit als Medienserver.

Die App verwaltet Konten, Räume, Warteraum, Host-Rechte, Chat und Aufnahmen; Audio/Video laufen direkt zwischen
Browser und LiveKit. Die App gibt dafür kurzlebige LiveKit-Token aus und steuert Räume über die LiveKit-Server-API.
Ein uvicorn-Worker (Begrenzer und Cache liegen im Arbeitsspeicher).
"""
import asyncio
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from . import aufnahmen, db, konto, mail, meeting, raeume
from . import umfragen
from . import breakout
from . import kalender      # Kalender-Abo (iCal-Feed)
from . import vorlagen      # Meeting-Vorlagen
from . import livekit_dienst as lk
from .kern import LIVEKIT_URL, MAX_TEILNEHMER, TEST, VERSION, WEB, jetzt, livekit_herkunft

db.einrichten()      # idempotent; gleich beim Laden, damit auch Tests ohne Lifespan eine Datenbank haben


@asynccontextmanager
async def lebenszeit(_app):
    if not TEST:
        threading.Thread(target=hintergrund, daemon=True).start()
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lebenszeit)


@app.exception_handler(StarletteHTTPException)
async def http_fehler(req, e: StarletteHTTPException):
    text = e.detail
    if not isinstance(text, str) or text in ('Not Found', 'Method Not Allowed'):
        text = {404: 'Nicht gefunden', 405: 'Methode nicht erlaubt'}.get(e.status_code, 'Fehler')
    return JSONResponse({'fehler': text}, status_code=e.status_code, headers=getattr(e, 'headers', None))


@app.exception_handler(RequestValidationError)
async def eingabe_fehler(req, e):
    return JSONResponse({'fehler': 'Ungültige Anfrage'}, status_code=400)


SICHERHEIT = {
    # 'wasm-unsafe-eval': nur WebAssembly kompilieren (MediaPipe für Video-Effekte, Dateien selbst gehostet unter /vendor/),
    # kein eval() für JavaScript. worker-src blob: für LiveKit (E2EE/Audio-Worklets).
    'Content-Security-Policy': "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data: blob:; "
                               f"media-src 'self' blob:; connect-src 'self' {livekit_herkunft()}; worker-src 'self' blob:; "
                               "manifest-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
                               "frame-ancestors 'none'",
    'Referrer-Policy': 'no-referrer',
    'Permissions-Policy': 'camera=(self), microphone=(self), display-capture=(self), fullscreen=(self), picture-in-picture=(self), '
                          'geolocation=(), payment=(), usb=()',
    'X-Content-Type-Options': 'nosniff',
    'Cross-Origin-Opener-Policy': 'same-origin',
    'Cross-Origin-Resource-Policy': 'same-origin',
    'X-Frame-Options': 'DENY',
}


@app.middleware('http')
async def schutz(req: Request, call_next):
    # Zustandsändernde Aufrufe nur mit eigenem Header (CSRF: fremde Seiten können ihn ohne CORS nicht setzen)
    if req.method in ('POST', 'PUT', 'PATCH', 'DELETE') and req.headers.get('x-agora') != '1':
        r = JSONResponse({'fehler': 'Anfrage abgelehnt'}, status_code=403)
    else:
        r = await call_next(req)
    for k, v in SICHERHEIT.items():
        r.headers.setdefault(k, v)
    p = req.url.path
    if p.startswith('/assets/') or (p.startswith('/vendor/') and r.status_code == 200):   # Hash bzw. Version im Pfad
        r.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    elif not p.startswith('/icons/'):
        r.headers['Cache-Control'] = 'no-store'
    return r


app.include_router(konto.router)
app.include_router(raeume.router)
app.include_router(meeting.router)
app.include_router(aufnahmen.router)
app.include_router(umfragen.router)
app.include_router(breakout.router)
app.include_router(kalender.router)
app.include_router(vorlagen.router)


@app.api_route('/api/gesund', methods=['GET', 'HEAD'])
def gesund():
    db.verbindung().execute('SELECT 1')
    return {'ok': True}


@app.get('/api/einstellungen')
def einstellungen():
    return {'livekit_url': LIVEKIT_URL, 'max_teilnehmer': MAX_TEILNEHMER, 'version': VERSION}


# ---------------------------------------------------------------- Test-Hilfe (nur AGORA_TEST=1)
if TEST:
    @app.get('/api/test/posteingang')
    def test_posteingang():
        return {'mails': [{'an': m[0], 'betreff': m[1], 'text': m[2]} for m in mail.POSTEINGANG_TEST]}

    @app.get('/api/test/livekit')
    def test_livekit():
        return {'fake': lk.FAKE, 'aufrufe': [[n, {k: v for k, v in kw.items()}] for n, kw in lk.AUFRUFE]}


# ---------------------------------------------------------------- Hintergrund: Leerlauf, Ablauf, Aufräumen, Sicherung
def aufraeumen():
    t = jetzt()
    c = db.verbindung()
    c.execute('DELETE FROM sitzungen WHERE gueltig_bis < ?', (t,))
    c.execute('DELETE FROM token WHERE gueltig_bis < ?', (t,))
    c.execute('DELETE FROM benutzer WHERE bestaetigt = 0 AND erstellt < ?', (t - 48 * 3600,))
    c.execute('DELETE FROM protokoll WHERE zeit < ?', (t - 30 * 86400,))
    aufnahmen.aufraeumen(t)
    raeume.aufraeumen(t)
    tmp = os.path.join(db.DATEN, 'tmp')
    for n in os.listdir(tmp):                # abgebrochene Uploads
        p = os.path.join(tmp, n)
        if os.path.getmtime(p) < time.time() - 6 * 3600:
            os.remove(p)


def hintergrund():
    letzte_sicherung = letztes_aufraeumen = 0
    while True:
        try:
            asyncio.run(raeume.leere_pruefen())
            asyncio.run(breakout.faellige_pruefen())     # Timer/Countdown, falls die geplante Aufgabe verloren ging
            if time.time() - letztes_aufraeumen > 300:
                aufraeumen()
                letztes_aufraeumen = time.time()
            if time.time() - letzte_sicherung > 6 * 3600:
                db.sichern()
                letzte_sicherung = time.time()
        except Exception as e:   # noqa: BLE001
            print(f'hintergrund-fehler {type(e).__name__}: {e}', flush=True)
        time.sleep(60)


# ---------------------------------------------------------------- Seiten (Vite-Build); fehlt er (Tests), gibt es nur die API
SEITEN = {'/': 'index.html', '/konto': 'konto.html', '/passwort': 'passwort.html'}


def seite(datei):
    return lambda: FileResponse(os.path.join(WEB, datei), media_type='text/html')


if os.path.isdir(WEB):
    for pfad, datei in SEITEN.items():
        app.add_api_route(pfad, seite(datei), methods=['GET', 'HEAD'])
    app.add_api_route('/r/{code}', (lambda code: FileResponse(os.path.join(WEB, 'raum.html'), media_type='text/html')), methods=['GET', 'HEAD'])
    app.add_api_route('/a/{token}', (lambda token: FileResponse(os.path.join(WEB, 'a.html'), media_type='text/html')), methods=['GET', 'HEAD'])
    app.mount('/', StaticFiles(directory=WEB, html=False), name='web')
