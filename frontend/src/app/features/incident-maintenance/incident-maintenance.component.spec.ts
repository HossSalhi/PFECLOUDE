import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { IncidentRoutingService } from '../../core/services/incident-routing.service';
import { RoutingView } from '../../core/models/org.models';
import { IncidentMaintenanceComponent } from './incident-maintenance.component';

describe('IncidentMaintenanceComponent', () => {
  let fixture: ComponentFixture<IncidentMaintenanceComponent>;
  let service: jasmine.SpyObj<IncidentRoutingService>;

  const base: RoutingView = {
    incidentId: 9, incidentTitle: 'CPU élevé', incidentType: 'NODE_HIGH_CPU', incidentStatus: 'OPEN', scope: 'VM',
    team: { id: 1, name: 'Infra-VM', specialty: 'VM', lead: { id: 5, username: 'chef', displayName: 'Chef VM' } },
    maintenance: { id: 3, title: 'Patch noyau', resourceType: 'NODE', resourceName: 'k8s-worker1', status: 'IN_PROGRESS' },
    state: 'AWAITING_DECISION', canDecide: true, canResume: false, tasks: [{ id: 1, title: 'Décider', event: 'DECISION', status: 'TODO' }]
  };

  function setup(view: RoutingView, action: string | null = null) {
    service = jasmine.createSpyObj<IncidentRoutingService>('IncidentRoutingService', ['view', 'decide', 'resume']);
    service.view.and.returnValue(of(view));
    TestBed.configureTestingModule({
      imports: [IncidentMaintenanceComponent],
      providers: [
        provideRouter([]),
        { provide: IncidentRoutingService, useValue: service },
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: { get: () => '9' }, queryParamMap: { get: () => action } } } }
      ]
    });
    fixture = TestBed.createComponent(IncidentMaintenanceComponent);
    fixture.detectChanges();
  }

  const buttons = () => Array.from(fixture.nativeElement.querySelectorAll('button')) as HTMLButtonElement[];
  const byText = (t: string) => buttons().find(b => b.textContent!.includes(t));

  it('loads the situation of the incident and shows no action until the user clicks', () => {
    setup(base, 'pause');

    expect(service.view).toHaveBeenCalledWith(9);
    expect(service.decide).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain('Patch noyau');
    expect(byText('Suspendre')!.getAttribute('data-highlight')).toBe('true');
  });

  it('suspends the maintenance only after the click, and shows the new state', () => {
    setup(base);
    service.decide.and.returnValue(of({ ...base, state: 'PAUSED', canDecide: false, maintenance: { ...base.maintenance!, status: 'SUSPENDED' } }));

    byText('Suspendre')!.click();
    fixture.detectChanges();

    expect(service.decide).toHaveBeenCalledOnceWith(9, 3, 'PAUSE');
    expect(fixture.nativeElement.textContent).toContain('Maintenance suspendue');
    expect(byText('Suspendre')).toBeUndefined();
  });

  it('can continue the maintenance', () => {
    setup(base);
    service.decide.and.returnValue(of({ ...base, state: 'CONTINUED', canDecide: false }));

    byText('Continuer')!.click();

    expect(service.decide).toHaveBeenCalledOnceWith(9, 3, 'CONTINUE');
  });

  it('proposes the resume once the incident is resolved and resumes on click', () => {
    setup({ ...base, state: 'AWAITING_RESUME', canDecide: false, canResume: true, incidentStatus: 'RESOLVED',
      maintenance: { ...base.maintenance!, status: 'SUSPENDED' } });
    service.resume.and.returnValue(of({ ...base, state: 'RESUMED', canDecide: false, canResume: false }));

    expect(byText('Suspendre')).toBeUndefined();
    byText('Reprendre')!.click();
    fixture.detectChanges();

    expect(service.resume).toHaveBeenCalledOnceWith(9, 3);
    expect(fixture.nativeElement.textContent).toContain('Maintenance reprise');
  });

  it('shows no action buttons to a user who cannot decide', () => {
    setup({ ...base, canDecide: false });

    expect(buttons().length).toBe(0);
    expect(fixture.nativeElement.textContent).toContain('Seuls le responsable');
  });

  it('shows the backend error and keeps the state when the action is refused', () => {
    setup(base);
    service.decide.and.returnValue(throwError(() => ({ status: 403, error: {} })));

    byText('Suspendre')!.click();
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('Action non autorisée');
    expect(fixture.nativeElement.textContent).toContain('Décision attendue');
  });
});
