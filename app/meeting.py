"""Im laufenden Meeting (Header X-Teilnahme): Warteraum, Host-Steuerung, Zustand, Chat, Aufnahme-Anzeige.

Rollen: host > cohost > teilnehmer/gast. Wer steuert, muss einen höheren Rang haben als das Ziel (der Host darf
alles, ein Co-Host nicht am Host oder an anderen Co-Hosts). Ereignisse gehen über den LiveKit-Datenkanal (Topic
`agora`); Chat-Nachrichten über Topic `chat` – beides schickt nur der Server (Absender ohne Teilnehmer).
"""
import json

from fastapi import APIRouter, Request

from . import db
from . import livekit_dienst as lk
from .kern import benutzer_aus_sitzung, fehler, grenze, h, iso, jetzt, json_body, text_feld
from .raeume import (RANG, meeting_beenden, meta, nachricht_json, neu_laden, raum_laden, rechte, teilnahmen,
                     warteraum_melden, wartend)

router = APIRouter()
NUR_HOST = {'rolle', 'host_uebertragen', 'beenden'}


def teilnahme(req: Request, code):
    """(raum, teilnahme) aus X-Teilnahme – nur gültig für das laufende Meeting."""
    r = raum_laden(code)
    g = req.headers.get('x-teilnahme', '')
    t = None
    if g and len(g) < 100 and r['aktiv']:
        t = db.verbindung().execute('SELECT * FROM teilnahmen WHERE geheim_hash = ? AND raum_id = ? AND sitzung = ?',
                                    (h(g), r['id'], r['sitzung'])).fetchone()
    if not t:
        fehler(403, 'Du bist nicht in diesem Meeting.')
    return r, t


def steuernd(req, code):
    r, t = teilnahme(req, code)
    if t['rolle'] not in ('host', 'cohost'):
        fehler(403, 'Nur Host oder Co-Host dürfen das.')
    return r, t


def ziel_laden(r, identity):
    z = db.verbindung().execute('SELECT * FROM teilnahmen WHERE identity = ? AND raum_id = ? AND sitzung = ?',
                                (identity if isinstance(identity, str) else '', r['id'], r['sitzung'])).fetchone()
    if not z:
        fehler(404, 'Teilnehmer nicht gefunden')
    return z


def rang_pruefen(t, z):
    if z['identity'] == t['identity']:
        fehler(400, 'Das geht nicht bei dir selbst.')
    if RANG[t['rolle']] <= RANG[z['rolle']]:
        fehler(403, 'Das darfst du bei dieser Person nicht.')


def protokoll(t, r, ereignis):
    db.protokollieren(ereignis, t['benutzer_id'], r['code'])


async def metadaten_aktualisieren(r):
    await lk.metadaten(r['code'], meta(neu_laden(r)))


async def rolle_setzen(r, z, rolle):
    db.verbindung().execute('UPDATE teilnahmen SET rolle = ? WHERE identity = ?', (rolle, z['identity']))
    await lk.aktualisieren(r['code'], z['identity'], rolle=rolle, farbe=z['farbe'], rechte=rechte(r))
    await lk.senden(r['code'], {'typ': 'rolle', 'identity': z['identity'], 'rolle': rolle})


# ---------------------------------------------------------------- Warteraum
@router.get('/api/raeume/{code}/warteraum')
def warteraum(code: str, req: Request):
    r, _t = steuernd(req, code)
    rows = db.verbindung().execute("SELECT * FROM anfragen WHERE raum_id = ? AND sitzung = ? AND status = 'wartet' ORDER BY erstellt",
                                   (r['id'], r['sitzung'])).fetchall()
    return {'wartend': [{'anfrage': a['id'], 'name': a['name'], 'seit': iso(a['erstellt'])} for a in rows]}


@router.post('/api/raeume/{code}/warteraum/{anfrage}')
async def warteraum_entscheiden(code: str, anfrage: str, req: Request):
    r, t = steuernd(req, code)
    d = await json_body(req)
    if d.get('aktion') not in ('zulassen', 'ablehnen'):
        fehler(400, 'Aktion: zulassen oder ablehnen.')
    c = db.verbindung()
    a = c.execute("SELECT * FROM anfragen WHERE id = ? AND raum_id = ? AND sitzung = ? AND status = 'wartet'",
                  (anfrage, r['id'], r['sitzung'])).fetchone()
    if not a:
        fehler(404, 'Anfrage nicht gefunden')
    c.execute('UPDATE anfragen SET status = ? WHERE id = ?', ('zugelassen' if d['aktion'] == 'zulassen' else 'abgelehnt', anfrage))
    protokoll(t, r, f'Warteraum: {d["aktion"]}')
    await warteraum_melden(r)
    return {'ok': True}


