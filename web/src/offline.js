// „Keine Verbindung“: liefert der Service Worker statt einer Seite, wenn das Netz fehlt (unter der angefragten Adresse).
// Wieder online → neu laden; „Erneut versuchen“ lädt sofort neu.
import { $ } from './common.js';
import { t } from './i18n.js';

$('#erneut').addEventListener('click', () => {
  if (!navigator.onLine) $('#offline-status').textContent = t('pwa.nochOffline');
  location.reload();
});
addEventListener('online', () => location.reload());
