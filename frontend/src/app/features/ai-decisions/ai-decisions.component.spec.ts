import { TestBed, ComponentFixture } from '@angular/core/testing';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { of } from 'rxjs';
import { AiDecisionsComponent } from './ai-decisions.component';
import { ApiService, Incident, PagedResponse } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { SseService } from '../../core/services/sse.service';

/**
 * HELIOS — la page « Décisions de remédiation » affiche la RÈGLE DÉTERMINISTE (scénario + action
 * configurée) par incident, SANS « confiance IA » ni « Décisions IA », et neutralise les titres
 * hérités à l'affichage sans réécrire la donnée. Données simulées (HTTP mocké).
 */
describe('AiDecisionsComponent — déterministe & neutralité IA (TestBed)', () => {
  let fixture: ComponentFixture<AiDecisionsComponent>;

  function setup(incidentType: string): string {
    const inc: Incident = {
      id: 1, title: 'Incident (IA: ' + incidentType + ')', description: 'desc',
      severity: 'HIGH', status: 'OPEN', source: 'AI',
      createdAt: '2026-10-15T10:00:00Z', updatedAt: '2026-10-15T10:00:00Z',
      incidentType
    };
    const page: PagedResponse<Incident> = { content: [inc], totalElements: 1, totalPages: 1, number: 0, size: 100 };
    const api: any = { getIncidents: () => of(page), submitFeedback: () => of({}) };
    const auth: any = { getUsername: () => 'op' };
    const sse: any = { onIncidentCreated: () => of(), onIncidentUpdated: () => of() };
    TestBed.configureTestingModule({
    imports: [CommonModule, FormsModule, AiDecisionsComponent],
    providers: [
        { provide: ApiService, useValue: api },
        { provide: AuthService, useValue: auth },
        { provide: SseService, useValue: sse }
    ]
});
    fixture = TestBed.createComponent(AiDecisionsComponent);
    fixture.detectChanges();
    return (fixture.nativeElement as HTMLElement).textContent || '';
  }

  afterEach(() => TestBed.resetTestingModule());

  it('affiche le titre déterministe, le scénario et l\'action configurée (plus de confiance)', () => {
    const text = setup('NODE_HIGH_CPU');
    expect(text).toContain('Décisions de remédiation');
    expect(text).toContain('CPU');
    expect(text).toContain('HOST_SERVICE_CPU_QUOTA');           // action RÉELLE du mapping (bridge.py : NODE_HIGH_CPU -> HOST_SERVICE_CPU_QUOTA)
    expect(text).not.toContain('Confiance');                   // notion IA supprimée
    expect(text).not.toContain('Décisions IA');
  });

  it('neutralise le titre hérité « (IA: …) » à l\'affichage (donnée persistée inchangée)', () => {
    const text = setup('NODE_DISK_FULL');
    expect(text).toContain('DISK');
    expect(text).toContain('HOST_RECLAIM_DISK');                // action RÉELLE du mapping (bridge.py : NODE_DISK_FULL -> HOST_RECLAIM_DISK)
    expect(text).not.toContain('(IA:');
  });

  it('détail déplié : règle présentée comme RÉFÉRENCE par type, jamais « réellement appliquée »', () => {
    setup('NODE_HIGH_CPU');
    (fixture.componentInstance as any).toggleExpand(1);
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('Règle configurée (référence par type');
    expect(text).toContain("n'implique pas l'exécution");
    expect(text).toContain('détail de l\'incident');           // renvoie vers l'action réellement exécutée
    expect(text).not.toContain('réellement appliquée');
  });

  it('type hors catalogue : « Hors catalogue », pas d\'escalade affirmée dans la liste', () => {
    const text = setup('DNS_FAILURE');
    expect(text).toContain('Hors catalogue');
    expect(text).not.toContain('escalade opérateur');
  });
});
