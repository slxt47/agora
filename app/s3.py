"""Kleiner S3-Client (nur Standardbibliothek): AWS Signature Version 4, PUT (gestreamt aus einer Datei), GET (auch Range),
HEAD, DELETE, ListObjectsV2. Getestet gegen die Testvektoren der AWS-Dokumentation (tests/test_speicher.py) und gegen
einen Wegwerf-MinIO (Integrationstest).

Warum selbst? boto3 + botocore sind ~90 MB und viele transitive Pakete, der `minio`-Client bringt urllib3, certifi und
pycryptodome mit. Agora braucht nur fünf Aufrufe auf ein Bucket – das sind ~200 Zeilen mit http.client/hmac/hashlib,
ohne neue Abhängigkeit (und damit ohne neue Updates/CVE-Quellen im Image).

Nutzlast: Uploads laufen mit `x-amz-content-sha256: UNSIGNED-PAYLOAD` (die Datei muss nicht zweimal gelesen werden);
Integrität sichert TLS – bei http:// (nur lokal, z. B. MinIO auf 127.0.0.1) nicht. Leere Anfragen (GET/HEAD/DELETE)
signieren den Hash der leeren Nutzlast.
"""
import hashlib
import hmac
import http.client
import ssl
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

LEER = hashlib.sha256(b'').hexdigest()
UNSIGNIERT = 'UNSIGNED-PAYLOAD'
BLOCK = 1 << 20


class S3Fehler(RuntimeError):
    def __init__(self, status, text=''):
        super().__init__(f'S3 {status}: {text[:200]}')
        self.status = status


def _hmac(schluessel, text):
    return hmac.new(schluessel, text.encode(), hashlib.sha256).digest()


def signierschluessel(geheim, datum, region, dienst):
    k = _hmac(('AWS4' + geheim).encode(), datum)
    k = _hmac(k, region)
    k = _hmac(k, dienst)
    return _hmac(k, 'aws4_request')


def uri_kodieren(pfad):
    return quote(pfad, safe='/-_.~')


def query_kodieren(query):
    paare = sorted((quote(str(k), safe='-_.~'), quote(str(v), safe='-_.~')) for k, v in (query or {}).items())
    return '&'.join(f'{k}={v}' for k, v in paare)


def signieren(methode, pfad, query, kopf, nutzlast_hash, schluessel, geheim, region, dienst='s3', zeit=None):
    """Authorization-Kopf (SigV4) für genau die übergebenen Kopfzeilen (müssen host und x-amz-date enthalten).
    → (authorization, kanonische_anfrage, zu_signieren) – die beiden letzten für Tests."""
    zeit = zeit or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    datum = zeit[:8]
    k = {n.lower().strip(): ' '.join(str(v).split()) for n, v in kopf.items()}
    namen = sorted(k)
    kanonisch = '\n'.join([methode, uri_kodieren(pfad), query_kodieren(query), ''.join(f'{n}:{k[n]}\n' for n in namen),
                           ';'.join(namen), nutzlast_hash])
    bereich = f'{datum}/{region}/{dienst}/aws4_request'
    zu_signieren = '\n'.join(['AWS4-HMAC-SHA256', zeit, bereich, hashlib.sha256(kanonisch.encode()).hexdigest()])
    signatur = hmac.new(signierschluessel(geheim, datum, region, dienst), zu_signieren.encode(), hashlib.sha256).hexdigest()
    return (f'AWS4-HMAC-SHA256 Credential={schluessel}/{bereich}, SignedHeaders={";".join(namen)}, Signature={signatur}',
            kanonisch, zu_signieren)


