// Bildschirmauswahl: Quellen als Kacheln (Bildschirm/Fenster), Auswahl per Klick, Doppelklick, Enter; Esc bricht ab.
'use strict';
const liste = document.getElementById('liste');
const teilen = document.getElementById('teilen');
let quellen = [], art = 'bildschirm', gewaehlt = null;

function zeichnen() {
  liste.replaceChildren();
  for (const q of quellen.filter((x) => x.bildschirm === (art === 'bildschirm'))) {
    const li = document.createElement('li');
    li.setAttribute('role', 'option'); li.tabIndex = 0; li.dataset.id = q.id;
    li.setAttribute('aria-selected', String(q.id === gewaehlt));
    const img = document.createElement('img'); img.src = q.bild; img.alt = '';
    const name = document.createElement('span'); name.textContent = q.name;
    li.append(img, name);
    li.addEventListener('click', () => {
      gewaehlt = q.id; teilen.disabled = false;
      for (const x of liste.children) x.setAttribute('aria-selected', String(x === li));
    });
    li.addEventListener('dblclick', () => window.auswahl.waehlen(q.id));
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); window.auswahl.waehlen(q.id); } });
    liste.append(li);
  }
}

for (const [id, wert] of [['r-bildschirm', 'bildschirm'], ['r-fenster', 'fenster']]) {
  document.getElementById(id).addEventListener('click', (e) => {
    art = wert;
    for (const b of document.querySelectorAll('[role=tab]')) b.setAttribute('aria-selected', String(b === e.currentTarget));
    zeichnen();
  });
}
document.getElementById('abbrechen').addEventListener('click', () => window.auswahl.waehlen(null));
teilen.addEventListener('click', () => gewaehlt && window.auswahl.waehlen(gewaehlt));
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') window.auswahl.waehlen(null); });
window.auswahl.quellen().then((q) => { quellen = q; if (!quellen.some((x) => x.bildschirm)) art = 'fenster'; zeichnen(); });
