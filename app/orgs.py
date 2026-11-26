"""Teams / Organisationen: Ein Konto legt Organisationen an (Name, Kurzname, Logo-Farbe), lädt Mitglieder per E-Mail ein
(Link, annehmen/ablehnen) und vergibt Rollen owner | admin | mitglied.

Was einer Organisation gehört (Spalte org_id): Räume (jeder owner/admin verwaltet sie und ist darin Host, Mitglieder
sehen die Liste), gemeinsame Vorlagen, die Aufnahmen-Bibliothek mit eigenem Kontingent (Standard 20 GB) und API-Schlüssel,
die nur für die Organisation gelten. Einstellungen je Organisation: Standard-Rechte neuer Räume, „Warteraum für Externe“
(Mitglieder kommen direkt hinein, alle anderen warten) und eine E-Mail-Domain, mit der bestätigte Konten selbst beitreten.

Austritt/Entfernen: Die persönlichen Daten (eigene Räume, Aufnahmen, Vorlagen) bleiben beim Konto. Was die Person für die
Organisation angelegt hat, geht an einen owner über; ihre Schlüssel für die Organisation werden gelöscht. Wird das Konto
gelöscht, passiert dasselbe – war es der letzte owner, wird das älteste admin- bzw. Mitglied owner; war es das letzte
Mitglied, wird die Organisation gelöscht. Organisation löschen (nur owner) entfernt Räume, Vorlagen, Aufnahmen, Schlüssel.

Autorisierung: Wer nicht Mitglied ist, bekommt für alles unter /api/orgs/<slug> 404 (die Organisation „gibt es nicht“),
Mitglieder ohne nötige Rolle 403. Alles läuft über `mitgliedschaft()`.
"""
import json
import os
import re
import secrets

from fastapi import APIRouter, Request

from . import aufnahmen, db, konto, mail, raeume, texte, vorlagen
from .kern import SEITE, benutzer_aus_sitzung, fehler, grenze, h, iso, jetzt, json_body, text_feld

router = APIRouter()
ROLLEN = ('owner', 'admin', 'mitglied')
RANG = {'mitglied': 1, 'admin': 2, 'owner': 3}
SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$')
FARBE_RE = re.compile(r'^#[0-9a-f]{6}$')
DOMAIN_RE = re.compile(r'^(?=.{4,190}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$')
FARBEN = ['#6d4aff', '#3b6ff6', '#0e8a7a', '#b4237a', '#c2410c', '#7c3aed', '#0f766e', '#be123c', '#1d4ed8', '#9333ea']
KONTINGENT = int(float(os.environ.get('AGORA_ORG_KONTINGENT_GB', '20')) * 1024 ** 3)
EINLADUNG_S = 7 * 86400
MAX_EIGENE = 10               # Organisationen, in denen ein Konto owner ist
MAX_MITGLIEDER = 500
MAX_VORLAGEN = 50
# Öffentliche Mail-Anbieter: als „Domain für Selbst-Beitritt“ gesperrt (sonst könnte jede*r mit einer Gmail-Adresse beitreten)
FREEMAIL = {'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com', 'msn.com', 'yahoo.com', 'yahoo.de', 'icloud.com',
            'me.com', 'mac.com', 'gmx.de', 'gmx.at', 'gmx.net', 'web.de', 't-online.de', 'aol.com', 'proton.me', 'protonmail.com',
            'mail.de', 'posteo.de', 'freenet.de', 'yandex.com', 'mail.ru', 'zoho.com', 'gmx.ch', 'bluewin.ch', 'aon.at', 'chello.at'}
STANDARD_EINST = {'rechte': dict(raeume.RECHTE_STANDARD), 'warteraum_extern': False, 'domain': None}
NICHT_DA = 'Diese Organisation gibt es nicht.'


# ---------------------------------------------------------------- Hilfen
def rolle(org_id, benutzer_id):
    """Rolle des Kontos in der Organisation oder None."""
    if not org_id or not benutzer_id:
        return None
    m = db.verbindung().execute('SELECT rolle FROM org_mitglieder WHERE org_id = ? AND benutzer_id = ?', (org_id, benutzer_id)).fetchone()
    return m['rolle'] if m else None


def einstellungen(o):
    e = json.loads(json.dumps(STANDARD_EINST))
    gespeichert = json.loads(o['einstellungen'] or '{}')
    e['rechte'] |= gespeichert.get('rechte') or {}
    e |= {k: v for k, v in gespeichert.items() if k != 'rechte'}
    return e


def mitgliedschaft(b, slug, mindestens='mitglied'):
    """Organisation, in der das Konto mindestens diese Rolle hat → dict(org) + rolle. Kein Mitglied → 404, zu wenig → 403."""
    o = db.verbindung().execute('SELECT * FROM orgs WHERE slug = ?', (slug,)).fetchone() \
        if isinstance(slug, str) and SLUG_RE.match(slug.lower()) else None
    r = rolle(o['id'], b['id']) if o else None
    if not r:
        fehler(404, NICHT_DA)
    if RANG[r] < RANG[mindestens]:
        fehler(403, 'Das darf nur ein Owner der Organisation.' if mindestens == 'owner' else 'Dafür brauchst du Admin-Rechte in dieser Organisation.')
    return dict(o) | {'rolle': r}


