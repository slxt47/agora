"""Migration lokal → S3: Aufnahmen, Whisper-Transkripte (.vtt) und Dateien aus Meetings unter daten/ ins Bucket kopieren.

    docker compose run --rm --no-deps app python -m app.speicher_migrieren --trocken     # nur zeigen
    docker compose run --rm --no-deps app python -m app.speicher_migrieren               # kopieren (idempotent)
    docker compose run --rm --no-deps app python -m app.speicher_migrieren --loeschen    # kopieren + lokal löschen

Liest AGORA_S3_* aus der Umgebung (app.env). Schon vorhandene Objekte mit gleicher Größe werden übersprungen; nach dem
Upload prüft HEAD die Größe, erst dann löscht `--loeschen` die lokale Datei. Nur Dateien, die in der Datenbank stehen
(verwaiste werden genannt, nicht kopiert). Exit 1, wenn etwas fehlschlug.
"""
import argparse
import os
import sys

from . import db, speicher
from .s3 import S3Fehler


def kandidaten():
    c = db.verbindung()
    for a in c.execute('SELECT id FROM aufnahmen').fetchall():
        yield f'aufnahmen/{a["id"]}'
        yield f'aufnahmen/{a["id"]}.vtt'
    for d in c.execute('SELECT id FROM dateien').fetchall():
        yield f'dateien/{d["id"]}'


def migrieren(ziel, loeschen=False, trocken=False, ausgabe=print):
    """→ (kopiert, uebersprungen, fehler)."""
    lokal = speicher.Lokal()
    bekannt = set(kandidaten())
    kopiert = uebersprungen = fehler = 0
    for ordner in ('aufnahmen', 'dateien'):
        for name, _t in lokal.liste(ordner):
            k = f'{ordner}/{name}'
            if k not in bekannt:
                ausgabe(f'verwaist (nicht kopiert): {k}')
    for k in sorted(bekannt):
        if not lokal.existiert(k):
            continue
        groesse = os.path.getsize(lokal.pfad(k))
        try:
            info = ziel.client.head(ziel.key(k))
            if info and info['groesse'] == groesse:
                uebersprungen += 1
                ausgabe(f'schon da: {k}')
            elif trocken:
                ausgabe(f'würde kopieren: {k} ({groesse} B)')
                continue
            else:
                ziel.client.put_datei(ziel.key(k), lokal.pfad(k), groesse, 'text/vtt' if k.endswith('.vtt') else 'application/octet-stream')
                info = ziel.client.head(ziel.key(k))
                if not info or info['groesse'] != groesse:
                    raise S3Fehler(0, 'Größe nach dem Upload stimmt nicht')
                kopiert += 1
                ausgabe(f'kopiert: {k} ({groesse} B)')
            if loeschen and not trocken:
                lokal.loeschen(k)
        except (OSError, S3Fehler) as e:
            fehler += 1
            ausgabe(f'FEHLER {k}: {e}')
    return kopiert, uebersprungen, fehler


def main(argv=None):
    p = argparse.ArgumentParser(description='Aufnahmen/Dateien von daten/ ins S3-Bucket kopieren')
    p.add_argument('--loeschen', action='store_true', help='lokale Datei nach geprüftem Upload löschen')
    p.add_argument('--trocken', action='store_true', help='nur anzeigen, nichts kopieren')
    a = p.parse_args(argv)
    ziel = speicher.aus_umgebung()
    if ziel.art != 's3':
        print('AGORA_S3_ENDPOINT/BUCKET/KEY/SECRET fehlen – nichts zu tun.')
        return 2
    db.einrichten()
    k, u, f = migrieren(ziel, a.loeschen, a.trocken)
    print(f'fertig: {k} kopiert, {u} schon vorhanden, {f} Fehler')
    return 1 if f else 0


if __name__ == '__main__':
    sys.exit(main())
