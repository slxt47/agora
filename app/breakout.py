"""Breakout-Räume im laufenden Meeting (Header X-Teilnahme).

Host/Co-Host legen 2–50 Räume an, teilen Teilnehmende von Hand oder automatisch (gleichmäßig) zu, stellen optional
einen Timer ein und öffnen die Räume. Jeder Breakout-Raum ist ein eigener LiveKit-Raum `<code>__b<nr>`. Dieser Name
ist kein gültiger Meeting-Code (Beitreten/Info per Code geht nicht) – hinein kommt man nur mit einem Token, das der
Server über POST …/breakout/wechseln ausstellt: Teilnehmende nur in den eigenen Raum, Host/Co-Host in jeden.

Ablauf im Client: Ereignis {typ:"breakout", aktion, runde} → GET …/breakout → bei Zuteilung …/wechseln → Verbindung
zum Hauptraum trennen, Breakout-Raum verbinden. Schließen startet einen Countdown (60 s); danach meldet der Server
„geschlossen“, alle holen sich ein Token für den Hauptraum, die Breakout-Räume werden gelöscht. Läuft ein Timer ab,
beginnt das Schließen von selbst.

Ereignisse und Chat erreichen alle Räume (livekit_dienst arbeitet über nebenraeume()/ort()).
"""
import asyncio
import os
import random

from fastapi import APIRouter, Request

from . import db
from . import livekit_dienst as lk
from .kern import MAX_TEILNEHMER, fehler, grenze, iso, jetzt, json_body, text_feld
from .meeting import protokoll, steuernd, teilnahme
from .raeume import BEIM_ENDE, lk_url, meta, neu_laden, rechte, teilnahmen

router = APIRouter()
MOD = ('host', 'cohost')
COUNTDOWN_S = int(os.environ.get('AGORA_BREAKOUT_COUNTDOWN_S', '60'))
GNADE_S = 15                    # so lange dürfen Nachzügler nach „geschlossen“ noch im Breakout-Raum sein
OFFEN = ('offen', 'schliesst')
_aufgaben = set()


def raumname(code, nr):
    return f'{code}__b{nr}'


# ---------------------------------------------------------------- Anbindung an livekit_dienst und Meeting-Ende
def _runde_fuer_code(code):
    return db.verbindung().execute('SELECT b.* FROM breakout b JOIN raeume r ON r.id = b.raum_id WHERE r.code = ? AND b.sitzung = r.sitzung',
                                   (code,)).fetchone()


def _nebenraeume(code):
    b = _runde_fuer_code(code)
    return [raumname(code, n) for n in range(1, b['anzahl'] + 1)] if b and b['status'] in OFFEN else []


def _ort(code, identity):
    b = _runde_fuer_code(code)
    if not b or b['status'] not in OFFEN:
        return code
    p = db.verbindung().execute('SELECT ort FROM breakout_teilnehmer WHERE raum_id = ? AND identity = ?', (b['raum_id'], identity)).fetchone()
    return raumname(code, p['ort']) if p and p['ort'] else code


def _beim_ende(r):
    c = db.verbindung()
    c.execute('DELETE FROM breakout WHERE raum_id = ?', (r['id'],))
    c.execute('DELETE FROM breakout_teilnehmer WHERE raum_id = ?', (r['id'],))


lk.nebenraeume = _nebenraeume
lk.ort = _ort
BEIM_ENDE.append(_beim_ende)


# ---------------------------------------------------------------- Hilfen
def runde(r):
    return db.verbindung().execute('SELECT * FROM breakout WHERE raum_id = ? AND sitzung = ?', (r['id'], r['sitzung'])).fetchone()


def runde_pflicht(r, status=None):
    b = runde(r)
    if not b:
        fehler(404, 'Es gibt keine Breakout-Räume.')
    if status and b['status'] not in status:
        fehler(409, {'offen': 'Die Breakout-Räume sind schon offen.', 'schliesst': 'Die Breakout-Räume werden gerade geschlossen.'}
               .get(b['status'], 'Die Breakout-Räume sind nicht offen.'))
    return b


