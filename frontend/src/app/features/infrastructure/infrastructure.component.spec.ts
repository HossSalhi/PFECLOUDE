import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { InfrastructureComponent } from './infrastructure.component';

/**
 * Politique VM_DOWN (consigne finale du 2026-11-06, mise à jour le 2026-11-17) dans l'interface :
 * .155/.158/.162 et .153 (depuis le 2026-11-17 : HA + auto-rallumage) redémarrage automatique, .160 (VM de supervision) en
 * DÉTECTION SEULE (interface seulement) ; le mode DENY_RESTART est conservé (libellé) mais aucune VM ne l'utilise ;
 * une erreur HTTP est AFFICHÉE (jamais de données périmées sans alerte).
 */
describe('InfrastructureComponent (politique VM_DOWN)', () => {
  let fixture: ComponentFixture<InfrastructureComponent>;
  let component: InfrastructureComponent;
  let api: jasmine.SpyObj<ApiService>;

  const policy = [
    { name: 'k8s-worker2', ip: '192.168.148.155', mode: 'AUTO', vmClass: 'K8S_WORKER', restartRoute: 'CAMUNDA', notifications: 'EMAIL_AND_UI', powerState: 'RUNNING' },
    { name: 'k8s-worker1', ip: '192.168.148.158', mode: 'AUTO', vmClass: 'K8S_WORKER', restartRoute: 'CAMUNDA', notifications: 'EMAIL_AND_UI', powerState: 'RUNNING' },
    { name: 'k8s-lb2', ip: '192.168.148.162', mode: 'AUTO', vmClass: 'INFRA_LB', restartRoute: 'CAMUNDA', notifications: 'EMAIL_AND_UI', powerState: 'POWERED_OFF' },
    { name: 'k8s-monitoring', ip: '192.168.148.160', mode: 'DETECT_ONLY', vmClass: 'INFRA_MONITORING', restartRoute: 'NONE', notifications: 'UI_ONLY', powerState: 'UNKNOWN' },
    { name: 'k8s-master', ip: '192.168.148.153', mode: 'AUTO', vmClass: 'K8S_CONTROL_PLANE', restartRoute: 'CAMUNDA', notifications: 'EMAIL_AND_UI', powerState: 'RUNNING' },
  ];

  const health = {
    checkedAt: '2026-11-05T18:00:00',
    total: 2,
    up: 1,
    down: 1,
    components: [
      { namespace: 'infra', app: 'prometheus', up: false, status: 'DOWN', detail: 'Injoignable' },
      { namespace: 'k8s-nodes', app: 'k8s-master', up: true, status: 'UP', detail: 'Ready' },
    ],
    vmPolicy: policy,
  };

  beforeEach(() => {
    api = jasmine.createSpyObj<ApiService>('ApiService', ['getInfrastructureHealth']);
    TestBed.configureTestingModule({
      declarations: [InfrastructureComponent],
      providers: [{ provide: ApiService, useValue: api }],
    });
  });

  function create(response: any): void {
    api.getInfrastructureHealth.and.returnValue(response);
    fixture = TestBed.createComponent(InfrastructureComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  it('charge la politique et expose le mode de chaque VM (noms exacts)', () => {
    create(of(health));
    expect(component.vmPolicy.length).toBe(5);
    expect(component.modeOf('k8s-worker1')).toBe('AUTO');
    expect(component.modeOf('k8s-worker2')).toBe('AUTO');
    expect(component.modeOf('k8s-lb2')).toBe('AUTO');
    expect(component.modeOf('k8s-monitoring')).toBe('DETECT_ONLY');   // .160 : détection seule (consigne finale du 2026-11-06)
    expect(component.modeOf('k8s-master')).toBe('AUTO');              // .153 : AUTO depuis la consigne du 2026-11-17
    expect(component.modeOf('lb2')).toBe('');            // alias : hors politique
    expect(component.modeOf('K8S-WORKER1')).toBe('');    // casse différente : hors politique
  });

  it('seule .160 (supervision) est en détection seule ; quatre VM sont automatiques (.153 depuis le 2026-11-17)', () => {
    create(of(health));
    expect(component.isDetectionOnly('k8s-monitoring')).toBeTrue();
    for (const vm of ['k8s-worker1', 'k8s-worker2', 'k8s-lb2', 'k8s-master', 'inconnue']) {
      expect(component.isDetectionOnly(vm)).toBeFalse();
    }
    const auto = component.vmPolicy.filter(v => v.mode === 'AUTO').map(v => v.name).sort();
    expect(auto).toEqual(['k8s-lb2', 'k8s-master', 'k8s-worker1', 'k8s-worker2']);
    expect(component.modeOf('k8s-master')).toBe('AUTO');
  });

  it('affiche le redémarrage automatique de .155 .158 .162 .153, la détection seule de .160 et le libellé du mode DENY_RESTART conservé', () => {
    create(of(health));
    expect(component.modeLabel('AUTO')).toBe('Redémarrage automatique');
    expect(component.modeLabel('DENY_RESTART')).toBe('Aucun redémarrage automatique');
    expect(component.modeLabel('DETECT_ONLY')).toContain('DÉTECTION SEULE');
    const monitoring = component.vmPolicy.find(v => v.name === 'k8s-monitoring')!;
    expect(component.routeNote(monitoring)).toContain('interface seulement');
    expect(component.routeNote(monitoring)).toContain('ni e-mail');
    expect(component.routeNote(monitoring)).not.toContain('chemin direct');
    for (const vm of ['k8s-worker1', 'k8s-worker2', 'k8s-lb2', 'k8s-master']) {
      expect(component.routeNote(component.vmPolicy.find(v => v.name === vm)!)).toBe('');
    }
    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('DÉTECTION SEULE');
    expect(text).toContain('k8s-lb2');
    expect(text).toContain('k8s-monitoring');
    expect(text).not.toContain('chemin direct du backend');
  });

  it('libellés d\'alimentation : inconnu par défaut, jamais « allumée » sans preuve', () => {
    create(of(health));
    expect(component.powerLabel('RUNNING')).toBe('Allumée');
    expect(component.powerLabel('POWERED_OFF')).toBe('Éteinte');
    expect(component.powerLabel('UNKNOWN')).toBe('État inconnu');
    expect(component.powerLabel(undefined)).toBe('État inconnu');
  });

  it('connaît .160 et .162 dans les métadonnées de VM', () => {
    create(of(health));
    expect(component.getMeta('k8s-monitoring').ip).toBe('192.168.148.160');
    expect(component.getMeta('k8s-lb2').ip).toBe('192.168.148.162');
    expect(component.getMeta('k8s-master').ip).toBe('192.168.148.153');
  });

  it('une erreur HTTP est affichée et ne laisse pas des données périmées sans alerte', () => {
    create(throwError(() => new Error('HTTP 500')));
    expect(component.loading).toBeFalse();
    expect(component.loadError).toContain('périmées');
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('n\'a pas pu être chargé');
  });

  it('un chargement réussi efface l\'erreur précédente', () => {
    create(throwError(() => new Error('HTTP 500')));
    expect(component.loadError).not.toBe('');
    api.getInfrastructureHealth.and.returnValue(of(health));
    component.refresh();
    expect(component.loadError).toBe('');
    expect(component.vmPolicy.length).toBe(5);
  });

  it('reste compatible avec une ancienne réponse sans vmPolicy', () => {
    create(of({ ...health, vmPolicy: undefined }));
    expect(component.vmPolicy).toEqual([]);
    expect(component.modeOf('k8s-lb2')).toBe('');
    expect(component.loadError).toBe('');
  });
});