class S3Client:
    def __init__(self, endpunkt, bucket, schluessel, geheim, region='us-east-1', pfadstil=True, sse='', kms_schluessel='', timeout=120):
        u = urlsplit(endpunkt.rstrip('/'))
        if u.scheme not in ('http', 'https') or not u.hostname:
            raise ValueError('AGORA_S3_ENDPOINT muss http(s)://host[:port] sein')
        self.tls = u.scheme == 'https'
        self.host_basis = u.netloc
        self.bucket, self.schluessel, self.geheim, self.region = bucket, schluessel, geheim, region
        self.pfadstil, self.sse, self.kms = pfadstil, sse, kms_schluessel
        self.timeout = timeout
        self.ctx = ssl.create_default_context() if self.tls else None

    # ------------------------------------------------------------ Grundlage
    def _ziel(self, key):
        if self.pfadstil:
            return self.host_basis, f'/{self.bucket}/{key}' if key else f'/{self.bucket}'
        return f'{self.bucket}.{self.host_basis}', f'/{key}'

    def _verbindung(self, host):
        if self.tls:
            return http.client.HTTPSConnection(host, timeout=self.timeout, context=self.ctx)
        return http.client.HTTPConnection(host, timeout=self.timeout)

    def anfrage(self, methode, key, query=None, kopf=None, body=None, nutzlast_hash=LEER):
        """→ (verbindung, antwort). Wer die Antwort liest, schließt die Verbindung (stream) – sonst schließen()."""
        host, pfad = self._ziel(key)
        k = {'host': host, 'x-amz-date': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'x-amz-content-sha256': nutzlast_hash}
        k.update({n.lower(): v for n, v in (kopf or {}).items()})
        signierte = {n: v for n, v in k.items() if n in ('host', 'range', 'content-type', 'content-md5') or n.startswith('x-amz-')}
        k['authorization'] = signieren(methode, pfad, query, signierte, nutzlast_hash, self.schluessel, self.geheim, self.region,
                                       zeit=k['x-amz-date'])[0]
        q = query_kodieren(query)
        v = self._verbindung(host)
        try:
            v.request(methode, uri_kodieren(pfad) + (f'?{q}' if q else ''), body=body, headers=k)
            return v, v.getresponse()
        except BaseException:
            v.close()
            raise

    def _fertig(self, methode, key, erlaubt=(200, 204), **kw):
        v, r = self.anfrage(methode, key, **kw)
        try:
            daten = r.read()
            if r.status not in erlaubt:
                raise S3Fehler(r.status, daten.decode('utf-8', 'replace'))
            return r, daten
        finally:
            v.close()

    # ------------------------------------------------------------ Aufrufe
    def _sse_kopf(self):
        if not self.sse:
            return {}
        k = {'x-amz-server-side-encryption': self.sse}
        if self.sse == 'aws:kms' and self.kms:
            k['x-amz-server-side-encryption-aws-kms-key-id'] = self.kms
        return k

    def put_datei(self, key, pfad, groesse, typ='application/octet-stream'):
        """Datei gestreamt hochladen (nie ganz im Speicher; ein PUT bis 5 GB)."""
        with open(pfad, 'rb') as f:
            self._fertig('PUT', key, kopf={'content-type': typ, 'content-length': str(groesse), **self._sse_kopf()}, body=f,
                         nutzlast_hash=UNSIGNIERT)

    def put_bytes(self, key, daten, typ='application/octet-stream'):
        self._fertig('PUT', key, kopf={'content-type': typ, 'content-length': str(len(daten)), **self._sse_kopf()}, body=daten,
                     nutzlast_hash=hashlib.sha256(daten).hexdigest())

    def head(self, key):
        """→ {groesse, typ, geaendert} oder None (404)."""
        v, r = self.anfrage('HEAD', key)
        try:
            r.read()
            if r.status == 404:
                return None
            if r.status != 200:
                raise S3Fehler(r.status)
            return {'groesse': int(r.getheader('content-length') or 0), 'typ': r.getheader('content-type') or '',
                    'geaendert': r.getheader('last-modified') or ''}
        finally:
            v.close()

    def get(self, key, bereich=None):
        """→ (verbindung, antwort) zum Streamen; bereich = (start, ende) inklusive. 404 → None."""
        v, r = self.anfrage('GET', key, kopf={'range': f'bytes={bereich[0]}-{bereich[1]}'} if bereich else None)
        if r.status == 404:
            r.read()
            v.close()
            return None
        if r.status not in (200, 206):
            text = r.read().decode('utf-8', 'replace')
            v.close()
            raise S3Fehler(r.status, text)
        return v, r

    def get_bytes(self, key, maximal=20 * 1024 * 1024):
        x = self.get(key)
        if x is None:
            return None
        v, r = x
        try:
            return r.read(maximal)
        finally:
            v.close()

    def delete(self, key):
        self._fertig('DELETE', key, erlaubt=(200, 204, 404))

    def liste(self, praefix):
        """ListObjectsV2 (alle Seiten) → [(key, geaendert: datetime)]."""
        aus, weiter = [], None
        ns = '{http://s3.amazonaws.com/doc/2006-03-01/}'
        while True:
            q = {'list-type': '2', 'prefix': praefix, 'max-keys': '1000'} | ({'continuation-token': weiter} if weiter else {})
            _r, daten = self._fertig('GET', '', query=q)
            wurzel = ET.fromstring(daten)
            for o in wurzel.iter(f'{ns}Contents'):
                zeit = o.findtext(f'{ns}LastModified') or ''
                try:
                    t = datetime.fromisoformat(zeit.replace('Z', '+00:00'))
                except ValueError:
                    t = datetime.now(timezone.utc)
                aus.append((o.findtext(f'{ns}Key') or '', t))
            if (wurzel.findtext(f'{ns}IsTruncated') or '').lower() != 'true':
                return aus
            weiter = wurzel.findtext(f'{ns}NextContinuationToken')
            if not weiter:
                return aus
