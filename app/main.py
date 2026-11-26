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
from . import erinnerungen  # Erinnerungs-Mails
from . import verlauf       # Meeting-Verlauf
from . import dauerchat     # Dauer-Chat im persönlichen Raum
from . import zwei_faktor
from . import melden
from . import admin
from . import sicherheit
from . import qualitaet
from . import metriken
from . import whiteboard     # Zusammenarbeit: Whiteboard
from . import notizen        # Zusammenarbeit: gemeinsame Notizen
from . import fragen         # Zusammenarbeit: Q&A
from . import dateien        # Zusammenarbeit: Dateien teilen
from . import webinar       # Webinar-Modus + Registrierung (/w/<code>)
from . import egress        # Server-Aufnahme + Livestream (LiveKit Egress, nur mit AGORA_EGRESS=1)
from . import untertitel    # Live-Untertitel, Transkript, Transkript aus Aufnahme (Whisper)
from . import anmerkungen     # Anmerkungen auf geteilten Bildschirmen (Recht)
from . import api_schluessel  # Öffentliche API: persönliche API-Schlüssel
from . import api_v1          # Öffentliche API /api/v1 (Bearer)
from . import webhooks        # Webhooks (Zustellung im eigenen Hintergrund-Thread)
from . import einbetten       # Einbetten: /e/<code> mit frame-ancestors je Raum, /embed.js
from . import orgs            # Teams / Organisationen (Räume, Vorlagen, Aufnahmen, Schlüssel einer Organisation)
from . import status          # Öffentliche Status-Seite /status, /api/status (eigener Prüf-Thread jede Minute)
from . import android         # Android-TWA: /.well-known/assetlinks.json (nur mit AGORA_ANDROID_SHA256)
from . import push            # Push-Benachrichtigungen (Web Push, opt-in je Gerät)
from . import dolmetsch       # Dolmetscher-Kanäle (Spur dolmetsch:<sprache>, Rechte vom Server)
from . import livekit_dienst as lk
from . import texte          # Sprachen: Fehlermeldungen und Mails Deutsch/Englisch (ADR 0006)
from .kern import LIVEKIT_URL, MAX_TEILNEHMER, SEITE, TEST, VERSION, WEB, jetzt, livekit_herkunft

db.einrichten()      # idempotent; gleich beim Laden, damit auch Tests ohne Lifespan eine Datenbank haben


@asynccontextmanager
async def lebenszeit(_app):
    if not TEST:
        threading.Thread(target=hintergrund, daemon=True).start()
        webhooks.starten()
        status.starten()
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lebenszeit)


@app.exception_handler(StarletteHTTPException)
async def http_fehler(req, e: StarletteHTTPException):
    text = e.detail
    if not isinstance(text, str) or text in ('Not Found', 'Method Not Allowed'):
        text = {404: 'Nicht gefunden', 405: 'Methode nicht erlaubt'}.get(e.status_code, 'Fehler')
    text = texte.fehler_text(text, texte.aus_header(req.headers.get('accept-language')))
    return JSONResponse({'fehler': text}, status_code=e.status_code, headers=getattr(e, 'headers', None))


@app.exception_handler(RequestValidationError)
async def eingabe_fehler(req, e):
    return JSONResponse({'fehler': texte.fehler_text('Ungültige Anfrage', texte.aus_header(req.headers.get('accept-language')))},
                        status_code=400)


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
if SEITE.startswith('https://'):
    # HSTS auch von der App selbst (nicht nur, falls in Cloudflare eingeschaltet): nach dem ersten Besuch nie mehr http://.
    # Ohne includeSubDomains/preload – andere Subdomains von slxt.dev entscheiden selbst.
    SICHERHEIT['Strict-Transport-Security'] = 'max-age=31536000'


@app.middleware('http')
async def schutz(req: Request, call_next):
    sprache = texte.aus_header(req.headers.get('accept-language'))
    texte.SPRACHE.set(sprache)          # für Termin-Texte, .ics, Kalender-Links in dieser Anfrage
    # Zustandsändernde Aufrufe nur mit eigenem Header (CSRF: fremde Seiten können ihn ohne CORS nicht setzen).
    # Ausnahme /api/v1/: dort zählt nur der Bearer-Schlüssel, nie ein Cookie – CSRF greift dort nicht.
    if req.method in ('POST', 'PUT', 'PATCH', 'DELETE') and req.headers.get('x-agora') != '1' and not req.url.path.startswith('/api/v1/'):
        r = JSONResponse({'fehler': texte.fehler_text('Anfrage abgelehnt', sprache)}, status_code=403)
    else:
        r = await call_next(req)
    for k, v in SICHERHEIT.items():
        r.headers.setdefault(k, v)
    p = req.url.path
    if p.startswith('/assets/') or (p.startswith('/vendor/') and r.status_code == 200):   # Hash bzw. Version im Pfad
        r.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    elif not p.startswith('/icons/'):
        r.headers['Cache-Control'] = 'no-store'
    einbetten.kopf_anpassen(req, r)      # /e/<code>: frame-ancestors = erlaubte Herkünfte; /embed.js: CORP cross-origin
    return r


