"""Termine: nächstes Vorkommen wiederkehrender Meetings und iCalendar-Dateien (RFC 5545).

Wiederholungen laufen in der Ortszeit der Zeitzone des Meetings (09:00 bleibt über die Zeitumstellung 09:00).
Die .ics enthält dafür eine VTIMEZONE, die aus den Umstellungen der IANA-Zeitzone abgeleitet wird.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


def kalender(code, titel, beschreibung, link, start, dauer_min, zeitzone, wiederholung, bis, erstellt, veranstalter='Agora', sequenz=0):
    """Vollständige .ics (METHOD:PUBLISH) für ein geplantes Meeting."""
    tz = zone(zeitzone)
    ende = start + (dauer_min or 60) * 60
    text = (beschreibung + '\n\n' if beschreibung else '') + f'Beitreten: {link}\nMeeting-Code: {code}'
    zeilen = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//slxt//Agora//DE', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH']
    wiederkehrend = wiederholung in RRULE
    if wiederkehrend:
        zeilen += vtimezone(zeitzone, datetime.fromtimestamp(start, tz).year)
    zeilen += ['BEGIN:VEVENT', f'UID:{code}@agora.slxt.dev', f'DTSTAMP:{_utc(erstellt)}', f'SEQUENCE:{sequenz}']
    if wiederkehrend:
        zeilen += [f'DTSTART;TZID={zeitzone}:{_lokal(start, tz)}', f'DTEND;TZID={zeitzone}:{_lokal(ende, tz)}']
        regel = f'RRULE:FREQ={RRULE[wiederholung]}'
        if bis is not None:
            regel += f';UNTIL={_utc(bis)}'
        zeilen.append(regel)
    else:
        zeilen += [f'DTSTART:{_utc(start)}', f'DTEND:{_utc(ende)}']
    zeilen += [f'SUMMARY:{_esc(titel)}', f'DESCRIPTION:{_esc(text)}', f'LOCATION:{_esc(link)}', f'URL:{link}',
               'ORGANIZER;CN="' + veranstalter.replace('"', "'")[:60] + '":mailto:noreply@slxt.dev', 'STATUS:CONFIRMED', 'TRANSP:OPAQUE',
               'BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:Meeting beginnt', 'TRIGGER:-PT10M', 'END:VALARM',
               'END:VEVENT', 'END:VCALENDAR']
    return '\r\n'.join(_falten(z) for z in zeilen) + '\r\n'
