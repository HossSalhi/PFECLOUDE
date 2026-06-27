import { Component, OnInit, OnDestroy } from '@angular/core';
import { Subscription } from 'rxjs';
import { ApiService, Incident } from '../../core/services/api.service';
import { SseService } from '../../core/services/sse.service';

/**
 * "Remédiations Échouées" — dans le modèle 100% autonome, l'humain n'intervient QUE
 * sur les incidents que HELIOS n'a pas pu réparer (statut RESOLUTION_FAILED). Cette
 * page est le bureau de l'opérateur : la liste de ce qu'il doit reprendre à la main
 * (hors HELIOS). Plus de tâches d'approbation (supprimées avec le mode autonome).
 */
@Component({
  selector: 'app-human-tasks',
  templateUrl: './human-tasks.component.html'
})
export class HumanTasksComponent implements OnInit, OnDestroy {
  failedIncidents: Incident[] = [];
  loading = true;
  private subs: Subscription[] = [];

  constructor(private api: ApiService, private sse: SseService) {}

  ngOnInit(): void {
    this.load();
    // Temps réel : un incident peut basculer en RESOLUTION_FAILED après l'échec de la remédiation auto.
    this.subs.push(this.sse.onIncidentCreated().subscribe(() => this.load()));
    this.subs.push(this.sse.onIncidentUpdated().subscribe(() => this.load()));
  }

  load(): void {
    this.loading = true;
    this.subs.push(
      this.api.getIncidents(0, 100, 'RESOLUTION_FAILED').subscribe({
        next: res => { this.failedIncidents = res?.content || []; this.loading = false; },
        error: () => { this.loading = false; }
      })
    );
  }

  trackById(_: number, inc: Incident): number { return inc.id; }
  sevClass(s: string): string { return (s || '').toLowerCase(); }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }
}
