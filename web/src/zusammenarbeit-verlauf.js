// Meeting-Verlauf (Konto): gespeicherte Inhalte aus „Zusammenarbeit“ – Whiteboard als Bild (mit PNG-Download).
// Nur bei Räumen mit „Chat speichern“; die Daten kommen aus GET /api/verlauf/{id}/inhalt.
import { h } from './common.js';
import { t } from './i18n.js';
import { exportCanvas } from './raum/whiteboard-zeichnen.js';
import './zusammenarbeit-verlauf.css';

const canvasNeu = (w, hh) => Object.assign(document.createElement('canvas'), { width: w, height: hh });

/** Blob als Datei herunterladen (blob:-URL, kein Server-Abruf). */
export function blobSpeichern(blob, name) {
  const a = h('a', { href: URL.createObjectURL(blob), download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}

export function zusammenarbeitVerlauf(inhalt, r, m) {
  if (r.whiteboard?.length) {
    const c = exportCanvas(r.whiteboard, canvasNeu);
    if (c) {
      c.className = 'verlauf-whiteboard';
      c.setAttribute('role', 'img');
      c.setAttribute('aria-label', t('zv.whiteboard'));
      const laden = h('button', { type: 'button', class: 'btn klein', onclick: () => c.toBlob((b) => b && blobSpeichern(b, `whiteboard-${m.code}.png`), 'image/png') },
        t('zv.whiteboardPng'));
      inhalt.append(h('h4', {}, t('zv.whiteboard')), c, h('div', { class: 'knoepfe' }, laden));
    }
  }
}
