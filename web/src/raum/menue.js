// Kleines Popover-Menü (WAI-ARIA menu): öffnet an einem Anker, Pfeiltasten/Pos1/Ende, Esc schließt und gibt den Fokus
// zurück, Klick daneben schließt. Einträge: {text, symbol?, aktion, gefahr?, deaktiviert?, an? (Häkchen)} | '-' | {titel}.
// Oder eigener Inhalt: menue(anker, null, {inhalt: element}).
import { h } from '../common.js';
import { icon } from '../icons.js';

let offen = null;

export function menueSchliessen() {
  if (!offen) return;
  const { el, anker, aufraeumen } = offen;
  offen = null;
  aufraeumen();
  el.remove();
  anker.setAttribute('aria-expanded', 'false');
}

export function menue(anker, eintraege, { inhalt, label, oben = true } = {}) {
  if (offen?.anker === anker) { const a = offen.anker; menueSchliessen(); a.focus(); return null; }
  menueSchliessen();
  const el = h('div', { class: 'menue', role: inhalt ? 'dialog' : 'menu', 'aria-label': label || anker.getAttribute('aria-label') || '' });
  if (inhalt) el.append(inhalt);
  else {
    for (const e of eintraege.filter(Boolean)) {
      if (e === '-') { el.append(h('hr', { role: 'separator' })); continue; }
      if (e.titel) { el.append(h('div', { class: 'menue-titel', role: 'presentation' }, e.titel)); continue; }
      const b = h('button', {
        type: 'button', role: e.an === undefined ? 'menuitem' : 'menuitemcheckbox', class: e.gefahr ? 'gefahr' : '', tabindex: '-1',
        disabled: e.deaktiviert, 'aria-checked': e.an === undefined ? null : String(!!e.an),
        html: e.symbol ? icon(e.symbol) : null,
        onclick: () => { menueSchliessen(); anker.focus({ preventScroll: true }); e.aktion?.(); },
      }, h('span', {}, e.text));
      el.append(b);
    }
  }
  document.body.append(el);
  anker.setAttribute('aria-expanded', 'true');
  anker.setAttribute('aria-haspopup', inhalt ? 'dialog' : 'menu');

  // Position: über (oder unter) dem Anker, im Fenster gehalten
  const r = anker.getBoundingClientRect(), m = el.getBoundingClientRect();
  let x = Math.min(Math.max(8, r.left + r.width / 2 - m.width / 2), innerWidth - m.width - 8);
  let y = oben ? r.top - m.height - 8 : r.bottom + 8;
  if (y < 8) y = r.bottom + 8;
  if (y + m.height > innerHeight - 8) y = Math.max(8, r.top - m.height - 8);
  el.style.left = `${x}px`; el.style.top = `${y}px`;

  const punkte = () => [...el.querySelectorAll('[role^="menuitem"]:not(:disabled), .emoji-raster button, input, select, textarea, button')];
  const taste = (e) => {
    const liste = punkte(), i = liste.indexOf(document.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); menueSchliessen(); anker.focus(); }
    else if (!inhalt && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) { e.preventDefault(); liste[(i + (e.key === 'ArrowDown' ? 1 : -1) + liste.length) % liste.length]?.focus(); }
    else if (!inhalt && e.key === 'Home') { e.preventDefault(); liste[0]?.focus(); }
    else if (!inhalt && e.key === 'End') { e.preventDefault(); liste[liste.length - 1]?.focus(); }
    else if (e.key === 'Tab' && !inhalt) { menueSchliessen(); }
  };
  const klick = (e) => { if (!el.contains(e.target) && !anker.contains(e.target)) menueSchliessen(); };
  const weg = () => menueSchliessen();
  el.addEventListener('keydown', taste);
  setTimeout(() => document.addEventListener('pointerdown', klick, true));
  addEventListener('resize', weg);
  offen = { el, anker, aufraeumen: () => { document.removeEventListener('pointerdown', klick, true); removeEventListener('resize', weg); } };
  punkte()[0]?.focus({ preventScroll: true });
  return el;
}
