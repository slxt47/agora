"""Firmen-Login: Organisationen melden ihre Mitglieder über den eigenen Identity-Provider an (OpenID Connect, generisch –
Entra ID, Okta, Keycloak, Google Workspace, Authentik, Dex …). SAML ist bewusst nicht dabei: docs/adr/0008-firmen-login-oidc-ohne-saml.md.

Standardmäßig AUS (AGORA_FIRMEN_LOGIN=1 schaltet es frei). Dann richtet ein Owner je Organisation ein: Aussteller-URL
(issuer), Client-ID und -Geheimnis (verschlüsselt gespeichert: AES-256-GCM, Schlüssel per HKDF aus AGORA_GEHEIM, an die
Organisation gebunden), die E-Mail-Domain (nur die eigene, keine Freemail-Anbieter, jede Domain nur bei einer
Organisation), „automatisch beitreten“ (als mitglied) und „Firmen-Login erzwingen“.

Anmelden: Startseite → „Mit Firmenkonto anmelden“ → E-Mail-Adresse → Domain → Weiterleitung zum IdP der Organisation
(Ablauf und Prüfungen wie bei Google, app/anmeldung.py + app/oidc.py). Zusätzlich gilt:
• Discovery, JWKS und Token-Endpunkt trägt ein Owner ein → nur https und öffentliche Adressen, Verbindung zur geprüften IP
  (kein SSRF ins eigene Netz, kein DNS-Rebinding). Ausnahme nur im Testmodus mit AGORA_SSO_LOKAL=1.
• Neue/unverknüpfte Identitäten nur mit einer Adresse in der eingetragenen Domain und nicht ausdrücklich
  email_verified=false. Ein bestehendes Agora-Konto wird – wie bei Google – nur nach dem Agora-Passwort verknüpft; ein
  Owner mit eigenem IdP kann also kein fremdes Konto übernehmen, auch nicht in „seiner“ Domain.
• Wer per Mail-Link ein Passwort zurücksetzt (Postfach bewiesen), verliert seine Firmen-Verknüpfungen: ein vom IdP
  vorab für die eigene Adresse angelegtes Konto bleibt so nicht für den IdP offen.
• „Erzwingen“ gilt nur für Mitglieder dieser Organisation mit Adresse in der Domain, nicht für Owner (Notzugang, falls der
  IdP ausfällt): Passwort-Login und Google/Microsoft/GitHub werden für sie abgelehnt.
"""
import base64
import os
import re
import secrets
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from . import anmeldung, db, konto, oidc, orgs
from .kern import GEHEIM, benutzer_aus_sitzung, fehler, grenze, iso, jetzt, json_body, text_feld
from .oidc import OidcFehler

router = APIRouter()
PFLICHT = 'Für dein Konto ist der Firmen-Login vorgeschrieben – bitte „Mit Firmenkonto anmelden“.'
KEINER = 'Für diese E-Mail-Domain ist kein Firmen-Login eingerichtet.'
CLIENT_RE = re.compile(r'^[\x21-\x7e]{1,200}$')


def an():
    return os.environ.get('AGORA_FIRMEN_LOGIN') == '1'


# ---------------------------------------------------------------- Geheimnis verschlüsseln (AES-256-GCM)
def _schluessel():
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b'agora', info=b'agora-firmen-login-client-geheimnis').derive(GEHEIM)


def verschluesseln(text, org_id):
    nonce = secrets.token_bytes(12)
    return 'g1.' + base64.urlsafe_b64encode(nonce + AESGCM(_schluessel()).encrypt(nonce, text.encode(), f'org:{org_id}'.encode())).decode()


