// Construction de la grille du calendrier mensuel (sans bibliothèque externe).

export interface CalendarEvent {
  id: number;
  title: string;
  start: string;
  end: string;
  status: string;
}

export interface CalendarDay {
  date: Date;
  iso: string;          // « 2026-11-25 »
  inMonth: boolean;
  isToday: boolean;
  events: CalendarEvent[];
}

const pad = (n: number): string => String(n).padStart(2, '0');
const isoDay = (d: Date): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** Premier jour affiché (le lundi de la semaine du 1er du mois) et jour qui suit le dernier (grille de 6 semaines). */
export function gridRange(year: number, month: number): { from: Date; to: Date } {
  const first = new Date(year, month, 1);
  const offset = (first.getDay() + 6) % 7;                 // lundi = 0
  const from = new Date(year, month, 1 - offset);
  const to = new Date(from.getFullYear(), from.getMonth(), from.getDate() + 42);
  return { from, to };
}

/** Grille mensuelle de 6 lignes × 7 colonnes (lundi en premier), avec les événements qui couvrent chaque jour. */
export function buildMonthGrid(year: number, month: number, events: CalendarEvent[], today: Date = new Date()): CalendarDay[][] {
  const { from } = gridRange(year, month);
  const todayIso = isoDay(today);
  const weeks: CalendarDay[][] = [];
  for (let w = 0; w < 6; w++) {
    const week: CalendarDay[] = [];
    for (let d = 0; d < 7; d++) {
      const date = new Date(from.getFullYear(), from.getMonth(), from.getDate() + w * 7 + d);
      const dayStart = date.getTime();
      const dayEnd = new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1).getTime();
      week.push({
        date,
        iso: isoDay(date),
        inMonth: date.getMonth() === month,
        isToday: isoDay(date) === todayIso,
        events: events.filter(e => new Date(e.start).getTime() < dayEnd && new Date(e.end).getTime() > dayStart)
      });
    }
    weeks.push(week);
  }
  return weeks;
}

export const MONTH_NAMES = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août',
  'septembre', 'octobre', 'novembre', 'décembre'];

export const WEEKDAY_NAMES = ['lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.', 'dim.'];
