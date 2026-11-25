import { TaskStatus } from '../core/models/org.models';

/**
 * Transitions de statut proposées par l'interface — miroir des règles du Backend ({@code TeamTaskService}) :
 * l'API reste l'autorité et refuse toute transition interdite, ceci évite seulement de proposer des choix inutiles.
 *
 * - TODO / IN_PROGRESS / ON_HOLD : passage libre entre eux, vers DONE ou CANCELLED.
 * - DONE → IN_PROGRESS et CANCELLED → TODO : réouverture, réservée aux gestionnaires.
 * - Annuler : réservé aux gestionnaires (le responsable d'une tâche ne peut que la faire avancer).
 */
const TRANSITIONS: Record<TaskStatus, TaskStatus[]> = {
  TODO: ['IN_PROGRESS', 'ON_HOLD', 'DONE', 'CANCELLED'],
  IN_PROGRESS: ['TODO', 'ON_HOLD', 'DONE', 'CANCELLED'],
  ON_HOLD: ['TODO', 'IN_PROGRESS', 'DONE', 'CANCELLED'],
  DONE: ['IN_PROGRESS'],
  CANCELLED: ['TODO']
};

export function isClosedStatus(status: TaskStatus): boolean {
  return status === 'DONE' || status === 'CANCELLED';
}

/** Statuts vers lesquels la tâche peut passer, selon que l'utilisateur est gestionnaire (TASKS_UPDATE) ou non. */
export function allowedTransitions(from: TaskStatus, manager: boolean): TaskStatus[] {
  if (!manager && isClosedStatus(from)) { return []; }
  const next = TRANSITIONS[from] || [];
  return manager ? [...next] : next.filter(s => s !== 'CANCELLED');
}