app.include_router(konto.router)
app.include_router(raeume.router)
app.include_router(meeting.router)
app.include_router(aufnahmen.router)
app.include_router(umfragen.router)
app.include_router(breakout.router)
app.include_router(kalender.router)
app.include_router(vorlagen.router)
app.include_router(erinnerungen.router)
app.include_router(verlauf.router)
app.include_router(dauerchat.router)
app.include_router(zwei_faktor.router)
app.include_router(melden.router)
app.include_router(admin.router)
app.include_router(sicherheit.router)
app.include_router(qualitaet.router)
app.include_router(metriken.router)
app.include_router(whiteboard.router)
app.include_router(notizen.router)
app.include_router(fragen.router)
app.include_router(dateien.router)
app.include_router(webinar.router)
app.include_router(egress.router)
app.include_router(untertitel.router)
app.include_router(anmerkungen.router)
app.include_router(api_schluessel.router)
app.include_router(api_v1.router)
app.include_router(webhooks.router)
app.include_router(einbetten.router)
app.include_router(orgs.router)
app.include_router(status.router)
app.include_router(android.router)
app.include_router(push.router)
app.include_router(dolmetsch.router)
metriken.einrichten(app)      # Antwortzeiten + Anmelde-/Registrierungszähler für /api/metriken


@app.api_route('/api/gesund', methods=['GET', 'HEAD'])
async def gesund(req: Request, tief: int = 0):
    if tief:      # Datenbank schreibt, LiveKit, Platte, Hintergrund – nur lokal oder mit Token (sonst 404)
        return await metriken.gesund_tief(req)
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
    c.execute("DELETE FROM protokoll WHERE zeit < ? AND ereignis NOT LIKE 'Admin:%'", (t - 30 * 86400,))   # Admin: 365 Tage
    admin.aufraeumen(t)
    aufnahmen.aufraeumen(t)
    raeume.aufraeumen(t)
    verlauf.aufraeumen(t)
    dauerchat.aufraeumen()
    melden.aufraeumen(t)
    dateien.aufraeumen(t)      # abgelaufene und verwaiste Dateien aus Meetings
    webinar.aufraeumen(t)
    untertitel.aufraeumen(t)
    api_schluessel.aufraeumen(t)   # abgelaufene API-Schlüssel (30 Tage nach Ablauf)
    webhooks.aufraeumen(t)         # Zustell-Protokoll 14 Tage, verwaiste Warteschlange/Anwesenheit
    orgs.aufraeumen(t)             # abgelaufene Einladungen in Organisationen (7 Tage)
    status.aufraeumen(t)           # Status-Fenster 90 Tage, Meldungen 365 Tage nach dem Ende
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
            erinnerungen.pruefen()                        # Erinnerungs-Mails (idempotent je Termin)
            push.erinnerungen_pruefen()                   # Erinnerungen als Push (nur Konten mit Geräten)
            asyncio.run(egress.alle_abgleichen())         # Server-Aufnahmen in die Bibliothek, Livestream-Fehler
            if time.time() - letztes_aufraeumen > 300:
                aufraeumen()
                letztes_aufraeumen = time.time()
            if time.time() - letzte_sicherung > 6 * 3600:
                db.sichern()
                letzte_sicherung = time.time()
            metriken.hintergrund_gelaufen()
        except Exception as e:   # noqa: BLE001
            metriken.zaehlen('agora_hintergrund_fehler_total')
            print(f'hintergrund-fehler {type(e).__name__}: {e}', flush=True)
        time.sleep(60)


# ---------------------------------------------------------------- Seiten (Vite-Build); fehlt er (Tests), gibt es nur die API
SEITEN = {'/': 'index.html', '/konto': 'konto.html', '/passwort': 'passwort.html', '/status': 'status.html'}


def seite(datei):
    return lambda: FileResponse(os.path.join(WEB, datei), media_type='text/html')


if os.path.isdir(WEB):
    for pfad, datei in SEITEN.items():
        app.add_api_route(pfad, seite(datei), methods=['GET', 'HEAD'])
    app.add_api_route('/r/{code}', (lambda code: FileResponse(os.path.join(WEB, 'raum.html'), media_type='text/html')), methods=['GET', 'HEAD'])
    app.add_api_route('/w/{code}', (lambda code: FileResponse(os.path.join(WEB, 'webinar.html'), media_type='text/html')), methods=['GET', 'HEAD'])
    app.add_api_route('/a/{token}', (lambda token: FileResponse(os.path.join(WEB, 'a.html'), media_type='text/html')), methods=['GET', 'HEAD'])
    app.mount('/', StaticFiles(directory=WEB, html=False), name='web')
