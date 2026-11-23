"""Meeting-Vorlagen: benannte Einstellungen (Titel, Dauer, Passcode an/aus, Warteraum, Chat speichern, Beschreibung,
Standard-Rechte, „alle stumm beim Beitritt“), aus denen man ein Sofort-Meeting startet oder einen Termin plant.

Ein Raum aus einer Vorlage merkt sich die Standard-Rechte (`raeume.rechte_standard`): Ändert der Host die Rechte im
Meeting, gilt das nur für dieses Meeting – beim Meeting-Ende werden die Rechte auf den Standard zurückgesetzt, jedes
neue Meeting beginnt also mit den Vorgaben der Vorlage. „Alle stumm beim Beitritt“ (`raeume.stumm_beitritt`) steht im
Zugang (`zugang.stumm_beitritt`) für alle außer dem Host: Der Client tritt mit stummem Mikrofon bei; ist zusätzlich
das Recht „selbst_unmute“ aus, kann er sich erst nach Freigabe durch den Host entstummen.
"""
import json
import secrets

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from . import db, konto, raeume
from .kern import benutzer_aus_sitzung, fehler, grenze, hash_pw, iso, jetzt, json_body, text_feld

router = APIRouter()
MAX_VORLAGEN = 20
STANDARD = {'titel': '', 'dauer_min': 60, 'passcode': False, 'warteraum': False, 'chat_speichern': False, 'beschreibung': '',
            'rechte': dict(raeume.RECHTE_STANDARD), 'stumm_beitritt': False}
PASSCODE_ZEICHEN = 'abcdefghjkmnpqrstuvwxyz23456789'


def _beim_ende(r):
    """Rechte nach dem Meeting auf die Vorgaben der Vorlage zurücksetzen."""
    if r['rechte_standard']:
        db.verbindung().execute('UPDATE raeume SET rechte = rechte_standard WHERE id = ?', (r['id'],))


raeume.BEIM_ENDE.append(_beim_ende)


def einstellungen_pruefen(e):
    """Eingabe → vollständige, geprüfte Einstellungen (fehlende Felder = Standard)."""
    if not isinstance(e, dict):
        fehler(400, 'Ungültige Einstellungen.')
    aus = json.loads(json.dumps(STANDARD))
    if 'titel' in e:
        aus['titel'] = text_feld(e, 'titel', 120).replace('\n', ' ')
    if 'beschreibung' in e:
        aus['beschreibung'] = text_feld(e, 'beschreibung', 2000)
    if 'dauer_min' in e:
        d = e['dauer_min']
        if not isinstance(d, int) or isinstance(d, bool) or not 5 <= d <= 1440:
            fehler(400, 'Dauer: 5 bis 1440 Minuten.')
        aus['dauer_min'] = d
    for k in ('passcode', 'warteraum', 'chat_speichern', 'stumm_beitritt'):
        if k in e:
            if not isinstance(e[k], bool):
                fehler(400, 'Ungültige Einstellungen.')
            aus[k] = e[k]
    if 'rechte' in e:
        if not isinstance(e['rechte'], dict):
            fehler(400, 'Ungültige Einstellungen.')
        for k, v in e['rechte'].items():
            if k not in raeume.RECHTE_STANDARD or not isinstance(v, bool):
                fehler(400, 'Rechte sind true oder false.')
            aus['rechte'][k] = v
    return aus


def aus_raum(r):
    """Einstellungen eines bestehenden Raums als Vorlage (Passcode nur als an/aus)."""
    return {'titel': r['titel'], 'dauer_min': r['dauer_min'] or 60, 'passcode': bool(r['passcode_hash']), 'warteraum': bool(r['warteraum']),
            'chat_speichern': bool(r['chat_speichern']), 'beschreibung': r['beschreibung'] or '',
            'rechte': json.loads(r['rechte_standard'] or 'null') or raeume.rechte(r), 'stumm_beitritt': bool(r['stumm_beitritt'])}


def vorlage_json(v):
    return {'id': v['id'], 'name': v['name'], 'einstellungen': STANDARD | json.loads(v['einstellungen']),
            'erstellt': iso(v['erstellt']), 'geaendert': iso(v['geaendert'])}


def laden(b, vid):
    v = db.verbindung().execute('SELECT * FROM vorlagen WHERE id = ? AND benutzer_id = ?', (vid, b['id'])).fetchone()
    if not v:
        fehler(404, 'Vorlage nicht gefunden')
    return v


def name_feld(d, pflicht):
    if 'name' not in d and not pflicht:
        return None
    n = text_feld(d, 'name', 80).replace('\n', ' ')
    if not n:
        fehler(400, 'Bitte einen Namen für die Vorlage eingeben.')
    return n


@router.get('/api/vorlagen')
def liste(req: Request):
    b = benutzer_aus_sitzung(req)
    rows = db.verbindung().execute('SELECT * FROM vorlagen WHERE benutzer_id = ? ORDER BY name COLLATE NOCASE, id', (b['id'],)).fetchall()
    return {'vorlagen': [vorlage_json(v) for v in rows]}


