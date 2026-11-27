"""Alle Aufrufe an LiveKit (livekit-api 2.0.1) an einer Stelle.

- Zugangs-Token (JWT) mit VideoGrants: room_join, room = Raum-Code, Quellen je Rolle/Rechten, Attribute rolle/farbe.
  Ohne can_update_own_metadata: Name, Attribute (rolle, farbe, hand) und Metadaten setzt nur der Server – sonst könnte
  jeder Client sich selbst umbenennen (trotz Recht „umbenennen“ aus) oder Attribute fälschen.
- Server-API (Twirp über HTTP an LIVEKIT_API_HOST): Raum anlegen/Metadaten/löschen, Teilnehmer auflisten,
  stummschalten, entfernen (Token widerrufen), aktualisieren (Name/Rechte/Attribute), Daten senden.

Jeder Aufruf öffnet einen eigenen LiveKitAPI-Client (aiohttp-Sitzung gehört zur laufenden Event-Loop) – so geht es
aus FastAPI-Endpunkten und aus dem Hintergrund-Thread (asyncio.run) gleichermaßen. Fehler werden protokolliert und
als None/False gemeldet: Ein kurz nicht erreichbares LiveKit darf keine Raum-Aktion der Datenbank kaputt machen.

Testmodus (AGORA_TEST=1 ohne LIVEKIT_API_HOST und AGORA_LIVEKIT_KNOTEN): Fälschung, die alle Aufrufe in AUFRUFE mitschreibt; die
Teilnehmerliste kommt aus FAKE_TEILNEHMER[code] (von Tests befüllt).

Mehrere LiveKit-Knoten (ADR 0007, optional): `AGORA_LIVEKIT_KNOTEN` nennt je Knoten Region, Signal-URL (für Browser)
und API-URL. Server-API-Aufrufe gehen an den ersten erreichbaren Knoten (bevorzugt in der gewünschten Region); ist
ein Knoten nicht erreichbar (Verbindungsaufbau scheitert), wird er 15 s gemieden und der nächste gefragt. Welcher
Knoten den Raum trägt, entscheidet LiveKit (gemeinsames Redis, `node_selector`): Mit `regionaware` landet ein neuer
Raum in der Region des Knotens, der CreateRoom bekommt – deshalb schickt raum_anlegen() den Aufruf an einen Knoten der
Wunsch-Region. Ohne die Variable: genau ein Knoten aus AGORA_LIVEKIT_URL/LIVEKIT_API_HOST (wie bisher).

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
FAKE = os.environ.get('AGORA_TEST') == '1' and not API_HOST and not os.environ.get('AGORA_LIVEKIT_KNOTEN')
if FAKE:
    API_KEY = API_KEY or 'agora-test'
    API_SECRET = API_SECRET or 'agora-test-geheimnis-mit-genug-laenge-0123456789'
KNOTEN_MEIDEN_S = 15                          # nicht erreichbarer Knoten: so lange erst die anderen fragen
TOKEN_TTL = datetime.timedelta(minutes=15)   # nur zum Verbinden; LiveKit erneuert das Token verbundener Teilnehmer
LEER_TIMEOUT_S = 300                          # LiveKit schließt leere Räume selbst nach 5 min

AUFRUFE = []            # Fälschung: (name, dict)
FAKE_TEILNEHMER = {}    # Fälschung: code → [{identity, name, tracks:[{sid, quelle, stumm}]}]

QUELLEN_JWT = {'kamera': 'camera', 'mikrofon': 'microphone', 'bildschirm': 'screen_share', 'bildschirm_ton': 'screen_share_audio'}
QUELLEN_PROTO = {'kamera': api.TrackSource.CAMERA, 'mikrofon': api.TrackSource.MICROPHONE,
                 'bildschirm': api.TrackSource.SCREEN_SHARE, 'bildschirm_ton': api.TrackSource.SCREEN_SHARE_AUDIO}
QUELLE_NAME = {v: k for k, v in QUELLEN_PROTO.items()}


# ---------------------------------------------------------------- Knoten (ADR 0007)
def knoten_lesen(text):
    """`<region> <signal-url> <api-url>` je Knoten, durch Komma getrennt → [{name, region, signal, api}]."""
    erg = []
    for i, teil in enumerate(t for t in (text or '').split(',') if t.strip()):
        f = teil.split()
        if len(f) != 3 or not f[1].startswith(('ws://', 'wss://')) or not f[2].startswith(('http://', 'https://')):
            raise ValueError(f'AGORA_LIVEKIT_KNOTEN: Eintrag {i + 1} braucht „<region> <ws(s)://signal> <http(s)://api>“')
        erg.append({'name': f'{f[0]}-{i + 1}', 'region': f[0], 'signal': f[1].rstrip('/'), 'api': f[2].rstrip('/')})
    return erg


def laender_lesen(text):
    """`DE:eu-central,FR:eu-west` → {'DE': 'eu-central', …} (Land aus CF-IPCountry → Wunsch-Region)."""
    return {k.strip().upper(): v.strip() for k, _, v in (x.partition(':') for x in (text or '').split(',')) if k.strip() and v.strip()}


KNOTEN = knoten_lesen(os.environ.get('AGORA_LIVEKIT_KNOTEN', ''))
LAENDER = laender_lesen(os.environ.get('AGORA_LIVEKIT_LAENDER', ''))
_GEMIEDEN = {}          # api-url → monotonic-Zeit, bis zu der der Knoten nach hinten rückt
_RAUM_REGION = {}       # LiveKit-Raum → Region des Knotens, über den er angelegt wurde (nur Hinweis für die Signal-URL)


def knoten():
    """Alle Knoten; ohne AGORA_LIVEKIT_KNOTEN genau einer (AGORA_LIVEKIT_URL + LIVEKIT_API_HOST)."""
    if KNOTEN:
        return KNOTEN
    from .kern import LIVEKIT_URL
    return [{'name': 'livekit', 'region': '', 'signal': LIVEKIT_URL, 'api': API_HOST}]


def regionen():
    return {k['region'] for k in KNOTEN}


def region_wahl(wunsch=None, land=None):
    """Wunsch-Region: ausdrücklich (nur bekannte Regionen) → Land (CF-IPCountry über AGORA_LIVEKIT_LAENDER) → None."""
    if isinstance(wunsch, str) and wunsch in regionen():
        return wunsch
    r = LAENDER.get((land or '').strip().upper())
    return r if r in regionen() else None


def reihenfolge(region=None):
    """Knoten in Frage-Reihenfolge: Wunsch-Region zuerst, sonst Reihenfolge der Konfiguration; gemiedene ans Ende."""
    alle = knoten()
    if region:
        alle = [k for k in alle if k['region'] == region] + [k for k in alle if k['region'] != region]
    t = time.monotonic()
    return [k for k in alle if _GEMIEDEN.get(k['api'], 0) <= t] + [k for k in alle if _GEMIEDEN.get(k['api'], 0) > t]


def raum_region(raum):
    """Region, in der ein Raum (oder sein Hauptraum bei Breakout `<code>__b<n>`) angelegt wurde – oder None."""
    return _RAUM_REGION.get(raum) or _RAUM_REGION.get(raum.split('__b')[0])


def signal_url(region=None):
    """URL für den Browser: erster erreichbarer Knoten der Region. Jeder Knoten nimmt jeden Raum an (LiveKit leitet
    die Signalisierung über Redis zum Knoten des Raums weiter); die Medien gehen direkt an den Knoten des Raums."""
    return reihenfolge(region)[0]['signal']


# Nur Fehler beim Verbindungsaufbau lösen den Wechsel aus: Dann hat der Knoten die Anfrage sicher nicht bekommen
# (ein Zeitlimit mitten in der Antwort könnte einen Aufruf sonst doppelt ausführen).
WECHSELN_BEI = (aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError)


async def mit_knoten(f, region=None, timeout=5):
    """f(lk, knoten) am ersten erreichbaren Knoten ausführen; Fehler des Knotens (Twirp) gehen unverändert hoch."""
    letzter = None
    for k in reihenfolge(region):
        try:
            async with api.LiveKitAPI(url=k['api'], api_key=API_KEY, api_secret=API_SECRET, failover=False,
                                      timeout=aiohttp.ClientTimeout(total=timeout, sock_connect=min(2, timeout))) as lk:
                erg = await f(lk, k)
            _GEMIEDEN.pop(k['api'], None)
            return erg
        except WECHSELN_BEI as e:
            letzter = e
            _GEMIEDEN[k['api']] = time.monotonic() + KNOTEN_MEIDEN_S
            if len(knoten()) > 1:
                _fehler_zaehlen('knoten_' + k['name'])
                print(f'livekit-knoten {k["name"]} nicht erreichbar: {type(e).__name__}', flush=True)
    raise letzter


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


def _fehler_zaehlen(aufruf):
    from .metriken import zaehlen      # spät importiert (metriken importiert dieses Modul)
    zaehlen('agora_livekit_fehler_total', aufruf=aufruf)


async def _aufruf(_name, _art, **kw):
    """Einen RoomService-Aufruf ausführen (erster erreichbarer Knoten); bei Fehler None."""
    if FAKE:
        AUFRUFE.append((_name, kw))
        return True
    try:
        return await mit_knoten(lambda lk, _k: _art(lk, **kw))
    except Exception as e:   # noqa: BLE001
        _fehler_zaehlen(_name)
        print(f'livekit-fehler {_name}: {type(e).__name__}: {str(e)[:200]}', flush=True)
        return None


async def raum_anlegen(code, metadaten, max_teilnehmer, region=None):
    """Legt den LiveKit-Raum an (oder liefert den bestehenden) – mit unseren Metadaten. region: Wunsch-Region für einen
    neuen Raum (CreateRoom geht an einen Knoten dort; ein bestehender Raum bleibt, wo er ist). Breakout-Räume folgen
    ohne Angabe der Region ihres Hauptraums."""
    region = region or raum_region(code)
    if FAKE:
        AUFRUFE.append(('raum_anlegen', {'code': code, 'metadaten': metadaten, 'max_teilnehmer': max_teilnehmer}
                        | ({'region': region} if region else {})))
        return True

    async def f(lk, k):
        await lk.room.create_room(api.CreateRoomRequest(name=code, empty_timeout=LEER_TIMEOUT_S, departure_timeout=20,
                                                        max_participants=max_teilnehmer, metadata=json.dumps(metadaten)))
        # Ein schon bestehender Raum übernimmt die Metadaten aus create_room nicht zwingend
        await lk.room.update_room_metadata(api.UpdateRoomMetadataRequest(room=code, metadata=json.dumps(metadaten)))
        if k['region'] and code not in _RAUM_REGION:
            if len(_RAUM_REGION) > 5000:
                _RAUM_REGION.pop(next(iter(_RAUM_REGION)))
            _RAUM_REGION[code] = k['region']
        return True
    try:
        return await mit_knoten(f, region)
    except Exception as e:   # noqa: BLE001
        _fehler_zaehlen('raum_anlegen')
        print(f'livekit-fehler raum_anlegen: {type(e).__name__}: {str(e)[:200]}', flush=True)
        return None


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
    async def alle(lk, _k):
        erg = []
        for raum in alle_raeume(code):
            try:
                erg += await f(lk, raum)
            except api.TwirpError as e:
                if getattr(e, 'code', '') != 'not_found':   # Raum gibt es bei LiveKit (noch/nicht mehr) nicht → leer
                    raise
        return erg
    try:
        return await mit_knoten(alle)
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
        return await mit_knoten(lambda lk, _k: _f(lk, **kw), timeout=15), None
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


async def knoten_lage():
    """Je Knoten (nur bei mehreren): [{name, region, ok, ms}] – ein ListRooms direkt an genau diesen Knoten."""
    erg = []
    for k in knoten() if len(knoten()) > 1 and not FAKE else []:
        t0 = time.perf_counter()
        try:
            async with api.LiveKitAPI(url=k['api'], api_key=API_KEY, api_secret=API_SECRET, failover=False,
                                      timeout=aiohttp.ClientTimeout(total=3)) as lk:
                await lk.room.list_rooms(api.ListRoomsRequest())
            ok = True
        except Exception:   # noqa: BLE001
            ok = False
        erg.append({'name': k['name'], 'region': k['region'], 'ok': ok, 'ms': round((time.perf_counter() - t0) * 1000, 1)})
    return erg


def fake_zuruecksetzen():
    _RAUM_REGION.clear()
    _GEMIEDEN.clear()
    AUFRUFE.clear()
    FAKE_TEILNEHMER.clear()
    FAKE_EGRESS.clear()
