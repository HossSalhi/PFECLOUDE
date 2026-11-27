import { Component, OnInit } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Observable } from 'rxjs';
import { IncidentRoutingService } from '../../core/services/incident-routing.service';
import { ROUTING_STATE_LABELS, RoutingDecision, RoutingView } from '../../core/models/org.models';

/**
 * Page ouverte depuis l'e-mail envoyé au responsable d'équipe : incident survenu pendant une maintenance en cours.
 * Aucune action n'est exécutée à l'ouverture : « continuer », « suspendre » et « reprendre » exigent un clic de
 * confirmation (le paramètre {@code ?action=} ne fait que mettre le bouton concerné en avant).
 */
@Component({
  selector: 'app-incident-maintenance',
  standalone: true,
  templateUrl: './incident-maintenance.component.html',
  imports: [RouterLink]
})
export class IncidentMaintenanceComponent implements OnInit {
  incidentId = 0;
  highlight: string | null = null;
  view: RoutingView | null = null;
  loading = true;
  busy = false;
  error: string | null = null;
  readonly stateLabels = ROUTING_STATE_LABELS;

  constructor(private route: ActivatedRoute, private routing: IncidentRoutingService) {}

  ngOnInit(): void {
    this.incidentId = Number(this.route.snapshot.paramMap.get('id'));
    this.highlight = this.route.snapshot.queryParamMap.get('action');
    this.load();
  }

  load(): void {
    this.loading = true;
    this.error = null;
    this.routing.view(this.incidentId).subscribe({
      next: v => { this.view = v; this.loading = false; },
      error: e => { this.error = this.message(e); this.loading = false; }
    });
  }

  decide(decision: RoutingDecision): void {
    if (!this.view?.maintenance) { return; }
    this.run(this.routing.decide(this.incidentId, this.view.maintenance.id, decision));
  }

  resume(): void {
    if (!this.view?.maintenance) { return; }
    this.run(this.routing.resume(this.incidentId, this.view.maintenance.id));
  }

  private run(call: Observable<RoutingView>): void {
    this.busy = true;
    this.error = null;
    call.subscribe({
      next: v => { this.view = v; this.busy = false; },
      error: e => { this.error = this.message(e); this.busy = false; }
    });
  }

  private message(e: any): string {
    return e?.error?.message || e?.error?.error || (e?.status === 403 ? 'Action non autorisée.' : 'Une erreur est survenue.');
  }
}
