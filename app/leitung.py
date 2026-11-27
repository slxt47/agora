"""Mehrere Worker: Hintergrund-Aufgaben (Aufräumen, Erinnerungen, Egress-Abgleich, Sicherung, Webhook-Zustellung,
Status-Prüfung) laufen nur in EINEM Prozess – wer die Sperre `daten/hintergrund.lock` (flock) hält.

Die übrigen Worker warten in einem Thread blockierend auf die Sperre; stirbt der Leiter, gibt das Betriebssystem sie frei
und der nächste übernimmt. Mit einem Worker (Standard) ändert sich nichts: er bekommt die Sperre sofort. Das gilt für
Worker auf demselben Rechner mit demselben `daten/` – mehrere Rechner gehen ohnehin nicht (SQLite).
Der letzte erfolgreiche Lauf steht zusätzlich in `daten/hintergrund.zeit` (mtime), damit /api/gesund?tief=1 in jedem
Worker stimmt.
"""
import fcntl
import os
import threading
import time

from . import db

_datei = []          # offene Sperrdatei (bleibt offen, solange der Prozess lebt)


def sperre_pfad():
    return os.path.join(db.DATEN, 'hintergrund.lock')


def zeit_pfad():
    return os.path.join(db.DATEN, 'hintergrund.zeit')


def versuchen(blockieren=False):
    """Sperre holen → True (dieser Prozess leitet)."""
    if _datei:
        return True
    f = open(sperre_pfad(), 'a+')     # noqa: SIM115 – bleibt absichtlich offen
    try:
        fcntl.flock(f, fcntl.LOCK_EX | (0 if blockieren else fcntl.LOCK_NB))
    except OSError:
        f.close()
        return False
    f.seek(0)
    f.truncate()
    f.write(f'{os.getpid()}\n')
    f.flush()
    _datei.append(f)
    return True


def starten(aufgaben):
    """aufgaben() sofort starten, wenn dieser Worker leitet – sonst, sobald er die Sperre bekommt."""
    if versuchen():
        aufgaben()
        return True

    def warten():
        versuchen(blockieren=True)
        print(f'hintergrund: Worker {os.getpid()} übernimmt', flush=True)
        aufgaben()
    threading.Thread(target=warten, daemon=True, name='leitung').start()
    return False


def gelaufen():
    try:
        with open(zeit_pfad(), 'a'):
            pass
        os.utime(zeit_pfad())
    except OSError:
        pass


def letzter_lauf():
    try:
        return os.path.getmtime(zeit_pfad())
    except OSError:
        return 0.0


def freigeben():
    """Nur für Tests."""
    while _datei:
        f = _datei.pop()
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()
    time.sleep(0)
