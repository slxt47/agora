// Seitenleiste: Chat (Gruppe + privat über die Backend-API, ungelesen-Zähler, sichere Links, Emojis) und
// Teilnehmende (Status Mikro/Kamera/Hand/Rolle, Host-Aktionen, Warteraum mit Zulassen/Ablehnen, Rechte, Sperre, alle stumm).
import { $, h, knopf, toast } from '../common.js';
import { t } from '../i18n.js';
import { icon } from '../icons.js';
import { initialen, linkTeile, uhrzeitText, seitText } from '../helfer.js';
import { menue } from './menue.js';

export const EMOJIS = ['👍', '👏', '❤️', '😂', '😮', '🎉', '🙌', '🤔', '👋', '🙏', '🔥', '✅', '😊', '😅', '👀', '💡'];

// ================================================================ Chat
export class Chat {
  constructor(m) {
    this.m = m;
    this.nachrichten = new Map();
    this.ungelesen = 0;
    this.sichtbar = false;
    const panel = $('#panel-chat');
    this.liste = h('ol', { class: 'chat-liste', role: 'log', 'aria-live': 'polite', 'aria-label': t('chat.verlauf') });
    this.leer = h('p', { class: 'chat-leer' }, t('chat.leer'));
    this.scroll = h('div', { class: 'panel-scroll' }, this.leer, this.liste);
    this.an = h('select', { class: 'eingabe', 'aria-label': t('chat.empfaenger') });
    this.feld = h('textarea', { rows: '1', maxlength: '2000', placeholder: t('chat.platzhalter'), 'aria-label': t('chat.nachricht'), enterkeyhint: 'send' });
    this.emojiKnopf = h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('chat.emoji'), title: t('chat.emoji'), html: icon('smiley'), onclick: () => this.emojiWahl() });
    this.sendenKnopf = h('button', { type: 'submit', class: 'icon-btn senden', 'aria-label': t('chat.senden'), title: t('chat.senden'), html: icon('senden') });
    this.form = h('form', { class: 'chat-eingabe' },
      h('label', { class: 'an-zeile' }, h('span', {}, t('chat.an')), this.an),
      h('div', { class: 'feld-zeile' }, this.emojiKnopf, this.feld, this.sendenKnopf));
    this.gesperrt = h('p', { class: 'chat-gesperrt', hidden: true }, t('chat.gesperrt'));
    panel.append(this.scroll, h('div', {}, this.form, this.gesperrt));
    this.form.addEventListener('submit', (e) => { e.preventDefault(); this.senden(); });
    this.feld.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.senden(); }
    });
    this.feld.addEventListener('input', () => { this.feld.style.height = 'auto'; this.feld.style.height = `${Math.min(140, this.feld.scrollHeight + 2)}px`; });
    this.empfaengerAktualisieren();
  }

  async laden() {
    try {
      const r = await this.m.api(`/api/raeume/${this.m.code}/chat`);
      for (const n of r.nachrichten || []) this.empfangen(n, true);
    } catch { /* Chat aus oder leer */ }
  }

  empfaengerAktualisieren() {
    const alt = this.an.value;
    const leute = [...this.m.room?.remoteParticipants.values() || []].sort((a, b) => this.m.name(a).localeCompare(this.m.name(b), 'de'));
    this.an.replaceChildren(h('option', { value: '' }, t('chat.alle')),
      ...leute.map((p) => h('option', { value: p.identity }, t('chat.privatAn', { name: this.m.name(p) }))));
    this.an.value = leute.some((p) => p.identity === alt) ? alt : '';
  }

  rechteAktualisieren() {
    const erlaubt = this.m.rechte.chat !== false || this.m.istMod();
    this.form.hidden = !erlaubt;
    this.gesperrt.hidden = erlaubt;
  }

  async senden() {
    const text = this.feld.value.trim();
    if (!text) return;
    if (text.length > 2000) { toast(t('chat.zuLang'), 'err'); return; }
    this.sendenKnopf.disabled = true;
    try {
      const body = { text };
      if (this.an.value) body.an = this.an.value;
      const r = await this.m.api(`/api/raeume/${this.m.code}/chat`, { methode: 'POST', json: body });
      this.feld.value = '';
      this.feld.style.height = '';
      if (r.nachricht) this.empfangen(r.nachricht, true);
    } catch (e) { toast(e.message, 'err'); } finally { this.sendenKnopf.disabled = false; this.feld.focus(); }
  }

  empfangen(n, still = false) {
    if (!n || n.id == null || this.nachrichten.has(n.id)) return;
    this.nachrichten.set(n.id, n);
    const ich = this.m.room?.localParticipant.identity;
    const meins = n.von === ich;
    const nahUnten = this.scroll.scrollHeight - this.scroll.scrollTop - this.scroll.clientHeight < 80;
    const kopf = h('div', { class: 'kopfzeile' }, h('b', {}, meins ? t('allg.du') : n.von_name), h('span', {}, uhrzeitText(n.zeit)));
    if (n.an) kopf.append(h('span', { class: 'privat-hinweis' }, meins ? t('chat.privatAnKurz', { name: n.an_name || '' }) : t('chat.privatVon')));
    const blase = h('div', { class: 'blase' }, ...linkTeile(n.text).map((x) => (x.link
      ? h('a', { href: x.link, target: '_blank', rel: 'noopener noreferrer' }, x.text) : x.text)));
    this.liste.append(h('li', { class: `${meins ? 'meins' : ''}${n.an ? ' privat' : ''}` }, kopf, blase));
    this.leer.hidden = true;
    if (nahUnten || meins) this.scroll.scrollTop = this.scroll.scrollHeight;
    if (!this.sichtbar && !meins && !still) {
      this.ungelesen++;
      this.m.zaehlerSetzen();
      if (n.an) toast(t('chat.privatToast', { name: n.von_name }), '', { aktion: { text: t('chat.oeffnen'), fn: () => this.m.leisteOeffnen('chat') } });
    }
    if (!meins && n.an && !still) this.an.value = this.m.room?.remoteParticipants.has(n.von) ? n.von : this.an.value;   // Antwort geht privat zurück
  }

  zeigen(an) {
    this.sichtbar = an;
    if (an) { this.ungelesen = 0; this.m.zaehlerSetzen(); this.scroll.scrollTop = this.scroll.scrollHeight; }
  }

  emojiWahl() {
    const raster = h('div', { class: 'emoji-raster' }, EMOJIS.map((e) => h('button', { type: 'button', 'aria-label': e, onclick: () => {
      const s = this.feld.selectionStart ?? this.feld.value.length, en = this.feld.selectionEnd ?? s;
      this.feld.value = this.feld.value.slice(0, s) + e + this.feld.value.slice(en);
      this.feld.focus(); this.feld.selectionStart = this.feld.selectionEnd = s + e.length;
    } }, e)));
    menue(this.emojiKnopf, null, { inhalt: raster, label: t('chat.emoji') });
  }
}

