"""Alle Aufrufe an LiveKit (livekit-api 2.0.1) an einer Stelle.

- Zugangs-Token (JWT) mit VideoGrants: room_join, room = Raum-Code, Quellen je Rolle/Rechten, Attribute rolle/farbe.
- Server-API (Twirp über HTTP an LIVEKIT_API_HOST): Raum anlegen/Metadaten/löschen, Teilnehmer auflisten,
  stummschalten, entfernen (Token widerrufen), aktualisieren (Name/Rechte/Attribute), Daten senden.

Jeder Aufruf öffnet einen eigenen LiveKitAPI-Client (aiohttp-Sitzung gehört zur laufenden Event-Loop) – so geht es
aus FastAPI-Endpunkten und aus dem Hintergrund-Thread (asyncio.run) gleichermaßen. Fehler werden protokolliert und
als None/False gemeldet: Ein kurz nicht erreichbares LiveKit darf keine Raum-Aktion der Datenbank kaputt machen.

Testmodus (AGORA_TEST=1 ohne LIVEKIT_API_HOST): Fälschung, die alle Aufrufe in AUFRUFE mitschreibt; die
Teilnehmerliste kommt aus FAKE_TEILNEHMER[code] (von Tests befüllt).

Breakout-Räume sind eigene LiveKit-Räume (`<code>__b<n>`). Damit Ereignisse, Metadaten, Stummschalten, Entfernen
und das Ende des Meetings alle erreichen, arbeiten diese Aufrufe über alle Räume eines Meetings: `nebenraeume(code)`
und `ort(code, identity)` setzt das Modul breakout (ohne Breakout: nur der Hauptraum).
"""
import asyncio
import datetime
import json
import os
import time

import aiohttp
from livekit import api

API_HOST = os.environ.get('LIVEKIT_API_HOST', '')
API_KEY = os.environ.get('LIVEKIT_API_KEY', '')
API_SECRET = os.environ.get('LIVEKIT_API_SECRET', '')
FAKE = os.environ.get('AGORA_TEST') == '1' and not API_HOST
if FAKE:
    API_KEY = API_KEY or 'agora-test'
    API_SECRET = API_SECRET or 'agora-test-geheimnis-mit-genug-laenge-0123456789'
TOKEN_TTL = datetime.timedelta(minutes=15)   # nur zum Verbinden; LiveKit erneuert das Token verbundener Teilnehmer
LEER_TIMEOUT_S = 300                          # LiveKit schließt leere Räume selbst nach 5 min

AUFRUFE = []            # Fälschung: (name, dict)
FAKE_TEILNEHMER = {}    # Fälschung: code → [{identity, name, tracks:[{sid, quelle, stumm}]}]

QUELLEN_JWT = {'kamera': 'camera', 'mikrofon': 'microphone', 'bildschirm': 'screen_share', 'bildschirm_ton': 'screen_share_audio'}
QUELLEN_PROTO = {'kamera': api.TrackSource.CAMERA, 'mikrofon': api.TrackSource.MICROPHONE,
                 'bildschirm': api.TrackSource.SCREEN_SHARE, 'bildschirm_ton': api.TrackSource.SCREEN_SHARE_AUDIO}
QUELLE_NAME = {v: k for k, v in QUELLEN_PROTO.items()}


def nebenraeume(code):            # wird von breakout.py ersetzt: offene Breakout-Räume (LiveKit-Raumnamen)
    return []


def ort(code, identity):          # wird von breakout.py ersetzt: LiveKit-Raum, in dem identity gerade ist
    return code


def alle_raeume(code):
    """Breakout-Räume zuerst, der Hauptraum zuletzt (im Testmodus ist der letzte Aufruf so der des Hauptraums)."""
    return [*nebenraeume(code), code]


def quellen(rolle, rechte):
    """Welche Quellen darf jemand senden? Bildschirm nur Host/Co-Host oder wenn Recht „bildschirm“ an ist."""
    q = ['kamera', 'mikrofon']
    if rolle in ('host', 'cohost') or rechte.get('bildschirm'):
        q += ['bildschirm', 'bildschirm_ton']
    return q


