"""Speicher für große Dateien: Aufnahmen (`aufnahmen/<id>`), Whisper-Transkripte (`aufnahmen/<id>.vtt`) und Dateien aus
Meetings (`dateien/<id>`). Zusammenfassungen, Kapitel und Meeting-Transkripte liegen in der Datenbank.

- Standard: lokal unter `daten/` – genau die bisherigen Pfade, Produktion ändert sich ohne Einstellung nicht.
- S3-kompatibel (AWS, Hetzner, MinIO …), sobald AGORA_S3_ENDPOINT, AGORA_S3_BUCKET, AGORA_S3_KEY und AGORA_S3_SECRET
  gesetzt sind (AGORA_S3_REGION, Standard us-east-1). Optional: AGORA_S3_SSE (AES256 | aws:kms) + AGORA_S3_KMS_KEY,
  AGORA_S3_PFADSTIL=0 (virtuelle Hosts statt Pfad-Stil), AGORA_S3_PRAEFIX (z. B. „agora/“). Nur ein Teil gesetzt → Fehler beim Start.
  Uploads landen wie bisher erst in daten/tmp (Größe, Kontingent, Virenprüfung), dann gestreamt per PUT. Auslieferung
  gestreamt durch die App (kein Umleiten auf S3 – das Bucket bleibt privat, Kopfzeilen wie CSP/nosniff bleiben), mit Range.
- Migration lokal → S3: `python -m app.speicher_migrieren` (siehe docs/BETRIEB.md).
"""
import os
import re
import secrets
import shutil
from contextlib import contextmanager
from urllib.parse import quote

import anyio
from fastapi.responses import FileResponse
from starlette.responses import Response

from . import db
from .s3 import S3Client

SCHLUESSEL_RE = re.compile(r'^(aufnahmen|dateien)/[A-Za-z0-9_-]{1,64}(\.vtt)?$')
BLOCK = 256 * 1024


def _pruefen(k):
    if not SCHLUESSEL_RE.match(k or ''):
        raise ValueError(f'ungültiger Speicher-Schlüssel {k!r}')
    return k


def disposition(art, name):
    q = quote(name)
    return f"{art}; filename*=utf-8''{q}" if q != name else f'{art}; filename="{name}"'


class Lokal:
    art = 'lokal'

    def pfad(self, k):
        return os.path.join(db.DATEN, _pruefen(k))

    def ablegen(self, k, quelle):
        """Fertige Datei übernehmen (umbenennen; anderes Dateisystem, z. B. Egress-Ausgabe: kopieren + löschen)."""
        ziel = self.pfad(k)
        os.makedirs(os.path.dirname(ziel), exist_ok=True)
        shutil.move(quelle, ziel)

    def schreiben(self, k, daten):
        ziel = self.pfad(k)
        os.makedirs(os.path.dirname(ziel), exist_ok=True)
        tmp = f'{ziel}.neu-{secrets.token_hex(4)}'
        with open(tmp, 'wb') as f:
            f.write(daten)
        os.replace(tmp, ziel)

    def lesen(self, k, maximal=20 * 1024 * 1024):
        try:
            with open(self.pfad(k), 'rb') as f:
                return f.read(maximal)
        except FileNotFoundError:
            return None

    def existiert(self, k):
        return os.path.isfile(self.pfad(k))

    def loeschen(self, k):
        try:
            os.remove(self.pfad(k))
        except FileNotFoundError:
            pass

    def liste(self, ordner):
        """→ [(name, mtime)] im Ordner (aufnahmen | dateien)."""
        o = os.path.join(db.DATEN, ordner)
        if not os.path.isdir(o):
            return []
        aus = []
        for n in os.listdir(o):
            try:
                aus.append((n, os.path.getmtime(os.path.join(o, n))))
            except FileNotFoundError:
                pass
        return aus

    @contextmanager
    def lokal(self, k):
        yield self.pfad(k)

    def antwort(self, k, typ, name, art='inline', kopf=None):
        r = FileResponse(self.pfad(k), media_type=typ, filename=name, content_disposition_type=art)
        r.headers.update(kopf or {})
        return r


