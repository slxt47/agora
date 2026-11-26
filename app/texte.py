"""Sprachen (Deutsch, Englisch) im Backend – ADR 0006.

• Sprache je Anfrage: Der Browser schickt bei jedem API-Aufruf `Accept-Language` mit der Sprache der Oberfläche
  (web/src/common.js). Eine Middleware (main.py) legt sie in `SPRACHE` (ContextVar) ab; tiefe Funktionen (Termin-Details,
  .ics-Beschreibung, Kalender-Links) lesen sie mit `sprache()`.
• Fehlermeldungen schreibt der Code weiter deutsch (`fehler(400, '…')`). Der Fehler-Handler übersetzt sie beim Antworten
  mit `fehler_text()`: genaue Treffer aus FEHLER_EN, Texte mit Zahlen/Namen über MUSTER_EN. Fehlt eine Übersetzung, bleibt
  der deutsche Text (tests/test_sprachen.py prüft, dass alle Meldungen im Code übersetzt sind).
• Mails: `tx(sprache, schluessel, **werte)` aus MAIL. Die Sprache kommt vom Empfänger (Konto) bzw. bei Einladungen und
  Webinar-Anmeldungen von der Seite, auf der eingeladen/angemeldet wurde. Meldungen an Admins bleiben deutsch.
"""
import re
from contextlib import contextmanager
from contextvars import ContextVar

SPRACHEN = ('de', 'en')
SPRACHE: ContextVar[str] = ContextVar('agora_sprache', default='de')


def sprache():
    return SPRACHE.get()


def aus_header(wert):
    """Accept-Language → 'de' | 'en' (erste passende Angabe in der Reihenfolge des Headers; q-Werte absteigend)."""
    teile = []
    for i, roh in enumerate((wert or '').split(',')[:20]):
        tag, _, rest = roh.strip().partition(';')
        q = 1.0
        m = re.search(r'q=([0-9.]+)', rest)
        if m:
            try:
                q = float(m.group(1))
            except ValueError:
                q = 0.0
        teile.append((-q, i, tag.strip()[:2].lower()))
    for _, _, s in sorted(teile):
        if s in SPRACHEN:
            return s
    return 'de'


def gueltig(s, standard='de'):
    return s if s in SPRACHEN else standard


@contextmanager
def mit_sprache(s):
    """Für Hintergrund-Aufgaben (Erinnerungen, Kalender-Abo): Sprache vorübergehend setzen."""
    tok = SPRACHE.set(gueltig(s))
    try:
        yield
    finally:
        SPRACHE.reset(tok)


