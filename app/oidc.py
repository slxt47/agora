"""OAuth 2.0 / OpenID Connect – Protokoll-Bausteine für „Anmelden mit …“ (app/anmeldung.py) und den Firmen-Login.

- Authorization Code Flow mit PKCE (S256), `state` und (OIDC) `nonce`.
- OIDC-Discovery (`/.well-known/openid-configuration`) und JWKS, beides im Arbeitsspeicher zwischengespeichert (1 h);
  unbekannte Schlüssel-ID → JWKS höchstens einmal je Minute neu laden (Schlüsselwechsel beim Anbieter).
- id_token-Prüfung mit PyJWT + cryptography (beide schon gepinnt, keine neue Abhängigkeit): nur asymmetrische Verfahren
  (RS*/PS*/ES*, nie `none` oder HS* – sonst könnte ein Token mit dem öffentlichen Schlüssel als HMAC-Geheimnis
  „signiert“ werden), Signatur gegen den Schlüssel mit passender `kid` aus dem JWKS, `iss` exakt (Microsoft „common“:
  Vorlage `{tenantid}` mit der `tid` aus dem Token), `aud` enthält die Client-ID (bei mehreren Empfängern muss `azp`
  passen), `exp`/`iat` mit 60 s Toleranz, `nonce` aus dem eigenen Vorgang.
- Ausgehende Anfragen: ohne Weiterleitungen, höchstens 1 MB Antwort, 10 s. Ziele, die Organisationen selbst eintragen
  (Firmen-Login), nur an öffentliche Adressen (gleiche Prüfung wie Webhooks, Verbindung zur geprüften IP – kein
  DNS-Rebinding); http:// und eigenes Netz nur mit AGORA_TEST=1 + AGORA_SSO_LOKAL=1 (Wegwerf-IdP in den Tests).
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import ssl
import threading
import time
from urllib.parse import urlencode, urlsplit

import jwt

from .kern import TEST
from .netz import oeffentlich
from .webhooks import _Http, _Https

LOKAL_ERLAUBT = TEST and os.environ.get('AGORA_SSO_LOKAL') == '1'
TIMEOUT_S = 10
MAX_ANTWORT = 1_000_000
CACHE_S = 3600
LEEWAY_S = 60
# Nur asymmetrische Verfahren: `none` und HS* (HMAC mit dem öffentlichen Schlüssel als Geheimnis) sind ausgeschlossen
ALGORITHMEN = ('RS256', 'RS384', 'RS512', 'PS256', 'PS384', 'PS512', 'ES256', 'ES384', 'ES512')
TENANT_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

_cache = {}               # url → (gueltig_bis, daten)
_jwks_neu = {}            # jwks_uri → letzte Neuladung wegen unbekannter kid
_lock = threading.Lock()


class OidcFehler(Exception):
    """Anmeldung beim Anbieter gescheitert. `grund` ist ein fester Code (geht in die Umleitung, nie Text des Anbieters)."""
    def __init__(self, grund, info=''):
        super().__init__(f'{grund}: {info}')
        self.grund, self.info = grund, str(info)[:200]


# ---------------------------------------------------------------- PKCE, Zufall
def zufall():
    return secrets.token_urlsafe(32)


def pkce_paar():
    """→ (code_verifier, code_challenge) nach RFC 7636 (S256)."""
    v = secrets.token_urlsafe(48)
    return v, b64u(hashlib.sha256(v.encode()).digest())


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


# ---------------------------------------------------------------- HTTP
def url_pruefen(url, oeffentlich_pflicht):
    """→ (teile, [IPs]). Mit oeffentlich_pflicht: nur https und öffentliche Adressen (außer im lokalen Testmodus)."""
    try:
        u = urlsplit(url)
        port = u.port
    except (ValueError, TypeError):
        raise OidcFehler('anbieter', 'ungültige Adresse') from None
    if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password or u.fragment:
        raise OidcFehler('anbieter', 'ungültige Adresse')
    if oeffentlich_pflicht and u.scheme != 'https' and not LOKAL_ERLAUBT:
        raise OidcFehler('anbieter', 'nur https')
    try:
        ips = list(dict.fromkeys(a[4][0] for a in socket.getaddrinfo(
            u.hostname, port or (443 if u.scheme == 'https' else 80), proto=socket.IPPROTO_TCP)))
    except (socket.gaierror, UnicodeError, OSError):
        raise OidcFehler('anbieter', 'Name nicht auflösbar') from None
    if not ips:
        raise OidcFehler('anbieter', 'Name nicht auflösbar')
    if oeffentlich_pflicht and not LOKAL_ERLAUBT and not all(oeffentlich(a) for a in ips):
        raise OidcFehler('anbieter', 'Adresse im eigenen Netz')
    return u, ips


def _anfrage(methode, url, kopf, body, oeffentlich_pflicht):
    """Eine Anfrage ohne Weiterleitungen → (status, bytes). Tests ersetzen diese Funktion."""
    u, ips = url_pruefen(url, oeffentlich_pflicht)
    art = _Https if u.scheme == 'https' else _Http
    extra = {'context': ssl.create_default_context()} if u.scheme == 'https' else {}
    conn = art(u.hostname, ips[0], u.port or (443 if u.scheme == 'https' else 80), timeout=TIMEOUT_S, **extra)
    try:
        conn.request(methode, (u.path or '/') + (f'?{u.query}' if u.query else ''), body=body, headers=kopf)
        r = conn.getresponse()
        daten = r.read(MAX_ANTWORT + 1)
        if len(daten) > MAX_ANTWORT:
            raise OidcFehler('anbieter', 'Antwort zu groß')
        return r.status, daten
    except (OSError, ssl.SSLError) as e:
        raise OidcFehler('anbieter', f'Verbindung: {type(e).__name__}') from None
    finally:
        conn.close()


def holen(url, *, form=None, kopf=None, oeffentlich_pflicht=False, erwartet=(200,)):
    """GET (oder POST mit Formular) → JSON-Objekt bzw. Liste."""
    k = {'Accept': 'application/json', 'User-Agent': 'Agora-Anmeldung/1'} | (kopf or {})
    body = None
    if form is not None:
        body = urlencode(form).encode()
        k['Content-Type'] = 'application/x-www-form-urlencoded'
    status, daten = _anfrage('POST' if form is not None else 'GET', url, k, body, oeffentlich_pflicht)
    try:
        j = json.loads(daten or b'null')
    except ValueError:
        raise OidcFehler('anbieter', f'keine JSON-Antwort ({status})') from None
    if status not in erwartet:
        fehler = j.get('error') if isinstance(j, dict) else None
        raise OidcFehler('anbieter', f'HTTP {status} {str(fehler)[:60] if fehler else ""}'.strip())
    return j


def _gecacht(url, laden, neu=False):
    with _lock:
        x = _cache.get(url)
    if x and x[0] > time.time() and not neu:
        return x[1]
    daten = laden()
    with _lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[url] = (time.time() + CACHE_S, daten)
    return daten


def cache_leeren():
    with _lock:
        _cache.clear()
        _jwks_neu.clear()


# ---------------------------------------------------------------- Discovery, JWKS
def discovery(url, erwarteter_issuer, oeffentlich_pflicht=False):
    """Konfiguration des Anbieters. `erwarteter_issuer` muss (bis auf einen Schrägstrich am Ende) passen – sonst könnte
    eine fremde Discovery-Antwort Tokens eines anderen Ausstellers einschleusen."""
    def laden():
        d = holen(url, oeffentlich_pflicht=oeffentlich_pflicht)
        if not isinstance(d, dict):
            raise OidcFehler('anbieter', 'Discovery ungültig')
        for feld in ('issuer', 'authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            if not isinstance(d.get(feld), str) or not d[feld] or len(d[feld]) > 500:
                raise OidcFehler('anbieter', f'Discovery ohne {feld}')
        for feld in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            url_pruefen_form(d[feld], oeffentlich_pflicht)
        return d
    d = _gecacht(url, laden)
    if erwarteter_issuer is not None and d['issuer'].rstrip('/') != erwarteter_issuer.rstrip('/'):
        raise OidcFehler('anbieter', 'Discovery: issuer passt nicht')
    return d


def url_pruefen_form(url, oeffentlich_pflicht):
    """Nur die Form einer Adresse aus der Discovery (Auflösen/Netz prüft `holen` beim Aufruf)."""
    try:
        u = urlsplit(url)
    except ValueError:
        raise OidcFehler('anbieter', 'ungültige Adresse') from None
    erlaubt = ('https',) if oeffentlich_pflicht and not LOKAL_ERLAUBT else ('https', 'http')
    if u.scheme not in erlaubt or not u.hostname or u.username or u.password or u.fragment:
        raise OidcFehler('anbieter', 'Discovery: unsichere Adresse')


def jwks(uri, oeffentlich_pflicht=False, neu=False):
    def laden():
        d = holen(uri, oeffentlich_pflicht=oeffentlich_pflicht)
        if not isinstance(d, dict) or not isinstance(d.get('keys'), list):
            raise OidcFehler('anbieter', 'JWKS ungültig')
        return d['keys'][:50]
    return _gecacht(uri, laden, neu)


def _schluessel(keys, kid, alg):
    passend = [k for k in keys if isinstance(k, dict) and k.get('use', 'sig') == 'sig' and (not k.get('alg') or k['alg'] == alg)
               and (kid is None or k.get('kid') == kid)]
    if kid is None and len(passend) != 1:        # ohne kid nur, wenn es genau einen passenden Schlüssel gibt
        return None
    for k in passend:
        try:
            return jwt.PyJWK(k, algorithm=alg).key
        except (jwt.PyJWKError, jwt.InvalidKeyError, ValueError, TypeError, KeyError):
            continue
    return None


def id_token_pruefen(token, *, issuer, client_id, nonce, jwks_uri, oeffentlich_pflicht=False, weitere_issuer=(), jetzt=None):
    """Prüft ein id_token vollständig → Claims (dict). Wirft OidcFehler('token', …). `weitere_issuer`: gleichwertige
    Schreibweisen (Google: „accounts.google.com“ neben „https://accounts.google.com“)."""
    if not isinstance(token, str) or len(token) > 20000 or token.count('.') != 2:
        raise OidcFehler('token', 'kein JWT')
    try:
        kopf = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise OidcFehler('token', 'Kopf unlesbar') from None
    alg, kid = kopf.get('alg'), kopf.get('kid')
    if alg not in ALGORITHMEN:
        raise OidcFehler('token', f'Verfahren {str(alg)[:10]} nicht erlaubt')
    if kid is not None and (not isinstance(kid, str) or len(kid) > 200):
        raise OidcFehler('token', 'kid ungültig')
    key = _schluessel(jwks(jwks_uri, oeffentlich_pflicht), kid, alg)
    if key is None:          # Schlüsselwechsel beim Anbieter? höchstens einmal je Minute neu laden
        with _lock:
            darf = time.time() - _jwks_neu.get(jwks_uri, 0) > 60
            if darf:
                _jwks_neu[jwks_uri] = time.time()
        if darf:
            key = _schluessel(jwks(jwks_uri, oeffentlich_pflicht, neu=True), kid, alg)
    if key is None:
        raise OidcFehler('token', 'kein passender Schlüssel')
    try:
        claims = jwt.decode(token, key, algorithms=[alg], audience=client_id, leeway=LEEWAY_S,
                            options={'require': ['exp', 'iat', 'iss', 'aud', 'sub'], 'verify_iss': False})
    except jwt.ExpiredSignatureError:
        raise OidcFehler('token', 'abgelaufen') from None
    except jwt.InvalidAudienceError:
        raise OidcFehler('token', 'aud passt nicht') from None
    except jwt.ImmatureSignatureError:
        raise OidcFehler('token', 'iat/nbf in der Zukunft') from None
    except jwt.PyJWTError as e:
        raise OidcFehler('token', f'ungültig ({type(e).__name__})') from None
    if not isinstance(claims.get('iss'), str) or not any(gleich(claims['iss'], erwarteter_issuer(i, claims))
                                                         for i in (issuer, *weitere_issuer)):
        raise OidcFehler('token', 'iss passt nicht')
    aud = claims['aud']
    if isinstance(aud, list) and len(aud) > 1 and claims.get('azp') != client_id:
        raise OidcFehler('token', 'azp passt nicht')
    if not isinstance(claims.get('nonce'), str) or not gleich(claims['nonce'], nonce):
        raise OidcFehler('token', 'nonce passt nicht')
    if not isinstance(claims.get('sub'), str) or not 1 <= len(claims['sub']) <= 255:
        raise OidcFehler('token', 'sub ungültig')
    if (jetzt or time.time()) - claims['iat'] > 3600 + LEEWAY_S:      # id_token muss frisch sein (eben ausgestellt)
        raise OidcFehler('token', 'zu alt')
    return claims


def erwarteter_issuer(issuer, claims):
    """Microsoft „common“/„organizations“: Discovery liefert https://login.microsoftonline.com/{tenantid}/v2.0 – die
    Vorlage wird mit der `tid` aus dem (signierten) Token gefüllt, die eine GUID sein muss."""
    if '{tenantid}' not in issuer:
        return issuer
    tid = claims.get('tid')
    if not isinstance(tid, str) or not TENANT_RE.match(tid):
        return '\0'          # passt nie
    return issuer.replace('{tenantid}', tid)


def token_tauschen(token_endpoint, *, code, verifier, redirect_uri, client_id, client_secret, auth_methoden=None,
                   oeffentlich_pflicht=False, kopf=None):
    """Code gegen Tokens tauschen (mit code_verifier). client_secret_post, wenn der Anbieter es kann, sonst Basic."""
    form = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri, 'code_verifier': verifier}
    k = dict(kopf or {})
    if auth_methoden and 'client_secret_post' not in auth_methoden and 'client_secret_basic' in auth_methoden:
        from urllib.parse import quote
        paar = f'{quote(client_id, safe="")}:{quote(client_secret, safe="")}'.encode()
        k['Authorization'] = 'Basic ' + base64.b64encode(paar).decode()
    else:
        form |= {'client_id': client_id, 'client_secret': client_secret}
    d = holen(token_endpoint, form=form, kopf=k, oeffentlich_pflicht=oeffentlich_pflicht)
    if not isinstance(d, dict) or d.get('error'):
        raise OidcFehler('anbieter', f'Token-Tausch: {str(d.get("error") if isinstance(d, dict) else "")[:60]}')
    return d


def gleich(a, b):
    """Vergleich in konstanter Zeit, auch für Nicht-ASCII (compare_digest mit str wirft sonst TypeError)."""
    return hmac.compare_digest(str(a).encode(), str(b).encode())


def ja(wert):
    """Boolesches Claim (manche Anbieter schicken „true“ als Text)."""
    return wert is True or (isinstance(wert, str) and wert.lower() == 'true')
