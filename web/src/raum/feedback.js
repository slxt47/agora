// Rückmeldung nach dem Meeting (Ende-Bildschirm): „Wie war die Qualität?“ – 1–5 Sterne, optional Probleme zum
// Ankreuzen und ein Text. Geht mit dem Rückmelde-Token (kam mit der Statistik-Antwort) und dem eigenen Schnitt der
// Verbindungswerte an POST /api/raeume/{code}/feedback. Der Host sieht im Konto nur Schnitt und Zählungen ohne Namen.
import { h, api, ApiFehler } from '../common.js';
import { t } from '../i18n.js';
import './feedback.css';

export const PROBLEME = ['ton', 'echo', 'video', 'verbindung', 'bildschirm', 'bedienung'];

/** Formular bauen und vor `vor` einsetzen. feedback = {token, statistik}. */
export function feedbackZeigen(code, feedback, vor) {
  document.querySelector('.feedback')?.remove();
  if (!feedback?.token) return null;
  const sterne = h('div', { class: 'feedback-sterne', role: 'radiogroup', 'aria-label': t('fb.sterneLabel') },
    [1, 2, 3, 4, 5].map((n) => h('label', { class: 'feedback-stern', title: t(`fb.stern.${n}`) },
      h('input', { type: 'radio', name: 'sterne', value: String(n), 'aria-label': t(`fb.stern.${n}`) }),
      h('span', { 'aria-hidden': 'true' }, '★'))));
  const probleme = h('fieldset', { class: 'feedback-probleme', hidden: true },
    h('legend', {}, t('fb.probleme')),
    PROBLEME.map((p) => h('label', { class: 'feedback-chip' }, h('input', { type: 'checkbox', name: 'problem', value: p }), h('span', {}, t(`fb.problem.${p}`)))));
  const text = h('label', { class: 'feld', hidden: true }, h('span', {}, t('fb.text')),
    h('textarea', { name: 'text', rows: '3', maxlength: '1000', placeholder: t('fb.textPlatzhalter') }));
  const statusEl = h('p', { class: 'status', role: 'status', hidden: true });
  const senden = h('button', { type: 'submit', class: 'btn primary', disabled: true }, t('fb.senden'));
  const form = h('form', { class: 'feedback formular oben', 'aria-labelledby': 'fb-titel' },
    h('h2', { id: 'fb-titel' }, t('fb.titel')),
    h('p', { class: 'sec klein' }, t('fb.hinweis')),
    sterne, probleme, text, statusEl, h('div', { class: 'knoepfe' }, senden));
  sterne.addEventListener('change', () => {
    const n = Number(form.elements.sterne.value);
    senden.disabled = !n;
    probleme.hidden = false;
    text.hidden = false;
    form.classList.toggle('mit-problemen', n <= 3);
  });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const n = Number(form.elements.sterne.value);
    if (!n) return;
    senden.disabled = true;
    try {
      await api(`/api/raeume/${code}/feedback`, { methode: 'POST', json: {
        token: feedback.token, sterne: n, probleme: [...form.querySelectorAll('[name=problem]:checked')].map((x) => x.value),
        text: form.elements.text.value.trim(), statistik: feedback.statistik || undefined } });
      form.replaceChildren(h('p', { class: 'feedback-danke', role: 'status' }, t('fb.danke')));
    } catch (err) {
      statusEl.hidden = false;
      statusEl.className = 'status err';
      statusEl.textContent = err instanceof ApiFehler ? err.message : t('fehler.allgemein', { text: err?.message || '' });
      senden.disabled = false;
    }
  });
  vor.before(form);
  return form;
}