def belegt(org_id):
    return db.verbindung().execute('SELECT COALESCE(SUM(groesse), 0) FROM aufnahmen WHERE org_id = ?', (org_id,)).fetchone()[0]


def raum_vorgaben(org_id):
    """Spalten für einen neuen Raum der Organisation: Standard-Rechte (bleiben über Meetings hinweg)."""
    o = db.verbindung().execute('SELECT * FROM orgs WHERE id = ?', (org_id,)).fetchone()
    rechte = json.dumps(einstellungen(o)['rechte'])
    return {'rechte': rechte, 'rechte_standard': rechte}


def org_kurz(o):
    return {'slug': o['slug'], 'name': o['name'], 'farbe': o['farbe']}


def org_json(o, r, ich_id=None):
    """Organisation für Mitglieder (r = eigene Rolle); E-Mail-Adressen der Mitglieder sehen nur owner/admin."""
    c = db.verbindung()
    admin = RANG[r] >= RANG['admin']
    mitglieder = [{'id': m['benutzer_id'], 'name': m['name'], 'farbe': m['bfarbe'], 'rolle': m['rolle'], 'seit': iso(m['seit']),
                   'ich': m['benutzer_id'] == ich_id}
                  | ({'email': m['email']} if admin else {})
                  for m in c.execute('SELECT m.*, b.name, b.email, b.farbe AS bfarbe FROM org_mitglieder m JOIN benutzer b ON b.id = m.benutzer_id '
                                     "WHERE m.org_id = ? ORDER BY CASE m.rolle WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, "
                                     'b.name COLLATE NOCASE', (o['id'],)).fetchall()]
    return org_kurz(o) | {'rolle': r, 'erstellt': iso(o['erstellt']), 'einstellungen': einstellungen(o), 'mitglieder': mitglieder,
                          'belegt': belegt(o['id']), 'kontingent': o['kontingent'],
                          'raeume': c.execute('SELECT COUNT(*) FROM raeume WHERE org_id = ?', (o['id'],)).fetchone()[0]}


def name_feld(d):
    n = text_feld(d, 'name', 80).replace('\n', ' ')
    if len(n) < 2:
        fehler(400, 'Bitte einen Namen für die Organisation eingeben (mindestens 2 Zeichen).')
    return n


def slug_aus_name(name):
    ersatz = {'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss'}
    s = ''.join(ersatz.get(ch, ch) for ch in name.lower())
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:40].strip('-')
    if len(s) >= 3:
        return s
    return f'{s}-team' if s else f'team-{secrets.token_hex(3)}'


def farbe_feld(d, alt=None):
    f = d.get('farbe', alt)
    if f is None:
        return secrets.choice(FARBEN)
    if not isinstance(f, str) or not FARBE_RE.match(f.lower()):
        fehler(400, 'Farbe bitte als #rrggbb.')
    return f.lower()


def email_domain(email):
    return email.rsplit('@', 1)[-1].lower()


def einstellungen_lesen(d, b, alt):
    """PATCH-Feld einstellungen → geprüfte Einstellungen (fehlende Felder bleiben)."""
    e = d.get('einstellungen')
    if not isinstance(e, dict):
        fehler(400, 'Ungültige Einstellungen.')
    neu = json.loads(json.dumps(alt))
    if 'rechte' in e:
        if not isinstance(e['rechte'], dict):
            fehler(400, 'Ungültige Einstellungen.')
        for k, v in e['rechte'].items():
            if k not in raeume.RECHTE_STANDARD or not isinstance(v, bool):
                fehler(400, 'Rechte sind true oder false.')
            neu['rechte'][k] = v
    if 'warteraum_extern' in e:
        if not isinstance(e['warteraum_extern'], bool):
            fehler(400, 'Ungültige Einstellungen.')
        neu['warteraum_extern'] = e['warteraum_extern']
    if 'domain' in e:
        dom = e['domain']
        if dom in (None, ''):
            neu['domain'] = None
        else:
            dom = dom.strip().lower().lstrip('@') if isinstance(dom, str) else ''
            if not DOMAIN_RE.match(dom):
                fehler(400, 'Ungültige Domain (z. B. firma.de).')
            if dom in FREEMAIL:
                fehler(400, 'Domains öffentlicher Mail-Anbieter sind für den Selbst-Beitritt nicht erlaubt.')
            if email_domain(b['email']) != dom:
                fehler(400, 'Die Domain muss zu deiner eigenen E-Mail-Adresse passen.')
            neu['domain'] = dom
    return neu


