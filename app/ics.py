"""Termine: nächstes Vorkommen wiederkehrender Meetings und iCalendar-Dateien (RFC 5545).

Wiederholungen laufen in der Ortszeit der Zeitzone des Meetings (09:00 bleibt über die Zeitumstellung 09:00).
Die .ics enthält dafür eine VTIMEZONE, die aus den Umstellungen der IANA-Zeitzone abgeleitet wird.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .texte import sprache, tx

SCHRITT = {'taeglich': 1, 'woechentlich': 7}
RRULE = {'taeglich': 'DAILY', 'woechentlich': 'WEEKLY'}


def zone(name):
    try:
        return ZoneInfo(name or 'UTC')
    except Exception:   # noqa: BLE001
        return ZoneInfo('UTC')


def vorkommen(start, wiederholung, zeitzone, nr):
    """Beginn (Unix-Zeit) des nr-ten Vorkommens (0 = erstes), in Ortszeit fortgeschrieben."""
    if wiederholung not in SCHRITT:
        return start
    tz = zone(zeitzone)
    lokal = datetime.fromtimestamp(start, tz).replace(tzinfo=None) + timedelta(days=SCHRITT[wiederholung] * nr)
    return int(lokal.replace(tzinfo=tz).timestamp())


def naechster_termin(start, dauer_min, wiederholung, zeitzone, bis, jetzt):
    """Beginn des nächsten (oder gerade laufenden) Termins als Unix-Zeit, None wenn keiner mehr kommt."""
    if start is None:
        return None
    dauer = (dauer_min or 0) * 60
    if wiederholung not in SCHRITT:
        return start if start + dauer > jetzt else None
    schritt = SCHRITT[wiederholung] * 86400
    nr = max(0, (jetzt - dauer - start) // schritt - 1)        # grob vorspringen, dann genau suchen
    for _ in range(10):
        t = vorkommen(start, wiederholung, zeitzone, nr)
        if bis is not None and t > bis:
            return None
        if t + dauer > jetzt:
            return t
        nr += 1
    return None


# ---------------------------------------------------------------- iCalendar
def _esc(s):
    return (s or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\r', '').replace('\n', '\\n')


def _falten(zeile):
    """Zeilen auf 75 Oktette falten (Fortsetzung beginnt mit Leerzeichen), ohne UTF-8-Zeichen zu zerschneiden."""
    teile, aktuell, laenge = [], '', 0
    for ch in zeile:
        n = len(ch.encode())
        if laenge + n > (75 if not teile else 74):
            teile.append(aktuell)
            aktuell, laenge = '', 0
        aktuell += ch
        laenge += n
    teile.append(aktuell)
    return '\r\n '.join(teile)


def _utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _lokal(t, tz):
    return datetime.fromtimestamp(t, tz).strftime('%Y%m%dT%H%M%S')


def _offset(d):
    s = int(d.total_seconds())
    vz = '+' if s >= 0 else '-'
    s = abs(s)
    return f'{vz}{s // 3600:02d}{(s % 3600) // 60:02d}'


def _umstellungen(tz, jahr):
    """Zeitpunkte (UTC-Stunden) im Jahr, an denen sich der UTC-Abstand ändert."""
    t = datetime(jahr, 1, 1, tzinfo=timezone.utc)
    ende = datetime(jahr + 1, 1, 1, tzinfo=timezone.utc)
    alt = t.astimezone(tz).utcoffset()
    ergebnis = []
    while t < ende:
        n = t + timedelta(hours=1)
        o = n.astimezone(tz).utcoffset()
        if o != alt:
            ergebnis.append((n, alt, o))
            alt = o
        t = n
    return ergebnis


def vtimezone(name, jahr):
    tz = zone(name)
    zeilen = ['BEGIN:VTIMEZONE', f'TZID:{name}']
    wechsel = _umstellungen(tz, jahr)
    if not wechsel:
        o = datetime(jahr, 1, 1, tzinfo=timezone.utc).astimezone(tz)
        off = _offset(o.utcoffset())
        zeilen += ['BEGIN:STANDARD', 'DTSTART:19700101T000000', f'TZOFFSETFROM:{off}', f'TZOFFSETTO:{off}',
                   f'TZNAME:{o.tzname()}', 'END:STANDARD']
    for zeitpunkt, von, nach in wechsel:
        lokal = (zeitpunkt + von).replace(tzinfo=None)        # Wandzeit vor der Umstellung
        art = 'DAYLIGHT' if (zeitpunkt.astimezone(tz).dst() or timedelta(0)) > timedelta(0) else 'STANDARD'
        wt = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'][lokal.weekday()]
        letzte = (lokal + timedelta(days=7)).month != lokal.month
        nr = -1 if letzte else (lokal.day - 1) // 7 + 1
        zeilen += [f'BEGIN:{art}', f'DTSTART:{lokal.strftime("%Y%m%dT%H%M%S")}', f'TZOFFSETFROM:{_offset(von)}',
                   f'TZOFFSETTO:{_offset(nach)}', f'TZNAME:{zeitpunkt.astimezone(tz).tzname()}',
                   f'RRULE:FREQ=YEARLY;BYMONTH={lokal.month};BYDAY={nr}{wt}', f'END:{art}']
    zeilen.append('END:VTIMEZONE')
    return zeilen


def _beschreibung(beschreibung, link, code):
    s = sprache()       # Sprache der Anfrage (bzw. mit_sprache im Hintergrund)
    return (beschreibung + '\n\n' if beschreibung else '') + tx(s, 'ics.beitreten', link=link) + '\n' + tx(s, 'ics.code', code=code)


def _vevent(code, titel, beschreibung, link, start, dauer_min, zeitzone, wiederholung, bis, erstellt, veranstalter='Agora',
            sequenz=0):
    """Zeilen eines VEVENT (wiederkehrend in Ortszeit mit TZID + RRULE, sonst UTC)."""
    tz = zone(zeitzone)
    ende = start + (dauer_min or 60) * 60
    zeilen = ['BEGIN:VEVENT', f'UID:{code}@agora.slxt.dev', f'DTSTAMP:{_utc(erstellt)}', f'SEQUENCE:{sequenz}']
    if wiederholung in RRULE:
        zeilen += [f'DTSTART;TZID={zeitzone}:{_lokal(start, tz)}', f'DTEND;TZID={zeitzone}:{_lokal(ende, tz)}']
        regel = f'RRULE:FREQ={RRULE[wiederholung]}'
        if bis is not None:
            regel += f';UNTIL={_utc(bis)}'
        zeilen.append(regel)
    else:
        zeilen += [f'DTSTART:{_utc(start)}', f'DTEND:{_utc(ende)}']
    zeilen += [f'SUMMARY:{_esc(titel)}', f'DESCRIPTION:{_esc(_beschreibung(beschreibung, link, code))}', f'LOCATION:{_esc(link)}',
               f'URL:{link}', 'ORGANIZER;CN="' + veranstalter.replace('"', "'")[:60] + '":mailto:noreply@slxt.dev',
               'STATUS:CONFIRMED', 'TRANSP:OPAQUE',
               'BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:' + _esc(tx(sprache(), 'ics.alarm')), 'TRIGGER:-PT10M', 'END:VALARM', 'END:VEVENT']
    return zeilen


def _ausgabe(zeilen):
    return '\r\n'.join(_falten(z) for z in zeilen) + '\r\n'


def kalender(code, titel, beschreibung, link, start, dauer_min, zeitzone, wiederholung, bis, erstellt, veranstalter='Agora', sequenz=0):
    """Vollständige .ics (METHOD:PUBLISH) für ein geplantes Meeting."""
    zeilen = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//slxt//Agora//DE', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH']
    if wiederholung in RRULE:
        zeilen += vtimezone(zeitzone, datetime.fromtimestamp(start, zone(zeitzone)).year)
    zeilen += _vevent(code, titel, beschreibung, link, start, dauer_min, zeitzone, wiederholung, bis, erstellt, veranstalter, sequenz)
    zeilen.append('END:VCALENDAR')
    return _ausgabe(zeilen)


# ---------------------------------------------------------------- Kalender-Abo (iCal-Feed) und Links „Zu … hinzufügen“
def abo_kalender(name, termine):
    """Abo-Feed (webcal): alle anstehenden Meetings einer Person. termine = [dict mit den Argumenten von _vevent].
    Ohne METHOD (Abo, keine Einladung); Kalender-Apps fragen laut REFRESH-INTERVAL stündlich nach."""
    zeilen = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//slxt//Agora//DE', 'CALSCALE:GREGORIAN',
              f'X-WR-CALNAME:{_esc(name)}', 'X-WR-CALDESC:' + _esc(tx(sprache(), 'ics.abo')), 'REFRESH-INTERVAL;VALUE=DURATION:PT1H',
              'X-PUBLISHED-TTL:PT1H']
    zonen = {}
    for e in termine:
        if e['wiederholung'] in RRULE and e['zeitzone'] not in zonen:
            zonen[e['zeitzone']] = datetime.fromtimestamp(e['start'], zone(e['zeitzone'])).year
    for name_, jahr in sorted(zonen.items()):
        zeilen += vtimezone(name_, jahr)
    for e in termine:
        zeilen += _vevent(**e)
    zeilen.append('END:VCALENDAR')
    return _ausgabe(zeilen)


def _iso_z(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def kalender_links(code, titel, beschreibung, link, start, dauer_min, zeitzone, wiederholung, bis, naechster, ics_url):
    """Vorlagen-Links zum Eintragen ohne OAuth. Google kann Wiederholungen (recur=RRULE, Serie ab dem ersten Termin);
    Outlook/Office 365 kennen in den Links keine Wiederholung → dort der nächste Termin, Hinweis im Text auf die .ics."""
    from urllib.parse import urlencode, quote
    dauer = (dauer_min or 60) * 60
    text = _beschreibung(beschreibung, link, code)
    wiederkehrend = wiederholung in RRULE
    g = {'action': 'TEMPLATE', 'text': titel, 'dates': f'{_utc(start)}/{_utc(start + dauer)}', 'details': text, 'location': link,
         'ctz': zeitzone or 'UTC'}
    if wiederkehrend:
        g['recur'] = f'RRULE:FREQ={RRULE[wiederholung]}' + (f';UNTIL={_utc(bis)}' if bis is not None else '')
    einzeln = naechster if naechster is not None else start
    o_text = text
    if wiederkehrend:
        s = sprache()
        wie = tx(s, 'termin.taeglich' if wiederholung == 'taeglich' else 'termin.woechentlich')
        o_text += '\n\n' + tx(s, 'ics.wiederholt', wie=wie, url=ics_url)
    o = {'path': '/calendar/action/compose', 'rru': 'addevent', 'subject': titel, 'startdt': _iso_z(einzeln),
         'enddt': _iso_z(einzeln + dauer), 'body': o_text, 'location': link}
    q = lambda d: urlencode(d, quote_via=quote)   # noqa: E731  (Leerzeichen als %20 – Outlook mag kein +)
    return {'google': 'https://calendar.google.com/calendar/render?' + q(g),
            'outlook': 'https://outlook.live.com/calendar/0/action/compose?' + q(o),
            'office365': 'https://outlook.office.com/calendar/0/action/compose?' + q(o),
            'ics': ics_url}