def person(r, identity):
    return db.verbindung().execute('SELECT * FROM breakout_teilnehmer WHERE raum_id = ? AND identity = ?', (r['id'], identity)).fetchone()


def setzen(r, identity, nr=None, ort=None):
    c = db.verbindung()
    c.execute('INSERT OR IGNORE INTO breakout_teilnehmer (raum_id, identity) VALUES (?, ?)', (r['id'], identity))
    if nr is not None:
        c.execute('UPDATE breakout_teilnehmer SET nr = ? WHERE raum_id = ? AND identity = ?', (nr, r['id'], identity))
    if ort is not None:
        c.execute('UPDATE breakout_teilnehmer SET ort = ? WHERE raum_id = ? AND identity = ?', (ort, r['id'], identity))


async def anwesende(r):
    """Teilnahmen, die gerade verbunden sind (in irgendeinem Raum). Antwortet LiveKit nicht: alle Teilnahmen."""
    ts = teilnahmen(r)
    liste = await lk.teilnehmer(r['code'])
    if liste is None:
        return ts
    ids = {p['identity'] for p in liste}
    return [t for t in ts if t['identity'] in ids]


def verteilen(identities, anzahl):
    """Gleichmäßig zufällig auf die Räume 1..anzahl verteilen (Größen unterscheiden sich höchstens um 1)."""
    ids = list(identities)
    random.shuffle(ids)
    return {i: (k % anzahl) + 1 for k, i in enumerate(ids)}


def rest(bis, t=None):
    return max(0, bis - (t or jetzt())) if bis else None


async def zustand_json(r, t):
    b = runde(r)
    if not b:
        return None
    eigen = person(r, t['identity'])
    d = {'status': b['status'], 'anzahl': b['anzahl'], 'runde': b['runde'], 'dauer_min': b['dauer_min'], 'rueckkehr': bool(b['rueckkehr']),
         'endet': iso(b['endet']), 'rest_s': rest(b['endet']) if b['status'] == 'offen' else None,
         'schliesst_um': iso(b['schliesst_um']), 'schliesst_in_s': rest(b['schliesst_um']) if b['status'] == 'schliesst' else None,
         'mein_raum': eigen['nr'] if eigen else 0, 'ort': eigen['ort'] if eigen and b['status'] in OFFEN else 0}
    if t['rolle'] in MOD:
        zeilen = {p['identity']: p for p in db.verbindung().execute('SELECT * FROM breakout_teilnehmer WHERE raum_id = ?', (r['id'],))}
        leute = [{'identity': x['identity'], 'name': x['name'], 'rolle': x['rolle'],
                  'nr': zeilen[x['identity']]['nr'] if x['identity'] in zeilen else 0,
                  'ort': zeilen[x['identity']]['ort'] if x['identity'] in zeilen and b['status'] in OFFEN else 0}
                 for x in sorted(await anwesende(r), key=lambda x: x['name'].casefold())]
        d['raeume'] = [{'nr': n, 'teilnehmer': [p for p in leute if p['nr'] == n], 'anwesend': sum(1 for p in leute if p['ort'] == n)}
                       for n in range(1, b['anzahl'] + 1)]
        d['ohne_raum'] = [p for p in leute if p['nr'] == 0]
    return d


async def melden(r, aktion, nur_mods=False, **extra):
    b = runde(r)
    an = [x['identity'] for x in teilnahmen(r, MOD)] if nur_mods else None
    await lk.senden(r['code'], {'typ': 'breakout', 'aktion': aktion, 'runde': b['runde'] if b else 0} | extra, an=an)


