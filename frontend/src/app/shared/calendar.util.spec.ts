import { buildMonthGrid, CalendarEvent, gridRange } from './calendar.util';

describe('calendar.util', () => {

  // Novembre 2026 : le 1er est un dimanche → la grille commence le lundi 26 octobre.
  it('commence la grille le lundi de la semaine du 1er et couvre 6 semaines', () => {
    const { from, to } = gridRange(2026, 10);
    expect(from.getFullYear()).toBe(2026);
    expect(from.getMonth()).toBe(9);
    expect(from.getDate()).toBe(26);
    expect(from.getDay()).toBe(1);
    expect((to.getTime() - from.getTime()) / 86400000).toBe(42);
  });

  it('construit 6 lignes de 7 jours et marque les jours du mois et le jour courant', () => {
    const grid = buildMonthGrid(2026, 10, [], new Date(2026, 10, 25));
    expect(grid.length).toBe(6);
    grid.forEach(week => expect(week.length).toBe(7));
    const days = grid.reduce((all, w) => all.concat(w), []);
    expect(days.filter(d => d.inMonth).length).toBe(30);
    expect(days.filter(d => d.isToday).map(d => d.iso)).toEqual(['2026-11-25']);
    expect(days[0].iso).toBe('2026-10-26');
  });

  it("place un événement sur chaque jour qu'il couvre, et sur aucun autre", () => {
    const events: CalendarEvent[] = [
      { id: 1, title: 'Mise à jour', start: '2026-11-10T22:00:00', end: '2026-11-12T02:00:00', status: 'PLANNED' }
    ];
    const days = buildMonthGrid(2026, 10, events, new Date(2026, 10, 1)).reduce((all, w) => all.concat(w), []);
    const withEvent = days.filter(d => d.events.length > 0).map(d => d.iso);
    expect(withEvent).toEqual(['2026-11-10', '2026-11-11', '2026-11-12']);
  });

  it("n'affiche pas un événement qui se termine exactement à minuit sur le jour suivant", () => {
    const events: CalendarEvent[] = [
      { id: 2, title: 'Nuit', start: '2026-11-10T20:00:00', end: '2026-11-11T00:00:00', status: 'PLANNED' }
    ];
    const days = buildMonthGrid(2026, 10, events, new Date(2026, 10, 1)).reduce((all, w) => all.concat(w), []);
    expect(days.filter(d => d.events.length > 0).map(d => d.iso)).toEqual(['2026-11-10']);
  });

  it('gère février (année non bissextile) et les mois dont le 1er est un lundi', () => {
    // Février 2027 : le 1er est un lundi → pas de jour du mois précédent en tête de grille.
    const grid = buildMonthGrid(2027, 1, [], new Date(2027, 1, 10));
    expect(grid[0][0].iso).toBe('2027-02-01');
    const days = grid.reduce((all, w) => all.concat(w), []);
    expect(days.filter(d => d.inMonth).length).toBe(28);
  });
});
