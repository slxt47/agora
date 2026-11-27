"""Fernsteuerung (Remote Control) – nur mit der Desktop-App auf der Seite der teilenden Person.

Ablauf:
1. Teilt jemand in der Desktop-App den ganzen Bildschirm, meldet die Seite `bereit` (der Server prüft bei LiveKit, dass
   wirklich eine Bildschirmspur läuft). Die Raum-Metadaten nennen dann `fernsteuerung_bereit: [identity, …]`.
2. Eine andere Person fragt an (`anfragen`, Ziel = teilende Person) → Ereignis nur an die teilende Person.
3. Die Desktop-App zeigt einen eigenen Dialog (nicht nur die Seite). Bei „Erlauben“ erzeugt sie eine Kennung, die Seite
   schickt `erteilen` mit Ziel + Kennung. Erst jetzt schreibt der Server die Freigabe in die Raum-Metadaten:
   `fernsteuerung: [{von, an, an_name, id}]`. Die Desktop-App gibt Eingaben nur weiter, wenn Freigabe (Server),
   Zustimmung (eigener Dialog, gleiche Kennung) und Absender des LiveKit-Datenpakets zusammenpassen.
4. Ende: teilende oder steuernde Person (`beenden`), Host/Co-Host (`beenden` mit Ziel), Ende der Freigabe (`bereit` aus),
   Verlassen des Meetings, Meeting-Ende – und der Abgleich im Hintergrund, sobald die Bildschirmspur fehlt.
Eingaben selbst laufen nie über den Server: LiveKit-Datenkanal (zuverlässig, Topic „fernsteuerung“, nur an die
teilende Person; in E2EE-Meetings Ende-zu-Ende verschlüsselt).
"""
import re
import time

from fastapi import APIRouter, Request

from . import db, meeting, raeume
from . import livekit_dienst as lk
from .kern import fehler, grenze, json_body

router = APIRouter()
KENNUNG_RE = re.compile(r'^[A-Za-z0-9_-]{22,64}$')
ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
NICHT_ANFRAGEN = ('zuschauer',)          # Webinar-Zuschauer steuern nicht


def _zeilen(r):
    return db.verbindung().execute('SELECT * FROM fernsteuerung WHERE raum_id = ? AND sitzung = ?', (r['id'], r['sitzung'])).fetchall()


def _meta(r):
    zeilen = _zeilen(r) if r['aktiv'] else []
    return {'fernsteuerung_bereit': [z['identity'] for z in zeilen],
            'fernsteuerung': [{'von': z['identity'], 'an': z['an'], 'an_name': z['an_name'], 'id': z['kennung']} for z in zeilen if z['an']]}


def _beim_ende(r):
    db.verbindung().execute('DELETE FROM fernsteuerung WHERE raum_id = ?', (r['id'],))


raeume.META_EXTRA.append(_meta)
raeume.BEIM_ENDE.append(_beim_ende)


async def _teilt_bildschirm(code, identity):
    """Hat identity gerade eine Bildschirmspur bei LiveKit? None, wenn LiveKit nicht antwortet."""
    liste = await lk.teilnehmer(code)
    if liste is None:
        return None
    return any(p['identity'] == identity and any(t['quelle'] == 'bildschirm' for t in p['tracks']) for p in liste)


async def _metadaten(r):
    await lk.metadaten(r['code'], raeume.meta(raeume.neu_laden(r)))


async def _aufheben(r, z, grund, wer=None):
    """Freigabe einer Zeile aufheben (Zeile bleibt, solange `bereit`) und beide Seiten informieren."""
    if not z['an']:
        return
    db.verbindung().execute('UPDATE fernsteuerung SET an = NULL, an_name = NULL, kennung = NULL, erteilt = NULL WHERE raum_id = ? AND identity = ?',
                            (z['raum_id'], z['identity']))
    await lk.senden(r['code'], {'typ': 'fernsteuerung', 'art': 'beendet', 'von': z['identity'], 'an': z['an'], 'grund': grund},
                    an=[z['identity'], z['an']])
    db.protokollieren(f'Fernsteuerung beendet ({grund})', wer, r['code'])


