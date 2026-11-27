"""Nachgebauter Identity-Provider für die API-Tests (ohne Netz): ersetzt oidc._anfrage.

Kann OIDC (Discovery, JWKS, Token-Endpunkt mit PKCE-Prüfung) und GitHub (Token, /user, /user/emails). Die Tests holen
sich mit `autorisieren()` einen Code wie ein Browser beim Anbieter und können id_token gezielt verfälschen."""
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_SCHLUESSEL = {}


def schluessel(name):
    """RSA-Schlüssel je Name (einmal je Testlauf erzeugt – 2048 bit dauern etwas)."""
    if name not in _SCHLUESSEL:
        _SCHLUESSEL[name] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _SCHLUESSEL[name]


def jwk(key, kid):
    d = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return d | {'kid': kid, 'use': 'sig', 'alg': 'RS256'}


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


class FakeIdP:
    def __init__(self, issuer='https://idp.example.org', client_id='agora-client', geheim='agora-geheim', basis=None,
                 discovery_issuer=None):
        self.issuer, self.client_id, self.geheim = issuer, client_id, geheim
        self.basis = basis or issuer
        self.discovery_issuer = discovery_issuer or issuer
        self.key, self.kid = schluessel('idp'), 'k1'
        self.weitere_keys = []
        self.codes = {}
        self.anfragen = []
        self.github = {'user': {'id': 4242, 'login': 'octo', 'name': 'Octo Cat'},
                       'emails': [{'email': 'octo@example.org', 'primary': True, 'verified': True}]}

    # ---------------------------------------------------------------- wie der Browser beim Anbieter
    def autorisieren(self, start_url, *, claims=None, kopf=None, token=None, signier_key=None):
        """Aus der Start-Adresse (Parameter) einen Code ausstellen. claims/kopf überschreiben Werte im id_token."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(start_url).query).items()}
        assert q['response_type'] == 'code' and q['code_challenge_method'] == 'S256' and q['client_id'] == self.client_id
        code = secrets.token_urlsafe(16)
        self.codes[code] = {'q': q, 'claims': claims or {}, 'kopf': kopf or {}, 'token': token, 'key': signier_key}
        return q['state'], code

    # ---------------------------------------------------------------- Netz
    def __call__(self, methode, url, kopf, body, oeffentlich_pflicht):
        self.anfragen.append((methode, url))
        u = urlsplit(url)
        pfad = f'{u.scheme}://{u.netloc}{u.path}'
        if pfad == f'{self.basis}/.well-known/openid-configuration':
            return 200, json.dumps({'issuer': self.discovery_issuer, 'authorization_endpoint': f'{self.basis}/auth',
                                    'token_endpoint': f'{self.basis}/token', 'jwks_uri': f'{self.basis}/jwks',
                                    'token_endpoint_auth_methods_supported': ['client_secret_post', 'client_secret_basic']}).encode()
        if pfad == f'{self.basis}/jwks':
            return 200, json.dumps({'keys': [jwk(self.key, self.kid)] + self.weitere_keys}).encode()
        if pfad in (f'{self.basis}/token', 'https://github.com/login/oauth/access_token'):
            return self._token(parse_qs(body.decode()), kopf, github=pfad.startswith('https://github.com'))
        if pfad == 'https://api.github.com/user':
            return 200, json.dumps(self.github['user']).encode()
        if pfad == 'https://api.github.com/user/emails':
            return 200, json.dumps(self.github['emails']).encode()
        return 404, b'{"error": "not_found"}'

    def _token(self, f, kopf, github=False):
        f = {k: v[0] for k, v in f.items()}
        if f.get('client_id') != self.client_id or f.get('client_secret') != self.geheim:
            return 401, b'{"error": "invalid_client"}'
        eintrag = self.codes.pop(f.get('code'), None)
        if not eintrag:
            return 400, b'{"error": "invalid_grant"}'
        q = eintrag['q']
        # PKCE: S256(code_verifier) muss zur code_challenge passen, redirect_uri gleich
        if b64u(hashlib.sha256(f.get('code_verifier', '').encode()).digest()) != q['code_challenge'] or f.get('redirect_uri') != q['redirect_uri']:
            return 400, b'{"error": "invalid_grant", "error_description": "PKCE"}'
        if github:
            return 200, json.dumps({'access_token': 'gho_' + secrets.token_hex(10), 'token_type': 'bearer'}).encode()
        if eintrag['token'] is not None:
            return 200, json.dumps({'access_token': 'x', 'id_token': eintrag['token']}).encode()
        t = int(time.time())
        claims = {'iss': self.issuer, 'aud': self.client_id, 'sub': 'nutzer-1', 'email': 'neu@example.org', 'email_verified': True,
                  'name': 'Nora Neu', 'iat': t, 'exp': t + 600, 'nonce': q.get('nonce')} | eintrag['claims']
        claims = {k: v for k, v in claims.items() if v is not None}
        kopf_jwt = {k: v for k, v in ({'kid': self.kid} | eintrag['kopf']).items() if v is not None}
        alg = kopf_jwt.pop('alg', 'RS256')
        if alg == 'none':
            token = jwt.encode(claims, None, algorithm='none', headers=kopf_jwt)
        elif alg == 'HS256':        # „signiert“ mit dem öffentlichen Schlüssel als HMAC-Geheimnis (Algorithmus-Verwechslung)
            pem = self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            token = _hs256(claims, pem, kopf_jwt)
        else:
            token = jwt.encode(claims, eintrag['key'] or self.key, algorithm=alg, headers=kopf_jwt)
        return 200, json.dumps({'access_token': 'x', 'id_token': token, 'token_type': 'Bearer'}).encode()


def _hs256(claims, geheim, kopf):
    import hmac
    k = b64u(json.dumps({'alg': 'HS256', 'typ': 'JWT'} | kopf).encode())
    p = b64u(json.dumps(claims).encode())
    s = b64u(hmac.new(geheim, f'{k}.{p}'.encode(), hashlib.sha256).digest())
    return f'{k}.{p}.{s}'