// ================================================================ Teilnehmende
export class Leute {
  constructor(m) {
    this.m = m;
    this.wartend = [];
    this.sichtbar = false;
    this.geplant = 0;
    const panel = $('#panel-leute');
    this.scroll = h('div', { class: 'panel-scroll' });
    this.fuss = h('div', { class: 'leute-fuss' });
    panel.append(this.scroll, this.fuss);
  }

  zeigen(an) { this.sichtbar = an; if (an) { this.render(); if (this.m.istMod()) this.warteraumLaden(); } }

  planen() {
    if (!this.sichtbar || this.geplant) return;
    this.geplant = requestAnimationFrame(() => { this.geplant = 0; this.render(); });
  }

  async warteraumLaden() {
    if (!this.m.istMod()) { this.wartend = []; return; }
    try {
      const r = await this.m.api(`/api/raeume/${this.m.code}/warteraum`);
      this.wartend = r.wartend || [];
      this.m.wartendSetzen(this.wartend.length);
      this.planen();
    } catch { /* still */ }
  }

  async zulassen(anfrage, aktion) {
    try {
      await this.m.api(`/api/raeume/${this.m.code}/warteraum/${encodeURIComponent(anfrage)}`, { methode: 'POST', json: { aktion } });
      this.wartend = this.wartend.filter((w) => w.anfrage !== anfrage);
      this.m.wartendSetzen(this.wartend.length);
      this.render();
    } catch (e) { toast(e.message, 'err'); }
  }