@router.post('/api/raeume/{code}/fernsteuerung')
async def fernsteuerung(code: str, req: Request):
    r, t = meeting.teilnahme(req, code)
    d = await json_body(req, 2048)
    aktion = d.get('aktion')
    c = db.verbindung()
    ich = t['identity']
    eigene = c.execute('SELECT * FROM fernsteuerung WHERE raum_id = ? AND sitzung = ? AND identity = ?', (r['id'], r['sitzung'], ich)).fetchone()

    if aktion == 'bereit':                                 # teilende Person (Desktop-App) an/aus
        if not isinstance(d.get('an'), bool):
            fehler(400, 'Ungültige Anfrage')
        if d['an']:
            grenze.pruefen('fs-bereit:' + ich, 20, 60)
            if t['rolle'] == 'zuschauer':
                fehler(403, 'Nur wer den Bildschirm teilt, kann Fernsteuerung anbieten.')
            if not await _teilt_bildschirm(r['code'], ich):
                # Direkt nach dem Veröffentlichen kennt LiveKit die Spur evtl. noch nicht – die Seite fragt gleich noch einmal
                return {'ok': False, 'grund': 'keine-bildschirmspur'}
            c.execute('INSERT OR IGNORE INTO fernsteuerung (raum_id, sitzung, identity, seit) VALUES (?, ?, ?, ?)',
                      (r['id'], r['sitzung'], ich, int(time.time())))
        elif eigene:
            await _aufheben(r, eigene, 'freigabe-ende', t['benutzer_id'])
            c.execute('DELETE FROM fernsteuerung WHERE raum_id = ? AND identity = ?', (r['id'], ich))
        await _metadaten(r)
        return {'ok': True}

    if aktion == 'anfragen':                               # jemand möchte steuern
        if t['rolle'] in NICHT_ANFRAGEN:
            fehler(403, 'Webinar-Zuschauer können keine Fernsteuerung anfragen.')
        grenze.pruefen('fs-anfrage:' + ich, 5, 60)
        ziel = d.get('ziel')
        z = c.execute('SELECT * FROM fernsteuerung WHERE raum_id = ? AND sitzung = ? AND identity = ?',
                      (r['id'], r['sitzung'], ziel if isinstance(ziel, str) else '')).fetchone()
        if not z:
            fehler(404, 'Diese Person bietet gerade keine Fernsteuerung an.')
        if ziel == ich:
            fehler(400, 'Das geht nicht bei dir selbst.')
        if z['an']:
            fehler(409, 'Den Bildschirm steuert gerade schon jemand.')
        await lk.senden(r['code'], {'typ': 'fernsteuerung', 'art': 'anfrage', 'von': ich, 'name': t['name']}, an=[ziel])
        return {'ok': True}

    if aktion in ('erteilen', 'ablehnen'):                 # nur die teilende Person (nach dem Dialog der Desktop-App)
        if not eigene:
            fehler(403, 'Nur wer den Bildschirm teilt und Fernsteuerung anbietet, kann sie erlauben.')
        an = d.get('an')
        if not isinstance(an, str) or not ID_RE.match(an) or an == ich:
            fehler(400, 'Ungültige Anfrage')
        z = meeting.ziel_laden(r, an)
        if aktion == 'ablehnen':
            await lk.senden(r['code'], {'typ': 'fernsteuerung', 'art': 'abgelehnt', 'von': ich}, an=[an])
            return {'ok': True}
        kennung = d.get('kennung')
        if not isinstance(kennung, str) or not KENNUNG_RE.match(kennung):
            fehler(400, 'Ungültige Anfrage')
        if z['rolle'] in NICHT_ANFRAGEN:
            fehler(403, 'Webinar-Zuschauer können keine Fernsteuerung bekommen.')
        if eigene['an'] and eigene['an'] != an:
            fehler(409, 'Den Bildschirm steuert gerade schon jemand.')
        if not await _teilt_bildschirm(r['code'], ich):
            fehler(409, 'Fernsteuerung gibt es nur, während du deinen Bildschirm teilst.')
        c.execute('UPDATE fernsteuerung SET an = ?, an_name = ?, kennung = ?, erteilt = ? WHERE raum_id = ? AND identity = ?',
                  (an, z['name'], kennung, int(time.time()), r['id'], ich))
        db.protokollieren('Fernsteuerung erteilt', t['benutzer_id'], r['code'])
        await _metadaten(r)
        await lk.senden(r['code'], {'typ': 'fernsteuerung', 'art': 'erteilt', 'von': ich, 'name': t['name']}, an=[an])
        return {'ok': True}

    if aktion == 'beenden':                                # teilende/steuernde Person, oder Host/Co-Host für eine Zeile
        ziel = d.get('ziel')
        zeilen = [x for x in _zeilen(r) if x['an'] and (x['identity'] == ich or x['an'] == ich
                                                         or (t['rolle'] in ('host', 'cohost') and x['identity'] == ziel))]
        if not zeilen:
            fehler(404, 'Gerade steuert niemand.')
        for x in zeilen:
            await _aufheben(r, x, 'beendet', t['benutzer_id'])
        await _metadaten(r)
        return {'ok': True}

    fehler(400, 'Aktion: bereit, anfragen, erteilen, ablehnen oder beenden.')


async def alle_pruefen():
    """Hintergrund (jede Minute): Freigaben ohne Bildschirmspur oder ohne Teilnahme aufheben."""
    c = db.verbindung()
    for raum_id in {x['raum_id'] for x in c.execute('SELECT raum_id FROM fernsteuerung').fetchall()}:
        r = c.execute('SELECT * FROM raeume WHERE id = ?', (raum_id,)).fetchone()
        if not r or not r['aktiv']:
            c.execute('DELETE FROM fernsteuerung WHERE raum_id = ?', (raum_id,))
            continue
        liste = await lk.teilnehmer(r['code'])
        if liste is None:
            continue
        teilt = {p['identity'] for p in liste if any(t['quelle'] == 'bildschirm' for t in p['tracks'])}
        da = {p['identity'] for p in liste}
        geaendert = False
        for z in _zeilen(r):
            if z['identity'] not in teilt:
                await _aufheben(r, z, 'freigabe-ende')
                c.execute('DELETE FROM fernsteuerung WHERE raum_id = ? AND identity = ?', (raum_id, z['identity']))
                geaendert = True
            elif z['an'] and z['an'] not in da:
                await _aufheben(r, z, 'gegangen')
                geaendert = True
        if geaendert:
            await _metadaten(r)