@router.post('/api/vorlagen')
async def neu(req: Request):
    """{name, einstellungen} oder {name, aus_raum: code} (übernimmt die Einstellungen eines eigenen Raums)."""
    b = benutzer_aus_sitzung(req)
    d = await json_body(req)
    grenze.pruefen(f'vorlage:{b["id"]}', 60, 3600)
    name = name_feld(d, True)
    if d.get('aus_raum') is not None:
        r = raeume.raum_laden(d['aus_raum'] if isinstance(d['aus_raum'], str) else '')
        if r['besitzer_id'] != b['id']:
            fehler(403, 'Nur der Besitzer darf das.')
        e = aus_raum(r)
    else:
        e = einstellungen_pruefen(d.get('einstellungen', {}))
    c = db.verbindung()
    if c.execute('SELECT COUNT(*) FROM vorlagen WHERE benutzer_id = ?', (b['id'],)).fetchone()[0] >= MAX_VORLAGEN:
        fehler(409, f'Höchstens {MAX_VORLAGEN} Vorlagen.')
    vid = c.execute('INSERT INTO vorlagen (benutzer_id, name, einstellungen, erstellt, geaendert) VALUES (?, ?, ?, ?, ?)',
                    (b['id'], name, json.dumps(e), jetzt(), jetzt())).lastrowid
    return {'vorlage': vorlage_json(laden(b, vid))}


@router.put('/api/vorlagen/{vid}')
async def aendern(vid: int, req: Request):
    b = benutzer_aus_sitzung(req)
    v = laden(b, vid)
    d = await json_body(req)
    name = name_feld(d, False) or v['name']
    e = einstellungen_pruefen(d['einstellungen']) if 'einstellungen' in d else json.loads(v['einstellungen'])
    db.verbindung().execute('UPDATE vorlagen SET name = ?, einstellungen = ?, geaendert = ? WHERE id = ?', (name, json.dumps(e), jetzt(), vid))
    return {'vorlage': vorlage_json(laden(b, vid))}


@router.delete('/api/vorlagen/{vid}')
def loeschen(vid: int, req: Request):
    b = benutzer_aus_sitzung(req)
    laden(b, vid)
    db.verbindung().execute('DELETE FROM vorlagen WHERE id = ?', (vid,))
    return {'ok': True}


@router.post('/api/vorlagen/{vid}/raum')
async def raum_aus_vorlage(vid: int, req: Request):
    """Neues Meeting aus der Vorlage: ohne `geplant` Sofort-Meeting, sonst Termin. Felder im Body (titel, passcode,
    warteraum, chat_speichern, geplant.dauer_min/beschreibung) überschreiben die Vorlage. Passcode an und keiner
    angegeben → der Server erzeugt einen und gibt ihn einmal zurück."""
    b = benutzer_aus_sitzung(req)
    e = vorlage_json(laden(b, vid))['einstellungen']
    d = await json_body(req)
    grenze.pruefen(f'raum:{b["id"]}', 60, 3600)
    felder = {'titel': d['titel'] if isinstance(d.get('titel'), str) and d['titel'].strip() else e['titel'],
              'warteraum': d.get('warteraum', e['warteraum']), 'chat_speichern': d.get('chat_speichern', e['chat_speichern'])}
    passcode = None
    if 'passcode' in d:
        felder['passcode'] = d['passcode']
    elif e['passcode']:
        passcode = ''.join(secrets.choice(PASSCODE_ZEICHEN) for _ in range(8))
    if d.get('geplant') is not None:
        if not isinstance(d['geplant'], dict):
            fehler(400, 'Ungültige Angaben zum Termin.')
        felder['geplant'] = {'dauer_min': e['dauer_min'], 'beschreibung': e['beschreibung']} | d['geplant']
    f = await raeume.einstellungen_lesen(felder, b, True)
    if passcode:
        f['passcode_hash'] = await run_in_threadpool(hash_pw, passcode)
    art = 'geplant' if 'start' in f else 'sofort'
    titel = f.pop('titel', '') or (f'Meeting von {b["name"]}' if art == 'sofort' else 'Geplantes Meeting')
    rechte = json.dumps(e['rechte'])
    f |= {'rechte': rechte, 'rechte_standard': rechte, 'stumm_beitritt': int(e['stumm_beitritt'])}
    r = raeume.raum_anlegen(b['id'], art, titel[:120], f)
    db.protokollieren(f'Raum angelegt ({art}, Vorlage)', b['id'], r['code'])
    return {'raum': raeume.raum_json(r, b), 'passcode': passcode or (felder.get('passcode') or None)}


def export(b):
    """Für den Datenexport (GET /api/konto/export)."""
    rows = db.verbindung().execute('SELECT * FROM vorlagen WHERE benutzer_id = ? ORDER BY id', (b['id'],)).fetchall()
    return {'vorlagen': [vorlage_json(v) for v in rows]}


konto.EXPORT.append(export)
