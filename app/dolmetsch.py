"""Dolmetscher-Kanäle (Simultanübersetzung) im laufenden Meeting.

- Host/Co-Host legen die Sprachen fest (ISO-639-Codes, gespeichert am Raum) und machen Teilnehmende zu Dolmetschern
  mit einem Sprachpaar (a ⇄ b). Je Paar ist höchstens eine Person „auf Sendung“ (aktiv), weitere warten in Bereitschaft;
  „Übergabe“ wechselt (z. B. alle 20–30 min).
- Dolmetscher veröffentlichen eine zusätzliche Audiospur `dolmetsch:<sprache>` (Quelle Mikrofon) in die Sprache, in die
  sie gerade übersetzen (`sprache` ∈ {a, b}, „Kanal wechseln“). Das Recht dazu gibt nur der Server: LiveKit-Rechte per
  UpdateParticipant um die Quelle Mikrofon ergänzt (wichtig für Webinar-Zuschauer, die sonst nichts senden dürfen),
  zurückgenommen beim Entfernen. LiveKit kann Spurnamen nicht beschränken – deshalb (1) steht die Zuordnung Kanal →
  identity nur in den Raum-Metadaten, die allein der Server schreibt; Zuhörende abonnieren nur die dort genannte Spur,
  (2) schaltet der Server minütlich jede `dolmetsch:`-Spur stumm, die nicht zu einem zugewiesenen Dolmetscher und dessen
  Sprachpaar passt.
- Zuhörende wählen im Menü einen Kanal: Der Browser abonniert nur diese eine Spur und spielt den Saal leiser (20 %).
- Gespeichert wird nur für das laufende Meeting (identity, Name, Sprachpaar, Kanal, aktiv) – beim Ende gelöscht.
  Die Sprachliste bleibt am Raum, bis Host/Co-Host sie leeren.
"""
import json
import re
import time

from fastapi import APIRouter, Request

from . import db, meeting, raeume
from . import livekit_dienst as lk
from .kern import fehler, grenze, json_body

router = APIRouter()
SPRACHE_RE = re.compile(r'^[a-z]{2,3}$')
MAX_SPRACHEN = 8
MAX_DOLMETSCHER = 20
SPUR = 'dolmetsch:'


def sprachen(r):
    try:
        s = json.loads(r['dolmetsch_sprachen'] or '[]')
    except ValueError:
        return []
    return [x for x in s if isinstance(x, str) and SPRACHE_RE.match(x)]


def dolmetscher(r):
    return db.verbindung().execute('SELECT * FROM dolmetscher WHERE raum_id = ? AND sitzung = ? ORDER BY seit',
                                   (r['id'], r['sitzung'])).fetchall()


def zustand(r):
    """Für Metadaten, Ereignis und GET: {sprachen, dolmetscher:{identity:{name, a, b, sprache, aktiv}}, kanaele:{sprache: identity}}."""
    d = {x['identity']: {'name': x['name'], 'a': x['sprache_a'], 'b': x['sprache_b'], 'sprache': x['sprache'], 'aktiv': bool(x['aktiv'])}
         for x in (dolmetscher(r) if r['aktiv'] else [])}
    return {'sprachen': sprachen(r), 'dolmetscher': d, 'kanaele': {v['sprache']: k for k, v in d.items() if v['aktiv']}}


def _meta(r):
    z = zustand(r)
    return {'dolmetsch': z if (z['sprachen'] or z['dolmetscher']) else None}


def _beim_ende(r):
    db.verbindung().execute('DELETE FROM dolmetscher WHERE raum_id = ?', (r['id'],))


def _ist_dolmetscher(code, identity):
    """Für livekit_dienst.extra_quellen: Wer zugewiesen ist, darf zusätzlich das Mikrofon senden."""
    x = db.verbindung().execute('SELECT 1 FROM dolmetscher d JOIN raeume r ON r.id = d.raum_id AND r.sitzung = d.sitzung '
                                'WHERE r.code = ? AND d.identity = ? AND r.aktiv = 1', (code, identity)).fetchone()
    return ['mikrofon'] if x else []


async def _spuren_pruefen(r, liste):
    """Minütlich: fremde `dolmetsch:`-Spuren (nicht zugewiesen oder falsche Sprache) stumm schalten."""
    erlaubt = {k: {v['a'], v['b']} for k, v in zustand(r)['dolmetscher'].items()}
    for p in liste:
        for t in p.get('tracks', []):
            name = t.get('name') or ''
            if name.startswith(SPUR) and not t.get('stumm') and name[len(SPUR):] not in erlaubt.get(p['identity'], set()):
                await lk.spur_stumm(p.get('raum', r['code']), p['identity'], t['sid'])