def neuer_owner(org_id, ausser):
    """Wer übernimmt? Ältester owner, sonst ältestes admin-, sonst ältestes Mitglied (ohne `ausser`) – oder None."""
    m = db.verbindung().execute(
        "SELECT benutzer_id, rolle FROM org_mitglieder WHERE org_id = ? AND benutzer_id != ? "
        "ORDER BY CASE rolle WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, seit, benutzer_id LIMIT 1", (org_id, ausser)).fetchone()
    return m


def mitglied_entfernen(org_id, benutzer_id, grund):
    """Austritt/Entfernen: Daten der Organisation, die das Konto angelegt hat, gehen an einen owner; persönliche Daten bleiben."""
    c = db.verbindung()
    nachfolge = neuer_owner(org_id, benutzer_id)
    if nachfolge:
        if nachfolge['rolle'] != 'owner' and not c.execute(
                "SELECT 1 FROM org_mitglieder WHERE org_id = ? AND rolle = 'owner' AND benutzer_id != ?", (org_id, benutzer_id)).fetchone():
            c.execute("UPDATE org_mitglieder SET rolle = 'owner' WHERE org_id = ? AND benutzer_id = ?", (org_id, nachfolge['benutzer_id']))
        neu = nachfolge['benutzer_id']
        c.execute('UPDATE meeting_verlauf SET besitzer_id = ? WHERE besitzer_id = ? AND raum_id IN (SELECT id FROM raeume WHERE org_id = ?)',
                  (neu, benutzer_id, org_id))
        c.execute('UPDATE raeume SET besitzer_id = ? WHERE org_id = ? AND besitzer_id = ?', (neu, org_id, benutzer_id))
        c.execute('UPDATE vorlagen SET benutzer_id = ? WHERE org_id = ? AND benutzer_id = ?', (neu, org_id, benutzer_id))
        c.execute('UPDATE aufnahmen SET benutzer_id = ? WHERE org_id = ? AND benutzer_id = ?', (neu, org_id, benutzer_id))
    c.execute('DELETE FROM api_schluessel WHERE org_id = ? AND benutzer_id = ?', (org_id, benutzer_id))
    c.execute('DELETE FROM org_mitglieder WHERE org_id = ? AND benutzer_id = ?', (org_id, benutzer_id))
    db.protokollieren(f'Organisation: {grund} (#{org_id})', benutzer_id)


async def org_loeschen(o):
    """Organisation samt Räumen (laufende Meetings werden beendet), Vorlagen, Aufnahmen und Schlüsseln löschen."""
    c = db.verbindung()
    for r in c.execute('SELECT * FROM raeume WHERE org_id = ? AND aktiv = 1', (o['id'],)).fetchall():
        await raeume.meeting_beenden(r, 'Organisation gelöscht')
    for a in c.execute('SELECT id FROM aufnahmen WHERE org_id = ?', (o['id'],)).fetchall():
        aufnahmen.aufnahme_entfernen(a['id'])
    c.execute('DELETE FROM orgs WHERE id = ?', (o['id'],))      # Räume, Vorlagen, Mitglieder, Einladungen, Schlüssel per CASCADE


def laden_mit_id(org_id):
    return db.verbindung().execute('SELECT * FROM orgs WHERE id = ?', (org_id,)).fetchone()


# ---------------------------------------------------------------- Haken in andere Module
def _raum_json(r, b):
    if not r['org_id']:
        return {}
    o = laden_mit_id(r['org_id'])
    return {'org': org_kurz(o) | {'rolle': rolle(o['id'], b['id']) if b else None}} if o else {}


def _warteraum(r, b):
    """„Warteraum für Externe“: Mitglieder direkt hinein, alle anderen (auch Gäste) warten."""
    if not r['org_id']:
        return None
    o = laden_mit_id(r['org_id'])
    if not o or not einstellungen(o)['warteraum_extern']:
        return None
    return not (b and rolle(o['id'], b['id']))


def _aufnahme_ziel(benutzer_id, raum_code):
    """Aufnahme aus einem Raum der Organisation (von einem Mitglied) → Bibliothek der Organisation."""
    if not raum_code:
        return None
    r = db.verbindung().execute('SELECT org_id FROM raeume WHERE code = ?', (raum_code,)).fetchone()
    if not r or not r['org_id'] or not rolle(r['org_id'], benutzer_id):
        return None
    o = laden_mit_id(r['org_id'])
    return {'org_id': o['id'], 'belegt': lambda: belegt(o['id']), 'kontingent': o['kontingent'],
            'voll': 'Der Speicher der Organisation für Aufnahmen ist voll. Ein Admin kann alte Aufnahmen löschen.'}


async def _konto_weg(b):
    """Vor dem Löschen eines Kontos: Organisationen übergeben bzw. (als letztes Mitglied) löschen."""
    c = db.verbindung()
    for m in c.execute('SELECT * FROM org_mitglieder WHERE benutzer_id = ?', (b['id'],)).fetchall():
        o = laden_mit_id(m['org_id'])
        if not neuer_owner(o['id'], b['id']):
            await org_loeschen(o)
        else:
            mitglied_entfernen(o['id'], b['id'], 'Konto gelöscht')


