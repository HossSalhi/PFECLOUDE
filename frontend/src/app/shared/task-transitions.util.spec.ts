import { allowedTransitions, isClosedStatus } from './task-transitions.util';
import { TaskStatus } from '../core/models/org.models';

describe('task-transitions.util', () => {

  it('reconnaît les statuts finaux', () => {
    expect(isClosedStatus('DONE')).toBeTrue();
    expect(isClosedStatus('CANCELLED')).toBeTrue();
    (['TODO', 'IN_PROGRESS', 'ON_HOLD'] as TaskStatus[]).forEach(s => expect(isClosedStatus(s)).toBeFalse());
  });

  it('propose à un gestionnaire toutes les transitions du Backend', () => {
    expect(allowedTransitions('TODO', true)).toEqual(['IN_PROGRESS', 'ON_HOLD', 'DONE', 'CANCELLED']);
    expect(allowedTransitions('IN_PROGRESS', true)).toEqual(['TODO', 'ON_HOLD', 'DONE', 'CANCELLED']);
    expect(allowedTransitions('ON_HOLD', true)).toEqual(['TODO', 'IN_PROGRESS', 'DONE', 'CANCELLED']);
    expect(allowedTransitions('DONE', true)).toEqual(['IN_PROGRESS']);
    expect(allowedTransitions('CANCELLED', true)).toEqual(['TODO']);
  });

  it("ne propose jamais l'annulation ni la réouverture au responsable simple d'une tâche", () => {
    expect(allowedTransitions('TODO', false)).toEqual(['IN_PROGRESS', 'ON_HOLD', 'DONE']);
    expect(allowedTransitions('IN_PROGRESS', false)).toEqual(['TODO', 'ON_HOLD', 'DONE']);
    expect(allowedTransitions('DONE', false)).toEqual([]);
    expect(allowedTransitions('CANCELLED', false)).toEqual([]);
  });

  it('ne partage pas de tableau interne avec les appelants', () => {
    const first = allowedTransitions('TODO', true);
    first.push('TODO');
    expect(allowedTransitions('TODO', true)).toEqual(['IN_PROGRESS', 'ON_HOLD', 'DONE', 'CANCELLED']);
  });
});