def planen(sekunden, code):
    """Fällige Schritte (Timer-Ende, Countdown-Ende) im laufenden Event-Loop nachholen; der Hintergrund prüft zusätzlich."""
    if lk.FAKE:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    aufgabe = loop.create_task(_spaeter(sekunden, code))
    _aufgaben.add(aufgabe)
    aufgabe.add_done_callback(_aufgaben.discard)


async def _spaeter(sekunden, code):
    await asyncio.sleep(max(0, sekunden) + 0.3)
    try:
        await faellige_pruefen(code=code)
    except Exception as e:   # noqa: BLE001
        print(f'breakout-fehler {type(e).__name__}: {e}', flush=True)


async def schliessen_starten(r):
    t = jetzt()
    db.verbindung().execute("UPDATE breakout SET status = 'schliesst', schliesst_um = ? WHERE raum_id = ?", (t + COUNTDOWN_S, r['id']))
    await melden(r, 'schliesst', schliesst_in_s=COUNTDOWN_S)
    planen(COUNTDOWN_S, r['code'])


async def abschliessen(r):
    """Countdown vorbei: alle zurück in den Hauptraum (Ereignis an alle Räume), dann Breakout-Räume löschen."""
    b = runde(r)
    await melden(r, 'geschlossen')
    c = db.verbindung()
    c.execute("UPDATE breakout SET status = 'geschlossen', schliesst_um = NULL WHERE raum_id = ?", (r['id'],))
    c.execute('UPDATE breakout_teilnehmer SET ort = 0 WHERE raum_id = ?', (r['id'],))
    namen = [raumname(r['code'], n) for n in range(1, b['anzahl'] + 1)]
    if lk.FAKE:
        for n in namen:
            await lk.raum_loeschen(n, mit_nebenraeumen=False)
    else:
        async def loeschen():
            await asyncio.sleep(GNADE_S)
            for n in namen:
                await lk.raum_loeschen(n, mit_nebenraeumen=False)
        try:
            aufgabe = asyncio.get_running_loop().create_task(loeschen())
            _aufgaben.add(aufgabe)
            aufgabe.add_done_callback(_aufgaben.discard)
        except RuntimeError:
            pass                                   # ohne Loop: LiveKit schließt leere Räume selbst
    db.protokollieren('Breakout-Räume geschlossen', None, r['code'])


async def faellige_pruefen(t=None, code=None):
    """Timer abgelaufen → Schließen beginnen; Countdown abgelaufen → schließen. Aus Hintergrund und geplanten Aufgaben."""
    t = t or jetzt()
    c = db.verbindung()
    q = ("SELECT r.*, b.status AS b_status, b.endet AS b_endet, b.schliesst_um AS b_schliesst FROM breakout b JOIN raeume r ON r.id = b.raum_id "
         "WHERE r.aktiv = 1 AND b.sitzung = r.sitzung AND b.status IN ('offen', 'schliesst')")
    for r in c.execute(q + (' AND r.code = ?' if code else ''), (code,) if code else ()).fetchall():
        if r['b_status'] == 'offen' and r['b_endet'] and r['b_endet'] <= t:
            await schliessen_starten(r)
        elif r['b_status'] == 'schliesst' and r['b_schliesst'] and r['b_schliesst'] <= t:
            await abschliessen(r)


def zahl(d, feld, kleinste, groesste, text):
    v = d.get(feld)
    if not isinstance(v, int) or isinstance(v, bool) or not kleinste <= v <= groesste:
        fehler(400, text)
    return v


# ---------------------------------------------------------------- Endpunkte
@router.get('/api/raeume/{code}/breakout')
async def lesen(code: str, req: Request):
    r, t = teilnahme(req, code)
    return {'breakout': await zustand_json(r, t)}