def _export(b):
    c = db.verbindung()
    mitglied = [org_kurz(o) | {'rolle': o['rolle'], 'seit': iso(o['seit'])} for o in c.execute(
        'SELECT o.*, m.rolle, m.seit FROM org_mitglieder m JOIN orgs o ON o.id = m.org_id WHERE m.benutzer_id = ? ORDER BY m.seit',
        (b['id'],)).fetchall()]
    offen = [{'organisation': e['name'], 'rolle': e['rolle'], 'erstellt': iso(e['erstellt']), 'gueltig_bis': iso(e['gueltig_bis'])}
             for e in c.execute('SELECT e.*, o.name FROM org_einladungen e JOIN orgs o ON o.id = e.org_id WHERE e.email = ? AND e.gueltig_bis > ?',
                                (b['email'], jetzt())).fetchall()]
    return {'organisationen': mitglied, 'organisationen_einladungen': offen}


raeume.RAUM_JSON_EXTRA.append(_raum_json)
raeume.WARTERAUM_REGEL.append(_warteraum)
aufnahmen.ZIEL.append(_aufnahme_ziel)
konto.VOR_LOESCHEN.append(_konto_weg)
konto.EXPORT.append(_export)


def aufraeumen(t):
    db.verbindung().execute('DELETE FROM org_einladungen WHERE gueltig_bis < ?', (t,))


# ---------------------------------------------------------------- Organisationen
@router.post('/api/orgs')
async def anlegen(req: Request):
    b = benutzer_aus_sitzung(req)
    d = await json_body(req)
    grenze.pruefen(f'org:{b["id"]}', 10, 3600)
    name = name_feld(d)
    slug = d.get('slug') or slug_aus_name(name)
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        fehler(400, 'Kurzname: 3 bis 40 Zeichen aus a–z, 0–9 und Bindestrich.')
    c = db.verbindung()
    if not d.get('slug') and c.execute('SELECT 1 FROM orgs WHERE slug = ?', (slug,)).fetchone():
        slug = f'{slug[:33].rstrip("-")}-{secrets.token_hex(3)}'       # aus dem Namen abgeleitet: eindeutig machen
    if c.execute("SELECT COUNT(*) FROM org_mitglieder WHERE benutzer_id = ? AND rolle = 'owner'", (b['id'],)).fetchone()[0] >= MAX_EIGENE:
        fehler(409, f'Höchstens {MAX_EIGENE} eigene Organisationen.')
    if c.execute('SELECT 1 FROM orgs WHERE slug = ?', (slug,)).fetchone():
        fehler(409, 'Dieser Kurzname ist schon vergeben.')
    t = jetzt()
    oid = c.execute('INSERT INTO orgs (name, slug, farbe, einstellungen, kontingent, erstellt) VALUES (?, ?, ?, ?, ?, ?)',
                    (name, slug, farbe_feld(d), '{}', KONTINGENT, t)).lastrowid
    c.execute("INSERT INTO org_mitglieder VALUES (?, ?, 'owner', ?)", (oid, b['id'], t))
    db.protokollieren(f'Organisation angelegt ({slug})', b['id'])
    return {'org': org_json(laden_mit_id(oid), 'owner', b['id'])}


@router.get('/api/orgs')
def liste(req: Request):
    """Eigene Organisationen, offene Einladungen an die eigene Adresse und Organisationen mit passender Domain."""
    b = benutzer_aus_sitzung(req)
    c = db.verbindung()
    orgs = [org_kurz(o) | {'rolle': o['rolle'], 'mitglieder': o['n']} for o in c.execute(
        'SELECT o.*, m.rolle, (SELECT COUNT(*) FROM org_mitglieder x WHERE x.org_id = o.id) AS n '
        'FROM org_mitglieder m JOIN orgs o ON o.id = m.org_id WHERE m.benutzer_id = ? ORDER BY o.name COLLATE NOCASE', (b['id'],)).fetchall()]
    einladungen = [einladung_json(e) for e in c.execute(
        'SELECT e.*, o.name, o.slug, o.farbe, v.name AS von_name FROM org_einladungen e JOIN orgs o ON o.id = e.org_id '
        'LEFT JOIN benutzer v ON v.id = e.von_id WHERE e.email = ? AND e.gueltig_bis > ? ORDER BY e.erstellt DESC', (b['email'], jetzt())).fetchall()
        if not rolle(e['org_id'], b['id'])]
    dom = email_domain(b['email'])
    beitretbar = [org_kurz(o) for o in c.execute(
        "SELECT * FROM orgs WHERE json_extract(einstellungen, '$.domain') = ? AND id NOT IN "
        '(SELECT org_id FROM org_mitglieder WHERE benutzer_id = ?) ORDER BY name LIMIT 20', (dom, b['id'])).fetchall()]
    return {'orgs': orgs, 'einladungen': einladungen, 'beitretbar': beitretbar}


