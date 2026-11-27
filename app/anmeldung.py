"""Anmelden mit Google, Microsoft und GitHub (OAuth 2.0 / OpenID Connect) – siehe docs/ANMELDUNG.md.

Standardmäßig AUS: Ein Anbieter erscheint nur, wenn Client-ID und -Geheimnis gesetzt sind (AGORA_GOOGLE_CLIENT_ID/
_SECRET, AGORA_MICROSOFT_CLIENT_ID/_SECRET/_TENANT, AGORA_GITHUB_CLIENT_ID/_SECRET). Ohne sie gibt es weder Knöpfe noch
Routen (404) – Produktion läuft unverändert.

Ablauf (Authorization Code + PKCE, Protokoll in app/oidc.py):
1. POST /api/anmeldung/<anbieter>/start (X-Agora) legt einen Vorgang an: state (nur als Hash gespeichert), nonce,
   code_verifier, Ziel `weiter`, 10 min gültig, gebunden an ein Browser-Cookie (`…-anmeldung`, HttpOnly, SameSite=Lax).
   Antwort: Adresse beim Anbieter. Ohne das Cookie lässt sich ein fremder Vorgang nicht abschließen (Login-CSRF).
2. GET /api/anmeldung/<anbieter>/zurueck?code&state: Vorgang einlösen (einmalig, Cookie muss passen), Code gegen
   Tokens tauschen, id_token prüfen (OIDC) bzw. bei GitHub /user + /user/emails (nur verifizierte Haupt-Adresse).
3. Konto zuordnen – Schutz vor Kontoübernahme:
   • Verknüpfung (Aussteller + sub) bekannt → dieses Konto.
   • Sonst nur mit vom Anbieter BESTÄTIGTER E-Mail-Adresse weiter (Microsoft: nur private Konten oder Claim xms_edov).
   • Gibt es ein bestätigtes Agora-Konto mit der Adresse → KEINE Anmeldung, sondern einmal das Agora-Passwort (und ggf.
     den 2FA-Code) eingeben, erst dann wird verknüpft (POST /api/anmeldung/verknuepfen).
   • Gibt es nur ein unbestätigtes Konto mit der Adresse (möglicherweise von Dritten vorab angelegt) → wird gelöscht und
     neu angelegt; ein vorab gesetztes Passwort überlebt so nie.
   • Sonst neues, bestätigtes Konto ohne Passwort.
4. Zwei-Faktor gilt weiter: mit 2FA geht es zum Code-Schritt (`/#zf=<schritt>`, das Fragment erreicht keinen Server).

Verknüpfen/Trennen im Profil: POST …/start mit {verknuepfen: true, passwort}, Trennen nur, wenn das Konto ein Passwort hat.
"""
import os
import re
import secrets
import sqlite3
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from . import db, konto, oidc, raeume, texte, zwei_faktor
from .kern import (COOKIE, COOKIE_SECURE, SEITE, benutzer_aus_sitzung, client_ip, fehler, grenze, h, iso, jetzt, json_body,
                   pw_ok, sitzung_setzen, text_feld)
from .oidc import OidcFehler

router = APIRouter()
BINDUNG = COOKIE + '-anmeldung'          # Produktion: __Host-agora-anmeldung
VORGANG_S = 600
OFFEN_S = 900
MAX_VERSUCHE = 5
WEITER_RE = re.compile(r'^/(?!/)[\w\-/#?=&.]{0,300}$', re.ASCII)
MS_PRIVAT = '9188040d-6c67-4c5b-b112-36a304b66dad'      # Mandant der privaten Microsoft-Konten (outlook.com, live.com …)


def _env(n, standard=''):
    return (os.environ.get(n) or standard).strip()


def _google_email(c):
    return oidc.ja(c.get('email_verified'))


def _microsoft_email(c):
    """Microsoft bestätigt das Claim `email` bei Firmenkonten NICHT (Mandanten-Admins können jede Adresse eintragen –
    „nOAuth“). Vertrauenswürdig nur: private Microsoft-Konten oder das optionale Claim xms_edov (Domain des Mandanten
    verifiziert), das in der App-Registrierung eingeschaltet werden muss (docs/ANMELDUNG.md)."""
    return c.get('tid') == MS_PRIVAT or oidc.ja(c.get('xms_edov')) or c.get('xms_edov') == 1


