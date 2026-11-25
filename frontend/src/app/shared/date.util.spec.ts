import {
  endOfDayExclusiveIso, errorMessage, formatDate, formatDateTime, startOfDayIso, toDatetimeLocalValue, toDateValue,
  toLocalIso
} from './date.util';

describe('date.util', () => {

  it('formate une date/heure ISO sans fuseau en français', () => {
    expect(formatDateTime('2026-11-25T09:05:00')).toBe('25/11/2026 09:05');
    expect(formatDate('2026-11-25')).toBe('25/11/2026');
    expect(formatDate('2026-11-25T23:59:00')).toBe('25/11/2026');
  });

  it('affiche un tiret pour une valeur absente et renvoie telle quelle une valeur illisible', () => {
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime(undefined)).toBe('—');
    expect(formatDate('')).toBe('—');
    expect(formatDateTime('pas une date')).toBe('pas une date');
  });

  it("produit l'heure locale ISO envoyée au Backend, sans fuseau", () => {
    expect(toLocalIso(new Date(2026, 10, 5, 8, 7, 6))).toBe('2026-11-05T08:07:06');
    expect(toDateValue(new Date(2026, 0, 31))).toBe('2026-01-31');
  });

  it("convertit une date ISO en valeur d'un champ datetime-local", () => {
    expect(toDatetimeLocalValue('2026-11-25T10:30:00')).toBe('2026-11-25T10:30');
    expect(toDatetimeLocalValue(null)).toBe('');
    expect(toDatetimeLocalValue('zzz')).toBe('');
  });

  it("calcule les bornes d'une période [début, fin[ à partir de jours inclus", () => {
    expect(startOfDayIso('2026-11-25')).toBe('2026-11-25T00:00:00');
    expect(endOfDayExclusiveIso('2026-11-25')).toBe('2026-11-26T00:00:00');
    expect(endOfDayExclusiveIso('2026-11-30')).toBe('2026-12-01T00:00:00');
    expect(endOfDayExclusiveIso('2026-12-31')).toBe('2027-01-01T00:00:00');
  });

  describe('errorMessage', () => {
    it("utilise le message du corps d'erreur du Backend", () => {
      expect(errorMessage({ status: 409, error: { error: 'Conflit de maintenance', status: 409 } })).toBe('Conflit de maintenance');
    });

    it('explique un refus 403 sans exposer de détail technique', () => {
      expect(errorMessage({ status: 403, error: { error: 'autre' } })).toContain('non autorisée');
    });

    it('retombe sur le message du client HTTP puis sur le message par défaut', () => {
      expect(errorMessage({ status: 500, message: 'Http failure' })).toBe('Http failure');
      expect(errorMessage({ status: 500 }, 'Par défaut')).toBe('Par défaut');
      expect(errorMessage(null)).toBe('Une erreur est survenue.');
    });

    it('ne reprend pas un message trop long (page HTML, trace)', () => {
      expect(errorMessage({ status: 500, error: 'x'.repeat(500) }, 'Défaut')).toBe('Défaut');
    });
  });
});
