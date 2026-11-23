"""Wer ist Admin? Bestätigte Konten, deren E-Mail in AGORA_ADMIN_MAILS steht (Komma-Liste, Groß/Klein egal).
Die Liste wird bei jedem Aufruf gelesen (Tests können sie umstellen); fehlt sie, gibt es keine Admins."""
import os

from . import db


def adressen():
    return {a.strip().lower() for a in os.environ.get('AGORA_ADMIN_MAILS', '').split(',') if a.strip()}


def ist_admin(b):
    return bool(b) and bool(b['bestaetigt']) and b['email'].lower() in adressen()


def bestaetigte():
    """Konten der Admins (nur bestätigte) – Empfänger für Meldungen."""
    liste = sorted(adressen())[:50]
    if not liste:
        return []
    return db.verbindung().execute(f'SELECT * FROM benutzer WHERE bestaetigt = 1 AND email IN ({",".join("?" * len(liste))})',
                                   liste).fetchall()        # email ist COLLATE NOCASE