def eingebaute():
    """Anbieter aus der Umgebung (bei jedem Aufruf gelesen, damit Tests sie setzen können). Nur mit ID + Geheimnis."""
    tenant = _env('AGORA_MICROSOFT_TENANT', 'common')
    if not re.match(r'^[\w.-]{1,64}$', tenant):
        tenant = 'common'
    alle = {
        'google': {'name': 'Google', 'art': 'oidc', 'scope': 'openid email profile', 'prompt': 'select_account',
                   'discovery': _env('AGORA_GOOGLE_DISCOVERY', 'https://accounts.google.com/.well-known/openid-configuration'),
                   'issuer': _env('AGORA_GOOGLE_ISSUER', 'https://accounts.google.com'), 'weitere_issuer': ('accounts.google.com',),
                   'email_ok': _google_email},
        'microsoft': {'name': 'Microsoft', 'art': 'oidc', 'scope': 'openid email profile', 'prompt': 'select_account',
                      'discovery': f'https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration',
                      'issuer': None, 'email_ok': _microsoft_email},          # Issuer aus der Discovery (ggf. Vorlage)
        'github': {'name': 'GitHub', 'art': 'github', 'scope': 'read:user user:email',
                   'authorize': 'https://github.com/login/oauth/authorize', 'token': 'https://github.com/login/oauth/access_token',
                   'api': 'https://api.github.com'},
    }
    aus = {}
    for k, cfg in alle.items():
        cid, geheim = _env(f'AGORA_{k.upper()}_CLIENT_ID'), _env(f'AGORA_{k.upper()}_CLIENT_SECRET')
        if cid and geheim:
            aus[k] = cfg | {'id': k, 'client_id': cid, 'client_secret': geheim, 'oeffentlich': False}
    return aus


# Weitere Anbieter (Firmen-Login je Organisation, app/firmen_login.py):
#   ZUSATZ[id] = {'start': f(d, b) → (cfg, org_id, login_hint), 'cfg': f(org_id) → cfg | None}
ZUSATZ = {}
NACH_ANMELDUNG = []       # f(b, cfg) – z. B. automatisch der Organisation beitreten


def verfuegbar():
    """Für /api/einstellungen: konfigurierte Anbieter (Reihenfolge fest)."""
    return [{'id': k, 'name': v['name']} for k, v in eingebaute().items()]


def cfg_fuer(anbieter, org_id=None):
    if anbieter in ZUSATZ:
        return ZUSATZ[anbieter]['cfg'](org_id)
    return eingebaute().get(anbieter)


def redirect_uri(anbieter):
    return f'{SEITE}/api/anmeldung/{anbieter}/zurueck'


def weiter_feld(w):
    """Ziel nach der Anmeldung: nur eigene Pfade (kein //fremd, kein Schema, kein Backslash)."""
    return w if isinstance(w, str) and WEITER_RE.match(w) else ''


# ---------------------------------------------------------------- Browser-Bindung
def bindung(req):
    t = req.cookies.get(BINDUNG)
    return t if isinstance(t, str) and 30 <= len(t) <= 100 else None


def bindung_setzen(resp, wert):
    resp.set_cookie(BINDUNG, wert, max_age=1800, httponly=True, secure=COOKIE_SECURE, samesite='lax', path='/')


