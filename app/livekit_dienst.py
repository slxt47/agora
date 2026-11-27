"""Alle Aufrufe an LiveKit (livekit-api 2.0.1) an einer Stelle.

- Zugangs-Token (JWT) mit VideoGrants: room_join, room = Raum-Code, Quellen je Rolle/Rechten, Attribute rolle/farbe.
  Ohne can_update_own_metadata: Name, Attribute (rolle, farbe, hand) und Metadaten setzt nur der Server – sonst könnte
  jeder Client sich selbst umbenennen (trotz Recht „umbenennen“ aus) oder Attribute fälschen.
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
import secrets
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


def extra_quellen(code, identity):   # wird von dolmetsch.py ersetzt: zusätzliche Quellen (Dolmetscher: Mikrofon)
    return []


def alle_raeume(code):
    """Breakout-Räume zuerst, der Hauptraum zuletzt (im Testmodus ist der letzte Aufruf so der des Hauptraums)."""
    return [*nebenraeume(code), code]


def quellen(rolle, rechte, extra=()):
    """Welche Quellen darf jemand senden? Bildschirm nur Host/Co-Host oder wenn Recht „bildschirm“ an ist.
    Webinar-Zuschauer senden nichts (can_publish false) – außer `extra` (vom Server, z. B. Mikrofon für Dolmetscher)."""
    q = [] if rolle == 'zuschauer' else ['kamera', 'mikrofon']
    if rolle != 'zuschauer' and (rolle in ('host', 'cohost') or rechte.get('bildschirm')):
        q += ['bildschirm', 'bildschirm_ton']
    return q + [x for x in extra if x not in q]


def token(code, identity, name, rolle, farbe, rechte):
    grants = api.VideoGrants(room_join=True, room=code, can_publish=bool(quellen(rolle, rechte)), can_subscribe=True, can_publish_data=True,
                             can_update_own_metadata=False, can_publish_sources=[QUELLEN_JWT[q] for q in quellen(rolle, rechte)])
    return (api.AccessToken(API_KEY, API_SECRET).with_identity(identity).with_name(name).with_grants(grants)
            .with_attributes({'rolle': rolle, 'farbe': farbe}).with_ttl(TOKEN_TTL).to_jwt())


def berechtigung(rolle, rechte, extra=()):
    q = quellen(rolle, rechte, extra)
    return api.ParticipantPermission(can_subscribe=True, can_publish=bool(q), can_publish_data=True, can_update_metadata=False,
                                     can_publish_sources=[QUELLEN_PROTO[x] for x in q])


def _client():
    return api.LiveKitAPI(url=API_HOST, api_key=API_KEY, api_secret=API_SECRET,
                          timeout=aiohttp.ClientTimeout(total=5), failover=False)


def _fehler_zaehlen(aufruf):
    from .metriken import zaehlen      # spät importiert (metriken importiert dieses Modul)
    zaehlen('agora_livekit_fehler_total', aufruf=aufruf)


async def _aufruf(_name, _art, **kw):
    """Einen RoomService-Aufruf ausführen; bei Fehler None."""
    if FAKE:
        AUFRUFE.append((_name, kw))
        return True
    try:
        async with _client() as lk:
            return await _art(lk, **kw)
    except Exception as e:   # noqa: BLE001
        _fehler_zaehlen(_name)
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
                 'tracks': [{'sid': t.sid, 'quelle': QUELLE_NAME.get(t.source, ''), 'stumm': t.muted, 'name': t.name} for t in p.tracks]}
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
        _fehler_zaehlen('teilnehmer')
        print(f'livekit-fehler teilnehmer: {e}', flush=True)
        return None
    except Exception as e:   # noqa: BLE001
        _fehler_zaehlen('teilnehmer')
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


async def aktualisieren(code, identity, name=None, rolle=None, farbe=None, rechte=None, attribute=None):
    """Name, Attribut rolle und Rechte (Quellen) eines Teilnehmers ändern; attribute: weitere Attribute (z. B. hand).
    Zu den Quellen der Rolle kommen `extra_quellen` (Dolmetscher) – nur vom Server bestimmt."""
    q = quellen(rolle, rechte or {}, extra_quellen(code, identity)) if rolle is not None else None

    async def f(lk, code, identity, name, rolle, farbe, rechte, attribute, quellen=None):
        req = api.UpdateParticipantRequest(room=code, identity=identity)
        if name is not None:
            req.name = name
        for k, v in (attribute or {}).items():
            req.attributes[k] = v
        if rolle is not None:
            req.attributes['rolle'] = rolle
            if farbe:
                req.attributes['farbe'] = farbe
            req.permission.CopyFrom(api.ParticipantPermission(can_subscribe=True, can_publish=bool(quellen), can_publish_data=True,
                                                              can_update_metadata=False, can_publish_sources=[QUELLEN_PROTO[x] for x in quellen]))
        await lk.room.update_participant(req)
        return True
    return await _aufruf('aktualisieren', f, code=ort(code, identity), identity=identity, name=name, rolle=rolle, farbe=farbe, rechte=rechte,
                         attribute=attribute, **({'quellen': q} if q is not None else {}))


async def spur_stumm(raum, identity, sid):
    """Eine bestimmte Spur serverseitig stumm schalten (Dolmetsch-Spur ohne Zuweisung)."""
    async def f(lk, raum, identity, sid):
        await lk.room.mute_published_track(api.MuteRoomTrackRequest(room=raum, identity=identity, track_sid=sid, muted=True))
        return True
    return await _aufruf('spur_stumm', f, raum=raum, identity=identity, sid=sid)


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


# ======== Block „Egress“ (Server-Aufnahme + Livestream, app/egress.py) ========
# RoomComposite-Egress (Chrome im Egress-Container rendert das LiveKit-Layout „grid“ oder „speaker“): als MP4-Datei
# (EncodedFileOutput, Pfad aus Sicht des Egress-Containers) oder als RTMP(S)-Stream (StreamOutput). Stream-URLs
# enthalten den Stream-Schlüssel: Sie werden nie protokolliert, Fehlermeldungen laufen durch `schwaerzen`.
FAKE_EGRESS = {}        # Fälschung: egress_id → {status, fehler, datei, dauer, groesse}
EGRESS_STATUS = {'EGRESS_STARTING': 'startet', 'EGRESS_ACTIVE': 'laeuft', 'EGRESS_ENDING': 'endet', 'EGRESS_COMPLETE': 'fertig',
                 'EGRESS_FAILED': 'fehler', 'EGRESS_ABORTED': 'fehler', 'EGRESS_LIMIT_REACHED': 'fehler'}


def schwaerzen(text, *geheim):
    text = str(text or '')
    for g in geheim:
        if g:
            text = text.replace(g, '***')
    return text[:300]


async def _egress(_name, _f, _geheim=(), **kw):
    """Egress-Aufruf; (ergebnis, None) oder (None, Fehlertext ohne Geheimnisse)."""
    try:
        async with api.LiveKitAPI(url=API_HOST, api_key=API_KEY, api_secret=API_SECRET,
                                  timeout=aiohttp.ClientTimeout(total=15), failover=False) as lk:
            return await _f(lk, **kw), None
    except Exception as e:   # noqa: BLE001
        _fehler_zaehlen(_name)
        text = schwaerzen(getattr(e, 'message', None) or str(e), *_geheim)
        print(f'livekit-fehler {_name}: {type(e).__name__}: {text}', flush=True)
        return None, text or type(e).__name__


async def egress_datei(code, dateipfad, layout):
    """Raum als MP4 aufzeichnen → (egress_id, fehler)."""
    if FAKE:
        eid = f'EG_fake{secrets.token_hex(6)}'
        AUFRUFE.append(('egress_datei', {'code': code, 'datei': dateipfad, 'layout': layout}))
        FAKE_EGRESS[eid] = {'status': 'laeuft'}
        return eid, None

    async def f(lk):
        info = await lk.egress.start_room_composite_egress(api.RoomCompositeEgressRequest(
            room_name=code, layout=layout, preset=api.EncodingOptionsPreset.H264_720P_30,
            file_outputs=[api.EncodedFileOutput(file_type=api.EncodedFileType.MP4, filepath=dateipfad, disable_manifest=True)]))
        return info.egress_id
    return await _egress('egress_datei', f)


async def egress_stream(code, url, layout, geheim=()):
    """Raum als RTMP(S)-Stream senden → (egress_id, fehler). url enthält den Schlüssel."""
    if FAKE:
        eid = f'EG_fake{secrets.token_hex(6)}'
        AUFRUFE.append(('egress_stream', {'code': code, 'url_laenge': len(url), 'layout': layout}))   # nie die URL selbst
        FAKE_EGRESS[eid] = {'status': 'laeuft'}
        return eid, None

    async def f(lk):
        info = await lk.egress.start_room_composite_egress(api.RoomCompositeEgressRequest(
            room_name=code, layout=layout, preset=api.EncodingOptionsPreset.H264_720P_30,
            stream_outputs=[api.StreamOutput(protocol=api.StreamProtocol.RTMP, urls=[url])]))
        return info.egress_id
    return await _egress('egress_stream', f, _geheim=(url, *geheim))


HLS_KODIERUNG = {     # Stufe → Kodierung (720p: Preset; 360p gibt es als Preset nicht → eigene Werte, Keyframe = Segmentlänge)
    '720p': {'preset': api.EncodingOptionsPreset.H264_720P_30},
    '360p': {'advanced': api.EncodingOptions(width=640, height=360, framerate=30, video_bitrate=900, audio_bitrate=96,
                                             audio_frequency=48000, key_frame_interval=4)},
}


async def egress_hls(code, praefix, stufe, layout, segment_s=4):
    """Raum als HLS (MPEG-TS-Segmente) in den Ordner von `praefix` schreiben → (egress_id, fehler).
    Dateien: <stufe>.m3u8 (alles), <stufe>-live.m3u8 (gleitendes Fenster), <stufe>_<nr>.ts (Segmente)."""
    if FAKE:
        eid = f'EG_fake{secrets.token_hex(6)}'
        AUFRUFE.append(('egress_hls', {'code': code, 'praefix': praefix, 'stufe': stufe, 'layout': layout, 'segment_s': segment_s}))
        FAKE_EGRESS[eid] = {'status': 'laeuft'}
        return eid, None
    ordner = praefix.rsplit('/', 1)[0]

    async def f(lk):
        info = await lk.egress.start_room_composite_egress(api.RoomCompositeEgressRequest(
            room_name=code, layout=layout, **HLS_KODIERUNG[stufe],
            segment_outputs=[api.SegmentedFileOutput(protocol=api.SegmentedFileProtocol.HLS_PROTOCOL, filename_prefix=praefix,
                                                     playlist_name=f'{ordner}/{stufe}.m3u8', live_playlist_name=f'{ordner}/{stufe}-live.m3u8',
                                                     segment_duration=segment_s, filename_suffix=api.SegmentedFileSuffix.INDEX,
                                                     disable_manifest=True)]))
        return info.egress_id
    return await _egress('egress_hls', f)


async def egress_stoppen(egress_id):
    if FAKE:
        AUFRUFE.append(('egress_stoppen', {'egress_id': egress_id}))
        if FAKE_EGRESS.get(egress_id, {}).get('status') in ('startet', 'laeuft'):
            FAKE_EGRESS[egress_id]['status'] = 'endet'
        return True, None

    async def f(lk):
        await lk.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
        return True
    return await _egress('egress_stoppen', f)


async def egress_info(egress_id, geheim=()):
    """{status: startet|laeuft|endet|fertig|fehler, fehler, datei, dauer (s), groesse} oder None (nicht erreichbar)."""
    if FAKE:
        return dict(FAKE_EGRESS[egress_id]) if egress_id in FAKE_EGRESS else None

    async def f(lk):
        r = await lk.egress.list_egress(api.ListEgressRequest(egress_id=egress_id))
        if not r.items:
            return {'status': 'fehler', 'fehler': 'Egress unbekannt'}
        e = r.items[0]
        fehler = e.error or next((s.error for s in e.stream_results if s.error), '')
        d = {'status': EGRESS_STATUS.get(api.EgressStatus.Name(e.status), 'laeuft'), 'fehler': schwaerzen(fehler, *geheim)}
        if e.file_results:
            fi = e.file_results[0]
            d.update(datei=fi.filename, dauer=int(fi.duration / 1e9) if fi.duration else None, groesse=fi.size)
        return d
    erg, _f = await _egress('egress_info', f, _geheim=geheim)
    return erg
# ======== Ende Block „Egress“ ========


def fake_zuruecksetzen():
    AUFRUFE.clear()
    FAKE_TEILNEHMER.clear()
    FAKE_EGRESS.clear()