  render() {
    const m = this.m, room = m.room;
    if (!room) return;
    const fokusId = document.activeElement?.dataset?.fokus;
    const teile = [];
    // Warteraum
    if (m.istMod() && this.wartend.length) {
      teile.push(h('section', { class: 'leute-abschnitt', 'aria-label': t('leute.warteraum') },
        h('h3', {}, t('leute.warteraum'), h('span', { class: 'zaehler-inline' }, String(this.wartend.length))),
        h('ul', { class: 'leute-liste' }, this.wartend.map((w) => h('li', {},
          h('span', { class: 'mini-avatar', 'aria-hidden': 'true' }, initialen(w.name)),
          h('span', { class: 'wer' }, h('b', {}, w.name), h('small', {}, w.seit ? t('leute.wartetSeit', { seit: seitText(w.seit) }) : '')),
          h('button', { type: 'button', class: 'btn primary', dataset: { fokus: `z-${w.anfrage}` }, onclick: () => this.zulassen(w.anfrage, 'zulassen') }, t('leute.zulassen')),
          h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('leute.ablehnenName', { name: w.name }), title: t('leute.ablehnen'), html: icon('schliessen'),
            onclick: () => this.zulassen(w.anfrage, 'ablehnen') })))),
        this.wartend.length > 1 ? h('div', { class: 'knoepfe oben' }, h('button', { type: 'button', class: 'btn klein', onclick: async () => {
          for (const w of [...this.wartend]) await this.zulassen(w.anfrage, 'zulassen');
        } }, t('leute.alleZulassen'))) : null));
    }
    // Im Meeting
    const alle = [room.localParticipant, ...room.remoteParticipants.values()];
    const rang = { host: 0, cohost: 1, teilnehmer: 2, gast: 3 };
    alle.sort((a, b) => (b.isLocal - a.isLocal) || ((b.attributes?.hand === '1') - (a.attributes?.hand === '1'))
      || (rang[m.rolleVon(a)] ?? 9) - (rang[m.rolleVon(b)] ?? 9) || m.name(a).localeCompare(m.name(b), 'de'));
    teile.push(h('section', { class: 'leute-abschnitt', 'aria-label': t('leute.imMeeting') },
      h('h3', {}, t('leute.imMeetingN', { n: alle.length })),
      h('ul', { class: 'leute-liste' }, alle.map((p) => this.zeile(p)))));
    this.scroll.replaceChildren(...teile);

    // Fuß: Moderation oder eigene Aktionen
    const fuss = [];
    if (m.istMod()) {
      fuss.push(h('div', { class: 'knoepfe' },
        knopf(t('leute.alleStumm'), { klasse: 'btn', symbol: 'micAus', onclick: () => m.steuern('alle_stumm', {}, t('leute.alleStummOk')) }),
        knopf(m.gesperrt ? t('leute.entsperren') : t('leute.sperren'), { klasse: 'btn', symbol: m.gesperrt ? 'schlossOffen' : 'schloss',
          onclick: () => m.steuern('sperren', { an: !m.gesperrt }) })));
      const rechte = [['chat', t('recht.chat')], ['bildschirm', t('recht.bildschirm')], ['selbst_unmute', t('recht.selbstUnmute')],
        ['umbenennen', t('recht.umbenennen')], ['mehrfach_freigabe', t('recht.mehrfach')]];
      const det = h('details', { class: 'rechte', open: this.rechteOffen || null },
        h('summary', {}, t('leute.rechte')),
        h('div', { class: 'rechte-liste' }, rechte.map(([k, text]) => h('label', { class: 'schalter' }, h('span', { class: 'txt' }, text),
          h('input', { type: 'checkbox', role: 'switch', checked: m.rechte[k] !== false && (k !== 'mehrfach_freigabe' || !!m.rechte[k]),
            onchange: (e) => m.steuern('rechte', { [k]: e.target.checked }) })))));
      det.addEventListener('toggle', () => { this.rechteOffen = det.open; });
      fuss.push(det);
    } else {
      fuss.push(h('div', { class: 'knoepfe' },
        knopf(m.hand ? t('steuer.handSenken') : t('steuer.handHeben'), { klasse: 'btn', onclick: () => m.handUmschalten() }),
        m.darfUmbenennen(room.localParticipant) ? knopf(t('leute.umbenennen'), { klasse: 'btn', symbol: 'stift', onclick: () => m.umbenennenDialog(room.localParticipant) }) : null));
    }
    this.fuss.replaceChildren(...fuss);
    if (fokusId) this.scroll.querySelector(`[data-fokus="${CSS.escape(fokusId)}"]`)?.focus();
  }

  zeile(p) {
    const m = this.m;
    const rolle = m.rolleVon(p);
    const rolleText = { host: t('rolle.host'), cohost: t('rolle.cohost'), gast: t('rolle.gast') }[rolle] || '';
    const unter = [p.isLocal ? t('leute.du') : '', rolleText].filter(Boolean).join(' · ');
    const mic = p.isMicrophoneEnabled, cam = p.isCameraEnabled, hand = p.attributes?.hand === '1';
    const av = h('span', { class: 'mini-avatar', 'aria-hidden': 'true' }, initialen(m.name(p)));
    av.style.background = m.farbe(p);
    const aktionen = m.aktionenFuer(p);
    const mehr = aktionen.length ? h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('leute.aktionenFuer', { name: m.name(p) }), html: icon('mehr'),
      dataset: { fokus: `a-${p.identity}` }, onclick: (e) => menue(e.currentTarget, m.aktionenFuer(p), { oben: false }) }) : null;
    const zustand = [hand ? t('kachel.hand') : '', mic ? t('leute.micAn') : t('leute.micAus'), cam ? t('leute.camAn') : t('leute.camAus')].filter(Boolean).join(', ');
    return h('li', {},
      av,
      h('span', { class: 'wer' }, h('b', {}, m.name(p)), unter ? h('small', {}, unter) : null),
      m.statistik?.marke(p),
      h('span', { class: 'zustand', role: 'img', 'aria-label': zustand },
        hand ? h('span', { class: 'hand', 'aria-hidden': 'true' }, '✋') : null,
        h('span', { class: mic ? '' : 'aus', html: icon(mic ? 'mic' : 'micAus') }),
        h('span', { class: cam ? '' : 'aus', html: icon(cam ? 'kamera' : 'kameraAus') })),
      mehr);
  }
}