# ---------------------------------------------------------------- Start
@router.post('/api/anmeldung/{anbieter}/start')
async def start(anbieter: str, req: Request):
    d = await json_body(req)
    grenze.pruefen('anm-start:' + client_ip(req), 30, 600)
    weiter = weiter_feld(d.get('weiter'))
    benutzer_id, b = None, None
    if d.get('verknuepfen') is True:          # aus dem Profil: an das angemeldete Konto hängen
        b = benutzer_aus_sitzung(req)
        grenze.pruefen(f'anm-verkn:{b["id"]}', 10, 900)
        if b['passwort_hash']:
            await zwei_faktor.passwort_pruefen(b, d)
        benutzer_id = b['id']
    org_id, hinweis = None, None
    if anbieter in ZUSATZ:
        cfg, org_id, hinweis = ZUSATZ[anbieter]['start'](d, b)
    else:
        cfg = eingebaute().get(anbieter)
    if not cfg:
        fehler(404, 'Diesen Anmeldeweg gibt es hier nicht.')
    state, nonce = oidc.zufall(), oidc.zufall()
    verifier, challenge = oidc.pkce_paar()
    params = {'response_type': 'code', 'client_id': cfg['client_id'], 'redirect_uri': redirect_uri(anbieter), 'scope': cfg['scope'],
              'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'}
    if cfg['art'] == 'github':
        ziel = cfg['authorize']
        params['allow_signup'] = 'true'
    else:
        try:
            disc = await run_in_threadpool(oidc.discovery, cfg['discovery'], cfg['issuer'], cfg['oeffentlich'])
        except OidcFehler as e:
            db.protokollieren(f'Anmeldung {cfg["name"]}: Anbieter nicht erreichbar ({e.info[:80]})')
            fehler(502, 'Der Anmeldedienst antwortet gerade nicht. Bitte später noch einmal versuchen.')
        ziel = disc['authorization_endpoint']
        params['nonce'] = nonce
        if cfg.get('prompt'):
            params['prompt'] = cfg['prompt']
    if hinweis:
        params['login_hint'] = hinweis
    wert = bindung(req) or oidc.zufall()
    c = db.verbindung()
    c.execute('DELETE FROM anmelde_vorgaenge WHERE gueltig_bis < ?', (jetzt(),))
    c.execute('INSERT INTO anmelde_vorgaenge (state_hash, bindung_hash, anbieter, org_id, verifier, nonce, weiter, benutzer_id, gueltig_bis) '
              'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (h(state), h(wert), anbieter, org_id, verifier, nonce, weiter, benutzer_id, jetzt() + VORGANG_S))
    resp = JSONResponse({'url': ziel + ('&' if '?' in ziel else '?') + urlencode(params)})
    bindung_setzen(resp, wert)
    return resp


# ---------------------------------------------------------------- Rückkehr vom Anbieter
def _umleiten(ziel_seite, grund, anbieter=''):
    """Fehler → Seite mit festem Code (der Browser zeigt den Text; nichts vom Anbieter wird gespiegelt)."""
    q = urlencode({'anmeldung': 'fehler', 'grund': grund} | ({'anbieter': anbieter} if anbieter else {}))
    if ziel_seite == '/konto':
        return RedirectResponse(f'/konto?{q}#profil', status_code=303)
    return RedirectResponse(f'/?{q}', status_code=303)


def vorgang_einloesen(state, wert, anbieter):
    if not isinstance(state, str) or not 20 <= len(state) <= 100 or not wert:
        return None
    c = db.verbindung()
    v = c.execute('SELECT * FROM anmelde_vorgaenge WHERE state_hash = ? AND gueltig_bis > ?', (h(state), jetzt())).fetchone()
    if not v:
        return None
    c.execute('DELETE FROM anmelde_vorgaenge WHERE state_hash = ?', (v['state_hash'],))      # einmalig, auch bei Fehlern
    if not oidc.gleich(v['bindung_hash'], h(wert)) or v['anbieter'] != anbieter:
        return None
    return v


@router.get('/api/anmeldung/{anbieter}/zurueck')
async def zurueck(anbieter: str, req: Request, state: str = '', code: str = '', error: str = ''):
    if not grenze.ok('anm-zurueck:' + client_ip(req), 60, 600):
        return _umleiten('/', 'zu_viele')
    v = vorgang_einloesen(state, bindung(req), anbieter)
    if not v:
        return _umleiten('/', 'ungueltig')
    seite = '/konto' if v['benutzer_id'] else '/'
    cfg = cfg_fuer(anbieter, v['org_id'])
    if not cfg:
        return _umleiten(seite, 'ungueltig')
    if error:
        return _umleiten(seite, 'abgebrochen', anbieter)
    if not code or len(code) > 4000:
        return _umleiten(seite, 'ungueltig')
    try:
        ident = await run_in_threadpool(identitaet_holen, cfg, v, code, anbieter)
    except OidcFehler as e:
        db.protokollieren(f'Anmeldung {cfg["name"]} gescheitert: {e.grund} ({e.info[:100]})', v['benutzer_id'])
        return _umleiten(seite, e.grund if e.grund in ('token', 'anbieter') else 'anbieter', anbieter)
    try:
        return await zuordnen(req, cfg, v, ident, anbieter)
    except HTTPException as e:        # Konflikt beim Anlegen/Verknüpfen → Umleitung statt JSON (Browser-Navigation)
        return _umleiten(seite, 'schon_verknuepft' if e.status_code == 409 else 'ungueltig', anbieter)


def _email(wert):
    e = wert.strip().lower() if isinstance(wert, str) else ''
    return e if len(e) <= 254 and konto.MAIL_RE.match(e) else ''


def _name(wert, email):
    n = text_feld({'n': wert[:200] if isinstance(wert, str) else ''}, 'n', 200).replace('\n', ' ')[:60].strip()
    return n or (email.split('@')[0][:60] if email else 'Agora')


def identitaet_holen(cfg, v, code, anbieter):
    """Code einlösen → {aussteller, sub, email, email_ok, name}."""
    if cfg['art'] == 'github':
        return _github(cfg, v, code, anbieter)
    disc = oidc.discovery(cfg['discovery'], cfg['issuer'], cfg['oeffentlich'])
    tok = oidc.token_tauschen(disc['token_endpoint'], code=code, verifier=v['verifier'], redirect_uri=redirect_uri(anbieter),
                              client_id=cfg['client_id'], client_secret=cfg['client_secret'],
                              auth_methoden=disc.get('token_endpoint_auth_methods_supported'), oeffentlich_pflicht=cfg['oeffentlich'])
    claims = oidc.id_token_pruefen(tok.get('id_token'), issuer=disc['issuer'], client_id=cfg['client_id'], nonce=v['nonce'],
                                   jwks_uri=disc['jwks_uri'], oeffentlich_pflicht=cfg['oeffentlich'],
                                   weitere_issuer=cfg.get('weitere_issuer', ()))
    email = _email(claims.get('email'))
    # Schlüssel der Verknüpfung: Aussteller + sub. Bei einer Vorlage (Microsoft „common“) der Aussteller aus dem Token
    aussteller = claims['iss'] if '{tenantid}' in disc['issuer'] else disc['issuer']
    name = claims.get('name') or ' '.join(x for x in (claims.get('given_name'), claims.get('family_name')) if isinstance(x, str))
    return {'aussteller': aussteller, 'sub': claims['sub'], 'email': email, 'email_ok': bool(email) and cfg['email_ok'](claims),
            'name': _name(name, email)}


def _github(cfg, v, code, anbieter):
    tok = oidc.token_tauschen(cfg['token'], code=code, verifier=v['verifier'], redirect_uri=redirect_uri(anbieter),
                              client_id=cfg['client_id'], client_secret=cfg['client_secret'])
    at = tok.get('access_token')
    if not isinstance(at, str) or not at or len(at) > 500:
        raise OidcFehler('anbieter', 'kein access_token')
    k = {'Authorization': f'Bearer {at}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    u = oidc.holen(cfg['api'] + '/user', kopf=k)
    mails = oidc.holen(cfg['api'] + '/user/emails', kopf=k)
    if not isinstance(u, dict) or not isinstance(u.get('id'), int) or not isinstance(mails, list):
        raise OidcFehler('anbieter', 'Antwort von GitHub unvollständig')
    haupt = next((m for m in mails if isinstance(m, dict) and m.get('primary') is True), None) or {}
    email = _email(haupt.get('email'))
    return {'aussteller': 'https://github.com', 'sub': str(u['id']), 'email': email, 'email_ok': bool(email) and haupt.get('verified') is True,
            'name': _name(u.get('name') or u.get('login'), email)}


# ---------------------------------------------------------------- Konto zuordnen
def _link(aussteller, sub):
    return db.verbindung().execute('SELECT * FROM anmelde_konten WHERE aussteller = ? AND sub = ?', (aussteller, sub)).fetchone()


def verknuepfen(b, anbieter, cfg, ident, org_id=None):
    try:
        db.verbindung().execute('INSERT INTO anmelde_konten (benutzer_id, anbieter, aussteller, sub, email, erstellt, zuletzt) '
                                'VALUES (?, ?, ?, ?, ?, ?, ?)', (b['id'], anbieter, ident['aussteller'], ident['sub'], ident['email'], jetzt(), jetzt()))
    except sqlite3.IntegrityError:
        fehler(409, 'Dieses Konto ist schon mit einem anderen Agora-Konto verknüpft.')
    db.protokollieren(f'Anmeldung verknüpft ({cfg["name"]})', b['id'])


def regel_verstoss(b, cfg):
    """Regeln anderer Module (Firmen-Login erzwungen …) → Fehlertext oder None. Gilt nicht für den Firmen-Login selbst."""
    if cfg.get('firma'):
        return None
    for f in konto.ANMELDE_REGELN:
        t = f(b)
        if t:
            return t
    return None


def anmelden_fertig(b, cfg, weiter):
    """Identität geklärt → Sitzung (oder 2FA-Schritt). Antwort ist eine Umleitung."""
    b = zwei_faktor.benutzer_neu(b['id'])
    if b['gesperrt']:
        db.protokollieren('Anmeldung: Konto gesperrt', b['id'])
        return _umleiten('/', 'gesperrt')
    if regel_verstoss(b, cfg):
        return _umleiten('/', 'firma_pflicht')
    for f in NACH_ANMELDUNG:
        f(b, cfg)
    if b['totp_an']:
        schritt = zwei_faktor.schritt_erstellen(b)
        return RedirectResponse(('/?' + urlencode({'weiter': weiter}) if weiter else '/') + f'#zf={schritt}', status_code=303)
    db.verbindung().execute('UPDATE benutzer SET letzte_anmeldung = ? WHERE id = ?', (jetzt(), b['id']))
    db.protokollieren(f'angemeldet über {cfg["name"]}', b['id'])
    resp = RedirectResponse(weiter or '/', status_code=303)
    sitzung_setzen(resp, b['id'])
    return resp


def neues_konto(ident, cfg):
    c = db.verbindung()
    try:
        bid = c.execute('INSERT INTO benutzer (name, email, passwort_hash, bestaetigt, farbe, erstellt, sprache) VALUES (?, ?, ?, 1, ?, ?, ?)',
                        (ident['name'], ident['email'], '', secrets.choice(konto.FARBEN), jetzt(), texte.sprache())).lastrowid
    except sqlite3.IntegrityError:
        fehler(409, 'Ein Konto mit dieser E-Mail-Adresse wird gerade angelegt – bitte noch einmal versuchen.')
    raeume.persoenlichen_raum_anlegen(bid, ident['name'])
    db.protokollieren(f'registriert über {cfg["name"]}', bid)
    return zwei_faktor.benutzer_neu(bid)


async def zuordnen(req, cfg, v, ident, anbieter):
    c = db.verbindung()
    link = _link(ident['aussteller'], ident['sub'])
    weiter = v['weiter']
    if v['benutzer_id']:                    # Verknüpfen aus dem Profil
        b = benutzer_aus_sitzung(req, pflicht=False)
        if not b or b['id'] != v['benutzer_id']:
            return _umleiten('/konto', 'ungueltig')
        if link:
            return _umleiten('/konto', 'schon_verknuepft' if link['benutzer_id'] != b['id'] else 'schon_da', anbieter)
        if c.execute('SELECT 1 FROM anmelde_konten WHERE benutzer_id = ? AND anbieter = ?' + (' AND org_id = ?' if v['org_id'] else ''),
                     (b['id'], anbieter, *([v['org_id']] if v['org_id'] else []))).fetchone():
            return _umleiten('/konto', 'schon_da', anbieter)
        verknuepfen(b, anbieter, cfg, ident, v['org_id'])
        _org_setzen(ident, v['org_id'])
        return RedirectResponse(f'/konto?verknuepft={quote(anbieter)}#profil', status_code=303)
    if link:
        c.execute('UPDATE anmelde_konten SET zuletzt = ?, email = ? WHERE id = ?', (jetzt(), ident['email'] or link['email'], link['id']))
        return anmelden_fertig(zwei_faktor.benutzer_neu(link['benutzer_id']), cfg, weiter)
    if not ident['email_ok']:
        db.protokollieren(f'Anmeldung {cfg["name"]}: E-Mail-Adresse nicht bestätigt')
        return _umleiten('/', 'email_unbestaetigt', anbieter)
    if cfg.get('email_pruefen'):              # Firmen-Login: Adresse muss zur Domain passen
        grund = cfg['email_pruefen'](ident['email'])
        if grund:
            return _umleiten('/', grund, anbieter)
    b = c.execute('SELECT * FROM benutzer WHERE email = ?', (ident['email'],)).fetchone()
    if b and b['bestaetigt']:
        # Bestehendes Konto: nie still übernehmen – einmal mit dem Agora-Passwort bestätigen
        if not b['passwort_hash']:
            return _umleiten('/', 'kein_passwort', anbieter)
        t = oidc.zufall()
        c.execute('DELETE FROM anmelde_offen WHERE gueltig_bis < ? OR benutzer_id = ?', (jetzt(), b['id']))
        c.execute('INSERT INTO anmelde_offen (token_hash, bindung_hash, benutzer_id, anbieter, org_id, aussteller, sub, email, weiter, gueltig_bis) '
                  'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (h(t), v['bindung_hash'], b['id'], anbieter, v['org_id'], ident['aussteller'],
                                                            ident['sub'], ident['email'], weiter, jetzt() + OFFEN_S))
        return RedirectResponse(('/?' + urlencode({'weiter': weiter}) if weiter else '/') + f'#verknuepfen={t}', status_code=303)
    if b:      # unbestätigt: evtl. von jemand anderem vorab angelegt (Passwort unbekannt) → verwerfen
        db.protokollieren(f'unbestätigtes Konto verworfen (Anmeldung über {cfg["name"]})', b['id'])
        await konto.konto_entfernen(b)
    b = neues_konto(ident, cfg)
    verknuepfen(b, anbieter, cfg, ident, v['org_id'])
    _org_setzen(ident, v['org_id'])
    return anmelden_fertig(b, cfg, weiter)


def _org_setzen(ident, org_id):
    if org_id:
        db.verbindung().execute('UPDATE anmelde_konten SET org_id = ? WHERE aussteller = ? AND sub = ?', (org_id, ident['aussteller'], ident['sub']))


# ---------------------------------------------------------------- Bestehendes Konto: mit Passwort bestätigen
def _offen(req, token):
    wert = bindung(req)
    if not isinstance(token, str) or not 20 <= len(token) <= 100 or not wert:
        return None
    o = db.verbindung().execute('SELECT * FROM anmelde_offen WHERE token_hash = ? AND gueltig_bis > ?', (h(token), jetzt())).fetchone()
    return o if o and oidc.gleich(o['bindung_hash'], h(wert)) else None


ABGELAUFEN = 'Die Anmeldung ist abgelaufen – bitte noch einmal starten.'


@router.post('/api/anmeldung/offen')
async def offen_info(req: Request):
    d = await json_body(req)
    grenze.pruefen('anm-offen:' + client_ip(req), 30, 600)
    o = _offen(req, d.get('token'))
    b = zwei_faktor.benutzer_neu(o['benutzer_id']) if o else None
    cfg = cfg_fuer(o['anbieter'], o['org_id']) if o else None
    if not o or not b or not cfg:
        fehler(400, ABGELAUFEN)
    return {'anbieter': o['anbieter'], 'name': cfg['name'], 'email': b['email'], 'zwei_faktor': bool(b['totp_an'])}


@router.post('/api/anmeldung/verknuepfen')
async def offen_verknuepfen(req: Request):
    d = await json_body(req)
    grenze.pruefen('anm-pw:' + client_ip(req), 20, 600)
    o = _offen(req, d.get('token'))
    c = db.verbindung()
    b = zwei_faktor.benutzer_neu(o['benutzer_id']) if o else None
    cfg = cfg_fuer(o['anbieter'], o['org_id']) if o else None
    if not o or not b or not b['bestaetigt'] or not cfg:
        fehler(400, ABGELAUFEN)
    e = b['email'].lower()
    if grenze.voll('login-fehl:' + e, konto.KONTO_FEHL, 900):        # gleiche Konto-Grenze wie beim Passwort-Login
        fehler(429, 'Zu viele Versuche – bitte kurz warten.')
    c.execute('UPDATE anmelde_offen SET versuche = versuche + 1 WHERE token_hash = ?', (o['token_hash'],))
    if o['versuche'] + 1 > MAX_VERSUCHE:
        c.execute('DELETE FROM anmelde_offen WHERE token_hash = ?', (o['token_hash'],))
        fehler(400, ABGELAUFEN)
    pw = d.get('passwort')
    if not isinstance(pw, str) or len(pw) > 200 or not await run_in_threadpool(pw_ok, b['passwort_hash'], pw):
        grenze.ok('login-fehl:' + e, konto.KONTO_FEHL, 900)
        db.protokollieren('Anmeldung: falsches Passwort', b['id'])
        fehler(400, 'Passwort falsch.')
    if b['totp_an']:
        if not d.get('code'):
            return {'zwei_faktor': True}
        if not zwei_faktor.zweiter_faktor_ok(b, d.get('code')):
            db.protokollieren('Anmeldung: falscher Code', b['id'])
            fehler(400, 'Der Code stimmt nicht.')
    if b['gesperrt']:
        db.protokollieren('Anmeldung: Konto gesperrt', b['id'])
        fehler(403, 'Dieses Konto ist gesperrt. Bei Fragen wende dich an das Agora-Team.')
    t = regel_verstoss(b, cfg)
    if t:
        fehler(403, t)
    c.execute('DELETE FROM anmelde_offen WHERE token_hash = ?', (o['token_hash'],))
    ident = {'aussteller': o['aussteller'], 'sub': o['sub'], 'email': o['email']}
    verknuepfen(b, o['anbieter'], cfg, ident, o['org_id'])
    _org_setzen(ident, o['org_id'])
    for f in NACH_ANMELDUNG:
        f(b, cfg)
    c.execute('UPDATE benutzer SET letzte_anmeldung = ? WHERE id = ?', (jetzt(), b['id']))
    db.protokollieren(f'angemeldet über {cfg["name"]}', b['id'])
    resp = JSONResponse({'ok': True, 'ich': konto.ich_json(zwei_faktor.benutzer_neu(b['id'])), 'weiter': o['weiter']})
    sitzung_setzen(resp, b['id'])
    return resp


# ---------------------------------------------------------------- Profil: verknüpfte Anmeldungen
def _anzeige(row):
    cfg = cfg_fuer(row['anbieter'], row['org_id'])
    return {'id': row['id'], 'anbieter': row['anbieter'], 'name': cfg['name'] if cfg else row['anbieter'], 'email': row['email'],
            'erstellt': iso(row['erstellt']), 'zuletzt': iso(row['zuletzt'])}


@router.get('/api/konto/anmeldungen')
def anmeldungen(req: Request):
    b = benutzer_aus_sitzung(req)
    rows = db.verbindung().execute('SELECT * FROM anmelde_konten WHERE benutzer_id = ? ORDER BY erstellt', (b['id'],)).fetchall()
    return {'verknuepft': [_anzeige(r) for r in rows], 'verfuegbar': verfuegbar(), 'passwort_gesetzt': bool(b['passwort_hash'])}


@router.delete('/api/konto/anmeldungen/{lid}')
def trennen(lid: int, req: Request):
    b = benutzer_aus_sitzung(req)
    c = db.verbindung()
    row = c.execute('SELECT * FROM anmelde_konten WHERE id = ? AND benutzer_id = ?', (lid, b['id'])).fetchone()
    if not row:
        fehler(404, 'Nicht gefunden')
    if not b['passwort_hash']:
        fehler(409, 'Lege zuerst ein Passwort fest – sonst kommst du nicht mehr in dein Konto.')
    c.execute('DELETE FROM anmelde_konten WHERE id = ?', (lid,))
    db.protokollieren(f'Anmeldung getrennt ({_anzeige(row)["name"]})', b['id'])
    return {'ok': True}


def _export(b):
    rows = db.verbindung().execute('SELECT * FROM anmelde_konten WHERE benutzer_id = ? ORDER BY erstellt', (b['id'],)).fetchall()
    return {'anmeldungen': [{'anbieter': r['anbieter'], 'email': r['email'], 'verknuepft': iso(r['erstellt']), 'zuletzt': iso(r['zuletzt'])}
                            for r in rows]}


konto.EXPORT.append(_export)


def aufraeumen(t):
    c = db.verbindung()
    c.execute('DELETE FROM anmelde_vorgaenge WHERE gueltig_bis < ?', (t,))
    c.execute('DELETE FROM anmelde_offen WHERE gueltig_bis < ?', (t,))
