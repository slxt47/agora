"""Web Push ohne Fremddienst-Bibliothek: Verschlüsselung nach RFC 8291 (aes128gcm, RFC 8188) und VAPID nach RFC 8292.

Bewusst klein gehalten und nur auf `cryptography` gebaut (ECDH P-256, HKDF/HMAC-SHA-256, AES-128-GCM, ECDSA P-256) –
die Primitive kommen aus OpenSSL, hier steht nur das Zusammensetzen laut RFC. Geprüft mit dem Testvektor aus RFC 8291
Anhang A (tests/test_push.py) und im Browser-E2E gegen WebCrypto.

- `verschluesseln(nutzdaten, p256dh, auth)` → Body für den Push-Dienst (ein Datensatz, Header salt|rs|idlen|keyid).
- `vapid_kopf(endpunkt, privat, oeffentlich_b64, sub)` → Wert für `Authorization: vapid t=<JWT>, k=<Schlüssel>`.
- `entschluesseln(...)` ist die Gegenrichtung (Browser-Seite) – nur für Tests.
"""
import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RS = 4096                   # Datensatzgröße (ein Datensatz reicht: Nutzlast höchstens ~3,9 kB)
MAX_KLARTEXT = 3800


def b64(daten):
    return base64.urlsafe_b64encode(daten).rstrip(b'=').decode()


def unb64(text):
    if not isinstance(text, str) or len(text) > 400:
        raise ValueError('kein Base64')
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _hmac(schluessel, daten):
    return hmac.new(schluessel, daten, hashlib.sha256).digest()


def oeffentlich_roh(privat):
    return privat.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def privat_aus_roh(roh):
    """32-Byte-Skalar → privater P-256-Schlüssel."""
    return ec.derive_private_key(int.from_bytes(roh, 'big'), ec.SECP256R1())


def privat_roh(privat):
    return privat.private_numbers().private_value.to_bytes(32, 'big')


def neuer_schluessel():
    return ec.generate_private_key(ec.SECP256R1())


def ua_schluessel_pruefen(p256dh, auth):
    """Schlüssel aus der Subscription des Browsers prüfen → (65 Byte Punkt, 16 Byte Auth). Wirft ValueError."""
    pub, a = unb64(p256dh), unb64(auth)
    if len(pub) != 65 or pub[0] != 4 or len(a) != 16:
        raise ValueError('falsche Länge')
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub)     # liegt der Punkt auf der Kurve?
    return pub, a


def _schluessel(ecdh, auth, ua_pub, as_pub, salz):
    """RFC 8291 §3.3/3.4 + RFC 8188 §2.2: (CEK, NONCE)."""
    prk_key = _hmac(auth, ecdh)
    ikm = _hmac(prk_key, b'WebPush: info\x00' + ua_pub + as_pub + b'\x01')
    prk = _hmac(salz, ikm)
    cek = _hmac(prk, b'Content-Encoding: aes128gcm\x00\x01')[:16]
    nonce = _hmac(prk, b'Content-Encoding: nonce\x00\x01')[:12]
    return cek, nonce


def verschluesseln(nutzdaten, p256dh, auth, *, _as_privat=None, _salz=None):
    """Klartext (bytes) für eine Subscription verschlüsseln → Body (aes128gcm). _as_privat/_salz nur für den Testvektor."""
    if len(nutzdaten) > MAX_KLARTEXT:
        raise ValueError('Nutzlast zu groß')
    ua_pub, a = ua_schluessel_pruefen(p256dh, auth)
    as_privat = _as_privat or neuer_schluessel()
    as_pub = oeffentlich_roh(as_privat)
    ecdh = as_privat.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    salz = _salz or os.urandom(16)
    cek, nonce = _schluessel(ecdh, a, ua_pub, as_pub, salz)
    geheim = AESGCM(cek).encrypt(nonce, nutzdaten + b'\x02', None)      # 0x02 = letzter Datensatz, ohne Auffüllung
    return salz + RS.to_bytes(4, 'big') + bytes([len(as_pub)]) + as_pub + geheim


def entschluesseln(body, ua_privat, auth):
    """Gegenrichtung (wie der Browser) – nur für Tests."""
    salz, rs, idlen = body[:16], int.from_bytes(body[16:20], 'big'), body[20]
    as_pub = body[21:21 + idlen]
    ua_pub = oeffentlich_roh(ua_privat)
    ecdh = ua_privat.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    cek, nonce = _schluessel(ecdh, auth, ua_pub, as_pub, salz)
    klar = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
    assert rs >= 18 and klar.rstrip(b'\x00').endswith(b'\x02')
    return klar.rstrip(b'\x00')[:-1]


# ---------------------------------------------------------------- VAPID (RFC 8292)
def vapid_kopf(endpunkt, privat, sub, gueltig_s=12 * 3600):
    """JWT (ES256) für die Herkunft des Push-Dienstes → Wert für den Authorization-Header."""
    u = urlsplit(endpunkt)
    kopf = b64(json.dumps({'typ': 'JWT', 'alg': 'ES256'}, separators=(',', ':')).encode())
    inhalt = b64(json.dumps({'aud': f'{u.scheme}://{u.netloc}', 'exp': int(time.time()) + gueltig_s, 'sub': sub},
                            separators=(',', ':')).encode())
    r, s = decode_dss_signature(privat.sign(f'{kopf}.{inhalt}'.encode(), ec.ECDSA(hashes.SHA256())))
    jwt = f'{kopf}.{inhalt}.{b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))}'
    return f'vapid t={jwt}, k={b64(oeffentlich_roh(privat))}'
