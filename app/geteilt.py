"""Geteilter Zustand für mehrere App-Worker/Instanzen – nur mit AGORA_REDIS_URL, sonst alles wie bisher im Arbeitsspeicher.

- Begrenzer (kern.grenze): gleitendes Fenster je Schlüssel als Sorted Set, atomar in einem Lua-Skript (Zeit vom
  Redis-Server, damit alle Worker dieselbe Uhr haben). Schlüssel nur als SHA-256 (keine IPs/E-Mail-Adressen im Klartext).
- Präsenz: wer gerade aktiv ist (angemeldete Konten, Teilnahmen im Meeting) – Sorted Set je Bereich, Wert = letzter
  Zeitpunkt. Für die Admin-Übersicht und /api/metriken (`agora_online{bereich}`).
- Redis weg (Zeitlimit 2 s) → Rückfall auf den Arbeitsspeicher dieses Workers (Zähler `agora_redis_fehler_total`,
  eine Logzeile je Minute). Die Begrenzer greifen dann je Worker weiter, nur nicht mehr gemeinsam.
"""
import hashlib
import os
import secrets
import threading
import time

from .redis_klein import Redis, RedisFehler

PRAEFIX = 'agora:'
_lock = threading.Lock()
_zustand = {'redis': None, 'url': None, 'gemeldet': 0.0}

GRENZE_LUA = """
local t = redis.call('TIME')
local jetzt = tonumber(t[1]) * 1000000 + tonumber(t[2])
local fenster = tonumber(ARGV[1]) * 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', jetzt - fenster)
local n = redis.call('ZCARD', KEYS[1])
if ARGV[3] == 'voll' then
  if n >= tonumber(ARGV[2]) then return 1 end
  return 0
end
if n >= tonumber(ARGV[2]) then return 0 end
redis.call('ZADD', KEYS[1], jetzt, ARGV[4])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[1]) * 1000 + 1000)
return 1
"""

PRAESENZ_LUA = """
local t = redis.call('TIME')
redis.call('ZADD', KEYS[1], tonumber(t[1]), ARGV[1])
redis.call('EXPIRE', KEYS[1], 3600)
return 1
"""

ZAEHLEN_LUA = """
local t = redis.call('TIME')
local jetzt = tonumber(t[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', jetzt - 3600)
return redis.call('ZCOUNT', KEYS[1], jetzt - tonumber(ARGV[1]), '+inf')
"""


def redis():
    """Redis-Verbindung aus AGORA_REDIS_URL oder None (bei jedem Aufruf gelesen – Tests stellen um)."""
    url = os.environ.get('AGORA_REDIS_URL', '').strip()
    if not url:
        return None
    with _lock:
        if _zustand['url'] != url:
            _zustand['redis'], _zustand['url'] = Redis(url), url
        return _zustand['redis']


def aktiv():
    return redis() is not None


def _fehler(e):
    from .metriken import zaehlen
    zaehlen('agora_redis_fehler_total')
    if time.time() - _zustand['gemeldet'] > 60:
        _zustand['gemeldet'] = time.time()
        print(f'redis-fehler (Rückfall auf Arbeitsspeicher): {e}', flush=True)


def _key(art, schluessel):
    return PRAEFIX + art + ':' + hashlib.sha256(str(schluessel).encode()).hexdigest()[:40]


def grenze(schluessel, anzahl, sekunden, nur_pruefen=False):
    """True/False (Redis) – oder None, wenn Redis nicht eingerichtet/erreichbar ist (→ Arbeitsspeicher)."""
    r = redis()
    if r is None:
        return None
    try:
        x = r.skript(GRENZE_LUA, [_key('g', schluessel)], [sekunden, anzahl, 'voll' if nur_pruefen else 'ok',
                                                            f'{time.time_ns()}-{secrets.token_hex(4)}'])
        return bool(x) if nur_pruefen else x == 1
    except RedisFehler as e:
        _fehler(e)
        return None


# ---------------------------------------------------------------- Präsenz
BEREICHE = ('konten', 'meeting')
_gesehen = {}                 # (bereich, wer) → letzte Zeit (Arbeitsspeicher; mit Redis nur als Drossel)
DROSSEL_S = 20


def gesehen(bereich, wer):
    t = time.time()
    k = (bereich, str(wer))
    if t - _gesehen.get(k, 0) < DROSSEL_S:
        return
    _gesehen[k] = t
    if len(_gesehen) > 50000:
        grenze_zeit = t - 3600
        for x in [x for x, v in _gesehen.items() if v < grenze_zeit][:20000] or list(_gesehen)[:10000]:
            _gesehen.pop(x, None)
    r = redis()
    if r is not None:
        try:
            r.skript(PRAESENZ_LUA, [PRAEFIX + 'p:' + bereich], [hashlib.sha256(str(wer).encode()).hexdigest()[:24]])
        except RedisFehler as e:
            _fehler(e)


def anzahl(bereich, sekunden=300):
    """Wie viele verschiedene waren in den letzten `sekunden` aktiv? (Redis: alle Worker; sonst dieser Worker)"""
    r = redis()
    if r is not None:
        try:
            return int(r.skript(ZAEHLEN_LUA, [PRAEFIX + 'p:' + bereich], [sekunden]))
        except RedisFehler as e:
            _fehler(e)
    grenze_zeit = time.time() - sekunden
    return sum(1 for (b, _w), v in list(_gesehen.items()) if b == bereich and v >= grenze_zeit)