def entschluesseln(wert, org_id):
    """Klartext oder None (falsches AGORA_GEHEIM, beschädigt, andere Organisation)."""
    try:
        art, rest = wert.split('.', 1)
        roh = base64.urlsafe_b64decode(rest)
        if art != 'g1' or len(roh) < 29:
            return None
        return AESGCM(_schluessel()).decrypt(roh[:12], roh[12:], f'org:{org_id}'.encode()).decode()
    except (AttributeError, ValueError, InvalidTag, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------- Konfiguration
def zeile(org_id):
    return db.verbindung().execute('SELECT * FROM org_sso WHERE org_id = ?', (org_id,)).fetchone()


def fuer_domain(domain):
    return db.verbindung().execute('SELECT * FROM org_sso WHERE domain = ? AND aktiv = 1', (domain,)).fetchone()


def irgendeiner():
    return an() and bool(db.verbindung().execute('SELECT 1 FROM org_sso WHERE aktiv = 1 LIMIT 1').fetchone())


def discovery_url(issuer):
    return issuer.rstrip('/') + '/.well-known/openid-configuration'


def _email_ok(claims):
    v = claims.get('email_verified')
    return not (v is False or (isinstance(v, str) and v.lower() == 'false'))


def cfg(org_id):
    """Anbieter-Konfiguration für app/anmeldung.py (oder None, wenn aus/gelöscht/inaktiv)."""
    if not an() or not org_id:
        return None
    z = zeile(org_id)
    o = orgs.laden_mit_id(org_id)
    if not z or not z['aktiv'] or not o:
        return None
    geheim = entschluesseln(z['geheim'], org_id)
    if geheim is None:
        return None
    dom = z['domain']
    return {'id': 'firma', 'name': o['name'], 'art': 'oidc', 'scope': 'openid email profile', 'firma': True, 'org_id': org_id,
            'discovery': discovery_url(z['issuer']), 'issuer': z['issuer'], 'client_id': z['client_id'], 'client_secret': geheim,
            'oeffentlich': True, 'email_ok': _email_ok, 'auto_beitritt': bool(z['auto_beitritt']),
            'email_pruefen': lambda e: None if orgs.email_domain(e) == dom else 'domain'}


def start(d, b):
    """POST /api/anmeldung/firma/start: {email} → IdP der Organisation mit dieser Domain (aus dem Profil: eigene Adresse)."""
    if not an():
        fehler(404, 'Diesen Anmeldeweg gibt es hier nicht.')
    email = b['email'] if b else str(d.get('email', '')).strip().lower()
    if not konto.MAIL_RE.match(email):
        fehler(400, 'Bitte eine gültige E-Mail-Adresse eingeben.')
    z = fuer_domain(orgs.email_domain(email))
    c = cfg(z['org_id']) if z else None
    if not c:
        fehler(404, KEINER)
    return c, z['org_id'], email


def nach_anmeldung(b, c):
    """Automatisch der Organisation beitreten (als mitglied), wenn eingestellt."""
    if not c.get('firma') or not c.get('auto_beitritt'):
        return
    o = orgs.laden_mit_id(c['org_id'])
    if o and not orgs.rolle(o['id'], b['id']):
        try:
            orgs._aufnehmen(o, b, 'mitglied', 'Firmen-Login')
        except Exception:   # noqa: BLE001  (Organisation voll: Anmeldung trotzdem)
            pass


def regel(b):
    """Firmen-Login erzwungen: Mitglied (nicht Owner) einer Organisation, die es für die Domain der Adresse verlangt."""
    if not an():
        return None
    z = db.verbindung().execute(
        "SELECT 1 FROM org_sso s JOIN org_mitglieder m ON m.org_id = s.org_id WHERE s.aktiv = 1 AND s.erzwingen = 1 "
        "AND s.domain = ? AND m.benutzer_id = ? AND m.rolle != 'owner'", (orgs.email_domain(b['email']), b['id'])).fetchone()
    return PFLICHT if z else None


def nach_reset(benutzer_id):
    n = db.verbindung().execute("DELETE FROM anmelde_konten WHERE benutzer_id = ? AND anbieter = 'firma'", (benutzer_id,)).rowcount
    if n:
        db.protokollieren(f'Anmeldung getrennt (Firmen-Login, Passwort zurückgesetzt: {n})', benutzer_id)


anmeldung.ZUSATZ['firma'] = {'start': start, 'cfg': cfg}
anmeldung.NACH_ANMELDUNG.append(nach_anmeldung)
konto.ANMELDE_REGELN.append(regel)
konto.NACH_RESET.append(nach_reset)


# ---------------------------------------------------------------- Verwalten (Owner)
def _json(z):
    if not z:
        return None
    return {'issuer': z['issuer'], 'client_id': z['client_id'], 'geheimnis_gesetzt': bool(z['geheim']), 'domain': z['domain'],
            'auto_beitritt': bool(z['auto_beitritt']), 'erzwingen': bool(z['erzwingen']), 'aktiv': bool(z['aktiv']),
            'geaendert': iso(z['geaendert']),
            'verknuepft': db.verbindung().execute('SELECT COUNT(*) FROM anmelde_konten WHERE org_id = ?', (z['org_id'],)).fetchone()[0]}


def _owner(req, slug):
    if not an():
        fehler(404, 'Nicht gefunden')
    b = benutzer_aus_sitzung(req)
    return b, orgs.mitgliedschaft(b, slug, 'owner')


@router.get('/api/orgs/{slug}/sso')
def lesen(slug: str, req: Request):
    b, o = _owner(req, slug)
    return {'sso': _json(zeile(o['id'])), 'redirect_uri': anmeldung.redirect_uri('firma'), 'domain_vorschlag': orgs.email_domain(b['email'])}


def issuer_feld(d):
    i = text_feld(d, 'issuer', 300, pflicht=True)
    try:
        u = urlsplit(i)
    except ValueError:
        u = None
    erlaubt = ('https', 'http') if oidc.LOKAL_ERLAUBT else ('https',)
    if not u or u.scheme not in erlaubt or not u.hostname or u.username or u.password or u.query or u.fragment or ' ' in i:
        fehler(400, 'Aussteller-URL: https://… ohne Benutzername, ? und # (z. B. https://login.firma.de/realms/firma).')
    return i


@router.put('/api/orgs/{slug}/sso')
async def speichern(slug: str, req: Request):
    b, o = _owner(req, slug)
    d = await json_body(req)
    grenze.pruefen(f'sso:{b["id"]}', 20, 3600)
    alt = zeile(o['id'])
    issuer = issuer_feld(d)
    client_id = d.get('client_id')
    if not isinstance(client_id, str) or not CLIENT_RE.match(client_id.strip()):
        fehler(400, 'Client-ID fehlt oder ist ungültig.')
    client_id = client_id.strip()
    geheim = d.get('client_secret')
    if geheim in (None, '') and alt:
        geheim = entschluesseln(alt['geheim'], o['id'])
    if not isinstance(geheim, str) or not 1 <= len(geheim) <= 500:
        fehler(400, 'Client-Geheimnis fehlt (höchstens 500 Zeichen).')
    dom = d.get('domain')
    dom = dom.strip().lower().lstrip('@') if isinstance(dom, str) else ''
    if not orgs.DOMAIN_RE.match(dom):
        fehler(400, 'Ungültige Domain (z. B. firma.de).')
    if dom in orgs.FREEMAIL:
        fehler(400, 'Domains öffentlicher Mail-Anbieter sind für den Firmen-Login nicht erlaubt.')
    if orgs.email_domain(b['email']) != dom:
        fehler(400, 'Die Domain muss zu deiner eigenen E-Mail-Adresse passen.')
    c = db.verbindung()
    if c.execute('SELECT 1 FROM org_sso WHERE domain = ? AND org_id != ?', (dom, o['id'])).fetchone():
        fehler(409, 'Diese Domain nutzt schon eine andere Organisation für den Firmen-Login.')
    schalter = {}
    for k in ('auto_beitritt', 'erzwingen', 'aktiv'):
        v = d.get(k, {'auto_beitritt': True, 'erzwingen': False, 'aktiv': True}[k] if not alt else bool(alt[k]))
        if not isinstance(v, bool):
            fehler(400, 'Ungültige Einstellungen.')
        schalter[k] = v
    # IdP prüfen: Discovery erreichbar (nur öffentliche Adressen) und Aussteller passt
    try:
        await run_in_threadpool(oidc.discovery, discovery_url(issuer), issuer, True)
    except OidcFehler as e:
        db.protokollieren(f'Firmen-Login: Discovery gescheitert ({o["slug"]}: {e.info[:80]})', b['id'])
        fehler(400, 'Der Identity-Provider antwortet nicht passend. Stimmt die Aussteller-URL? (Nur https und öffentliche Adressen.)')
    if alt and alt['issuer'] != issuer:          # anderer IdP: alte Verknüpfungen gehören nicht mehr dazu
        c.execute('DELETE FROM anmelde_konten WHERE org_id = ?', (o['id'],))
    c.execute('INSERT INTO org_sso (org_id, issuer, client_id, geheim, domain, auto_beitritt, erzwingen, aktiv, geaendert, von_id) '
              'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(org_id) DO UPDATE SET issuer = excluded.issuer, '
              'client_id = excluded.client_id, geheim = excluded.geheim, domain = excluded.domain, auto_beitritt = excluded.auto_beitritt, '
              'erzwingen = excluded.erzwingen, aktiv = excluded.aktiv, geaendert = excluded.geaendert, von_id = excluded.von_id',
              (o['id'], issuer, client_id, verschluesseln(geheim, o['id']), dom, int(schalter['auto_beitritt']), int(schalter['erzwingen']),
               int(schalter['aktiv']), jetzt(), b['id']))
    db.protokollieren(f'Firmen-Login eingerichtet ({o["slug"]}, {dom})', b['id'])
    return {'sso': _json(zeile(o['id'])), 'redirect_uri': anmeldung.redirect_uri('firma')}


@router.delete('/api/orgs/{slug}/sso')
def entfernen(slug: str, req: Request):
    b, o = _owner(req, slug)
    c = db.verbindung()
    c.execute('DELETE FROM org_sso WHERE org_id = ?', (o['id'],))
    c.execute('DELETE FROM anmelde_konten WHERE org_id = ?', (o['id'],))
    db.protokollieren(f'Firmen-Login entfernt ({o["slug"]})', b['id'])
    return {'ok': True}

