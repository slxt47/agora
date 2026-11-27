// Konto: Zusammenfassung, Kapitelmarken und Aufgaben zu einem gespeicherten Transkript (Meeting oder Whisper-Transkript
// einer Aufnahme). „Mit KI“ nur, wenn der Server ein Modell hat (AGORA_KI_URL) – vorher sagt ein Dialog, wohin das
// Transkript geht, und erst die Bestätigung schickt `zustimmung: true`. „Stichwort-Kapitel“ geht immer und ohne KI
// (Pausen, Sprecherwechsel, häufige Wörter) – so beschriftet. Kapitel gibt es als WebVTT für den Spieler.
import { h, api, blatt, knopf, beschaeftigt, bestaetigen, toast } from './common.js';
import { t } from './i18n.js';

/** 83000 → "01:23", 3723000 → "1:02:03" */
export function zeitMarke(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const zwei = (n) => String(n).padStart(2, '0');
  return s >= 3600 ? `${Math.floor(s / 3600)}:${zwei(Math.floor(s / 60) % 60)}:${zwei(s % 60)}` : `${zwei(Math.floor(s / 60))}:${zwei(s % 60)}`;
}

function pfade(q) {
  if (q.art === 'aufnahme') return { api: `/api/aufnahmen/${q.id}/zusammenfassung`, vtt: `/api/aufnahmen/${q.id}/kapitel.vtt` };
  const s = q.sitzung ? `?sitzung=${q.sitzung}` : '';
  return { api: `/api/raeume/${q.code}/zusammenfassung${s}`, vtt: `/api/raeume/${q.code}/kapitel.vtt${s}` };
}

export function zusammenfassungKnopf(q, klasse = 'btn klein') {
  return knopf(t('zf.knopf'), { klasse, symbol: 'funke', 'data-zusammenfassung': q.code || q.id, onclick: () => zusammenfassungDialog(q) });
}

export async function zusammenfassungDialog(q) {
  const { inhalt, dialog } = blatt(t('zf.titel', { titel: q.titel || '' }), { breit: true });
  const p = pfade(q);
  const box = h('div', { class: 'formular', id: 'zf-inhalt' });
  inhalt.append(h('p', { class: 'sec klein' }, t('zf.text')), box);
  let timer = 0;
  const starten = (json) => beschaeftigt(null, async () => { zeigen(await api(p.api, { methode: 'POST', json })); });

  function zeigen(r) {
    clearTimeout(timer);
    const z = r.zusammenfassung;
    const teile = [];
    if (z?.status === 'laeuft') {
      teile.push(h('div', { class: 'status', role: 'status', id: 'zf-status' }, t('zf.laeuft')));
      timer = setTimeout(async () => { if (dialog.open) { try { zeigen(await api(p.api)); } catch { /* nächster Versuch */ } } }, 2000);
    } else if (z?.status === 'fehler') {
      teile.push(h('div', { class: 'status err', role: 'status', id: 'zf-status' }, t('zf.fehler', { text: z.fehler || '' })));
    } else if (z?.status === 'fertig') {
      const e = z.ergebnis || {};
      teile.push(h('div', { class: `status ${z.art === 'ki' ? 'ok' : 'warn'}`, role: 'status', id: 'zf-status', dataset: { art: z.art } },
        z.art === 'ki' ? t('zf.artKi', { modell: z.modell || '' }) : t('zf.artStichwort')));
      if (e.zusammenfassung) teile.push(h('h4', {}, t('zf.zusammenfassung')), h('p', { class: 'zf-text' }, e.zusammenfassung));
      if (e.kapitel?.length) {
        teile.push(h('h4', {}, t('zf.kapitel')), h('ol', { class: 'liste zf-kapitel' }, e.kapitel.map((k) => h('li', {},
          h('div', { class: 'text' }, h('b', { class: 'mono' }, zeitMarke(k.start_ms)), ' ', k.titel,
            k.sprecher?.length ? h('div', { class: 'sec klein' }, k.sprecher.join(', ')) : null)))));
        teile.push(h('a', { class: 'btn klein', href: p.vtt, download: '' }, t('zf.vtt')));
      }
      if (e.aufgaben?.length) {
        teile.push(h('h4', {}, t('zf.aufgaben')), h('ul', { class: 'liste zf-aufgaben' }, e.aufgaben.map((a) => h('li', {},
          h('div', { class: 'text' }, a.text, a.wer ? h('div', { class: 'sec klein' }, a.wer) : null)))));
      }
      teile.push(knopf(t('zf.loeschen'), { klasse: 'btn klein gefahr', symbol: 'muell', onclick: async () => {
        if (!await bestaetigen(t('zf.loeschen'), null, { ja: t('konto.loeschen'), gefahr: true })) return;
        beschaeftigt(null, async () => { await api(p.api, { methode: 'DELETE' }); zeigen(await api(p.api)); });
      } }));
    }
    const laeuft = z?.status === 'laeuft';
    teile.push(h('h4', {}, t('zf.erstellen')), h('div', { class: 'knoepfe' },
      r.ki_verfuegbar ? knopf(t('zf.ki'), { klasse: 'btn primary', symbol: 'funke', id: 'zf-ki', disabled: laeuft, onclick: async () => {
        if (!await bestaetigen(t('zf.kiTitel'), t('zf.kiHinweis', { ziel: r.ki_ziel || '' }), { ja: t('zf.kiSenden') })) return;
        starten({ art: 'ki', zustimmung: true });
      } }) : null,
      knopf(t('zf.stichwort'), { klasse: 'btn', symbol: 'notizen', id: 'zf-stichwort', disabled: laeuft, onclick: () => starten({ art: 'stichwort' }) })),
    h('p', { class: 'sec klein' }, r.ki_verfuegbar ? t('zf.stichwortHinweis') : `${t('zf.kiNicht')} ${t('zf.stichwortHinweis')}`));
    box.replaceChildren(...teile);
  }
  try { zeigen(await api(p.api)); } catch (e) { box.replaceChildren(h('p', { class: 'leer' }, e.message)); toast(e.message, 'err'); }
}

/** Spieler im Konto: Kapitel als <track kind="chapters"> und als Liste zum Springen. */
export async function kapitelImSpieler(a, video, box) {
  if (a.zusammenfassung?.status !== 'fertig') return;
  video.append(h('track', { kind: 'chapters', src: `/api/aufnahmen/${a.id}/kapitel.vtt`, default: true, label: t('zf.kapitel') }));
  try {
    const r = await api(`/api/aufnahmen/${a.id}/zusammenfassung`);
    const k = r.zusammenfassung?.ergebnis?.kapitel || [];
    if (!k.length) return;
    box.append(h('h4', {}, t('zf.kapitel')), h('div', { class: 'knoepfe zf-sprung' }, k.map((x) => h('button', { type: 'button', class: 'btn klein',
      onclick: () => { video.currentTime = x.start_ms / 1000; video.play().catch(() => {}); } }, `${zeitMarke(x.start_ms)} ${x.titel}`))));
  } catch { /* ohne Liste */ }
}
