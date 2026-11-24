// Zusammenarbeit im Meeting: Whiteboard (Bühnen-Ansicht) und weitere Reiter der Seitenleiste. Ein Einstiegspunkt für
// meeting.js – Reiter und Panels legt dieses Modul selbst an, Server-Ereignisse (Topic „agora“) und Whiteboard-Pakete
// (Topic „whiteboard“, nur vom Server) werden hier verteilt.
import { $, h } from '../common.js';
import { t } from '../i18n.js';
import { Whiteboard } from './whiteboard.js';
import './zusammenarbeit.css';

export class Zusammenarbeit {
  constructor(m) {
    this.m = m;
    this.module = new Map();          // Reiter-Name → Modul {typen:[…], laden, ereignis, zeigen, rolleGeaendert}
    this.whiteboard = new Whiteboard(m);
  }

  get panels() { return [...this.module.keys()]; }

  /** Reiter + Panel in der Seitenleiste anlegen; gibt das (leere) Panel zurück. */
  panelAnlegen(name, label) {
    const tab = h('button', { type: 'button', role: 'tab', id: `tab-${name}`, 'aria-controls': `panel-${name}`, 'aria-selected': 'false',
      onclick: () => this.m.leisteOeffnen(name) }, label);
    $('#seitenleiste .umschalter').append(tab);
    const panel = h('div', { class: 'panel', id: `panel-${name}`, role: 'tabpanel', 'aria-labelledby': `tab-${name}`, hidden: true });
    $('#seitenleiste').append(panel);
    return panel;
  }

  anmelden(name, modul) { this.module.set(name, modul); }

  laden() {
    this.whiteboard.laden();
    for (const mod of this.module.values()) mod.laden?.();
  }

  /** Server-Ereignis (Topic „agora“); true, wenn ein Modul es kennt. */
  ereignis(d) {
    for (const mod of this.module.values()) if (mod.typen?.includes(d.typ)) { mod.ereignis(d); return true; }
    return false;
  }

  daten(topic, d) { if (topic === 'whiteboard') this.whiteboard.daten(d); }

  zeigen(welche) { for (const [name, mod] of this.module) mod.zeigen?.(welche === name); }

  rolleGeaendert() {
    this.whiteboard.rolleGeaendert();
    for (const mod of this.module.values()) mod.rolleGeaendert?.();
  }

  menueEintraege() {
    return [
      { text: this.whiteboard.offen ? t('wb.schliessen') : t('wb.titel'), symbol: 'whiteboard', aktion: () => this.whiteboard.umschalten() },
      ...[...this.module].map(([name, mod]) => mod.menueEintrag && { text: mod.menueEintrag.text, symbol: mod.menueEintrag.symbol, aktion: () => this.m.leisteOeffnen(name) }),
    ];
  }
}