def token(code, identity, name, rolle, farbe, rechte):
    grants = api.VideoGrants(room_join=True, room=code, can_publish=True, can_subscribe=True, can_publish_data=True,
                             can_update_own_metadata=True, can_publish_sources=[QUELLEN_JWT[q] for q in quellen(rolle, rechte)])
    return (api.AccessToken(API_KEY, API_SECRET).with_identity(identity).with_name(name).with_grants(grants)
            .with_attributes({'rolle': rolle, 'farbe': farbe}).with_ttl(TOKEN_TTL).to_jwt())


def berechtigung(rolle, rechte):
    return api.ParticipantPermission(can_subscribe=True, can_publish=True, can_publish_data=True, can_update_metadata=True,
                                     can_publish_sources=[QUELLEN_PROTO[q] for q in quellen(rolle, rechte)])


def _client():
    return api.LiveKitAPI(url=API_HOST, api_key=API_KEY, api_secret=API_SECRET,
                          timeout=aiohttp.ClientTimeout(total=5), failover=False)


async def _aufruf(_name, _art, **kw):
    """Einen RoomService-Aufruf ausführen; bei Fehler None."""
    if FAKE:
        AUFRUFE.append((_name, kw))
        return True
    try:
        async with _client() as lk:
            return await _art(lk, **kw)
    except Exception as e:   # noqa: BLE001
        print(f'livekit-fehler {_name}: {type(e).__name__}: {str(e)[:200]}', flush=True)
        return None


async def raum_anlegen(code, metadaten, max_teilnehmer):
    """Legt den LiveKit-Raum an (oder liefert den bestehenden) – mit unseren Metadaten."""
    async def f(lk, code, metadaten, max_teilnehmer):
        await lk.room.create_room(api.CreateRoomRequest(name=code, empty_timeout=LEER_TIMEOUT_S, departure_timeout=20,
                                                        max_participants=max_teilnehmer, metadata=json.dumps(metadaten)))
        # Ein schon bestehender Raum übernimmt die Metadaten aus create_room nicht zwingend
        await lk.room.update_room_metadata(api.UpdateRoomMetadataRequest(room=code, metadata=json.dumps(metadaten)))
        return True
    return await _aufruf('raum_anlegen', f, code=code, metadaten=metadaten, max_teilnehmer=max_teilnehmer)


async def metadaten(code, daten):
    async def f(lk, code, daten):
        await lk.room.update_room_metadata(api.UpdateRoomMetadataRequest(room=code, metadata=json.dumps(daten)))
        return True
    ergebnisse = [await _aufruf('metadaten', f, code=raum, daten=daten) for raum in alle_raeume(code)]
    return ergebnisse[-1]


async def raum_loeschen(code, mit_nebenraeumen=True):
    async def f(lk, code):
        await lk.room.delete_room(api.DeleteRoomRequest(room=code))
        return True
    ergebnisse = [await _aufruf('raum_loeschen', f, code=raum) for raum in (alle_raeume(code) if mit_nebenraeumen else [code])]
    return ergebnisse[-1]


async def teilnehmer(code):
    """Alle Verbundenen des Meetings (Hauptraum + offene Breakout-Räume):
    [{identity, name, raum, tracks:[{sid, quelle, stumm}]}] oder None, wenn LiveKit nicht antwortet."""
    if FAKE:
        AUFRUFE.append(('teilnehmer', {'code': code}))
        return [dict(t) | {'raum': raum} for raum in alle_raeume(code) for t in FAKE_TEILNEHMER.get(raum, [])]

    async def f(lk, raum):
        r = await lk.room.list_participants(api.ListParticipantsRequest(room=raum))
        return [{'identity': p.identity, 'name': p.name, 'raum': raum,
                 'tracks': [{'sid': t.sid, 'quelle': QUELLE_NAME.get(t.source, ''), 'stumm': t.muted} for t in p.tracks]}
                for p in r.participants]
    alle = []
    try:
        async with _client() as lk:
            for raum in alle_raeume(code):
                try:
                    alle += await f(lk, raum)
                except api.TwirpError as e:
                    if getattr(e, 'code', '') != 'not_found':   # Raum gibt es bei LiveKit (noch/nicht mehr) nicht → leer
                        raise
        return alle
    except api.TwirpError as e:
        print(f'livekit-fehler teilnehmer: {e}', flush=True)
        return None
    except Exception as e:   # noqa: BLE001
        print(f'livekit-fehler teilnehmer: {type(e).__name__}', flush=True)
        return None


