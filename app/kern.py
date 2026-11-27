"""Gemeinsame Einstellungen und Hilfen (Konfiguration, Begrenzer, Sitzungen, Passwörter, Zeit)."""
import hashlib
import ipaddress
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, Response

from . import db, geteilt

TEST = os.environ.get('AGORA_TEST') == '1'
GRENZEN_AUS = os.environ.get('AGORA_GRENZEN_AUS') == '1'      # nur für E2E-Tests
COOKIE = os.environ.get('AGORA_COOKIE', '__Host-agora')
COOKIE_SECURE = os.environ.get('AGORA_COOKIE_SECURE', '1') == '1'
GEHEIM = (os.environ.get('AGORA_GEHEIM') or secrets.token_hex(32)).encode()
SEITE = os.environ.get('AGORA_SEITE', 'https://agora.slxt.dev').rstrip('/')
WEB = os.environ.get('AGORA_WEB', '/srv/web/dist')
LIVEKIT_URL = os.environ.get('AGORA_LIVEKIT_URL', 'wss://agora.slxt.dev')
MAX_TEILNEHMER = int(os.environ.get('AGORA_MAX_TEILNEHMER', '50'))
VERSION = '1.0.0'
SITZUNG_S = 30 * 86400

ph = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)
DUMMY_HASH = ph.hash('dummy-passwort-für-gleiche-rechenzeit')


def jetzt():
    return int(time.time())


def iso(t):
    """Unix-Zeit → ISO 8601 in UTC (oder None)."""
    if t is None:
        return None
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def h(s):
    return hashlib.sha256(s.encode()).hexdigest()


def fehler(code, text):
    raise HTTPException(code, text)


def client_ip(req: Request):
    """IP für Begrenzer und Sperren. Nur cloudflared erreicht die App (kein Host-Port) → CF-Connecting-IP ist vertrauenswürdig.
    IPv6 als /64-Netz: Ein Anschluss bekommt meist eine ganze /64 (2^64 Adressen) – je Adresse zu zählen hieße, dass
    jeder Begrenzer (Login, Passcode, Freigabe-Passwort …) mit wechselnden Adressen umgangen werden kann."""
    return ip_gruppe(req.headers.get('cf-connecting-ip') or (req.client.host if req.client else '?'))


def ip_gruppe(wert):
    try:
        a = ipaddress.ip_address(wert.strip().split('%')[0])
    except ValueError:
        return wert[:64]
    if a.version == 6:
        if a.ipv4_mapped:
            return str(a.ipv4_mapped)
        return str(ipaddress.ip_network(f'{a}/64', strict=False))
    return str(a)


def livekit_herkunft():
    """Für die CSP: Herkunft der LiveKit-URL(s) als ws(s) und http(s) (der Client prüft vorab per HTTP).
    Mit mehreren Knoten (AGORA_LIVEKIT_KNOTEN, ADR 0007) die Signal-URLs aller Knoten."""
    from .livekit_dienst import knoten
    teile = []
    for url in dict.fromkeys(k['signal'] for k in knoten()):
        u = urlparse(url)
        if not u.netloc:
            continue
        ws = 'wss' if u.scheme in ('wss', 'https') else 'ws'
        teile.append(f'{ws}://{u.netloc} {"https" if ws == "wss" else "http"}://{u.netloc}')
    return ' '.join(teile)