def bereich_lesen(wert, groesse):
    """Range-Kopf → (start, ende) inklusive, None (ganze Datei) oder 'ungueltig' (416). Mehrere Bereiche → ganze Datei."""
    if not wert:
        return None
    m = re.fullmatch(r'\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*', wert)
    if not m:
        return None if ',' in wert else 'ungueltig'
    a, b = m.groups()
    if not a and not b:
        return 'ungueltig'
    if not a:                                   # die letzten n Bytes
        n = int(b)
        if n == 0:
            return 'ungueltig'
        return max(0, groesse - n), groesse - 1
    start = int(a)
    ende = min(int(b), groesse - 1) if b else groesse - 1
    if start >= groesse or (b and int(b) < start):
        return 'ungueltig'
    return start, ende


class S3Antwort(Response):
    """Objekt aus S3 gestreamt ausliefern (GET/HEAD, Range wie FileResponse), Kopfzeilen der App bleiben."""
    def __init__(self, speicher, k, typ, name, art, kopf=None):
        super().__init__(content=None, media_type=typ)
        self.s, self.k = speicher, k
        self.headers['content-disposition'] = disposition(art, name)
        self.headers['accept-ranges'] = 'bytes'
        self.headers.update(kopf or {})

    async def __call__(self, scope, receive, send):
        kopf_roh = {k.decode('latin-1').lower(): v.decode('latin-1') for k, v in scope.get('headers', [])}
        info = await anyio.to_thread.run_sync(self.s.client.head, self.s.key(self.k))
        if info is None:
            r = Response('{"fehler":"Nicht gefunden"}', status_code=404, media_type='application/json')
            return await r(scope, receive, send)
        groesse = info['groesse']
        bereich = bereich_lesen(kopf_roh.get('range'), groesse) if groesse else None
        kopf = [(k, v) for k, v in self.raw_headers if k not in (b'content-length',)]
        if bereich == 'ungueltig':
            await send({'type': 'http.response.start', 'status': 416,
                        'headers': kopf + [(b'content-range', f'bytes */{groesse}'.encode()), (b'content-length', b'0')]})
            return await send({'type': 'http.response.body', 'body': b''})
        status, laenge = (206, bereich[1] - bereich[0] + 1) if bereich else (200, groesse)
        kopf += [(b'content-length', str(laenge).encode())]
        if info['geaendert']:
            kopf.append((b'last-modified', info['geaendert'].encode('latin-1', 'replace')))
        if bereich:
            kopf.append((b'content-range', f'bytes {bereich[0]}-{bereich[1]}/{groesse}'.encode()))
        if scope.get('method') == 'HEAD':
            await send({'type': 'http.response.start', 'status': status, 'headers': kopf})
            return await send({'type': 'http.response.body', 'body': b''})
        x = await anyio.to_thread.run_sync(self.s.client.get, self.s.key(self.k), bereich)
        if x is None:
            r = Response('{"fehler":"Nicht gefunden"}', status_code=404, media_type='application/json')
            return await r(scope, receive, send)
        verbindung, antwort = x
        try:
            await send({'type': 'http.response.start', 'status': status, 'headers': kopf})
            rest = laenge
            while rest > 0:
                teil = await anyio.to_thread.run_sync(antwort.read, min(BLOCK, rest))
                if not teil:
                    break
                rest -= len(teil)
                await send({'type': 'http.response.body', 'body': teil, 'more_body': rest > 0})
            if rest > 0:
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
        finally:
            verbindung.close()