@router.post('/api/raeume/{code}/breakout')
async def anlegen(code: str, req: Request):
    """Neue Runde (Entwurf) anlegen bzw. ersetzen: {anzahl, dauer_min?, rueckkehr?, automatisch?}."""
    r, t = steuernd(req, code)
    d = await json_body(req)
    b = runde(r)
    if b and b['status'] in OFFEN:
        fehler(409, 'Die Breakout-Räume sind schon offen.')
    anzahl = zahl(d, 'anzahl', 2, 50, 'Bitte 2 bis 50 Räume angeben.')
    dauer = None if d.get('dauer_min') is None else zahl(d, 'dauer_min', 1, 240, 'Timer: 1 bis 240 Minuten.')
    for k in ('rueckkehr', 'automatisch'):
        if k in d and not isinstance(d[k], bool):
            fehler(400, 'Ungültige Anfrage')
    c = db.verbindung()
    c.execute('DELETE FROM breakout_teilnehmer WHERE raum_id = ?', (r['id'],))
    c.execute('INSERT INTO breakout (raum_id, sitzung, anzahl, dauer_min, rueckkehr, runde) VALUES (?, ?, ?, ?, ?, ?) '
              "ON CONFLICT(raum_id) DO UPDATE SET sitzung = excluded.sitzung, anzahl = excluded.anzahl, status = 'entwurf', "
              'dauer_min = excluded.dauer_min, rueckkehr = excluded.rueckkehr, geoeffnet = NULL, endet = NULL, schliesst_um = NULL, '
              'runde = CASE WHEN breakout.sitzung = excluded.sitzung THEN breakout.runde ELSE 0 END',
              (r['id'], r['sitzung'], anzahl, dauer, int(d.get('rueckkehr', True)), 0))
    if d.get('automatisch'):
        for identity, nr in verteilen([x['identity'] for x in await anwesende(r) if x['rolle'] not in MOD], anzahl).items():
            setzen(r, identity, nr=nr)
    protokoll(t, r, f'Breakout-Räume angelegt ({anzahl})')
    await melden(r, 'angelegt', nur_mods=True)
    return {'breakout': await zustand_json(r, t)}


@router.put('/api/raeume/{code}/breakout/zuteilung')
async def zuteilen(code: str, req: Request):
    """{zuteilung: {identity: nr (0 = keiner)}} ändert einzelne Zuteilungen; {automatisch: true} verteilt alle neu."""
    r, t = steuernd(req, code)
    d = await json_body(req, 65536)
    b = runde_pflicht(r, ('entwurf', 'offen'))
    if d.get('automatisch') is True:
        db.verbindung().execute('UPDATE breakout_teilnehmer SET nr = 0 WHERE raum_id = ?', (r['id'],))
        for identity, nr in verteilen([x['identity'] for x in await anwesende(r) if x['rolle'] not in MOD], b['anzahl']).items():
            setzen(r, identity, nr=nr)
    else:
        z = d.get('zuteilung')
        if not isinstance(z, dict) or not z or len(z) > 200:
            fehler(400, 'Bitte eine Zuteilung angeben.')
        bekannt = {x['identity'] for x in teilnahmen(r)}
        for identity, nr in z.items():
            if identity not in bekannt:
                fehler(404, 'Teilnehmer nicht gefunden')
            if not isinstance(nr, int) or isinstance(nr, bool) or not 0 <= nr <= b['anzahl']:
                fehler(400, f'Raum-Nummer: 0 bis {b["anzahl"]}.')
        for identity, nr in z.items():
            setzen(r, identity, nr=nr)
    await melden(r, 'zuteilung', nur_mods=b['status'] == 'entwurf')
    return {'breakout': await zustand_json(r, t)}