# ---------------------------------------------------------------- Fehlermeldungen
FEHLER_EN = {
    # ==== Block Push-Benachrichtigungen ====
    'Ungültige Push-Adresse.': 'Invalid push address.',
    'Dieser Push-Dienst ist nicht freigegeben.': 'This push service isn’t allowed.',
    'Ungültige Push-Schlüssel.': 'Invalid push keys.',
    'Höchstens 10 Geräte je Konto – entferne zuerst ein altes.': 'At most 10 devices per account – remove an old one first.',
    'Gerät nicht gefunden': 'Device not found',
    'Ereignisse: erinnerung, warteraum, einladung, fertig.': 'Events: erinnerung, warteraum, einladung, fertig.',
    'Auf keinem Gerät eingeschaltet.': 'Not turned on on any device.',
    # ==== Ende Block Push-Benachrichtigungen ====
    # ==== Block Öffentliche API, Webhooks, Einbetten ====
    'Ungültige Herkunft.': 'Invalid origin.',
    'Zum Einbetten mindestens eine Herkunft angeben (z. B. https://beispiel.de).': 'To embed, provide at least one origin (e.g. https://example.com).',
    'Dieses Meeting darf nicht eingebettet werden.': 'This meeting may not be embedded.',
    'Die Adresse ist zu lang (höchstens 500 Zeichen).': 'The address is too long (at most 500 characters).',
    'Ungültige Adresse.': 'Invalid address.',
    'Die Adresse muss mit https:// beginnen.': 'The address must start with https://.',
    'Ungültige Adresse (ohne Benutzername, Passwort und #Fragment).': 'Invalid address (no user name, password or #fragment).',
    'Der Name lässt sich nicht auflösen.': 'The host name can’t be resolved.',
    'Adressen im eigenen Netz (localhost, private IPs) sind nicht erlaubt.': 'Addresses in the local network (localhost, private IPs) aren’t allowed.',
    'Webhook nicht gefunden': 'Webhook not found',
    'aktiv: true oder false.': 'aktiv: true or false.',
    'art: sofort, geplant oder persoenlich.': 'art: sofort, geplant or persoenlich.',
    'Der Medienserver antwortet gerade nicht.': 'The media server isn’t responding right now.',
    'API-Schlüssel fehlt (Authorization: Bearer agora_…).': 'API key missing (Authorization: Bearer agora_…).',
    'Zu viele Anfragen mit ungültigem Schlüssel – bitte später wieder.': 'Too many requests with an invalid key – please try again later.',
    'API-Schlüssel ungültig, abgelaufen oder widerrufen.': 'API key invalid, expired or revoked.',
    'Gültigkeit: 30, 90 oder 365 Tage oder ohne Ablauf (null).': 'Validity: 30, 90 or 365 days or no expiry (null).',
    'API-Schlüssel nicht gefunden': 'API key not found',
    # ==== Ende Block ====
    # ==== Block Teams / Organisationen (app/orgs.py) ====
    'Diese Organisation gibt es nicht.': 'This organization doesn’t exist.',
    'Das darf nur ein Owner der Organisation.': 'Only an owner of the organization can do that.',
    'Dafür brauchst du Admin-Rechte in dieser Organisation.': 'You need admin rights in this organization for that.',
    'Bitte einen Namen für die Organisation eingeben (mindestens 2 Zeichen).': 'Please enter a name for the organization (at least 2 characters).',
    'Kurzname: 3 bis 40 Zeichen aus a–z, 0–9 und Bindestrich.': 'Short name: 3 to 40 characters from a–z, 0–9 and hyphen.',
    'Dieser Kurzname ist schon vergeben.': 'This short name is already taken.',
    'Ungültige Domain (z. B. firma.de).': 'Invalid domain (e.g. company.com).',
    'Domains öffentlicher Mail-Anbieter sind für den Selbst-Beitritt nicht erlaubt.': 'Domains of public email providers aren’t allowed for self-join.',
    'Die Domain muss zu deiner eigenen E-Mail-Adresse passen.': 'The domain must match your own email address.',
    'Zum Löschen bitte den Kurznamen der Organisation eingeben.': 'To delete, please enter the organization’s short name.',
    'Du bist der letzte Owner: Ernenne zuerst einen anderen Owner oder lösche die Organisation.':
        'You’re the last owner: make someone else an owner first or delete the organization.',
    'Rolle: owner, admin oder mitglied.': 'Role: owner, admin or mitglied.',
    'Rolle: admin oder mitglied.': 'Role: admin or mitglied.',
    'Mitglied nicht gefunden': 'Member not found',
    'Die Organisation braucht mindestens einen Owner.': 'The organization needs at least one owner.',
    'Zum Austreten bitte „Organisation verlassen“ benutzen.': 'To leave, please use “Leave organization”.',
    'Einladung nicht gefunden': 'Invitation not found',
    'Diese Einladung gilt für eine andere E-Mail-Adresse. Melde dich mit dem eingeladenen Konto an.':
        'This invitation is for a different email address. Sign in with the invited account.',
    'Diese Einladung gibt es nicht (mehr).': 'This invitation doesn’t exist (anymore).',
    'Ziel: org oder konto.': 'Target: org or konto.',
    'Der Speicher der Organisation für Aufnahmen ist voll. Ein Admin kann alte Aufnahmen löschen.':
        'The organization’s recording storage is full. An admin can delete old recordings.',
    'Schlüssel einer Organisation können keine Webhooks verwalten.': 'Organization keys can’t manage webhooks.',
    'Dieser API-Schlüssel gehört zu einer Organisation, in der du kein Admin mehr bist.':
        'This API key belongs to an organization in which you’re no longer an admin.',
    # ==== Ende Block Teams / Organisationen ====
    # ==== Block Status-Seite (app/status.py) ====
    'Bitte einen Beginn angeben.': 'Please enter a start.',
    'Art: stoerung, wartung oder hinweis.': 'Type: stoerung, wartung or hinweis.',
    'Ungültiger Zustand für diese Art.': 'Invalid state for this type.',
    'Bitte einen Titel eingeben.': 'Please enter a title.',
    'Dienste: app, medien, mail.': 'Services: app, medien, mail.',
    'Das Ende liegt vor dem Beginn.': 'The end is before the start.',
    # ==== Ende Block Status-Seite ====
    'Admin-Konten lassen sich hier nicht ändern (Liste AGORA_ADMIN_MAILS).': 'Admin accounts can’t be changed here (list AGORA_ADMIN_MAILS).',
    'Adresse und Schlüssel dürfen keine Leerzeichen enthalten.': 'URL and key must not contain spaces.',
    'Aktion: start oder stopp.': 'Action: start or stop.',
    'Aktion: zulassen oder ablehnen.': 'Action: admit or deny.',
    'Altes Passwort falsch.': 'Current password is wrong.',
    'Anfrage abgelehnt': 'Request denied',
    'Anfrage nicht gefunden': 'Request not found',
    'Anfrage zu groß': 'Request too large',
    'Antworten dürfen nicht leer sein.': 'Answers must not be empty.',
    'Auf dem Server wird schon aufgezeichnet.': 'The server is already recording.',
    'Aufnahme nicht gefunden': 'Recording not found',
    'Aufnahme und Livestream auf dem Server sind nicht eingerichtet.': 'Server recording and live streaming aren’t set up.',
    'Bei dieser Umfrage geht nur eine Antwort.': 'This poll allows only one answer.',
    'Bitte 1 bis 20 E-Mail-Adressen angeben.': 'Please enter 1 to 20 email addresses.',
    'Bitte 1 bis 5 Sterne vergeben.': 'Please give 1 to 5 stars.',
    'Bitte 2 bis 10 Antworten angeben.': 'Please enter 2 to 10 answers.',
    'Bitte 2 bis 50 Räume angeben.': 'Please choose 2 to 50 rooms.',
    'Bitte anmelden.': 'Please sign in.',
    'Bitte die Einrichtung neu beginnen.': 'Please start the setup again.',
    'Bitte eine Zuteilung angeben.': 'Please provide an assignment.',
    'Bitte eine gültige Auswahl treffen.': 'Please make a valid choice.',
    'Bitte eine gültige E-Mail-Adresse eingeben.': 'Please enter a valid email address.',
    'Bitte einen Grund auswählen.': 'Please choose a reason.',
    'Bitte einen Namen eingeben.': 'Please enter a name.',
    'Bitte einen Namen für die Vorlage eingeben.': 'Please enter a name for the template.',
    'Bitte im Meeting mit deinem Konto angemeldet sein.': 'Please be signed in to your account in the meeting.',
    'Bitte „aktiv“ (true/false) angeben.': 'Please specify “aktiv” (true/false).',
    'Bitte „an“ (true/false) angeben.': 'Please specify “an” (true/false).',
    'Bitte „erlaubt“ (true/false) angeben.': 'Please specify “erlaubt” (true/false).',
    'Bitte „speichern“ (true/false) angeben.': 'Please specify “speichern” (true/false).',
    'Chat: alle, panel oder aus.': 'Chat: alle, panel or aus.',
    'Das Ende der Wiederholung liegt vor dem Start.': 'The repeat end is before the start.',
    'Das Passwort ist zu lang (höchstens 200 Zeichen).': 'The password is too long (at most 200 characters).',
    'Das Passwort muss mindestens 10 Zeichen lang sein.': 'The password must be at least 10 characters long.',
    'Das Transkript wird schon erstellt.': 'The transcript is already being created.',
    'Das Webinar ist leider ausgebucht.': 'Sorry, the webinar is fully booked.',
    'Das Whiteboard ist gesperrt – nur Host und Co-Hosts zeichnen.': 'The whiteboard is locked – only host and co-hosts draw.',
    'Das Whiteboard ist voll (3 MB). Der Host kann es leeren.': 'The whiteboard is full (3 MB). The host can clear it.',
    'Das darfst du bei dieser Frage nicht.': 'You’re not allowed to do that with this question.',
    'Das darfst du bei dieser Person nicht.': 'You’re not allowed to do that to this person.',
    'Das geht nicht bei dir selbst.': 'You can’t do that to yourself.',
    'Das ist kein Webinar.': 'This isn’t a webinar.',
    'Datei nicht gefunden': 'File not found',
    'Dateien ohne Endung sind hier nicht erlaubt.': 'Files without an extension aren’t allowed here.',
    'Dateien teilen ist mit dem Chat ausgeschaltet.': 'File sharing is turned off along with the chat.',
    'Dateiname fehlt.': 'File name missing.',
    'Dauer: 5 bis 1440 Minuten.': 'Duration: 5 to 1440 minutes.',
    'Dein Speicher für Aufnahmen ist voll (5 GB). Lösche alte Aufnahmen.': 'Your recording storage is full (5 GB). Delete old recordings.',
    'Den Dauer-Chat gibt es nur im persönlichen Raum.': 'Persistent chat is only available in the personal room.',
    'Der Chat ist für Zuschauer ausgeschaltet.': 'Chat is turned off for attendees.',
    'Der Chat ist in diesem Meeting ausgeschaltet.': 'Chat is turned off in this meeting.',
    'Der Code stimmt nicht.': 'The code is wrong.',
    'Der Code stimmt nicht. Stimmt die Uhrzeit auf dem Handy?': 'The code is wrong. Is the time on your phone correct?',
    'Der Host hat die Rückkehr in den Hauptraum nicht erlaubt.': 'The host hasn’t allowed returning to the main room.',
    'Der Link ist ungültig oder abgelaufen – bitte neu anfordern.': 'The link is invalid or has expired – please request a new one.',
    'Der Medienserver antwortet gerade nicht – bitte gleich noch einmal.': 'The media server isn’t responding – please try again in a moment.',
    'Der Passcode stimmt nicht.': 'The passcode is wrong.',
    'Der Speicher für Aufnahmen des Besitzers ist voll (5 GB).': 'The owner’s recording storage is full (5 GB).',
    'Der Speicher für Dateien in diesem Meeting ist voll (200 MB).': 'File storage in this meeting is full (200 MB).',
    'Der Stream-Server ist nicht erreichbar (Name unbekannt).': 'The stream server can’t be reached (unknown host name).',
    'Der Termin liegt zu weit in der Vergangenheit oder Zukunft.': 'The date is too far in the past or future.',
    'Der persönliche Raum kann nicht gelöscht werden.': 'The personal room can’t be deleted.',
    'Diesen Raum gibt es nicht.': 'This room doesn’t exist.',
    'Die Anmeldung ist abgelaufen – bitte noch einmal mit Passwort anmelden.': 'The sign-in has expired – please sign in again with your password.',
    'Die Aufnahme ist größer als 500 MB.': 'The recording is larger than 500 MB.',
    'Die Breakout-Räume sind nicht offen.': 'The breakout rooms aren’t open.',
    'Die Breakout-Räume sind schon offen.': 'The breakout rooms are already open.',
    'Die Breakout-Räume werden gerade geschlossen.': 'The breakout rooms are closing.',
    'Die Datei ist größer als 25 MB.': 'The file is larger than 25 MB.',
    'Die Stream-Adresse muss mit rtmp:// oder rtmps:// beginnen.': 'The stream URL must start with rtmp:// or rtmps://.',
    'Die Umfrage ist nicht (mehr) offen.': 'The poll isn’t open (anymore).',
    'Die Umfrage läuft nicht.': 'The poll isn’t running.',
    'Die Umfrage wurde noch nicht gestartet.': 'The poll hasn’t been started yet.',
    'Die Umfrage wurde schon gestartet.': 'The poll has already been started.',
    'Die Verschlüsselung lässt sich nur ändern, solange niemand im Meeting ist.': 'Encryption can only be changed while no one is in the meeting.',
    'Die Virenprüfung ist gerade nicht möglich – bitte später nochmal versuchen.': 'Virus scanning isn’t possible right now – please try again later.',
    'Die Zwei-Faktor-Anmeldung ist nicht eingeschaltet.': 'Two-factor sign-in isn’t turned on.',
    'Die Zwei-Faktor-Anmeldung ist schon eingeschaltet.': 'Two-factor sign-in is already turned on.',
    'Diese Anmeldung gibt es nicht (mehr).': 'This registration doesn’t exist (anymore).',
    'Diese Frage wurde verworfen.': 'This question was dismissed.',
    'Diese Freigabe gibt es nicht (mehr).': 'This share doesn’t exist (anymore).',
    'Diese Person möchte keine privaten Nachrichten von dir.': 'This person doesn’t want private messages from you.',
    'Dieser Panel-Link ist nicht (mehr) gültig.': 'This panel link isn’t valid (anymore).',
    'Dieses Konto ist gesperrt. Bei Fragen wende dich an das Agora-Team.': 'This account is suspended. If you have questions, contact the Agora team.',
    'Dieses Meeting gibt es nicht.': 'This meeting doesn’t exist.',
    'Dieses Meeting läuft gerade nicht.': 'This meeting isn’t running right now.',
    'Dieses Objekt gibt es schon.': 'This object already exists.',
    'Dieses Webinar ist beendet.': 'This webinar has ended.',
    'Du bist gerade in keinem Breakout-Raum.': 'You’re not in a breakout room right now.',
    'Du bist nicht in diesem Meeting.': 'You’re not in this meeting.',
    'Du hast diese Person in diesem Meeting schon gemeldet.': 'You’ve already reported this person in this meeting.',
    'Du hast schon abgestimmt.': 'You’ve already voted.',
    'Du kannst diesem Meeting nicht beitreten.': 'You can’t join this meeting.',
    'Du wurdest aus diesem Meeting entfernt.': 'You were removed from this meeting.',
    'E-Mail oder Passwort falsch.': 'Wrong email or password.',
    'E-Mail-Adresse noch nicht bestätigt – bitte den Link in der Mail öffnen.': 'Email address not confirmed yet – please open the link in the email.',
    'Eintrag nicht gefunden': 'Entry not found',
    'Erinnerung: 10, 15, 30 oder 60 Minuten.': 'Reminder: 10, 15, 30 or 60 minutes.',
    'Erinnerung: 10, 15, 30 oder 60 Minuten (0 = aus).': 'Reminder: 10, 15, 30 or 60 minutes (0 = off).',
    'Es gibt keine Breakout-Räume.': 'There are no breakout rooms.',
    'Es läuft kein Livestream.': 'No live stream is running.',
    'Es läuft keine Aufnahme auf dem Server.': 'No server recording is running.',
    'Es läuft schon ein Livestream.': 'A live stream is already running.',
    'Es läuft schon eine Aufnahme im Browser.': 'A browser recording is already running.',
    'Falscher Passcode': 'Wrong passcode',
    'Falsches Passwort': 'Wrong password',
    'Farbe bitte als #rrggbb.': 'Please give the color as #rrggbb.',
    'Fehler': 'Error',
    'Format: txt oder vtt.': 'Format: txt or vtt.',
    'Frage nicht gefunden': 'Question not found',
    'Für die Ende-zu-Ende-Verschlüsselung fehlt der Prüfwert des Schlüssels.': 'The key check value for end-to-end encryption is missing.',
    'Für die eigene Frage kannst du nicht stimmen.': 'You can’t vote for your own question.',
    'Für dieses Meeting gibt es kein Transkript.': 'There’s no transcript for this meeting.',
    'Für dieses Meeting gibt es keinen Termin.': 'This meeting has no date.',
    'Für dieses Meeting wurde nichts gespeichert.': 'Nothing was saved for this meeting.',
    'Für dieses Webinar gibt es keine Anmeldung.': 'There’s no registration for this webinar.',
    'Für dieses Webinar ist eine Anmeldung nötig. Nutze den Link aus deiner Bestätigungsmail.': 'This webinar requires registration. Use the link from your confirmation email.',
    'Gäste ohne Konto kann man nicht blockieren – melde sie oder bitte den Host, sie zu entfernen.': 'Guests without an account can’t be blocked – report them or ask the host to remove them.',
    'Gültigkeit: 1, 7 oder 30 Tage.': 'Validity: 1, 7 or 30 days.',
    'In Ende-zu-Ende-verschlüsselten Meetings geht das nur im Browser – der Server kann Bild und Ton nicht lesen.': 'In end-to-end encrypted meetings this only works in the browser – the server can’t read video and audio.',
    'In diesem Webinar sind Fragen ausgeschaltet.': 'Questions are turned off in this webinar.',
    'In diesen Breakout-Raum darfst du nicht.': 'You’re not allowed in this breakout room.',
    'Inzwischen hat jemand anderes die Notizen geändert.': 'Someone else has changed the notes in the meantime.',
    'Jede Antwort darf nur einmal vorkommen.': 'Each answer may appear only once.',
    'Kapazität: 1 bis 100000 oder leer.': 'Capacity: 1 to 100000 or empty.',
    'Kein Transkript vorhanden.': 'No transcript available.',
    'Konto nicht gefunden': 'Account not found',
    'Layout: galerie oder sprecher.': 'Layout: galerie or sprecher.',
    'Leere Datei': 'Empty file',
    'Leere Nachricht': 'Empty message',
    'Leerer Text': 'Empty text',
    'Meeting ist beendet': 'Meeting has ended',
    'Meeting ist gesperrt': 'Meeting is locked',
    'Meeting ist voll': 'Meeting is full',
    'Meldung nicht gefunden': 'Report not found',
    'Methode nicht erlaubt': 'Method not allowed',
    'Nicht gefunden': 'Not found',
    'Nur Host oder Co-Host dürfen das.': 'Only the host or a co-host can do that.',
    'Nur Host, Co-Host oder wer gerade den Bildschirm teilt, darf das.': 'Only the host, a co-host or the person sharing the screen can do that.',
    'Nur Panelisten und Zuschauer lassen sich so verschieben.': 'Only panelists and attendees can be moved like this.',
    'Nur WebM- oder MP4-Videos.': 'Only WebM or MP4 videos.',
    'Nur der Besitzer darf das.': 'Only the owner can do that.',
    'Nur der Host darf das.': 'Only the host can do that.',
    'Nur geplante Meetings haben einen Termin.': 'Only scheduled meetings have a date.',
    'Nur wer die Datei geteilt hat oder die Moderation darf sie löschen.': 'Only the person who shared the file or the moderators can delete it.',
    'Passcode: 4 bis 32 Zeichen.': 'Passcode: 4 to 32 characters.',
    'Passwort falsch.': 'Wrong password.',
    'Passwort: 4 bis 100 Zeichen.': 'Password: 4 to 100 characters.',
    'Rechte sind true oder false.': 'Permissions are true or false.',
    'Rolle: cohost oder teilnehmer.': 'Role: cohost or teilnehmer.',
    'Rolle: panelist oder zuschauer.': 'Role: panelist or zuschauer.',
    'Rückmeldung nicht möglich.': 'Feedback isn’t possible.',
    'Sprache: de oder en.': 'Language: de or en.',
    'Status: offen oder erledigt.': 'Status: offen or erledigt.',
    'Stream-Ziele im eigenen Netz sind nicht erlaubt.': 'Stream targets in the local network aren’t allowed.',
    'Teilnehmer nicht gefunden': 'Participant not found',
    'Timer: 1 bis 240 Minuten.': 'Timer: 1 to 240 minutes.',
    'Transkripte aus Aufnahmen sind nicht eingerichtet.': 'Transcripts from recordings aren’t set up.',
    'Umbenennen ist in diesem Meeting ausgeschaltet.': 'Renaming is turned off in this meeting.',
    'Umfrage nicht gefunden': 'Poll not found',
    'Unbekannte Aktion': 'Unknown action',
    'Unbekannte Zeitzone.': 'Unknown time zone.',
    'Unbekanntes Problem.': 'Unknown problem.',
    'Ungültige Anfrage': 'Invalid request',
    'Ungültige Angaben zum Termin.': 'Invalid date details.',
    'Ungültige Antworten.': 'Invalid answers.',
    'Ungültige Einstellungen.': 'Invalid settings.',
    'Ungültige Farbe': 'Invalid color',
    'Ungültige Operation': 'Invalid operation',
    'Ungültige Statistik.': 'Invalid statistics.',
    'Ungültige Stream-Adresse.': 'Invalid stream URL.',
    'Ungültiger Raum-Code': 'Invalid room code',
    'Ungültiger Strich': 'Invalid stroke',
    'Ungültiges Whiteboard-Objekt': 'Invalid whiteboard object',
    'Vorlage nicht gefunden': 'Template not found',
    'Wiederholung: keine, taeglich oder woechentlich.': 'Repeat: keine, taeglich or woechentlich.',
    'Während das Meeting läuft, lässt sich der Webinar-Modus nicht umschalten.': 'Webinar mode can’t be switched while the meeting is running.',
    'Zu viele Versuche – bitte kurz warten.': 'Too many attempts – please wait a moment.',
    'Zu viele falsche Codes – bitte noch einmal mit Passwort anmelden.': 'Too many wrong codes – please sign in again with your password.',
    'Zuschauer schreiben in diesem Webinar nur privat an Host und Panel.': 'In this webinar, attendees only write privately to host and panel.',
    'Zuschauer senden keinen Ton – also auch keine Untertitel.': 'Attendees don’t send audio – so no captions either.',
    'rtt, verlust und jitter gehören zusammen.': 'rtt, verlust and jitter belong together.',
}

