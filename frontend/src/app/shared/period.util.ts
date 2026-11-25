import { endOfDayExclusiveIso, startOfDayIso, toDateValue } from './date.util';

export type PeriodPreset = 'last7' | 'last30' | 'thisMonth' | 'lastMonth';

export const PERIOD_PRESET_LABELS: Record<PeriodPreset, string> = {
  last7: '7 derniers jours',
  last30: '30 derniers jours',
  thisMonth: 'Mois en cours',
  lastMonth: 'Mois précédent'
};

/** Période (jours inclus, au format des champs date : « 2026-11-25 ») correspondant à un raccourci. */
export function presetRange(preset: PeriodPreset, today: Date = new Date()): { from: string; to: string } {
  const day = (offset: number) => new Date(today.getFullYear(), today.getMonth(), today.getDate() + offset);
  switch (preset) {
    case 'last7':
      return { from: toDateValue(day(-6)), to: toDateValue(day(0)) };
    case 'last30':
      return { from: toDateValue(day(-29)), to: toDateValue(day(0)) };
    case 'thisMonth':
      return { from: toDateValue(new Date(today.getFullYear(), today.getMonth(), 1)), to: toDateValue(day(0)) };
    case 'lastMonth':
    default:
      return {
        from: toDateValue(new Date(today.getFullYear(), today.getMonth() - 1, 1)),
        to: toDateValue(new Date(today.getFullYear(), today.getMonth(), 0))
      };
  }
}

/** Nombre de jours (bornes incluses) entre deux champs date ; 0 si l'un d'eux est invalide ou si la fin précède le début. */
export function inclusiveDays(from: string, to: string): number {
  const a = new Date(from + 'T00:00:00').getTime();
  const b = new Date(to + 'T00:00:00').getTime();
  if (isNaN(a) || isNaN(b) || b < a) { return 0; }
  return Math.round((b - a) / 86400000) + 1;
}

/**
 * Contrôles de saisie d'une période de rapport — mêmes bornes que le Backend (début avant fin, 366 jours maximum,
 * début non situé dans le futur). Retourne le message d'erreur, ou une chaîne vide si la période est valide.
 */
export function validatePeriod(from: string, to: string, today: Date = new Date()): string {
  if (!from || !to) { return 'Les dates de début et de fin sont obligatoires.'; }
  const days = inclusiveDays(from, to);
  if (days === 0) { return 'La date de fin doit être postérieure ou égale à la date de début.'; }
  if (days > 366) { return "La période d'analyse est limitée à 366 jours."; }
  if (from > toDateValue(today)) { return "La période d'analyse ne peut pas commencer dans le futur."; }
  return '';
}

/** Bornes envoyées au Backend : début à minuit, fin = minuit du lendemain (intervalle [début, fin[ ). */
export function toBackendPeriod(from: string, to: string): { from: string; to: string } {
  return { from: startOfDayIso(from), to: endOfDayExclusiveIso(to) };
}
