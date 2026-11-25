import { inclusiveDays, presetRange, toBackendPeriod, validatePeriod } from './period.util';

describe('period.util', () => {
  const today = new Date(2026, 10, 25);   // 25 novembre 2026

  it('calcule les raccourcis de période (jours inclus)', () => {
    expect(presetRange('last7', today)).toEqual({ from: '2026-11-19', to: '2026-11-25' });
    expect(presetRange('last30', today)).toEqual({ from: '2026-10-27', to: '2026-11-25' });
    expect(presetRange('thisMonth', today)).toEqual({ from: '2026-11-01', to: '2026-11-25' });
    expect(presetRange('lastMonth', today)).toEqual({ from: '2026-10-01', to: '2026-10-31' });
  });

  it('gère le mois précédent en janvier (changement d\'année)', () => {
    expect(presetRange('lastMonth', new Date(2027, 0, 15))).toEqual({ from: '2026-12-01', to: '2026-12-31' });
  });

  it('compte les jours bornes incluses', () => {
    expect(inclusiveDays('2026-11-01', '2026-11-01')).toBe(1);
    expect(inclusiveDays('2026-11-01', '2026-11-30')).toBe(30);
    expect(inclusiveDays('2026-11-30', '2026-11-01')).toBe(0);
    expect(inclusiveDays('', '2026-11-01')).toBe(0);
  });

  it('valide la période avec les mêmes bornes que le Backend', () => {
    expect(validatePeriod('2026-11-01', '2026-11-25', today)).toBe('');
    expect(validatePeriod('', '2026-11-25', today)).toContain('obligatoires');
    expect(validatePeriod('2026-11-25', '2026-11-01', today)).toContain('postérieure');
    expect(validatePeriod('2025-01-01', '2026-11-25', today)).toContain('366');
    expect(validatePeriod('2026-11-26', '2026-11-30', today)).toContain('futur');
  });

  it('accepte exactement 366 jours et refuse 367', () => {
    expect(validatePeriod('2025-11-25', '2026-11-25', today)).toBe('');                 // 366 jours inclus
    expect(validatePeriod('2025-11-24', '2026-11-25', today)).toContain('366');          // 367 jours
  });

  it("envoie au Backend un intervalle [début, fin[ : la fin est minuit du lendemain", () => {
    expect(toBackendPeriod('2026-11-01', '2026-11-30')).toEqual({ from: '2026-11-01T00:00:00', to: '2026-12-01T00:00:00' });
  });
});