class S3:
    art = 's3'

    def __init__(self, client, praefix=''):
        self.client, self.praefix = client, praefix

    def key(self, k):
        return self.praefix + _pruefen(k)

    def ablegen(self, k, quelle):
        typ = 'text/vtt' if k.endswith('.vtt') else 'application/octet-stream'
        self.client.put_datei(self.key(k), quelle, os.path.getsize(quelle), typ)
        os.remove(quelle)

    def schreiben(self, k, daten):
        self.client.put_bytes(self.key(k), daten, 'text/vtt' if k.endswith('.vtt') else 'application/octet-stream')

    def lesen(self, k, maximal=20 * 1024 * 1024):
        return self.client.get_bytes(self.key(k), maximal)

    def existiert(self, k):
        return self.client.head(self.key(k)) is not None

    def loeschen(self, k):
        self.client.delete(self.key(k))

    def liste(self, ordner):
        p = self.praefix + ordner + '/'
        return [(k[len(p):], t.timestamp()) for k, t in self.client.liste(p) if '/' not in k[len(p):]]

    @contextmanager
    def lokal(self, k):
        """Objekt gestreamt in daten/tmp holen (für Whisper), danach wieder weg."""
        tmp = os.path.join(db.DATEN, 'tmp', 's3-' + secrets.token_hex(8))
        x = self.client.get(self.key(k))
        if x is None:
            raise FileNotFoundError(k)
        v, r = x
        try:
            with open(tmp, 'wb') as f:
                while teil := r.read(1 << 20):
                    f.write(teil)
        finally:
            v.close()
        try:
            yield tmp
        finally:
            try:
                os.remove(tmp)
            except FileNotFoundError:
                pass

    def antwort(self, k, typ, name, art='inline', kopf=None):
        return S3Antwort(self, k, typ, name, art, kopf)


def aus_umgebung(env=os.environ):
    werte = {n: (env.get(f'AGORA_S3_{n}') or '').strip() for n in ('ENDPOINT', 'BUCKET', 'KEY', 'SECRET')}
    if not any(werte.values()):
        return Lokal()
    fehlt = [n for n, v in werte.items() if not v]
    if fehlt:
        raise RuntimeError('Objektspeicher: es fehlt ' + ', '.join(f'AGORA_S3_{n}' for n in fehlt))
    sse = (env.get('AGORA_S3_SSE') or '').strip()
    if sse not in ('', 'AES256', 'aws:kms'):
        raise RuntimeError('AGORA_S3_SSE: AES256 oder aws:kms')
    c = S3Client(werte['ENDPOINT'], werte['BUCKET'], werte['KEY'], werte['SECRET'], (env.get('AGORA_S3_REGION') or 'us-east-1').strip(),
                 pfadstil=(env.get('AGORA_S3_PFADSTIL') or '1') != '0', sse=sse, kms_schluessel=(env.get('AGORA_S3_KMS_KEY') or '').strip())
    praefix = (env.get('AGORA_S3_PRAEFIX') or '').strip().lstrip('/')
    if praefix and not re.fullmatch(r'[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*/', praefix):
        raise RuntimeError('AGORA_S3_PRAEFIX: z. B. „agora/“ (mit / am Ende)')
    return S3(c, praefix)


_aktiv = [None]


def aktiv():
    if _aktiv[0] is None:
        _aktiv[0] = aus_umgebung()
    return _aktiv[0]


def setzen(s):
    """Für Tests: anderen Speicher einsetzen (None = wieder aus der Umgebung)."""
    _aktiv[0] = s


# Kurzformen für die Module
def ablegen(k, quelle):
    return aktiv().ablegen(k, quelle)


def schreiben(k, daten):
    return aktiv().schreiben(k, daten)


def lesen(k, maximal=20 * 1024 * 1024):
    return aktiv().lesen(k, maximal)


def existiert(k):
    return aktiv().existiert(k)


def loeschen(k):
    return aktiv().loeschen(k)


def liste(ordner):
    return aktiv().liste(ordner)


def lokal(k):
    return aktiv().lokal(k)


def antwort(k, typ, name, art='inline', kopf=None):
    return aktiv().antwort(k, typ, name, art, kopf)
