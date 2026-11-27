import { AiCapabilities, AiFeatureState, AiNarrative } from '../../core/models/ai-assist.models';

/** Fabriques de données partagées par les specs de l'assistance IA. */
export const ON: AiFeatureState = { enabled: true, ready: true, message: null };
export const OFF: AiFeatureState = { enabled: false, ready: false, message: null };

export function caps(over: Partial<AiCapabilities> = {}): AiCapabilities {
  return {
    admin: true, ledTeams: [], allowed: true,
    reportsSummary: ON, teamSummary: ON, auditAnomalies: ON, taskAssignment: ON, maintenanceWindow: ON, ...over
  };
}

export function leaderCaps(teamId: number, over: Partial<AiCapabilities> = {}): AiCapabilities {
  return caps({ admin: false, ledTeams: [{ id: teamId, name: 'Réseau' }], reportsSummary: OFF, ...over });
}

export function narrative(over: Partial<AiNarrative> = {}): AiNarrative {
  return { status: 'OK', message: null, text: 'Synthèse de test.', model: 'test-model', generatedAt: '2026-11-27T10:00:00Z', ...over };
}