raeume.META_EXTRA.append(_meta)
raeume.BEIM_ENDE.append(_beim_ende)
raeume.BEI_LISTE_ASYNC.append(_spuren_pruefen)
lk.extra_quellen = _ist_dolmetscher


async def _melden(r, rechte_fuer=()):
    """Metadaten + Ereignis an alle; Rechte der genannten identities neu setzen (Mikrofon dazu oder weg)."""
    r = raeume.neu_laden(r)
    for identity in rechte_fuer:
        t = db.verbindung().execute('SELECT * FROM teilnahmen WHERE identity = ? AND raum_id = ?', (identity, r['id'])).fetchone()
        if t:
            await lk.aktualisieren(r['code'], identity, rolle=t['rolle'], farbe=t['farbe'], rechte=raeume.rechte(r),
                                   attribute={'dolmetsch': '1' if _ist_dolmetscher(r['code'], identity) else ''})
    z = zustand(r)
    await lk.metadaten(r['code'], raeume.meta(r))
    await lk.senden(r['code'], {'typ': 'dolmetsch', 'zustand': z})
    return z


def _sprache_lesen(d, feld, erlaubt):
    s = d.get(feld)
    if not isinstance(s, str) or s not in erlaubt:
        fehler(400, 'Diese Sprache ist im Meeting nicht eingerichtet.')
    return s


def _partner(r, x):
    """Andere Dolmetscher mit demselben Sprachpaar (in beliebiger Richtung)."""
    paar = {x['sprache_a'], x['sprache_b']}
    return [y for y in dolmetscher(r) if y['identity'] != x['identity'] and {y['sprache_a'], y['sprache_b']} == paar]


# ---------------------------------------------------------------- API (X-Teilnahme)
@router.get('/api/raeume/{code}/dolmetsch')
def lesen(code: str, req: Request):
    r, _t = meeting.teilnahme(req, code)
    return zustand(r)


@router.put('/api/raeume/{code}/dolmetsch/sprachen')
async def sprachen_setzen(code: str, req: Request):
    r, t = meeting.steuernd(req, code)
    d = await json_body(req, 2048)
    s = d.get('sprachen')
    if not isinstance(s, list) or any(not isinstance(x, str) or not SPRACHE_RE.match(x) for x in s) or len(set(s)) > MAX_SPRACHEN:
        fehler(400, 'Sprachen: höchstens 8 Sprachcodes wie de, en, fr.')
    s = list(dict.fromkeys(s))
    genutzt = {x['sprache_a'] for x in dolmetscher(r)} | {x['sprache_b'] for x in dolmetscher(r)}
    if genutzt - set(s):
        fehler(400, 'Diese Sprache nutzt noch ein Dolmetscher – erst die Zuweisung entfernen.')
    db.verbindung().execute('UPDATE raeume SET dolmetsch_sprachen = ? WHERE id = ?', (json.dumps(s), r['id']))
    meeting.protokoll(t, r, 'Dolmetschen: Sprachen geändert')
    return await _melden(r)