# ---------------------------------------------------------------- Steuerung
@router.post('/api/raeume/{code}/steuerung')
async def steuerung(code: str, req: Request):
    r, t = teilnahme(req, code)
    d = await json_body(req)
    aktion = d.get('aktion')
    grenze.pruefen('steuerung:' + t['identity'], 60, 60)
    c = db.verbindung()
    von = {'von_name': t['name']}

    if aktion == 'umbenennen':          # auch für Teilnehmer selbst (wenn Recht „umbenennen“ an ist)
        z = ziel_laden(r, d.get('ziel'))
        name = text_feld(d, 'name', 60).replace('\n', ' ')
        if not name:
            fehler(400, 'Bitte einen Namen eingeben.')
        if z['identity'] == t['identity']:
            if t['rolle'] not in ('host', 'cohost') and not rechte(r)['umbenennen']:
                fehler(403, 'Umbenennen ist in diesem Meeting ausgeschaltet.')
        else:
            rang_pruefen(t, z)
        c.execute('UPDATE teilnahmen SET name = ? WHERE identity = ?', (name, z['identity']))
        await lk.aktualisieren(r['code'], z['identity'], name=name)
        return {'ok': True}

    if t['rolle'] not in ('host', 'cohost'):
        fehler(403, 'Nur Host oder Co-Host dürfen das.')
    if aktion in NUR_HOST and t['rolle'] != 'host':
        fehler(403, 'Nur der Host darf das.')

    if aktion in ('stumm', 'kamera_aus', 'kamera_bitten', 'entfernen'):
        z = ziel_laden(r, d.get('ziel'))
        rang_pruefen(t, z)
        if aktion == 'stumm':
            await lk.stummschalten(r['code'], [z['identity']], 'mikrofon')
            await lk.senden(r['code'], {'typ': 'stumm'} | von, an=[z['identity']])
        elif aktion == 'kamera_aus':
            await lk.stummschalten(r['code'], [z['identity']], 'kamera')
            await lk.senden(r['code'], {'typ': 'kamera_aus'} | von, an=[z['identity']])
        elif aktion == 'kamera_bitten':
            await lk.senden(r['code'], {'typ': 'kamera_bitten'} | von, an=[z['identity']])
        else:
            await lk.senden(r['code'], {'typ': 'entfernt'}, an=[z['identity']])
            await lk.entfernen(r['code'], z['identity'])
            sperren = [('identity', z['identity'])]
            sperren.append(('benutzer', str(z['benutzer_id'])) if z['benutzer_id'] else ('ip', z['ip']))
            for art, wert in sperren:
                c.execute('INSERT INTO sperren VALUES (?, ?, ?, ?)', (r['id'], r['sitzung'], art, wert))
            c.execute('DELETE FROM teilnahmen WHERE identity = ?', (z['identity'],))
    elif aktion == 'alle_stumm':
        ziele = [x['identity'] for x in teilnahmen(r) if RANG[x['rolle']] < RANG[t['rolle']]]
        await lk.stummschalten(r['code'], ziele, 'mikrofon')
        await lk.senden(r['code'], {'typ': 'stumm'} | von, an=ziele)
    elif aktion == 'sperren':
        if not isinstance(d.get('an'), bool):
            fehler(400, 'Bitte „an“ (true/false) angeben.')
        c.execute('UPDATE raeume SET gesperrt = ? WHERE id = ?', (int(d['an']), r['id']))
        await metadaten_aktualisieren(r)
        await lk.senden(r['code'], {'typ': 'gesperrt', 'an': d['an']})
    elif aktion == 'rechte':
        alt = rechte(r)
        neu = dict(alt)
        for k in alt:
            if k in d:
                if not isinstance(d[k], bool):
                    fehler(400, 'Rechte sind true oder false.')
                neu[k] = d[k]
        c.execute('UPDATE raeume SET rechte = ? WHERE id = ?', (json.dumps(neu), r['id']))
        r = neu_laden(r)
        await metadaten_aktualisieren(r)
        await lk.senden(r['code'], {'typ': 'rechte', 'rechte': neu})
        if neu['bildschirm'] != alt['bildschirm']:       # erlaubte Quellen der Teilnehmer anpassen
            verbunden = {p['identity'] for p in (await lk.teilnehmer(r['code']) or [])}
            for x in teilnahmen(r, ('teilnehmer', 'gast')):
                if x['identity'] in verbunden:
                    await lk.aktualisieren(r['code'], x['identity'], rolle=x['rolle'], farbe=x['farbe'], rechte=neu)
    elif aktion == 'rolle':
        z = ziel_laden(r, d.get('ziel'))
        rang_pruefen(t, z)
        if d.get('rolle') not in ('cohost', 'teilnehmer'):
            fehler(400, 'Rolle: cohost oder teilnehmer.')
        await rolle_setzen(r, z, 'cohost' if d['rolle'] == 'cohost' else ('teilnehmer' if z['benutzer_id'] else 'gast'))
    elif aktion == 'host_uebertragen':
        z = ziel_laden(r, d.get('ziel'))
        rang_pruefen(t, z)
        await rolle_setzen(r, z, 'host')
        await rolle_setzen(r, t, 'teilnehmer' if t['benutzer_id'] else 'gast')
    elif aktion == 'beenden':
        protokoll(t, r, 'Steuerung: beenden')
        await meeting_beenden(r, 'host')
        return {'ok': True}
    else:
        fehler(400, 'Unbekannte Aktion')
    protokoll(t, r, f'Steuerung: {aktion}')
    return {'ok': True}