async def stummschalten(code, identities, quelle='mikrofon'):
    """Alle Spuren einer Quelle (mikrofon/kamera) der genannten Teilnehmer serverseitig stumm schalten."""
    identities = set(identities)
    liste = await teilnehmer(code)
    if FAKE:
        AUFRUFE.append(('stumm', {'code': code, 'identities': sorted(identities), 'quelle': quelle}))
    for p in liste or []:
        if p['identity'] not in identities:
            continue
        identity = p['identity']
        for t in p['tracks']:
            if t['quelle'] == quelle and not t['stumm']:
                async def f(lk, code, identity, sid):
                    await lk.room.mute_published_track(api.MuteRoomTrackRequest(room=code, identity=identity, track_sid=sid, muted=True))
                    return True
                await _aufruf('mute_published_track', f, code=p.get('raum', code), identity=identity, sid=t['sid'])
    return True


async def entfernen(code, identity):
    async def f(lk, code, identity):
        # revoke_token_ts: vorher ausgestellte Token dieser identity gelten nicht mehr (kein Wiederverbinden)
        await lk.room.remove_participant(api.RoomParticipantIdentity(room=code, identity=identity, revoke_token_ts=int(time.time())))
        return True
    ergebnisse = [await _aufruf('entfernen', f, code=raum, identity=identity) for raum in alle_raeume(code)]
    return ergebnisse[-1]


async def aktualisieren(code, identity, name=None, rolle=None, farbe=None, rechte=None):
    """Name, Attribut rolle und Rechte (Quellen) eines Teilnehmers ändern."""
    async def f(lk, code, identity, name, rolle, farbe, rechte):
        req = api.UpdateParticipantRequest(room=code, identity=identity)
        if name is not None:
            req.name = name
        if rolle is not None:
            req.attributes['rolle'] = rolle
            if farbe:
                req.attributes['farbe'] = farbe
            req.permission.CopyFrom(berechtigung(rolle, rechte or {}))
        await lk.room.update_participant(req)
        return True
    return await _aufruf('aktualisieren', f, code=ort(code, identity), identity=identity, name=name, rolle=rolle, farbe=farbe, rechte=rechte)


async def senden(code, daten, topic='agora', an=None):
    """Zuverlässig über den Datenkanal schicken; an = Liste von identities (None = alle)."""
    async def f(lk, code, daten, topic, an):
        await lk.room.send_data(api.SendDataRequest(room=code, data=json.dumps(daten).encode(), kind=api.DataPacket.Kind.RELIABLE,
                                                    destination_identities=list(an or []), topic=topic))
        return True
    if an is not None and not an:
        return True     # niemand zu benachrichtigen
    raeume = alle_raeume(code)
    if FAKE or len(raeume) == 1:
        ergebnisse = [await _aufruf('senden', f, code=raum, daten=daten, topic=topic, an=list(an) if an else None) for raum in raeume]
    else:               # an alle Räume gleichzeitig (Ziel-identities, die nicht im Raum sind, ignoriert LiveKit)
        ergebnisse = await asyncio.gather(*[_aufruf('senden', f, code=raum, daten=daten, topic=topic, an=list(an) if an else None)
                                            for raum in raeume])
    return ergebnisse[-1]


# ---------------------------------------------------------------- Admin-Übersicht
async def raeume_liste():
    """Alle LiveKit-Räume (auch Breakout): [{name, teilnehmer, erstellt}] oder None, wenn LiveKit nicht antwortet."""
    if FAKE:
        AUFRUFE.append(('raeume_liste', {}))
        return [{'name': n, 'teilnehmer': len(liste), 'erstellt': 0} for n, liste in FAKE_TEILNEHMER.items() if liste]

    async def f(lk):
        r = await lk.room.list_rooms(api.ListRoomsRequest())
        return [{'name': x.name, 'teilnehmer': x.num_participants, 'erstellt': x.creation_time} for x in r.rooms]
    return await _aufruf('raeume_liste', f)


def fake_zuruecksetzen():
    AUFRUFE.clear()
    FAKE_TEILNEHMER.clear()
