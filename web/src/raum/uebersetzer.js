// Live-Übersetzung der Untertitel – nur auf dem Gerät. Nutzt die im Browser eingebaute Translator-API (Chrome am
// Computer ab 138: globales `Translator`; ältere Vorschau: `self.translation.createTranslator`). Der Text verlässt das
// Gerät dafür nie; Chrome lädt beim ersten Mal je Sprachpaar ein Übersetzungsmodell herunter (nur das Modell).
// Ohne API: `verfuegbar` ist false – die Oberfläche sagt das, Agora schickt Untertitel an keinen fremden Dienst.
// Reine Logik ohne DOM (auch in den Node-Tests mit einer Attrappe).

export const ZIELSPRACHEN = ['de', 'en', 'fr', 'es', 'it', 'nl', 'pl', 'pt', 'tr', 'uk', 'ar', 'ja', 'zh'];

/** Welche Form der API gibt es? → {art: 'neu'|'alt', T} oder null. */
export function translatorApi(g = globalThis) {
  if (g.Translator && typeof g.Translator.create === 'function') return { art: 'neu', T: g.Translator };
  if (g.translation && typeof g.translation.createTranslator === 'function') return { art: 'alt', T: g.translation };
  return null;
}

export class Uebersetzer {
  /** fortschritt(anteil 0–1) beim Herunterladen eines Modells. */
  constructor({ global = globalThis, fortschritt } = {}) {
    this.api = translatorApi(global);
    this.cache = new Map();          // 'de>en' → Promise<Translator>
    this.fortschritt = fortschritt;
  }

  get verfuegbar() { return !!this.api; }

  /** 'ja' (sofort) | 'laden' (Modell kommt erst) | 'nein'. */
  async status(quelle, ziel) {
    if (!this.api) return 'nein';
    if (quelle === ziel) return 'ja';
    try {
      if (this.api.art === 'neu') {
        const a = await this.api.T.availability({ sourceLanguage: quelle, targetLanguage: ziel });
        return a === 'available' ? 'ja' : (a === 'downloadable' || a === 'downloading') ? 'laden' : 'nein';
      }
      const a = await this.api.T.canTranslate({ sourceLanguage: quelle, targetLanguage: ziel });
      return a === 'readily' ? 'ja' : a === 'after-download' ? 'laden' : 'nein';
    } catch { return 'nein'; }
  }

  /** Übersetzer für ein Paar (einmal erzeugt, danach aus dem Cache). Zum ersten Mal am besten aus einem Klick heraus
   *  aufrufen – Chrome verlangt für den Modell-Download eine Nutzeraktion. */
  holen(quelle, ziel) {
    const k = `${quelle}>${ziel}`;
    if (!this.cache.has(k)) {
      const opts = { sourceLanguage: quelle, targetLanguage: ziel };
      if (this.fortschritt) opts.monitor = (m) => m.addEventListener?.('downloadprogress', (e) => this.fortschritt(e.loaded ?? 0));
      const p = Promise.resolve().then(() => (this.api.art === 'neu' ? this.api.T.create(opts) : this.api.T.createTranslator(opts)));
      p.catch(() => this.cache.delete(k));
      this.cache.set(k, p);
    }
    return this.cache.get(k);
  }

  /** Text übersetzen; gleiche Sprache oder ohne Quelle → unverändert. */
  async uebersetzen(text, quelle, ziel) {
    if (!text || !quelle || !ziel || quelle === ziel) return text;
    if (!this.api) throw new Error('Translator-API fehlt');
    const tr = await this.holen(quelle, ziel);
    return tr.translate(text);
  }

  schliessen() {
    for (const p of this.cache.values()) p.then((tr) => tr.destroy?.()).catch(() => {});
    this.cache.clear();
  }
}