@router.get('/api/orgs/{slug}')
def info(slug: str, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug)
    return {'org': org_json(o, o['rolle'], b['id'])}


@router.patch('/api/orgs/{slug}')
async def aendern(slug: str, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug, 'admin')
    d = await json_body(req)
    c = db.verbindung()
    if 'name' in d:
        c.execute('UPDATE orgs SET name = ? WHERE id = ?', (name_feld(d), o['id']))
    if 'farbe' in d:
        c.execute('UPDATE orgs SET farbe = ? WHERE id = ?', (farbe_feld(d, o['farbe']), o['id']))
    if 'einstellungen' in d:
        e = einstellungen_lesen(d, b, einstellungen(o))
        c.execute('UPDATE orgs SET einstellungen = ? WHERE id = ?', (json.dumps(e), o['id']))
    db.protokollieren(f'Organisation geändert ({o["slug"]})', b['id'])
    return {'org': org_json(laden_mit_id(o['id']), o['rolle'], b['id'])}


@router.delete('/api/orgs/{slug}')
async def loeschen(slug: str, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug, 'owner')
    d = await json_body(req)
    if d.get('bestaetigung') != o['slug']:
        fehler(400, 'Zum Löschen bitte den Kurznamen der Organisation eingeben.')
    await org_loeschen(o)
    db.protokollieren(f'Organisation gelöscht ({o["slug"]})', b['id'])
    return {'ok': True}


# ---------------------------------------------------------------- Mitglieder
@router.post('/api/orgs/{slug}/verlassen')
def verlassen(slug: str, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug)
    c = db.verbindung()
    if o['rolle'] == 'owner' and not c.execute("SELECT 1 FROM org_mitglieder WHERE org_id = ? AND rolle = 'owner' AND benutzer_id != ?",
                                               (o['id'], b['id'])).fetchone():
        fehler(409, 'Du bist der letzte Owner: Ernenne zuerst einen anderen Owner oder lösche die Organisation.')
    mitglied_entfernen(o['id'], b['id'], 'verlassen')
    return {'ok': True}


@router.patch('/api/orgs/{slug}/mitglieder/{bid}')
async def rolle_aendern(slug: str, bid: int, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug, 'owner')
    d = await json_body(req)
    neu = d.get('rolle')
    if neu not in ROLLEN:
        fehler(400, 'Rolle: owner, admin oder mitglied.')
    c = db.verbindung()
    alt = rolle(o['id'], bid)
    if not alt:
        fehler(404, 'Mitglied nicht gefunden')
    if alt == 'owner' and neu != 'owner' and not c.execute(
            "SELECT 1 FROM org_mitglieder WHERE org_id = ? AND rolle = 'owner' AND benutzer_id != ?", (o['id'], bid)).fetchone():
        fehler(409, 'Die Organisation braucht mindestens einen Owner.')
    c.execute('UPDATE org_mitglieder SET rolle = ? WHERE org_id = ? AND benutzer_id = ?', (neu, o['id'], bid))
    if RANG[neu] < RANG['admin']:        # Schlüssel der Organisation gibt es nur für owner/admin
        c.execute('DELETE FROM api_schluessel WHERE org_id = ? AND benutzer_id = ?', (o['id'], bid))
    db.protokollieren(f'Organisation: Rolle {neu} für #{bid} ({o["slug"]})', b['id'])
    return {'org': org_json(laden_mit_id(o['id']), rolle(o['id'], b['id']), b['id'])}


@router.delete('/api/orgs/{slug}/mitglieder/{bid}')
def entfernen(slug: str, bid: int, req: Request):
    """owner/admin entfernen Mitglieder; owner/admin entfernen kann nur ein owner (sich selbst: „verlassen“)."""
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug, 'admin')
    ziel = rolle(o['id'], bid)
    if not ziel:
        fehler(404, 'Mitglied nicht gefunden')
    if bid == b['id']:
        fehler(400, 'Zum Austreten bitte „Organisation verlassen“ benutzen.')
    if RANG[ziel] >= RANG['admin'] and o['rolle'] != 'owner':
        fehler(403, 'Das darf nur ein Owner der Organisation.')
    mitglied_entfernen(o['id'], bid, f'entfernt von #{b["id"]}')
    return {'org': org_json(laden_mit_id(o['id']), o['rolle'], b['id'])}


@router.post('/api/orgs/{slug}/beitreten')
def selbst_beitreten(slug: str, req: Request):
    """Selbst beitreten (als mitglied), wenn die eigene, bestätigte E-Mail-Adresse zur Domain der Organisation passt."""
    b = benutzer_aus_sitzung(req)
    c = db.verbindung()
    o = c.execute('SELECT * FROM orgs WHERE slug = ?', (slug,)).fetchone() if SLUG_RE.match(slug.lower()) else None
    if not o or einstellungen(o)['domain'] != email_domain(b['email']):
        fehler(404, NICHT_DA)
    if not rolle(o['id'], b['id']):
        _aufnehmen(o, b, 'mitglied', 'Domain')
    return {'org': org_json(o, rolle(o['id'], b['id']), b['id'])}