@router.post('/api/raeume/{code}/dolmetsch')
async def zuweisen(code: str, req: Request):
    r, t = meeting.steuernd(req, code)
    d = await json_body(req, 2048)
    grenze.pruefen('dolmetsch:' + t['identity'], 60, 60)
    z = meeting.ziel_laden(r, d.get('ziel'))
    s = sprachen(r)
    a, b = _sprache_lesen(d, 'a', s), _sprache_lesen(d, 'b', s)
    if a == b:
        fehler(400, 'Bitte zwei verschiedene Sprachen wählen.')
    c = db.verbindung()
    vorhanden = c.execute('SELECT 1 FROM dolmetscher WHERE raum_id = ? AND identity = ?', (r['id'], z['identity'])).fetchone()
    if len(dolmetscher(r)) >= MAX_DOLMETSCHER and not vorhanden:
        fehler(400, 'Höchstens 20 Dolmetscher je Meeting.')
    c.execute('DELETE FROM dolmetscher WHERE raum_id = ? AND identity = ?', (r['id'], z['identity']))
    neu = {'identity': z['identity'], 'sprache_a': a, 'sprache_b': b}
    aktiv = not any(y['aktiv'] for y in _partner(r, neu))
    c.execute('INSERT INTO dolmetscher (raum_id, sitzung, identity, name, sprache_a, sprache_b, sprache, aktiv, seit) '
              'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (r['id'], r['sitzung'], z['identity'], z['name'], a, b, b, int(aktiv), int(time.time() * 1000)))
    meeting.protokoll(t, r, 'Dolmetschen: Dolmetscher zugewiesen')
    return await _melden(r, [z['identity']])


@router.delete('/api/raeume/{code}/dolmetsch/{identity}')
async def entfernen(code: str, identity: str, req: Request):
    r, t = meeting.steuernd(req, code)
    c = db.verbindung()
    x = c.execute('SELECT * FROM dolmetscher WHERE raum_id = ? AND sitzung = ? AND identity = ?', (r['id'], r['sitzung'], identity)).fetchone()
    if not x:
        fehler(404, 'Dolmetscher nicht gefunden')
    c.execute('DELETE FROM dolmetscher WHERE raum_id = ? AND identity = ?', (r['id'], identity))
    if x['aktiv']:                 # Bereitschaft desselben Paares übernimmt
        p = _partner(r, x)
        if p:
            c.execute('UPDATE dolmetscher SET aktiv = 1, sprache = ? WHERE raum_id = ? AND identity = ?', (x['sprache'], r['id'], p[0]['identity']))
    meeting.protokoll(t, r, 'Dolmetschen: Dolmetscher entfernt')
    return await _melden(r, [identity])


def _eintrag(r, identity):
    return db.verbindung().execute('SELECT * FROM dolmetscher WHERE raum_id = ? AND sitzung = ? AND identity = ?',
                                   (r['id'], r['sitzung'], identity)).fetchone()


@router.post('/api/raeume/{code}/dolmetsch/kanal')
async def kanal(code: str, req: Request):
    """Dolmetscher: Zielsprache wechseln (a ↔ b) – der Client veröffentlicht die Spur dann unter dem neuen Namen."""
    r, t = meeting.teilnahme(req, code)
    x = _eintrag(r, t['identity'])
    if not x:
        fehler(403, 'Du bist in diesem Meeting kein Dolmetscher.')
    grenze.pruefen('dolmetsch:' + t['identity'], 60, 60)
    s = _sprache_lesen(await json_body(req, 1024), 'sprache', (x['sprache_a'], x['sprache_b']))
    db.verbindung().execute('UPDATE dolmetscher SET sprache = ? WHERE raum_id = ? AND identity = ?', (s, r['id'], t['identity']))
    return await _melden(r)


@router.post('/api/raeume/{code}/dolmetsch/uebergabe')
async def uebergabe(code: str, req: Request):
    """Übergabe innerhalb eines Sprachpaares. Dolmetscher auf Sendung: an `an` (oder die erste Bereitschaft) abgeben;
    in Bereitschaft ohne `an`: selbst übernehmen. Host/Co-Host: `an` auf Sendung setzen."""
    r, t = meeting.teilnahme(req, code)
    d = await json_body(req, 1024)
    grenze.pruefen('dolmetsch:' + t['identity'], 60, 60)
    an = d.get('an') if isinstance(d.get('an'), str) else None
    ich = _eintrag(r, t['identity'])
    if ich:
        if an:
            ziel = next((y for y in _partner(r, ich) if y['identity'] == an), None)
            if not ziel:
                fehler(404, 'Dolmetscher nicht gefunden')
        elif ich['aktiv']:
            p = _partner(r, ich)
            if not p:
                fehler(400, 'Für dieses Sprachpaar ist niemand in Bereitschaft.')
            ziel = p[0]
        else:
            ziel = ich                  # übernehmen
    elif t['rolle'] in ('host', 'cohost'):
        ziel = _eintrag(r, an or '')
        if not ziel:
            fehler(404, 'Dolmetscher nicht gefunden')
    else:
        fehler(403, 'Du bist in diesem Meeting kein Dolmetscher.')
    gruppe = [ziel, *_partner(r, ziel)]
    sprache = next((y['sprache'] for y in gruppe if y['aktiv']), ziel['sprache'])     # Kanal bleibt bei der Übergabe gleich
    c = db.verbindung()
    c.execute('UPDATE dolmetscher SET aktiv = 0 WHERE raum_id = ? AND identity IN (%s)' % ','.join('?' * len(gruppe)),
              (r['id'], *[y['identity'] for y in gruppe]))
    c.execute('UPDATE dolmetscher SET aktiv = 1, sprache = ? WHERE raum_id = ? AND identity = ?', (sprache, r['id'], ziel['identity']))
    meeting.protokoll(t, r, 'Dolmetschen: Übergabe')
    return await _melden(r)
