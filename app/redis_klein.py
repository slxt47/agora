"""Kleiner Redis-Client (RESP2, nur Standardbibliothek) für Begrenzer und Präsenz – nur wenn AGORA_REDIS_URL gesetzt ist.

Warum nicht redis-py? Agora braucht AUTH, SELECT, EVALSHA/EVAL und ein paar Befehle; ~100 Zeilen ohne neue Abhängigkeit.
Eine Verbindung je Thread (die App ruft aus dem Event-Loop und aus Threadpool-Threads). Zeitlimit 2 s: Ist Redis weg,
fallen Begrenzer und Präsenz auf den Arbeitsspeicher des Workers zurück (kern.Begrenzer), statt Anfragen hängen zu lassen.

URL: redis://[:passwort@]host[:port][/db] oder rediss://… (TLS); Benutzer: redis://benutzer:passwort@host/db.
"""
import hashlib
import socket
import ssl
import threading
from urllib.parse import unquote, urlsplit


class RedisFehler(RuntimeError):
    pass


class Redis:
    def __init__(self, url, timeout=2.0):
        u = urlsplit(url)
        if u.scheme not in ('redis', 'rediss') or not u.hostname:
            raise ValueError('AGORA_REDIS_URL muss redis://[:passwort@]host[:port][/db] sein')
        self.host, self.port = u.hostname, u.port or 6379
        self.tls = u.scheme == 'rediss'
        self.benutzer = unquote(u.username) if u.username else None
        self.passwort = unquote(u.password) if u.password else None
        pfad = (u.path or '/').strip('/')
        self.db = int(pfad) if pfad.isdigit() else 0
        self.timeout = timeout
        self._lokal = threading.local()
        self._skripte = {}

    # ------------------------------------------------------------ Verbindung
    def _neu(self):
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        if self.tls:
            s = ssl.create_default_context().wrap_socket(s, server_hostname=self.host)
        s.settimeout(self.timeout)
        self._lokal.s, self._lokal.f = s, s.makefile('rb')
        if self.passwort:
            self._roh(*(['AUTH', self.benutzer, self.passwort] if self.benutzer else ['AUTH', self.passwort]))
        if self.db:
            self._roh('SELECT', str(self.db))

    def schliessen(self):
        s = getattr(self._lokal, 's', None)
        if s:
            try:
                s.close()
            except OSError:
                pass
        self._lokal.s = self._lokal.f = None

    @staticmethod
    def _kodieren(args):
        teile = [f'*{len(args)}\r\n'.encode()]
        for a in args:
            b = a if isinstance(a, bytes) else str(a).encode()
            teile.append(b'$%d\r\n%s\r\n' % (len(b), b))
        return b''.join(teile)

    def _lesen(self):
        z = self._lokal.f.readline()
        if not z.endswith(b'\r\n'):
            raise ConnectionError('Redis: Verbindung beendet')
        art, rest = z[:1], z[1:-2]
        if art == b'+':
            return rest.decode()
        if art == b'-':
            return RedisFehler(rest.decode('utf-8', 'replace'))
        if art == b':':
            return int(rest)
        if art == b'$':
            n = int(rest)
            if n < 0:
                return None
            daten = self._lokal.f.read(n + 2)
            return daten[:-2]
        if art == b'*':
            n = int(rest)
            return None if n < 0 else [self._lesen() for _ in range(n)]
        raise ConnectionError(f'Redis: unbekannte Antwort {z[:20]!r}')

    def _roh(self, *args):
        self._lokal.s.sendall(self._kodieren(args))
        a = self._lesen()
        if isinstance(a, RedisFehler):
            raise a
        return a

    def befehl(self, *args):
        """Befehl schicken; bei einer abgerissenen Verbindung einmal neu verbinden. Redis-Fehler → RedisFehler."""
        for versuch in (1, 2):
            try:
                if getattr(self._lokal, 's', None) is None:
                    self._neu()
                return self._roh(*args)
            except RedisFehler:
                raise
            except (OSError, ConnectionError, ValueError) as e:
                self.schliessen()
                if versuch == 2:
                    raise RedisFehler(f'Redis nicht erreichbar: {type(e).__name__}') from e
        return None

    def skript(self, lua, keys, args):
        """EVALSHA (bei NOSCRIPT einmal EVAL, das lädt es)."""
        sha = self._skripte.get(lua) or hashlib.sha1(lua.encode()).hexdigest()
        self._skripte[lua] = sha
        try:
            return self.befehl('EVALSHA', sha, len(keys), *keys, *args)
        except RedisFehler as e:
            if 'NOSCRIPT' not in str(e):
                raise
            return self.befehl('EVAL', lua, len(keys), *keys, *args)