def _aufnehmen(o, b, r, wie):
    c = db.verbindung()
    if c.execute('SELECT COUNT(*) FROM org_mitglieder WHERE org_id = ?', (o['id'],)).fetchone()[0] >= MAX_MITGLIEDER:
        fehler(409, f'Höchstens {MAX_MITGLIEDER} Mitglieder je Organisation.')
    c.execute('INSERT OR IGNORE INTO org_mitglieder VALUES (?, ?, ?, ?)', (o['id'], b['id'], r, jetzt()))
    c.execute('DELETE FROM org_einladungen WHERE org_id = ? AND email = ?', (o['id'], b['email']))
    db.protokollieren(f'Organisation beigetreten ({o["slug"]}, {wie})', b['id'])


# ---------------------------------------------------------------- Einladungen
def einladung_json(e):
    return {'id': e['id'], 'org': {'slug': e['slug'], 'name': e['name'], 'farbe': e['farbe']}, 'rolle': e['rolle'],
            'von_name': e['von_name'] or '', 'erstellt': iso(e['erstellt']), 'gueltig_bis': iso(e['gueltig_bis'])}


@router.post('/api/orgs/{slug}/einladungen')
async def einladen(slug: str, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug, 'admin')
    d = await json_body(req)
    r = d.get('rolle', 'mitglied')
    if r not in ('admin', 'mitglied'):
        fehler(400, 'Rolle: admin oder mitglied.')
    if r == 'admin' and o['rolle'] != 'owner':
        fehler(403, 'Das darf nur ein Owner der Organisation.')
    emails = d.get('emails')
    if not isinstance(emails, list) or not 1 <= len(emails) <= 20:
        fehler(400, 'Bitte 1 bis 20 E-Mail-Adressen angeben.')
    adressen = []
    for e in emails:
        if not isinstance(e, str) or not konto.zustellbar(e.strip()):
            fehler(400, f'Ungültige E-Mail-Adresse: {str(e)[:80]}')
        if e.strip().lower() not in adressen:
            adressen.append(e.strip().lower())
    grenze.pruefen(f'org-einladen:{o["id"]}', 100, 86400)
    c = db.verbindung()
    gesendet, schon = 0, 0
    for a in adressen:
        if c.execute('SELECT 1 FROM org_mitglieder m JOIN benutzer x ON x.id = m.benutzer_id WHERE m.org_id = ? AND x.email = ?',
                     (o['id'], a)).fetchone():
            schon += 1
            continue
        token = secrets.token_urlsafe(32)
        c.execute('INSERT OR REPLACE INTO org_einladungen (org_id, email, rolle, token_hash, von_id, erstellt, gueltig_bis) '
                  'VALUES (?, ?, ?, ?, ?, ?, ?)', (o['id'], a, r, h(token), b['id'], jetzt(), jetzt() + EINLADUNG_S))
        if grenze.ok('mail:' + a, 3, 3600):
            mail.im_hintergrund(mail.org_einladung_mail, a, b['name'], o['name'], f'{SEITE}/konto?einladung={token}#teams', texte.sprache())
            gesendet += 1
    db.protokollieren(f'Organisation: Einladungen verschickt ({gesendet}, {o["slug"]})', b['id'])
    return {'ok': True, 'gesendet': gesendet, 'schon_mitglied': schon}


@router.get('/api/orgs/{slug}/einladungen')
def einladungen(slug: str, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug, 'admin')
    rows = db.verbindung().execute('SELECT * FROM org_einladungen WHERE org_id = ? AND gueltig_bis > ? ORDER BY erstellt DESC',
                                   (o['id'], jetzt())).fetchall()
    return {'einladungen': [{'id': e['id'], 'email': e['email'], 'rolle': e['rolle'], 'erstellt': iso(e['erstellt']),
                             'gueltig_bis': iso(e['gueltig_bis'])} for e in rows]}


@router.delete('/api/orgs/{slug}/einladungen/{eid}')
def einladung_zurueckziehen(slug: str, eid: int, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug, 'admin')
    if not db.verbindung().execute('DELETE FROM org_einladungen WHERE id = ? AND org_id = ?', (eid, o['id'])).rowcount:
        fehler(404, 'Einladung nicht gefunden')
    return {'ok': True}


