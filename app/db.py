"""SQLite-Datenbank für Agora. WAL-Modus, eine Verbindung pro Thread, Schema per Version."""
import os
import sqlite3
import threading
import time

DATEN = os.environ.get('AGORA_DATEN', '/daten')
DB = os.path.join(DATEN, 'agora.sqlite3')
_lokal = threading.local()

SCHEMA = [
    # Version 1
    """
    CREATE TABLE benutzer (
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      email TEXT NOT NULL UNIQUE COLLATE NOCASE,
      passwort_hash TEXT NOT NULL,     -- argon2
      bestaetigt INTEGER NOT NULL DEFAULT 0,
      zeitzone TEXT NOT NULL DEFAULT 'Europe/Berlin',
      sprache TEXT NOT NULL DEFAULT 'de',
      farbe TEXT NOT NULL,             -- Avatar-Farbe #rrggbb
      erstellt INTEGER NOT NULL,
      letzte_anmeldung INTEGER
    );
    CREATE TABLE token (                -- Links aus Mails (Bestätigung, Passwort), nur als Hash gespeichert
      token_hash TEXT PRIMARY KEY,
      benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
      art TEXT NOT NULL,               -- bestaetigen | passwort
      gueltig_bis INTEGER NOT NULL
    );
    CREATE TABLE sitzungen (
      token_hash TEXT PRIMARY KEY,
      benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
      erstellt INTEGER NOT NULL,
      gueltig_bis INTEGER NOT NULL
    );
    CREATE TABLE raeume (
      id INTEGER PRIMARY KEY,
      code TEXT NOT NULL UNIQUE,       -- abc-defg-hij
      besitzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
      art TEXT NOT NULL,               -- sofort | geplant | persoenlich
      titel TEXT NOT NULL,
      passcode_hash TEXT,              -- argon2 oder NULL
      warteraum INTEGER NOT NULL DEFAULT 0,
      chat_speichern INTEGER NOT NULL DEFAULT 0,
      start INTEGER,                   -- geplant: Unix-Zeit (UTC)
      dauer_min INTEGER,
      zeitzone TEXT,
      beschreibung TEXT,
      wiederholung TEXT NOT NULL DEFAULT 'keine',
      bis INTEGER,
      rechte TEXT NOT NULL,            -- JSON
      gesperrt INTEGER NOT NULL DEFAULT 0,
      aktiv INTEGER NOT NULL DEFAULT 0,
      sitzung INTEGER NOT NULL DEFAULT 0,   -- laufende Nummer des Meetings in diesem Raum
      gestartet INTEGER,
      leer_seit INTEGER,
      beendet INTEGER NOT NULL DEFAULT 0,   -- Sofort-Meeting endgültig beendet
      aufnahme_von TEXT,               -- Name, wenn gerade aufgezeichnet wird
      erstellt INTEGER NOT NULL,
      letzte_aktivitaet INTEGER NOT NULL
    );
    CREATE INDEX raeume_besitzer ON raeume(besitzer_id);
    CREATE TABLE teilnahmen (
      geheim_hash TEXT PRIMARY KEY,    -- Geheimnis für X-Teilnahme
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      identity TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL,
      rolle TEXT NOT NULL,             -- host | cohost | teilnehmer | gast
      benutzer_id INTEGER,
      ip TEXT NOT NULL,
      farbe TEXT NOT NULL,
      erstellt INTEGER NOT NULL
    );
    CREATE INDEX teilnahmen_raum ON teilnahmen(raum_id, sitzung);
    CREATE TABLE anfragen (            -- Warteraum
      id TEXT PRIMARY KEY,
      geheim_hash TEXT NOT NULL,
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      name TEXT NOT NULL,
      benutzer_id INTEGER,
      ip TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'wartet',   -- wartet | zugelassen | abgelehnt | beendet
      erstellt INTEGER NOT NULL
    );
    CREATE TABLE sperren (             -- entfernte Teilnehmer, je Meeting
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      art TEXT NOT NULL,               -- identity | benutzer | ip
      wert TEXT NOT NULL
    );
    CREATE TABLE nachrichten (
      id INTEGER PRIMARY KEY,
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      von TEXT NOT NULL, von_name TEXT NOT NULL, von_benutzer INTEGER,
      an TEXT, an_name TEXT, an_benutzer INTEGER,
      text TEXT NOT NULL,
      zeit INTEGER NOT NULL
    );
    CREATE INDEX nachrichten_raum ON nachrichten(raum_id, sitzung);
    CREATE TABLE aufnahmen (
      id TEXT PRIMARY KEY,
      benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
      titel TEXT NOT NULL,
      raum_code TEXT,
      groesse INTEGER NOT NULL,
      typ TEXT NOT NULL,
      dauer INTEGER,                   -- Sekunden (vom Browser gemeldet) oder NULL
      erstellt INTEGER NOT NULL,
      ablauf INTEGER NOT NULL,
      freigabe_token TEXT UNIQUE,
      freigabe_bis INTEGER,
      freigabe_passwort TEXT           -- argon2 oder NULL
    );
    CREATE INDEX aufnahmen_benutzer ON aufnahmen(benutzer_id);
    CREATE TABLE protokoll (            -- Host-Aktionen und Anmeldungen ohne Inhalte, 30 Tage
      zeit INTEGER NOT NULL, benutzer_id INTEGER, raum_code TEXT, ereignis TEXT NOT NULL
    );
    """,
    # Version 2: Umfragen (je Meeting; mit dem Meeting gelöscht, außer der Raum hat chat_speichern)
    """
    CREATE TABLE umfragen (
      id INTEGER PRIMARY KEY,
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      frage TEXT NOT NULL,
      optionen TEXT NOT NULL,          -- JSON-Liste der Antworttexte (2–10)
      mehrfach INTEGER NOT NULL DEFAULT 0,
      anonym INTEGER NOT NULL DEFAULT 1,
      status TEXT NOT NULL DEFAULT 'entwurf',   -- entwurf | laeuft | beendet
      geteilt INTEGER NOT NULL DEFAULT 0,       -- Ergebnis für alle sichtbar
      von TEXT NOT NULL, von_name TEXT NOT NULL,
      erstellt INTEGER NOT NULL, gestartet INTEGER, beendet INTEGER
    );
    CREATE INDEX umfragen_raum ON umfragen(raum_id, sitzung);
    CREATE TABLE stimmen (             -- genau eine Stimme je Teilnahme (identity)
      umfrage_id INTEGER NOT NULL REFERENCES umfragen(id) ON DELETE CASCADE,
      identity TEXT NOT NULL,
      name TEXT NOT NULL,
      auswahl TEXT NOT NULL,           -- JSON-Liste der gewählten Indizes
      zeit INTEGER NOT NULL,
      PRIMARY KEY (umfrage_id, identity)
    );
    """,
    # Version 3: Breakout-Räume (je Meeting eine Runde; wird beim Meeting-Ende gelöscht)
    """
    CREATE TABLE breakout (
      raum_id INTEGER PRIMARY KEY REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      anzahl INTEGER NOT NULL,         -- 2–50 Räume, LiveKit-Name <code>__b<nr>
      status TEXT NOT NULL DEFAULT 'entwurf',   -- entwurf | offen | schliesst | geschlossen
      runde INTEGER NOT NULL DEFAULT 0,         -- zählt jedes Öffnen hoch
      dauer_min INTEGER,               -- Timer (NULL = ohne)
      rueckkehr INTEGER NOT NULL DEFAULT 1,     -- Teilnehmende dürfen selbst in den Hauptraum zurück
      geoeffnet INTEGER, endet INTEGER, schliesst_um INTEGER
    );
    CREATE TABLE breakout_teilnehmer (
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      identity TEXT NOT NULL,
      nr INTEGER NOT NULL DEFAULT 0,   -- zugeteilter Breakout-Raum (0 = keiner)
      ort INTEGER NOT NULL DEFAULT 0,  -- wo die Person gerade ist (0 = Hauptraum)
      PRIMARY KEY (raum_id, identity)
    );
    """,
    # ==== Version 4 (Kalender-Abo) – eigener Block, beim Zusammenführen ggf. umnummerieren ====
    # Geheimer iCal-Feed je Konto (nur nach Einschalten). Das Token wird aus AGORA_GEHEIM + salz abgeleitet
    # (HMAC) und nur als Hash gespeichert; neues salz = neues Token (alter Link ungültig).
    """
    CREATE TABLE kalender_abo (
      benutzer_id INTEGER PRIMARY KEY REFERENCES benutzer(id) ON DELETE CASCADE,
      salz TEXT NOT NULL,
      token_hash TEXT NOT NULL UNIQUE,
      erstellt INTEGER NOT NULL,
      abgerufen INTEGER                -- letzter Abruf (Anzeige im Profil), keine IP
    );
    """,
    # ==== Version 5 (Meeting-Vorlagen) – eigener Block, beim Zusammenführen ggf. umnummerieren ====
    """
    CREATE TABLE vorlagen (
      id INTEGER PRIMARY KEY,
      benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
      name TEXT NOT NULL,
      einstellungen TEXT NOT NULL,     -- JSON: titel, dauer_min, passcode (an/aus), warteraum, chat_speichern, beschreibung, rechte, stumm_beitritt
      erstellt INTEGER NOT NULL,
      geaendert INTEGER NOT NULL
    );
    CREATE INDEX vorlagen_benutzer ON vorlagen(benutzer_id);
    ALTER TABLE raeume ADD COLUMN rechte_standard TEXT;                        -- JSON; beim Meeting-Ende rechte := rechte_standard
    ALTER TABLE raeume ADD COLUMN stumm_beitritt INTEGER NOT NULL DEFAULT 0;   -- alle außer Host treten stumm bei
    """,
    # ==== Version 6 (Erinnerungen) – eigener Block, beim Zusammenführen ggf. umnummerieren ====
    """
    CREATE TABLE erinnerung_konto (    -- opt-in im Profil: Erinnerung vor eigenen geplanten Meetings
      benutzer_id INTEGER PRIMARY KEY REFERENCES benutzer(id) ON DELETE CASCADE,
      minuten INTEGER NOT NULL         -- 10 | 15 | 30 | 60
    );
    CREATE TABLE erinnerung_gaeste (   -- nur wenn der Host „Erinnerung schicken“ ankreuzt; nach dem Termin gelöscht
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      email TEXT NOT NULL COLLATE NOCASE,
      minuten INTEGER NOT NULL,
      termin INTEGER,                  -- Vorkommen, zu dem eingeladen wurde (bei Wiederholung maßgeblich)
      erstellt INTEGER NOT NULL,
      PRIMARY KEY (raum_id, email)
    );
    CREATE TABLE erinnerungen_gesendet (   -- Hash(Raum, Termin, Empfänger) → höchstens eine Mail je Termin; 2 Tage
      schluessel TEXT PRIMARY KEY,
      zeit INTEGER NOT NULL
    );
    """,
    # ==== Version 7 (Meeting-Verlauf) – eigener Block, beim Zusammenführen ggf. umnummerieren ====
    """
    CREATE TABLE meeting_verlauf (     -- je Meeting (Raum + Sitzung): Zeiten und Zahlen, keine Namen; 90 Tage
      id INTEGER PRIMARY KEY,
      raum_id INTEGER REFERENCES raeume(id) ON DELETE SET NULL,
      sitzung INTEGER NOT NULL,
      besitzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
      code TEXT NOT NULL,
      titel TEXT NOT NULL,
      start INTEGER NOT NULL,
      ende INTEGER,
      spitze INTEGER NOT NULL DEFAULT 0,       -- höchste Zahl gleichzeitig Verbundener (minütlich gemessen)
      teilnahmen INTEGER NOT NULL DEFAULT 0,   -- ausgegebene Zugänge (Beitritte)
      chat INTEGER NOT NULL DEFAULT 0,         -- Chat/Umfragen dieser Sitzung gespeichert
      UNIQUE (raum_id, sitzung)
    );
    CREATE INDEX meeting_verlauf_besitzer ON meeting_verlauf(besitzer_id, start);
    """,
    # ==== Version 8 (Dauer-Chat im persönlichen Raum) – eigener Block, beim Zusammenführen ggf. umnummerieren ====
    """
    ALTER TABLE raeume ADD COLUMN dauer_chat INTEGER NOT NULL DEFAULT 0;      -- nur persönlicher Raum, Besitzer schaltet
    ALTER TABLE nachrichten ADD COLUMN dauer INTEGER NOT NULL DEFAULT 0;      -- 1 = gehört zum Dauer-Chat (bleibt nach dem Meeting)
    """,
    # ======== Block „Zwei-Faktor-Anmeldung (TOTP)“ (beim Zusammenführen ggf. neu nummerieren) ========
    # Version 9: TOTP-Geheimnis (verschlüsselt mit einem aus AGORA_GEHEIM abgeleiteten Schlüssel), letzter benutzter
    # Zeitschritt (Schutz gegen Wiederverwendung), Wiederherstellungscodes (nur Hash), zweiter Anmeldeschritt
    """
    ALTER TABLE benutzer ADD COLUMN totp_geheim TEXT;            -- verschlüsselt (zwei_faktor.verschluesseln) oder NULL
    ALTER TABLE benutzer ADD COLUMN totp_an INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE benutzer ADD COLUMN totp_schritt INTEGER NOT NULL DEFAULT 0;   -- zuletzt angenommener 30-s-Schritt
    CREATE TABLE wiederherstellung (    -- Wiederherstellungscodes, je einmal nutzbar, nur als HMAC gespeichert
      benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
      code_hash TEXT NOT NULL,
      PRIMARY KEY (benutzer_id, code_hash)
    );
    CREATE TABLE anmeldeschritte (      -- Passwort war richtig, Code fehlt noch (5 min, höchstens 5 Versuche)
      token_hash TEXT PRIMARY KEY,
      benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
      gueltig_bis INTEGER NOT NULL,
      versuche INTEGER NOT NULL DEFAULT 0
    );
    """,
    # ======== Ende Block „Zwei-Faktor-Anmeldung“ ========
    # ======== Block „Melden und Blockieren“ (beim Zusammenführen ggf. neu nummerieren) ========
    # Version 10: Meldungen aus Meetings (an die Admins, 365 Tage nach Erledigung gelöscht) und blockierte Konten
    """
    CREATE TABLE meldungen (
      id INTEGER PRIMARY KEY,
      zeit INTEGER NOT NULL,
      raum_code TEXT NOT NULL, raum_titel TEXT NOT NULL DEFAULT '', sitzung INTEGER NOT NULL,
      von_name TEXT NOT NULL, von_identity TEXT NOT NULL, von_benutzer INTEGER,      -- ohne Fremdschlüssel: Meldung bleibt,
      ziel_name TEXT NOT NULL, ziel_identity TEXT NOT NULL, ziel_benutzer INTEGER,   -- auch wenn ein Konto gelöscht wird
      grund TEXT NOT NULL,             -- belaestigung | hass | unangemessen | stoerung | spam | sonstiges
      text TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'offen',     -- offen | erledigt
      notiz TEXT, bearbeitet_von INTEGER, bearbeitet INTEGER
    );
    CREATE INDEX meldungen_status ON meldungen(status, zeit);
    CREATE TABLE blockiert (
      benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,    -- wer blockiert
      blockiert_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,   -- wen
      name TEXT NOT NULL,              -- Anzeigename beim Blockieren
      zeit INTEGER NOT NULL,
      PRIMARY KEY (benutzer_id, blockiert_id)
    );
    """,
    # ======== Ende Block „Melden und Blockieren“ ========
    # ======== Block „Admin-Bereich“ (beim Zusammenführen ggf. neu nummerieren) ========
    # Version 11: Konten sperren (gesperrt = keine Anmeldung, Sitzungen werden beim Sperren gelöscht)
    """
    ALTER TABLE benutzer ADD COLUMN gesperrt INTEGER NOT NULL DEFAULT 0;
    """,
    # ======== Ende Block „Admin-Bereich“ ========
    # ==== Block Verbindungsqualität (app/qualitaet.py) – eigene Version, bei Bedarf neu nummerieren ====
    # Version 12: Verbindungsqualität je Meeting (aggregiert, ohne IPs) + letzte Werte je Teilnahme für den Host
    """
    CREATE TABLE qualitaet (           -- Summen über alle Zusammenfassungen eines Meetings (raum_id + sitzung)
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      proben INTEGER NOT NULL DEFAULT 0,        -- Zahl der Zusammenfassungen
      teilnehmer INTEGER NOT NULL DEFAULT 0,    -- verschiedene Teilnahmen, die gemeldet haben
      rtt_summe REAL NOT NULL DEFAULT 0, verlust_summe REAL NOT NULL DEFAULT 0, jitter_summe REAL NOT NULL DEFAULT 0,
      ein_summe REAL NOT NULL DEFAULT 0, aus_summe REAL NOT NULL DEFAULT 0,      -- kbit/s
      rtt_max REAL NOT NULL DEFAULT 0, verlust_max REAL NOT NULL DEFAULT 0,
      schlecht INTEGER NOT NULL DEFAULT 0, mittel INTEGER NOT NULL DEFAULT 0,   -- Proben mit dieser Bewertung
      udp INTEGER NOT NULL DEFAULT 0, tcp INTEGER NOT NULL DEFAULT 0, relay INTEGER NOT NULL DEFAULT 0,
      beginn INTEGER NOT NULL, aktualisiert INTEGER NOT NULL,
      PRIMARY KEY (raum_id, sitzung)
    );
    CREATE TABLE qualitaet_person (    -- letzte Werte je Teilnahme (Host-Ansicht); wird beim Meeting-Ende gelöscht
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      identity TEXT NOT NULL,
      zeit INTEGER NOT NULL,
      werte TEXT NOT NULL,             -- JSON {rtt, verlust, jitter, ein, aus, kandidat, aufloesung, fps, bewertung}
      PRIMARY KEY (raum_id, identity)
    );
    """,
    # Version 13: Rückmeldung nach dem Meeting (Sterne, Probleme, Text) – je Teilnahme einmal, mit eigenem Statistik-Schnitt
    """
    CREATE TABLE feedback (
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      identity TEXT NOT NULL,          -- nur, damit jede Teilnahme einmal bewertet (wird nie angezeigt)
      sterne INTEGER NOT NULL,         -- 1–5
      probleme TEXT NOT NULL,          -- JSON-Liste (ton, echo, video, verbindung, bildschirm, bedienung)
      text TEXT NOT NULL DEFAULT '',
      statistik TEXT,                  -- JSON: Schnitt der eigenen Verbindungswerte oder NULL
      zeit INTEGER NOT NULL,
      PRIMARY KEY (raum_id, sitzung, identity)
    );
    """,
    # ==== Ende Block Verbindungsqualität ====
    # ======== Block „Zusammenarbeit“ (Whiteboard, Notizen, Q&A, Dateien) – beim Zusammenführen ggf. neu nummerieren ========
    # Version 14: Gemeinsames Whiteboard – Operations-Log je Meeting (raum_id + sitzung), Sperre/Zähler je Raum.
    # Mit dem Meeting gelöscht, außer der Raum hat chat_speichern (dann sieht der Besitzer das Bild im Meeting-Verlauf).
    """
    CREATE TABLE whiteboard (          -- je Raum: laufende Nummer der Operationen und Sperre (nur Moderation zeichnet)
      raum_id INTEGER PRIMARY KEY REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      seq INTEGER NOT NULL DEFAULT 0,
      gesperrt INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE whiteboard_ops (      -- {art: neu|aendern|loeschen, ...} als JSON, in Reihenfolge von id
      id INTEGER PRIMARY KEY,
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      seq INTEGER NOT NULL,
      identity TEXT NOT NULL,
      op TEXT NOT NULL,
      zeit INTEGER NOT NULL
    );
    CREATE INDEX whiteboard_ops_raum ON whiteboard_ops(raum_id, sitzung, id);
    """,
    # Version 15: Gemeinsame Notizen – ein Text je Meeting, Version für die optimistische Sperre (letzter Schreiber gewinnt,
    # Konflikt → 409). Mit dem Meeting gelöscht, außer chat_speichern.
    """
    CREATE TABLE notizen (
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      text TEXT NOT NULL,
      version INTEGER NOT NULL,
      geaendert INTEGER NOT NULL,
      von_name TEXT NOT NULL,          -- wer zuletzt gespeichert hat (Anzeige „zuletzt geändert von“)
      PRIMARY KEY (raum_id, sitzung)
    );
    """,
    # Version 16: Q&A – Fragen je Meeting (anonym nur gegenüber anderen Teilnehmenden; identity/Name/Konto speichert der
    # Server immer) und Stimmen (eine je Teilnahme). Mit dem Meeting gelöscht, außer chat_speichern.
    """
    CREATE TABLE fragen (
      id INTEGER PRIMARY KEY,
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      identity TEXT NOT NULL, name TEXT NOT NULL, benutzer_id INTEGER,
      anonym INTEGER NOT NULL DEFAULT 0,
      text TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'offen',      -- offen | beantwortet | verworfen
      hervorgehoben INTEGER NOT NULL DEFAULT 0,
      antwort TEXT, antwort_von TEXT,
      erstellt INTEGER NOT NULL, beantwortet INTEGER
    );
    CREATE INDEX fragen_raum ON fragen(raum_id, sitzung);
    CREATE TABLE fragen_stimmen (
      frage_id INTEGER NOT NULL REFERENCES fragen(id) ON DELETE CASCADE,
      identity TEXT NOT NULL,
      PRIMARY KEY (frage_id, identity)
    );
    """,
    # Version 17: Dateien im Meeting (Inhalt unter DATEN/dateien/<id>). ablauf NULL = läuft noch; beim Ende gelöscht oder
    # (chat_speichern) ablauf = Ende + 30 Tage.
    """
    CREATE TABLE dateien (
      id TEXT PRIMARY KEY,
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      identity TEXT NOT NULL, von_name TEXT NOT NULL,
      name TEXT NOT NULL,              -- gesäuberter Dateiname
      endung TEXT NOT NULL,
      typ TEXT NOT NULL,               -- aus der Endung abgeleitet
      groesse INTEGER NOT NULL,
      geprueft INTEGER NOT NULL DEFAULT 0,      -- 1 = clamd hat geprüft
      erstellt INTEGER NOT NULL,
      ablauf INTEGER
    );
    CREATE INDEX dateien_raum ON dateien(raum_id, sitzung);
    """,
    # ======== Block „Webinar + Registrierung“ (app/webinar.py) – Version 18 ========
    # (Die Version ergibt sich aus der Position in dieser Liste.)
    """
    ALTER TABLE raeume ADD COLUMN webinar INTEGER NOT NULL DEFAULT 0;    -- 1 = Webinar: nur Panel sendet, Zuschauer sehen zu
    ALTER TABLE raeume ADD COLUMN webinar_einst TEXT;                     -- JSON: chat, fragen_an, registrierung, kapazitaet, fragen
    CREATE TABLE webinar_panel (       -- Einladungslinks für Panelisten (Token = HMAC aus AGORA_GEHEIM + salz, nur Hash gespeichert)
      id INTEGER PRIMARY KEY,
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      name TEXT NOT NULL,              -- Bezeichnung für den Besitzer (z. B. „Referentin“)
      salz TEXT NOT NULL,
      token_hash TEXT NOT NULL UNIQUE,
      erstellt INTEGER NOT NULL
    );
    CREATE TABLE webinar_anmeldungen ( -- Registrierungen; gelöscht 30 Tage nach dem Webinar (loeschen_ab)
      id INTEGER PRIMARY KEY,
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      name TEXT NOT NULL,
      email TEXT NOT NULL COLLATE NOCASE,
      antworten TEXT NOT NULL DEFAULT '[]',   -- JSON-Liste der Antworten auf die Zusatzfragen
      token_hash TEXT NOT NULL UNIQUE,         -- persönlicher Beitrittslink (nur Hash)
      erstellt INTEGER NOT NULL,
      beigetreten INTEGER,                     -- erster Beitritt mit dem Link
      loeschen_ab INTEGER NOT NULL,
      UNIQUE (raum_id, email)
    );
    CREATE INDEX webinar_anmeldungen_ablauf ON webinar_anmeldungen(loeschen_ab);
    """,
    # ======== Ende Block „Webinar + Registrierung“ ========
    # ======== Block „Server-Aufnahme + Livestream (Egress)“ (app/egress.py) – Version 19 ========
    """
    CREATE TABLE egress_laeufe (       -- laufende und abgeschlossene Egress-Aufträge (fertige: 7 Tage, dann gelöscht)
      egress_id TEXT PRIMARY KEY,
      raum_id INTEGER REFERENCES raeume(id) ON DELETE SET NULL,
      sitzung INTEGER NOT NULL,
      art TEXT NOT NULL,               -- aufnahme | stream
      benutzer_id INTEGER REFERENCES benutzer(id) ON DELETE CASCADE,   -- Besitzer (Bibliothek)
      von_name TEXT NOT NULL,
      titel TEXT NOT NULL DEFAULT '',
      raum_code TEXT NOT NULL,
      layout TEXT NOT NULL,
      datei TEXT,                      -- Dateiname im Ausgabeordner (nur aufnahme)
      ziel_verschluesselt TEXT,        -- nur stream, nur solange er läuft: RTMP-URL mit Schlüssel (zwei_faktor.verschluesseln)
      status TEXT NOT NULL DEFAULT 'startet',   -- startet | laeuft | endet | fertig | fehler
      fehler TEXT,
      aufnahme_id TEXT,                -- Eintrag in der Bibliothek nach dem Abschluss
      gestartet INTEGER NOT NULL,
      beendet INTEGER
    );
    CREATE INDEX egress_laeufe_raum ON egress_laeufe(raum_id, status);
    """,
    # ======== Ende Block „Server-Aufnahme + Livestream (Egress)“ ========
    # ======== Block „Live-Untertitel + Transkript“ (app/untertitel.py) – Version 20 ========
    """
    ALTER TABLE raeume ADD COLUMN transkript_speichern INTEGER NOT NULL DEFAULT 0;   -- Besitzer: Untertitel als Transkript speichern
    CREATE TABLE transkript (          -- fertige Untertitel-Abschnitte (nur mit transkript_speichern), 90 Tage
      id INTEGER PRIMARY KEY,
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      identity TEXT NOT NULL,
      name TEXT NOT NULL,
      text TEXT NOT NULL,
      beginn_ms INTEGER NOT NULL, ende_ms INTEGER NOT NULL,   -- Unix-Zeit in ms (Server-Uhr, Beginn = Ende − gemeldete Dauer)
      zeit INTEGER NOT NULL
    );
    CREATE INDEX transkript_raum ON transkript(raum_id, sitzung);
    CREATE TABLE aufnahme_transkript ( -- Whisper-Job je Aufnahme; Ergebnis als Datei <aufnahme>.vtt daneben
      aufnahme_id TEXT PRIMARY KEY REFERENCES aufnahmen(id) ON DELETE CASCADE,
      status TEXT NOT NULL,            -- laeuft | fertig | fehler
      fehler TEXT,
      erstellt INTEGER NOT NULL,
      fertig INTEGER
    );
    """,
    # ======== Ende Block „Live-Untertitel + Transkript“ ========
    # ======== Block „E2EE“ (Ende-zu-Ende-Verschlüsselung, app/e2ee.py) – Version 21 ========
    # Version 21: E2EE je Raum: Schalter + Prüfwert (HKDF aus dem Geheimnis im Link-Fragment); der Schlüssel selbst nie
    """
    ALTER TABLE raeume ADD COLUMN e2ee INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE raeume ADD COLUMN e2ee_pruef TEXT;      -- 32 Hex-Zeichen oder NULL
    """,
    # ======== Ende Block „E2EE“ ========
    # ======== Block „Anmerkungen“ (app/anmerkungen.py) – Version 22 ========
    # Version 22: Recht „Anmerkungen auf geteilten Bildschirmen erlaubt“ je laufendem Meeting (beim Ende wieder 1)
    """
    ALTER TABLE raeume ADD COLUMN anmerkungen INTEGER NOT NULL DEFAULT 1;
    """,
    # ======== Ende Block „Anmerkungen“ ========
    # ======== Block „Öffentliche API“ (app/api_schluessel.py, app/api_v1.py) – Version 23 ========
    # Persönliche API-Schlüssel: nur der SHA-256 des Schlüssels wird gespeichert (der Schlüssel hat 256 Bit Zufall,
    # ein langsamer Hash bringt da nichts), dazu ein kurzer Anfang zum Wiedererkennen. Widerrufen = Zeile löschen.
    """
    CREATE TABLE api_schluessel (
      id INTEGER PRIMARY KEY,
      benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
      name TEXT NOT NULL,
      anfang TEXT NOT NULL,            -- z. B. „agora_Ab12Cd“ (Anzeige, kein Geheimnis)
      schluessel_hash TEXT NOT NULL UNIQUE,
      rechte TEXT NOT NULL,            -- JSON-Liste: meetings:lesen, meetings:schreiben, aufnahmen:lesen, webhooks
      erstellt INTEGER NOT NULL,
      gueltig_bis INTEGER,             -- NULL = ohne Ablauf
      benutzt INTEGER                  -- letzte Nutzung (höchstens minütlich aktualisiert), keine IP
    );
    CREATE INDEX api_schluessel_benutzer ON api_schluessel(benutzer_id);
    """,
    # ======== Ende Block „Öffentliche API“ ========
    # ======== Block „Webhooks“ (app/webhooks.py) – Version 24 ========
    """
    CREATE TABLE webhooks (            -- höchstens 5 je Konto
      id INTEGER PRIMARY KEY,
      benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
      url TEXT NOT NULL,               -- https://, öffentliche IP (bei jeder Zustellung neu geprüft)
      ereignisse TEXT NOT NULL,        -- JSON-Liste
      geheim TEXT NOT NULL,            -- Signier-Geheimnis, verschlüsselt (zwei_faktor.verschluesseln)
      aktiv INTEGER NOT NULL DEFAULT 1,
      fehler_folge INTEGER NOT NULL DEFAULT 0,  -- endgültig fehlgeschlagene Zustellungen in Folge
      deaktiviert_grund TEXT,
      erstellt INTEGER NOT NULL,
      letzter_erfolg INTEGER
    );
    CREATE INDEX webhooks_benutzer ON webhooks(benutzer_id);
    CREATE TABLE webhook_zustellungen (   -- Warteschlange (offene Versuche); höchstens 2 Tage
      id TEXT PRIMARY KEY,
      webhook_id INTEGER NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
      ereignis TEXT NOT NULL,
      nutzlast TEXT NOT NULL,
      versuch INTEGER NOT NULL DEFAULT 0,
      naechster INTEGER NOT NULL,
      erstellt INTEGER NOT NULL
    );
    CREATE INDEX webhook_zustellungen_faellig ON webhook_zustellungen(naechster);
    CREATE TABLE webhook_log (         -- je Versuch: Status, Dauer, Fehler (keine Antwort-Inhalte); letzte 50 je Webhook, 14 Tage
      id INTEGER PRIMARY KEY,
      webhook_id INTEGER NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
      zustellung TEXT NOT NULL,
      ereignis TEXT NOT NULL,
      zeit INTEGER NOT NULL,
      versuch INTEGER NOT NULL,
      status INTEGER,
      ok INTEGER NOT NULL,
      dauer_ms INTEGER NOT NULL,
      fehler TEXT
    );
    CREATE INDEX webhook_log_webhook ON webhook_log(webhook_id, id);
    CREATE TABLE webhook_anwesend (    -- für teilnehmer.gegangen: wer einen Zugang bekam; nur bis zum Ende des Meetings
      raum_id INTEGER NOT NULL REFERENCES raeume(id) ON DELETE CASCADE,
      sitzung INTEGER NOT NULL,
      identity TEXT NOT NULL,
      name TEXT NOT NULL,
      rolle TEXT NOT NULL,
      verbunden INTEGER NOT NULL DEFAULT 0,     -- 1 = schon einmal bei LiveKit gesehen
      zeit INTEGER NOT NULL,
      PRIMARY KEY (raum_id, identity)
    );
    """,
    # ======== Ende Block „Webhooks“ ========
    # ======== Block „Einbetten“ (app/einbetten.py) – Version 25 ========
    """
    ALTER TABLE raeume ADD COLUMN einbetten INTEGER NOT NULL DEFAULT 0;   -- /e/<code> erlaubt
    ALTER TABLE raeume ADD COLUMN einbetten_herkunft TEXT;                -- JSON-Liste geprüfter Origins (frame-ancestors)
    """,
    # ======== Ende Block „Einbetten“ ========
]