# Meldungen mit Werten (f-Strings im Code). Die Gruppen werden übernommen; „unbekannt“ wird übersetzt.
MUSTER_EN = [
    # ==== Block Öffentliche API, Webhooks, Einbetten ====
    (r'Ungültige Herkunft: (.*)', 'Invalid origin: {0}'),
    (r'Nur die Herkunft angeben, z\. B\. https://beispiel\.de \(ohne Pfad, ohne \*\): (.*)', 'Only give the origin, e.g. https://example.com (no path, no *): {0}'),
    (r'Herkünfte müssen mit https:// beginnen \(http:// nur für localhost\): (.*)', 'Origins must start with https:// (http:// only for localhost): {0}'),
    (r'Höchstens (\d+) Herkünfte\.', 'At most {0} origins.'),
    (r'Höchstens (\d+) Webhooks je Konto\.', 'At most {0} webhooks per account.'),
    (r'Unbekanntes Feld: (.*)', 'Unknown field: {0}'),
    (r'Zu viele Anfragen – höchstens (\d+) pro Minute je Schlüssel\.', 'Too many requests – at most {0} per minute per key.'),
    (r'Diesem API-Schlüssel fehlt das Recht „(.*)“\.', 'This API key lacks the permission “{0}”.'),
    (r'Höchstens (\d+) API-Schlüssel je Konto – widerrufe zuerst einen alten\.', 'At most {0} API keys per account – revoke an old one first.'),
    (r'Bitte mindestens ein gültiges Recht wählen: (.*)', 'Please choose at least one valid permission: {0}'),
    (r'Bitte mindestens ein gültiges Ereignis wählen: (.*)', 'Please choose at least one valid event: {0}'),
    # ==== Ende Block ====
    (r'Höchstens (\d+) eigene Organisationen\.', 'At most {0} own organizations.'),
    (r'Höchstens (\d+) Mitglieder je Organisation\.', 'At most {0} members per organization.'),
    (r'Raum-Nummer: 0 bis (\d+)\.', 'Room number: 0 to {0}.'),
    (r'Dateien vom Typ „\.(.*)“ sind hier nicht erlaubt\.', 'Files of type “.{0}” aren’t allowed here.'),
    (r'Die Datei wurde abgelehnt: Virenprüfung meldet „(.*)“\.', 'The file was rejected: the virus scan reports “{0}”.'),
    (r'Die Aufnahme auf dem Server ließ sich nicht starten: (.*)', 'The server recording couldn’t start: {0}'),
    (r'Der Livestream ließ sich nicht starten: (.*)', 'The live stream couldn’t start: {0}'),
    (r'Die Aufnahme ließ sich nicht stoppen: (.*)', 'The recording couldn’t be stopped: {0}'),
    (r'Der Livestream ließ sich nicht stoppen: (.*)', 'The live stream couldn’t be stopped: {0}'),
    (r'Höchstens (\d+) Fragen je Meeting\.', 'At most {0} questions per meeting.'),
    (r'Höchstens (\d+) Umfragen je Meeting\.', 'At most {0} polls per meeting.'),
    (r'Höchstens (\d+) Vorlagen\.', 'At most {0} templates.'),
    (r'Höchstens (\d+) Panel-Links\.', 'At most {0} panel links.'),
    (r'Höchstens (\d+) Zusatzfragen\.', 'At most {0} extra questions.'),
    (r'Text zu lang \(höchstens (\d+) Zeichen\)\.', 'Text too long (at most {0} characters).'),
    (r'Die Notizen sind zu lang \(höchstens (\d+) Zeichen\)\.', 'The notes are too long (at most {0} characters).'),
    (r'Ungültiger Wert: (.*)', 'Invalid value: {0}'),
    (r'Ungültige Zeitangabe \((.*)\)\.', 'Invalid time ({0}).'),
    (r'Ungültige E-Mail-Adresse: (.*)', 'Invalid email address: {0}'),
    (r'(\d+) bis (\d+) Operationen je Anfrage\.', '{0} to {1} operations per request.'),
    (r'Das Whiteboard ist voll \(höchstens (\d+) Objekte\)\. Der Host kann es leeren\.',
     'The whiteboard is full (at most {0} objects). The host can clear it.'),
    (r'Aktion: (.*)\.', 'Action: {0}.'),
]
_MUSTER = [(re.compile(m, re.S), z) for m, z in MUSTER_EN]
_WERT_EN = {'unbekannt': 'unknown'}