def eigene_einladung(b, eid=None, token=None):
    """Offene Einladung an die E-Mail-Adresse dieses Kontos (per ID oder Link-Token)."""
    c = db.verbindung()
    q = ('SELECT e.*, o.name, o.slug, o.farbe, v.name AS von_name FROM org_einladungen e JOIN orgs o ON o.id = e.org_id '
         'LEFT JOIN benutzer v ON v.id = e.von_id WHERE e.gueltig_bis > ? AND ')
    if token is not None:
        e = c.execute(q + 'e.token_hash = ?', (jetzt(), h(token))).fetchone() if isinstance(token, str) and 20 <= len(token) <= 100 else None
        if e and e['email'].lower() != b['email'].lower():
            fehler(403, 'Diese Einladung gilt für eine andere E-Mail-Adresse. Melde dich mit dem eingeladenen Konto an.')
    else:
        e = c.execute(q + 'e.id = ? AND e.email = ?', (jetzt(), eid, b['email'])).fetchone()
    if not e:
        fehler(404, 'Diese Einladung gibt es nicht (mehr).')
    return e


@router.post('/api/org-einladungen/link')
async def einladung_aus_link(req: Request):
    b = benutzer_aus_sitzung(req)
    d = await json_body(req)
    grenze.pruefen(f'org-link:{b["id"]}', 30, 600)
    return {'einladung': einladung_json(eigene_einladung(b, token=d.get('token')))}


@router.post('/api/org-einladungen/{eid}/annehmen')
def annehmen(eid: int, req: Request):
    b = benutzer_aus_sitzung(req)
    e = eigene_einladung(b, eid)
    o = laden_mit_id(e['org_id'])
    if not rolle(o['id'], b['id']):
        _aufnehmen(o, b, e['rolle'], 'Einladung')
    db.verbindung().execute('DELETE FROM org_einladungen WHERE id = ?', (eid,))
    return {'org': org_json(o, rolle(o['id'], b['id']), b['id'])}


@router.post('/api/org-einladungen/{eid}/ablehnen')
def ablehnen(eid: int, req: Request):
    b = benutzer_aus_sitzung(req)
    eigene_einladung(b, eid)
    db.verbindung().execute('DELETE FROM org_einladungen WHERE id = ?', (eid,))
    return {'ok': True}


# ---------------------------------------------------------------- Räume der Organisation
@router.get('/api/orgs/{slug}/raeume')
def raeume_liste(slug: str, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug)
    rows = db.verbindung().execute('SELECT * FROM raeume WHERE org_id = ? ORDER BY erstellt DESC', (o['id'],)).fetchall()
    geplant, vergangen, _p = raeume.einteilen(rows, b)
    return {'geplant': geplant, 'vergangen': vergangen[:50]}


@router.post('/api/orgs/{slug}/raeume')
async def raum_neu(slug: str, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug, 'admin')
    d = await json_body(req)
    r = await raeume.raum_aus_body(b, d, o['id'], raum_vorgaben(o['id']))
    return {'raum': raeume.raum_json(r, b)}


# ---------------------------------------------------------------- Gemeinsame Vorlagen
def vorlage_laden(o, vid):
    v = db.verbindung().execute('SELECT * FROM vorlagen WHERE id = ? AND org_id = ?', (vid, o['id'])).fetchone()
    if not v:
        fehler(404, 'Vorlage nicht gefunden')
    return v


@router.get('/api/orgs/{slug}/vorlagen')
def vorlagen_liste(slug: str, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug)
    rows = db.verbindung().execute('SELECT * FROM vorlagen WHERE org_id = ? ORDER BY name COLLATE NOCASE, id', (o['id'],)).fetchall()
    return {'vorlagen': [vorlagen.vorlage_json(v) for v in rows]}


@router.post('/api/orgs/{slug}/vorlagen')
async def vorlage_neu(slug: str, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug, 'admin')
    d = await json_body(req)
    grenze.pruefen(f'vorlage:{b["id"]}', 60, 3600)
    name = vorlagen.name_feld(d, True)
    if d.get('aus_raum') is not None:
        r = raeume.raum_laden(d['aus_raum'] if isinstance(d['aus_raum'], str) else '')
        if r['org_id'] != o['id']:
            fehler(403, 'Nur der Besitzer darf das.')
        e = vorlagen.aus_raum(r)
    else:
        e = vorlagen.einstellungen_pruefen(d.get('einstellungen', {}))
    c = db.verbindung()
    if c.execute('SELECT COUNT(*) FROM vorlagen WHERE org_id = ?', (o['id'],)).fetchone()[0] >= MAX_VORLAGEN:
        fehler(409, f'Höchstens {MAX_VORLAGEN} Vorlagen.')
    vid = c.execute('INSERT INTO vorlagen (benutzer_id, name, einstellungen, erstellt, geaendert, org_id) VALUES (?, ?, ?, ?, ?, ?)',
                    (b['id'], name, json.dumps(e), jetzt(), jetzt(), o['id'])).lastrowid
    return {'vorlage': vorlagen.vorlage_json(vorlage_laden(o, vid))}