@router.get('/api/raeume/{code}/zustand')
def zustand(code: str, req: Request):
    r, t = teilnahme(req, code)
    m = meta(r)
    return {'rolle': t['rolle'], 'rechte': m['rechte'], 'gesperrt': m['gesperrt'], 'aufnahme': m['aufnahme'],
            'rollen': {x['identity']: x['rolle'] for x in teilnahmen(r)},
            'wartend': wartend(r) if t['rolle'] in ('host', 'cohost') else 0}


# ---------------------------------------------------------------- Aufnahme-Anzeige
@router.post('/api/raeume/{code}/aufnahme')
async def aufnahme_anzeige(code: str, req: Request):
    r, t = steuernd(req, code)
    d = await json_body(req)
    if not isinstance(d.get('aktiv'), bool):
        fehler(400, 'Bitte „aktiv“ (true/false) angeben.')
    db.verbindung().execute('UPDATE raeume SET aufnahme_von = ? WHERE id = ?', (t['name'] if d['aktiv'] else None, r['id']))
    await metadaten_aktualisieren(r)
    await lk.senden(r['code'], {'typ': 'aufnahme', 'aktiv': d['aktiv'], 'von_name': t['name']})
    protokoll(t, r, 'Aufnahme gestartet' if d['aktiv'] else 'Aufnahme beendet')
    return {'ok': True}


# ---------------------------------------------------------------- Chat
@router.post('/api/raeume/{code}/chat')
async def chat_senden(code: str, req: Request):
    r, t = teilnahme(req, code)
    d = await json_body(req)
    if not rechte(r)['chat'] and t['rolle'] not in ('host', 'cohost'):
        fehler(403, 'Der Chat ist in diesem Meeting ausgeschaltet.')
    grenze.pruefen('chat:' + t['identity'], 20, 10)
    text = text_feld(d, 'text', 2000)
    if not text:
        fehler(400, 'Leere Nachricht')
    z = ziel_laden(r, d['an']) if d.get('an') else None
    if z and z['identity'] == t['identity']:
        fehler(400, 'Das geht nicht bei dir selbst.')
    c = db.verbindung()
    nid = c.execute('INSERT INTO nachrichten (raum_id, sitzung, von, von_name, von_benutzer, an, an_name, an_benutzer, text, zeit, dauer) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (r['id'], r['sitzung'], t['identity'], t['name'], t['benutzer_id'], z['identity'] if z else None,
                     z['name'] if z else None, z['benutzer_id'] if z else None, text, jetzt(),
                     int(bool(r['dauer_chat']) and not z))).lastrowid      # Dauer-Chat: nur öffentliche Nachrichten
    n = nachricht_json(c.execute('SELECT * FROM nachrichten WHERE id = ?', (nid,)).fetchone())
    await lk.senden(r['code'], n, topic='chat', an=[t['identity'], z['identity']] if z else None)
    return {'nachricht': n}


@router.get('/api/raeume/{code}/chat')
def chat_lesen(code: str, req: Request):
    r, t = teilnahme(req, code)
    bid = t['benutzer_id'] or -1
    rows = db.verbindung().execute(
        'SELECT * FROM (SELECT * FROM nachrichten WHERE raum_id = ? AND (sitzung = ? OR dauer = 1) AND '
        '(an IS NULL OR von = ? OR an = ? OR von_benutzer = ? OR an_benutzer = ?) ORDER BY id DESC LIMIT 500) ORDER BY id',
        (r['id'], r['sitzung'], t['identity'], t['identity'], bid, bid)).fetchall()
    return {'nachrichten': [nachricht_json(n) for n in rows]}


@router.get('/api/raeume/{code}/verlauf')
def verlauf(code: str, req: Request):
    """Gespeicherter Chat (Raum-Einstellung chat_speichern) – für den Besitzer: öffentliche und eigene private Nachrichten."""
    b = benutzer_aus_sitzung(req)
    r = raum_laden(code)
    if r['besitzer_id'] != b['id']:
        fehler(403, 'Nur der Besitzer darf das.')
    rows = db.verbindung().execute('SELECT * FROM nachrichten WHERE raum_id = ? AND (an IS NULL OR von_benutzer = ? OR an_benutzer = ?) '
                                   'ORDER BY id', (r['id'], b['id'], b['id'])).fetchall()
    return {'nachrichten': [nachricht_json(n) | {'sitzung': n['sitzung']} for n in rows]}