def fehler_text(text, s=None):
    """Deutsche Fehlermeldung in die Sprache s (Standard: Sprache der Anfrage)."""
    s = s or sprache()
    if s == 'de' or not isinstance(text, str):
        return text
    if text in FEHLER_EN:
        return FEHLER_EN[text]
    for m, ziel in _MUSTER:
        treffer = m.fullmatch(text)
        if treffer:
            return ziel.format(*(_WERT_EN.get(g, g) for g in treffer.groups()))
    return text


# ---------------------------------------------------------------- Mails und Termin-Texte
MAIL = {
    'de': {
        'hallo': 'Hallo {name},',
        'ignorieren': 'Wenn du das nicht warst, kannst du diese Mail ignorieren. Wir fragen dich nie nach deinem Passwort.',
        'fuss': 'Videokonferenzen · ohne Werbung, ohne Tracking', 'impressum': 'Impressum', 'datenschutz': 'Datenschutz',
        'automatisch': 'Diese Nachricht wurde automatisch verschickt.',
        'bestaetigen.satz': 'Bitte bestätige deine E-Mail-Adresse. Danach kannst du Meetings starten, planen und deinen persönlichen Raum nutzen.',
        'bestaetigen.gilt': 'Der Link gilt 48 Stunden.', 'bestaetigen.betreff': 'Agora: E-Mail-Adresse bestätigen',
        'bestaetigen.titel': 'Willkommen bei Agora', 'bestaetigen.knopf': 'E-Mail-Adresse bestätigen',
        'bestaetigen.vorschau': 'Bitte bestätige deine E-Mail-Adresse',
        'schon.satz': 'Jemand wollte mit dieser Adresse ein neues Konto anlegen – du hast aber schon eins. Falls du dein Passwort vergessen hast:',
        'schon.betreff': 'Agora: Du hast schon ein Konto', 'schon.titel': 'Du hast schon ein Konto', 'schon.knopf': 'Passwort zurücksetzen',
        'schon.vorschau': 'Du hast schon ein Konto bei Agora',
        'passwort.satz': 'Mit diesem Link setzt du ein neues Passwort. Danach wirst du überall abgemeldet.',
        'passwort.gilt': 'Der Link gilt 1 Stunde.', 'passwort.betreff': 'Agora: Passwort zurücksetzen', 'passwort.titel': 'Passwort zurücksetzen',
        'passwort.knopf': 'Neues Passwort setzen', 'passwort.vorschau': 'Neues Passwort setzen',
        'kal.google': 'Zu Google Kalender', 'kal.outlook': 'Zu Outlook.com', 'kal.office365': 'Zu Outlook/Office 365',
        'einladung.satz': '{von} lädt dich zu einem Meeting ein: „{titel}“.', 'einladung.passcode': 'Passcode',
        'einladung.beitreten': 'Beitreten', 'einladung.ics': 'Den Termin kannst du mit dem Anhang in deinen Kalender übernehmen.',
        'einladung.betreff': 'Einladung: {titel}', 'einladung.titel': 'Einladung zum Meeting', 'einladung.knopf': 'Meeting beitreten',
        'einladung.vorschau': '{von} lädt dich ein', 'einladung.datei': 'einladung.ics',
        'erinnerung.hostSatz': 'dein Meeting „{titel}“ beginnt in {n} Minuten.', 'erinnerung.hostKnopf': 'Meeting starten',
        'erinnerung.hostHinweis': 'Du bekommst diese Erinnerung, weil du sie in deinem Agora-Profil eingeschaltet hast.',
        'erinnerung.gastSatz': '„{titel}“ beginnt in {n} Minuten.', 'erinnerung.gastSatzVon': '„{titel}“ von {von} beginnt in {n} Minuten.',
        'erinnerung.gastKnopf': 'Meeting beitreten',
        'erinnerung.gastHinweis': 'Du bekommst diese einmalige Erinnerung, weil du zu diesem Meeting eingeladen wurdest. Den Passcode (falls nötig) '
                                  'findest du in der Einladung. Deine Adresse wird nach dem Termin gelöscht.',
        'erinnerung.wann': 'Wann', 'erinnerung.link': 'Link', 'erinnerung.betreff': 'Erinnerung: {titel} beginnt in {n} Minuten',
        'erinnerung.titel': 'Gleich geht’s los', 'erinnerung.vorschau': '„{titel}“ beginnt in {n} Minuten',
        'webinar.satz': 'du bist für das Webinar „{titel}“ angemeldet. Mit deinem persönlichen Link kommst du hinein – bitte gib ihn nicht weiter.',
        'webinar.hinweis': 'Abmelden (deine Angaben werden sofort gelöscht): {link} · Sonst löschen wir deine Anmeldung 30 Tage nach dem Webinar.',
        'webinar.teilnehmen': 'Teilnehmen', 'webinar.betreff': 'Anmeldung bestätigt: {titel}', 'webinar.titel': 'Du bist angemeldet',
        'webinar.knopf': 'Zum Webinar', 'webinar.vorschau': 'Dein Link für „{titel}“',
        # Termin-Details (Einladung, Webinar-Anmeldung), Erinnerung, .ics
        'termin.wann': 'Wann', 'termin.wannWert': '{datum}, {uhr} Uhr ({zone}), {dauer} min', 'termin.wiederholung': 'Wiederholung',
        'termin.taeglich': 'täglich', 'termin.woechentlich': 'wöchentlich', 'termin.code': 'Meeting-Code', 'termin.beschreibung': 'Beschreibung',
        'termin.erinnerungWann': '{tag} {datum}, {uhr} Uhr ({zone})', 'termin.tage': 'Mo.,Di.,Mi.,Do.,Fr.,Sa.,So.',
        'ics.beitreten': 'Beitreten: {link}', 'ics.code': 'Meeting-Code: {code}', 'ics.alarm': 'Meeting beginnt',
        'ics.wiederholt': 'Wiederholt sich {wie} – alle Termine auf einmal: {url}', 'ics.abo': 'Meetings aus Agora',
        # Standard-Titel neuer Räume (gespeichert in der Sprache, in der sie angelegt werden)
        'titel.persoenlich': 'Persönlicher Raum von {name}', 'titel.sofort': 'Meeting von {name}', 'titel.geplant': 'Geplantes Meeting',
        # Organisationen (orgs.py)
        'org.satz': '{von} lädt dich in die Organisation „{org}“ bei Agora ein.',
        'org.satz2': 'Als Mitglied siehst du die Meetings der Organisation, nutzt gemeinsame Vorlagen und die Aufnahmen-Bibliothek.',
        'org.hinweis': 'Melde dich mit dieser E-Mail-Adresse an (oder registriere dich damit) und nimm die Einladung an. Der Link gilt 7 Tage. '
                       'Wenn du das nicht erwartet hast, ignoriere diese Mail einfach.',
        'org.betreff': 'Einladung in „{org}“', 'org.titel': 'Einladung in eine Organisation', 'org.knopf': 'Einladung ansehen',
        'org.vorschau': '{von} lädt dich in „{org}“ ein',
        # ==== Block Push-Benachrichtigungen (app/push.py) ====
        'push.erinnerung.titel': '{titel} beginnt bald', 'push.erinnerung.text': '{wann} – in {n} Minuten. Tippen zum Beitreten.',
        'push.warteraum.titel': 'Jemand wartet: {titel}', 'push.warteraum.text': '{name} wartet im Warteraum deines Meetings.',
        'push.einladung.titel': 'Einladung: {titel}', 'push.einladung.text': '{name} hat dich eingeladen.',
        'push.aufnahme.titel': 'Aufnahme fertig', 'push.aufnahme.text': '„{titel}“ liegt jetzt in deiner Bibliothek.',
        'push.transkript.titel': 'Transkript fertig', 'push.transkript.text': 'Das Transkript zu „{titel}“ ist fertig.',
        'push.test.titel': 'Agora', 'push.test.text': 'So sehen Benachrichtigungen von Agora aus.',
        # ==== Ende Block Push-Benachrichtigungen ====
    },
    'en': {
        'hallo': 'Hi {name},',
        'ignorieren': 'If this wasn’t you, you can ignore this email. We’ll never ask for your password.',
        'fuss': 'Video meetings · no ads, no tracking', 'impressum': 'Legal notice (German)', 'datenschutz': 'Privacy (German)',
        'automatisch': 'This message was sent automatically.',
        'bestaetigen.satz': 'Please confirm your email address. Then you can start and schedule meetings and use your personal room.',
        'bestaetigen.gilt': 'The link is valid for 48 hours.', 'bestaetigen.betreff': 'Agora: confirm your email address',
        'bestaetigen.titel': 'Welcome to Agora', 'bestaetigen.knopf': 'Confirm email address',
        'bestaetigen.vorschau': 'Please confirm your email address',
        'schon.satz': 'Someone tried to create a new account with this address – but you already have one. In case you forgot your password:',
        'schon.betreff': 'Agora: you already have an account', 'schon.titel': 'You already have an account', 'schon.knopf': 'Reset password',
        'schon.vorschau': 'You already have an Agora account',
        'passwort.satz': 'Use this link to set a new password. You’ll then be signed out everywhere.',
        'passwort.gilt': 'The link is valid for 1 hour.', 'passwort.betreff': 'Agora: reset your password', 'passwort.titel': 'Reset password',
        'passwort.knopf': 'Set new password', 'passwort.vorschau': 'Set a new password',
        'kal.google': 'Add to Google Calendar', 'kal.outlook': 'Add to Outlook.com', 'kal.office365': 'Add to Outlook/Office 365',
        'einladung.satz': '{von} invites you to a meeting: “{titel}”.', 'einladung.passcode': 'Passcode',
        'einladung.beitreten': 'Join', 'einladung.ics': 'Use the attachment to add the meeting to your calendar.',
        'einladung.betreff': 'Invitation: {titel}', 'einladung.titel': 'Meeting invitation', 'einladung.knopf': 'Join meeting',
        'einladung.vorschau': '{von} invites you', 'einladung.datei': 'invitation.ics',
        'erinnerung.hostSatz': 'your meeting “{titel}” starts in {n} minutes.', 'erinnerung.hostKnopf': 'Start meeting',
        'erinnerung.hostHinweis': 'You’re getting this reminder because you turned it on in your Agora profile.',
        'erinnerung.gastSatz': '“{titel}” starts in {n} minutes.', 'erinnerung.gastSatzVon': '“{titel}” by {von} starts in {n} minutes.',
        'erinnerung.gastKnopf': 'Join meeting',
        'erinnerung.gastHinweis': 'You’re getting this one-time reminder because you were invited to this meeting. You’ll find the passcode '
                                  '(if needed) in the invitation. Your address will be deleted after the meeting.',
        'erinnerung.wann': 'When', 'erinnerung.link': 'Link', 'erinnerung.betreff': 'Reminder: {titel} starts in {n} minutes',
        'erinnerung.titel': 'Starting soon', 'erinnerung.vorschau': '“{titel}” starts in {n} minutes',
        'webinar.satz': 'you’re registered for the webinar “{titel}”. Your personal link gets you in – please don’t share it.',
        'webinar.hinweis': 'Cancel your registration (your details are deleted immediately): {link} · Otherwise we delete your registration 30 days after the webinar.',
        'webinar.teilnehmen': 'Join', 'webinar.betreff': 'Registration confirmed: {titel}', 'webinar.titel': 'You’re registered',
        'webinar.knopf': 'Go to webinar', 'webinar.vorschau': 'Your link for “{titel}”',
        'termin.wann': 'When', 'termin.wannWert': '{datum}, {uhr} ({zone}), {dauer} min', 'termin.wiederholung': 'Repeats',
        'termin.taeglich': 'daily', 'termin.woechentlich': 'weekly', 'termin.code': 'Meeting code', 'termin.beschreibung': 'Description',
        'termin.erinnerungWann': '{tag} {datum}, {uhr} ({zone})', 'termin.tage': 'Mon,Tue,Wed,Thu,Fri,Sat,Sun',
        'ics.beitreten': 'Join: {link}', 'ics.code': 'Meeting code: {code}', 'ics.alarm': 'Meeting starts',
        'ics.wiederholt': 'Repeats {wie} – all dates at once: {url}', 'ics.abo': 'Meetings from Agora',
        'titel.persoenlich': '{name}’s personal room', 'titel.sofort': '{name}’s meeting', 'titel.geplant': 'Scheduled meeting',
        'org.satz': '{von} invites you to the organization “{org}” on Agora.',
        'org.satz2': 'As a member you see the organization’s meetings and use shared templates and the recordings library.',
        'org.hinweis': 'Sign in with this email address (or sign up with it) and accept the invitation. The link is valid for 7 days. '
                       'If you didn’t expect this, just ignore this email.',
        'org.betreff': 'Invitation to “{org}”', 'org.titel': 'Invitation to an organization', 'org.knopf': 'View invitation',
        'org.vorschau': '{von} invites you to “{org}”',
        # ==== Block Push-Benachrichtigungen (app/push.py) ====
        'push.erinnerung.titel': '{titel} starts soon', 'push.erinnerung.text': '{wann} – in {n} minutes. Tap to join.',
        'push.warteraum.titel': 'Someone is waiting: {titel}', 'push.warteraum.text': '{name} is waiting in your meeting’s waiting room.',
        'push.einladung.titel': 'Invitation: {titel}', 'push.einladung.text': '{name} invited you.',
        'push.aufnahme.titel': 'Recording ready', 'push.aufnahme.text': '“{titel}” is now in your library.',
        'push.transkript.titel': 'Transcript ready', 'push.transkript.text': 'The transcript for “{titel}” is ready.',
        'push.test.titel': 'Agora', 'push.test.text': 'This is what notifications from Agora look like.',
        # ==== Ende Block Push-Benachrichtigungen ====
    },
}

MONATE_EN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def tx(s, schluessel, **werte):
    s = gueltig(s)
    text = MAIL[s].get(schluessel) or MAIL['de'][schluessel]
    return text.format(**werte) if werte else text


def datum(lokal, s):
    """datetime → „24.11.2026“ bzw. „24 Nov 2026“."""
    if gueltig(s) == 'en':
        return f'{lokal.day} {MONATE_EN[lokal.month - 1]} {lokal.year}'
    return lokal.strftime('%d.%m.%Y')