def verbindung():
    c = getattr(_lokal, 'c', None)
    if c is None:
        c = sqlite3.connect(DB, timeout=10, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA busy_timeout=5000')
        _lokal.c = c
    return c


def einrichten():
    for d in ('aufnahmen', 'tmp', 'sicherung'):
        os.makedirs(os.path.join(DATEN, d), exist_ok=True)
    c = verbindung()
    c.execute('CREATE TABLE IF NOT EXISTS schema_version (v INTEGER NOT NULL)')
    v = (c.execute('SELECT v FROM schema_version').fetchone() or [0])[0]
    if v == 0 and not c.execute('SELECT 1 FROM schema_version').fetchone():
        c.execute('INSERT INTO schema_version VALUES (0)')
    for nr, sql in enumerate(SCHEMA, start=1):
        if v < nr:
            c.executescript('BEGIN;' + sql + f'UPDATE schema_version SET v = {nr}; COMMIT;')


def protokollieren(ereignis, benutzer_id=None, raum_code=None):
    verbindung().execute('INSERT INTO protokoll VALUES (?, ?, ?, ?)', (int(time.time()), benutzer_id, raum_code, ereignis[:200]))


def sichern():
    """Konsistente Kopie der Datenbank (für das nächtliche Server-Backup, das den Ordner per tar sichert)."""
    ziel = os.path.join(DATEN, 'sicherung', 'agora.sqlite3')
    tmp = ziel + '.neu'
    b = sqlite3.connect(tmp)
    verbindung().backup(b)
    b.close()
    os.replace(tmp, ziel)