@router.put('/api/orgs/{slug}/vorlagen/{vid}')
async def vorlage_aendern(slug: str, vid: int, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug, 'admin')
    v = vorlage_laden(o, vid)
    d = await json_body(req)
    name = vorlagen.name_feld(d, False) or v['name']
    e = vorlagen.einstellungen_pruefen(d['einstellungen']) if 'einstellungen' in d else json.loads(v['einstellungen'])
    db.verbindung().execute('UPDATE vorlagen SET name = ?, einstellungen = ?, geaendert = ? WHERE id = ?', (name, json.dumps(e), jetzt(), vid))
    return {'vorlage': vorlagen.vorlage_json(vorlage_laden(o, vid))}


@router.delete('/api/orgs/{slug}/vorlagen/{vid}')
def vorlage_loeschen(slug: str, vid: int, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug, 'admin')
    vorlage_laden(o, vid)
    db.verbindung().execute('DELETE FROM vorlagen WHERE id = ?', (vid,))
    return {'ok': True}


@router.post('/api/orgs/{slug}/vorlagen/{vid}/raum')
async def raum_aus_vorlage(slug: str, vid: int, req: Request):
    """Meeting aus einer gemeinsamen Vorlage: ziel „org“ (Raum der Organisation, nur owner/admin) oder „konto“ (eigener
    Raum – das dürfen alle Mitglieder). Übrige Felder wie POST /api/vorlagen/{id}/raum."""
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug)
    e = vorlagen.vorlage_json(vorlage_laden(o, vid))['einstellungen']
    d = await json_body(req)
    ziel = d.pop('ziel', 'org')
    if ziel not in ('org', 'konto'):
        fehler(400, 'Ziel: org oder konto.')
    if ziel == 'org' and RANG[o['rolle']] < RANG['admin']:
        fehler(403, 'Dafür brauchst du Admin-Rechte in dieser Organisation.')
    r, passcode = await vorlagen.raum_aus_einstellungen(b, e, d, o['id'] if ziel == 'org' else None)
    return {'raum': raeume.raum_json(r, b), 'passcode': passcode}


# ---------------------------------------------------------------- Aufnahmen der Organisation
def aufnahme_laden(o, i):
    a = db.verbindung().execute('SELECT * FROM aufnahmen WHERE id = ? AND org_id = ?', (i, o['id'])).fetchone() \
        if aufnahmen.ID_RE.match(i) else None
    if not a:
        fehler(404, 'Aufnahme nicht gefunden')
    return a


@router.get('/api/orgs/{slug}/aufnahmen')
def aufnahmen_liste(slug: str, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug)
    rows = db.verbindung().execute('SELECT * FROM aufnahmen WHERE org_id = ? ORDER BY erstellt DESC', (o['id'],)).fetchall()
    return {'aufnahmen': [aufnahmen.aufnahme_json(a) | {'freigabe': None} for a in rows], 'belegt': belegt(o['id']), 'kontingent': o['kontingent']}


@router.get('/api/orgs/{slug}/aufnahmen/{i}/datei')
def aufnahme_datei(slug: str, i: str, req: Request, download: int = 0):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug)
    return aufnahmen.datei_antwort(aufnahme_laden(o, i), bool(download))


@router.delete('/api/orgs/{slug}/aufnahmen/{i}')
def aufnahme_loeschen(slug: str, i: str, req: Request):
    b = benutzer_aus_sitzung(req)
    o = mitgliedschaft(b, slug, 'admin')
    a = aufnahme_laden(o, i)
    aufnahmen.aufnahme_entfernen(a['id'])
    db.protokollieren(f'Aufnahme gelöscht (Organisation {o["slug"]})', b['id'])
    return {'ok': True}


# ---------------------------------------------------------------- Admin-Bereich
@router.get('/api/admin/orgs')
def admin_liste(req: Request, q: str = ''):
    from .admin import admin
    admin(req)
    c = db.verbindung()
    q = q.strip()[:100]
    muster = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    rows = c.execute("SELECT * FROM orgs WHERE name LIKE ? ESCAPE '\\' OR slug LIKE ? ESCAPE '\\' ORDER BY erstellt DESC LIMIT 200",
                     (muster, muster)).fetchall()
    aus = []
    for o in rows:
        owner = c.execute("SELECT b.name, b.email FROM org_mitglieder m JOIN benutzer b ON b.id = m.benutzer_id "
                          "WHERE m.org_id = ? AND m.rolle = 'owner' ORDER BY m.seit LIMIT 1", (o['id'],)).fetchone()
        aus.append(org_kurz(o) | {
            'id': o['id'], 'erstellt': iso(o['erstellt']), 'kontingent': o['kontingent'], 'belegt': belegt(o['id']),
            'mitglieder': c.execute('SELECT COUNT(*) FROM org_mitglieder WHERE org_id = ?', (o['id'],)).fetchone()[0],
            'raeume': c.execute('SELECT COUNT(*) FROM raeume WHERE org_id = ?', (o['id'],)).fetchone()[0],
            'domain': einstellungen(o)['domain'], 'owner': {'name': owner['name'], 'email': owner['email']} if owner else None})
    return {'orgs': aus, 'gesamt': c.execute('SELECT COUNT(*) FROM orgs').fetchone()[0]}
