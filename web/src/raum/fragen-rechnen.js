// Q&A – Reihenfolge ohne DOM: hervorgehobene oben, dann offene, beantwortete, verworfene; innerhalb nach Stimmen
// (bei Gleichstand ältere zuerst) oder nach Zeit (neueste zuerst).
const RANG = { offen: 0, beantwortet: 1, verworfen: 2 };

export function fragenSortieren(liste, nach = 'stimmen') {
  return [...liste].sort((a, b) => (b.hervorgehoben - a.hervorgehoben) || (RANG[a.status] - RANG[b.status])
    || (nach === 'stimmen' ? (b.stimmen - a.stimmen) || (a.id - b.id) : b.id - a.id));
}
