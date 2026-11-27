import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AiCardComponent } from './ai-card.component';
import { narrative } from './ai-test.util';

describe('AiCardComponent', () => {
  let fixture: ComponentFixture<AiCardComponent>;
  let component: AiCardComponent;
  const el = () => fixture.nativeElement as HTMLElement;
  const text = () => el().textContent!.replace(/\s+/g, ' ');

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [AiCardComponent] });
    fixture = TestBed.createComponent(AiCardComponent);
    component = fixture.componentInstance;
  });

  it("montre les chiffres du Backend avant l'analyse IA, clairement distincte", () => {
    component.facts = [{ label: 'En retard', value: '3', tone: 'bad' }, { label: 'Avancement', value: '60 %', tone: 'good' }];
    component.narrative = narrative();
    fixture.detectChanges();

    const facts = el().querySelectorAll('.ai-fact');
    expect(facts.length).toBe(2);
    expect(facts[0].getAttribute('data-tone')).toBe('bad');
    expect(facts[0].textContent).toContain('En retard');
    expect(el().querySelector('.ai-badge')!.textContent).toContain('Analyse IA');
    expect(el().querySelector('.ai-text')!.textContent).toContain('Synthèse de test.');
    // l'analyse vient après les chiffres
    const html = el().innerHTML;
    expect(html.indexOf('ai-facts')).toBeLessThan(html.indexOf('ai-analysis'));
    expect(text()).toContain("l'IA ne décide ni n'exécute rien");
    expect(text()).toContain('modèle test-model');
  });

  it("le bouton propose Générer puis Régénérer et émet la demande", () => {
    component.runLabel = 'Résumé IA';
    fixture.detectChanges();
    const run = jasmine.createSpy('run');
    component.run.subscribe(run);

    const button = el().querySelector('button.ai-run') as HTMLButtonElement;
    expect(button.textContent).toContain('Résumé IA');
    button.click();
    expect(run).toHaveBeenCalledTimes(1);

    component.done = true;
    fixture.detectChanges();
    expect(el().querySelector('button.ai-run')!.textContent).toContain('Régénérer');
  });

  it("affiche un état de chargement et bloque le bouton pendant l'analyse", () => {
    component.loading = true;
    fixture.detectChanges();

    const button = el().querySelector('button.ai-run') as HTMLButtonElement;
    expect(button.disabled).toBeTrue();
    expect(text()).toContain('Analyse en cours');
    expect(el().querySelector('.ai-spin')).not.toBeNull();
    expect(el().querySelector('section')!.getAttribute('aria-busy')).toBe('true');
  });

  it("indique la fraîcheur de la génération", () => {
    component.generatedAt = new Date(2026, 10, 27, 9, 5, 7);
    fixture.detectChanges();

    expect(el().querySelector('.ai-fresh')!.textContent).toContain('09:05:07');
  });

  it("une IA indisponible ou non configurée garde les chiffres et explique la raison", () => {
    component.facts = [{ label: 'Tâches', value: '10', tone: 'neutral' }];
    component.narrative = narrative({ status: 'NOT_CONFIGURED', message: "L'assistance IA n'est pas configurée.", text: null, model: null });
    fixture.detectChanges();

    expect(el().querySelectorAll('.ai-fact').length).toBe(1);
    expect(el().querySelector('.ai-analysis.is-off')).not.toBeNull();
    expect(text()).toContain("n'est pas configurée");
    expect(text()).toContain('Les chiffres ci-dessus restent valables');
    expect(el().querySelector('.ai-text.is-ok')).toBeNull();

    component.narrative = narrative({ status: 'UNAVAILABLE', message: 'Le quota du fournisseur IA est dépassé.', text: null });
    fixture.detectChanges();
    expect(text()).toContain('quota');
  });

  it("désactivée : message discret ; sans demande (NONE) : aucun bloc IA", () => {
    component.narrative = narrative({ status: 'DISABLED', text: null });
    fixture.detectChanges();
    expect(text()).toContain('Assistance IA désactivée');

    component.narrative = narrative({ status: 'NONE', text: null });
    fixture.detectChanges();
    expect(el().querySelector('.ai-analysis')).toBeNull();
  });

  it("affiche une erreur et un avis sans casser la carte", () => {
    component.error = "Le serveur n'a pas pu répondre.";
    component.notice = 'Clé API manquante.';
    fixture.detectChanges();

    expect(el().querySelector('.alert-error')!.textContent).toContain("n'a pas pu répondre");
    expect(el().querySelector('.alert-warning')!.textContent).toContain('Clé API');
    expect(el().querySelector('button.ai-run')).not.toBeNull();
  });

  it("n'interprète jamais le texte généré comme du HTML", () => {
    component.narrative = narrative({ text: '<img src=x onerror=alert(1)> bonjour' });
    fixture.detectChanges();

    expect(el().querySelector('img')).toBeNull();
    expect(el().querySelector('.ai-text')!.textContent).toContain('<img src=x');
  });
});