@router.post('/api/raeume/{code}/breakout/oeffnen')
async def oeffnen(code: str, req: Request):
    r, t = steuernd(req, code)
    b = runde_pflicht(r, ('entwurf',))
    tz = jetzt()
    endet = tz + b['dauer_min'] * 60 if b['dauer_min'] else None
    db.verbindung().execute("UPDATE breakout SET status = 'offen', runde = runde + 1, geoeffnet = ?, endet = ?, schliesst_um = NULL "
                            'WHERE raum_id = ?', (tz, endet, r['id']))
    db.verbindung().execute('UPDATE breakout_teilnehmer SET ort = 0 WHERE raum_id = ?', (r['id'],))
    r = neu_laden(r)
    for n in range(1, b['anzahl'] + 1):
        await lk.raum_anlegen(raumname(r['code'], n), meta(r), MAX_TEILNEHMER)
    await melden(r, 'geoeffnet')
    if endet:
        planen(endet - tz, r['code'])
    protokoll(t, r, f'Breakout-Räume geöffnet ({b["anzahl"]})')
    return {'breakout': await zustand_json(r, t)}


@router.post('/api/raeume/{code}/breakout/wechseln')
async def wechseln(code: str, req: Request):
    """{nr}: 0 = Hauptraum. Gibt ein LiveKit-Token für den Zielraum aus und merkt sich, wo die Person ist."""
    r, t = teilnahme(req, code)
    d = await json_body(req)
    grenze.pruefen('wechseln:' + t['identity'], 30, 60)
    b = runde(r)
    offen = bool(b) and b['status'] in OFFEN
    nr = zahl(d, 'nr', 0, b['anzahl'] if offen else 0, 'Diesen Raum gibt es nicht.')
    mod = t['rolle'] in MOD
    eigen = person(r, t['identity'])
    mein = eigen['nr'] if eigen else 0
    if nr == 0:
        if offen and b['status'] == 'offen' and not mod and mein and not b['rueckkehr']:
            fehler(403, 'Der Host hat die Rückkehr in den Hauptraum nicht erlaubt.')
    else:
        if not mod and (b['status'] != 'offen' or nr != mein):
            fehler(403, 'In diesen Breakout-Raum darfst du nicht.')
    if offen:
        setzen(r, t['identity'], ort=nr)
    ziel = raumname(r['code'], nr) if nr else r['code']
    token = lk.token(ziel, t['identity'], t['name'], t['rolle'], t['farbe'], rechte(r))
    if offen:
        await melden(r, 'ort', nur_mods=True)
    return {'zugang': {'token': token, 'url': lk_url(lk.raum_region(r['code'])), 'raum': ziel, 'nr': nr}}


@router.post('/api/raeume/{code}/breakout/hilfe')
async def hilfe(code: str, req: Request):
    r, t = teilnahme(req, code)
    runde_pflicht(r, ('offen', 'schliesst'))
    eigen = person(r, t['identity'])
    if not eigen or not eigen['ort']:
        fehler(400, 'Du bist gerade in keinem Breakout-Raum.')
    grenze.pruefen('hilfe:' + t['identity'], 3, 60)
    mods = [x['identity'] for x in teilnahmen(r, MOD)]
    await lk.senden(r['code'], {'typ': 'breakout_hilfe', 'nr': eigen['ort'], 'identity': t['identity'], 'von_name': t['name']}, an=mods)
    return {'ok': True}


@router.post('/api/raeume/{code}/breakout/nachricht')
async def nachricht(code: str, req: Request):
    r, t = steuernd(req, code)
    d = await json_body(req)
    runde_pflicht(r, ('offen', 'schliesst'))
    grenze.pruefen('rundruf:' + t['identity'], 10, 60)
    text = text_feld(d, 'text', 500)
    if not text:
        fehler(400, 'Leere Nachricht')
    await lk.senden(r['code'], {'typ': 'breakout_nachricht', 'text': text, 'von_name': t['name']})
    return {'ok': True}


@router.post('/api/raeume/{code}/breakout/schliessen')
async def schliessen(code: str, req: Request):
    r, t = steuernd(req, code)
    runde_pflicht(r, ('offen',))
    await schliessen_starten(r)
    protokoll(t, r, 'Breakout-Räume schließen')
    return {'breakout': await zustand_json(r, t)}