class Begrenzer:
    """Gleitendes Fenster je Schlüssel. Mit AGORA_REDIS_URL gemeinsam für alle Worker/Instanzen (app/geteilt.py, atomar
    per Lua), sonst – und als Rückfall, wenn Redis nicht antwortet – im Arbeitsspeicher dieses Workers."""
    def __init__(self):
        self.d = defaultdict(deque)
        self.lock = threading.Lock()

    def ok(self, schluessel, anzahl, sekunden):
        if GRENZEN_AUS:
            return True
        x = geteilt.grenze(schluessel, anzahl, sekunden)
        if x is not None:
            return x
        t = time.monotonic()
        with self.lock:
            q = self.d[schluessel]
            while q and t - q[0] > sekunden:
                q.popleft()
            if len(q) >= anzahl:
                return False
            q.append(t)
            if len(self.d) > 50000:          # Speicher begrenzen
                for k in list(self.d)[:10000]:
                    del self.d[k]
            return True

    def voll(self, schluessel, anzahl, sekunden):
        """Ist das Fenster schon voll? Zählt selbst nicht mit (für „nur Fehlversuche zählen“)."""
        if GRENZEN_AUS:
            return False
        x = geteilt.grenze(schluessel, anzahl, sekunden, nur_pruefen=True)
        if x is not None:
            return x
        t = time.monotonic()
        with self.lock:
            q = self.d.get(schluessel)
            return bool(q) and sum(1 for x in q if t - x <= sekunden) >= anzahl

    def pruefen(self, schluessel, anzahl, sekunden):
        if not self.ok(schluessel, anzahl, sekunden):
            fehler(429, 'Zu viele Versuche – bitte kurz warten.')


grenze = Begrenzer()


async def json_body(req: Request, maximal=16384):
    """JSON-Objekt aus dem Body. Größe zuerst am Content-Length, dann beim Lesen prüfen: nie mehr als `maximal` Bytes im
    Speicher (sonst könnte jeder ohne Anmeldung mit ein paar 100-MB-Anfragen den Speicher füllen)."""
    laenge = req.headers.get('content-length')
    if laenge is not None:
        if not laenge.isdigit():
            fehler(400, 'Ungültige Anfrage')
        if int(laenge) > maximal:
            fehler(413, 'Anfrage zu groß')
    roh = bytearray()
    async for teil in req.stream():
        roh += teil
        if len(roh) > maximal:
            fehler(413, 'Anfrage zu groß')
    try:
        d = json.loads(bytes(roh) or b'{}')
    except (ValueError, RecursionError):
        fehler(400, 'Ungültige Anfrage')
    if not isinstance(d, dict):
        fehler(400, 'Ungültige Anfrage')
    return d


def text_feld(d, feld, maximal, pflicht=False, standard=''):
    """String-Feld säubern (Steuerzeichen raus, trimmen, Länge prüfen)."""
    v = d.get(feld, standard)
    if v is None:
        v = ''
    if not isinstance(v, str):
        fehler(400, 'Ungültige Anfrage')
    v = ''.join(ch for ch in v if ch == '\n' or ord(ch) >= 32).strip()
    if pflicht and not v:
        fehler(400, 'Ungültige Anfrage')
    if len(v) > maximal:
        fehler(400, f'Text zu lang (höchstens {maximal} Zeichen).')
    return v


def benutzer_aus_sitzung(req: Request, pflicht=True):
    t = req.cookies.get(COOKIE)
    row = None
    if t and len(t) < 100:
        row = db.verbindung().execute(
            'SELECT b.* FROM sitzungen s JOIN benutzer b ON b.id = s.benutzer_id WHERE s.token_hash = ? AND s.gueltig_bis > ?',
            (h(t), jetzt())).fetchone()
    if row and (not row['bestaetigt'] or row['gesperrt']):      # gesperrte Konten (Admin) gelten als abgemeldet
        row = None
    if pflicht and not row:
        fehler(401, 'Bitte anmelden.')
    if row:
        geteilt.gesehen('konten', row['id'])      # Präsenz: „gerade aktiv“ (Admin-Übersicht, Metriken)
    return row


def sitzung_setzen(resp: Response, benutzer_id):
    t = secrets.token_urlsafe(32)
    db.verbindung().execute('INSERT INTO sitzungen VALUES (?, ?, ?, ?)', (h(t), benutzer_id, jetzt(), jetzt() + SITZUNG_S))
    resp.set_cookie(COOKIE, t, max_age=SITZUNG_S, httponly=True, secure=COOKIE_SECURE, samesite='lax', path='/')


def cookie_loeschen(resp: Response):
    resp.delete_cookie(COOKIE, path='/', secure=COOKIE_SECURE, httponly=True, samesite='lax')


def hash_pw(pw):
    return ph.hash(pw)


def pw_ok(hash_, pw):
    try:
        return ph.verify(hash_ or DUMMY_HASH, pw) and bool(hash_)
    except (VerificationError, InvalidHashError):
        return False
