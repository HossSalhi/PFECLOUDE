import { ChangeDetectionStrategy, Component, OnInit, inject } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { SupervisionService } from '../../core/services/supervision.service';
import {
  CamundaIncident, CamundaIncidents, CamundaInstance, CamundaInstances, CamundaOverview, DiagramEdge, DiagramNode, ProcessDiagram
} from '../../core/models/supervision.models';

interface Block<T> { loading: boolean; data: T | null; error: string; }
const blank = <T>(): Block<T> => ({ loading: false, data: null, error: '' });

/** Une étape du diagramme prête à dessiner : couleur, lignes de texte et pastilles de compteurs calculées une fois. */
export interface DrawnNode {
  node: DiagramNode; lines: string[]; state: 'incident' | 'active' | 'done' | 'idle'; title: string;
}

export interface IncidentGroup { type: string; count: number; meaning: string; items: CamundaIncident[]; }

/**
 * Vue Camunda (tous les utilisateurs connectés, lecture seule) : état du moteur, DIAGRAMME du processus dessiné en SVG avec
 * les compteurs d'exécutions sur chaque étape, instances récentes avec leur durée, incidents regroupés par type. Aucune
 * bibliothèque supplémentaire : le serveur renvoie les formes et les flèches déjà placées.
 */
@Component({
  selector: 'app-camunda-panel',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.Eager,
  templateUrl: './camunda-panel.component.html',
  imports: [DatePipe, DecimalPipe]
})
export class CamundaPanelComponent implements OnInit {
  private api = inject(SupervisionService);

  readonly states = [{ id: '', label: 'Toutes' }, { id: 'ACTIVE', label: 'Actives' }, { id: 'COMPLETED', label: 'Terminées' }, { id: 'TERMINATED', label: 'Annulées' }];
  readonly zooms = [0.6, 0.8, 1, 1.25, 1.5];

  overview = blank<CamundaOverview>();
  instances = blank<CamundaInstances>();
  incidents = blank<CamundaIncidents>();
  diagram = blank<ProcessDiagram>();

  selectedKey = '';
  instanceState = '';
  zoom = 1;
  groups: IncidentGroup[] = [];
  drawn: DrawnNode[] = [];
  byId = new Map<string, DiagramNode>();

  ngOnInit(): void { this.refresh(); }

  refresh(): void {
    this.overview = { loading: true, data: null, error: '' };
    this.api.camundaOverview().subscribe({
      next: o => {
        this.overview = { loading: false, data: o, error: '' };
        if (o.available && o.definitions.length) {
          const preferred = o.definitions.find(d => d.id === 'helios-camunda-full-cycle') || o.definitions[0];
          if (!this.selectedKey || !o.definitions.some(d => d.key === this.selectedKey)) { this.selectedKey = preferred.key; }
          this.loadDiagram();
        }
      },
      error: () => this.overview = { loading: false, data: null, error: "Le serveur n'a pas pu répondre." }
    });
    this.loadInstances();
    this.incidents = { loading: true, data: null, error: '' };
    this.api.camundaIncidents(50).subscribe({
      next: i => { this.incidents = { loading: false, data: i, error: '' }; this.groups = this.group(i); },
      error: () => this.incidents = { loading: false, data: null, error: "Le serveur n'a pas pu répondre." }
    });
  }

  chooseProcess(key: string): void { this.selectedKey = key; this.loadDiagram(); }

  loadDiagram(): void {
    if (!this.selectedKey) { return; }
    this.diagram = { loading: true, data: null, error: '' };
    this.api.camundaDiagram(this.selectedKey).subscribe({
      next: d => { this.diagram = { loading: false, data: d, error: '' }; this.prepare(d); },
      error: () => this.diagram = { loading: false, data: null, error: "Le serveur n'a pas pu répondre." }
    });
  }

  setInstanceState(s: string): void { this.instanceState = s; this.loadInstances(); }

  loadInstances(): void {
    this.instances = { loading: true, data: null, error: '' };
    this.api.camundaInstances(this.instanceState, 15).subscribe({
      next: i => this.instances = { loading: false, data: i, error: '' },
      error: () => this.instances = { loading: false, data: null, error: "Le serveur n'a pas pu répondre." }
    });
  }

  setZoom(z: number): void { this.zoom = z; }
  zoomIn(): void { const i = this.zooms.indexOf(this.zoom); this.zoom = this.zooms[Math.min(this.zooms.length - 1, i + 1)]; }
  zoomOut(): void { const i = this.zooms.indexOf(this.zoom); this.zoom = this.zooms[Math.max(0, i - 1)]; }

  // ── Diagramme ──────────────────────────────────────────────────────────────

  private prepare(d: ProcessDiagram): void {
    this.byId = new Map(d.nodes.map(n => [n.id, n]));
    this.drawn = d.nodes.map(n => ({ node: n, lines: this.wrap(n.name, n.shape === 'GATEWAY' ? 14 : 20), state: this.stateOf(n), title: this.tip(n) }));
  }

  private stateOf(n: DiagramNode): DrawnNode['state'] {
    if (n.stats.incidents > 0) { return 'incident'; }
    if (n.stats.active > 0) { return 'active'; }
    return n.stats.completed > 0 ? 'done' : 'idle';
  }

  private tip(n: DiagramNode): string {
    const s = n.stats;
    return `${n.name} (${n.type}) — ${s.completed} terminée(s), ${s.active} en cours, ${s.canceled} annulée(s), ${s.incidents} incident(s)`;
  }

  /** Découpe un libellé en 3 lignes au plus, sur les espaces ; la dernière est tronquée avec « … ». */
  wrap(text: string, max: number): string[] {
    const words = (text || '').split(/\s+/).filter(Boolean);
    const lines: string[] = [];
    let cur = '';
    for (const w of words) {
      if ((cur + ' ' + w).trim().length <= max) { cur = (cur + ' ' + w).trim(); }
      else { if (cur) { lines.push(cur); } cur = w.length > max ? w.slice(0, max - 1) + '…' : w; }
    }
    if (cur) { lines.push(cur); }
    if (lines.length > 3) { const kept = lines.slice(0, 3); kept[2] = kept[2].slice(0, Math.max(1, max - 1)) + '…'; return kept; }
    return lines;
  }

  path(e: DiagramEdge): string {
    return e.points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ');
  }

  /** Position de l'étiquette d'une flèche : milieu du plus long segment. */
  labelAt(e: DiagramEdge): { x: number; y: number } {
    let best = { x: e.points[0].x, y: e.points[0].y }, bestLen = -1;
    for (let i = 1; i < e.points.length; i++) {
      const a = e.points[i - 1], b = e.points[i], len = Math.hypot(b.x - a.x, b.y - a.y);
      if (len > bestLen) { bestLen = len; best = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 5 }; }
    }
    return best;
  }

  diamond(n: DiagramNode): string {
    const cx = n.x + n.w / 2, cy = n.y + n.h / 2;
    return `${cx},${n.y} ${n.x + n.w},${cy} ${cx},${n.y + n.h} ${n.x},${cy}`;
  }

  get totals(): { completed: number; active: number; incidents: number } {
    const nodes = this.diagram.data?.nodes || [];
    return {
      completed: nodes.reduce((s, n) => s + n.stats.completed, 0), active: nodes.reduce((s, n) => s + n.stats.active, 0),
      incidents: nodes.reduce((s, n) => s + n.stats.incidents, 0)
    };
  }

  // ── Instances et incidents ─────────────────────────────────────────────────

  duration(ms: number | null): string {
    if (ms == null) { return '—'; }
    if (ms < 1000) { return `${ms} ms`; }
    const s = Math.round(ms / 1000);
    return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
  }

  /** Largeur (0-100 %) de la barre de durée, relative à l'instance la plus longue de la liste. */
  barWidth(i: CamundaInstance): number {
    const max = Math.max(1, ...(this.instances.data?.items || []).map(x => x.durationMs || 0));
    return Math.max(4, Math.round(((i.durationMs || 0) / max) * 100));
  }

  stateLabel(s: string): string {
    return { ACTIVE: 'Active', COMPLETED: 'Terminée', TERMINATED: 'Annulée' }[s] ?? s;
  }

  private group(i: CamundaIncidents): IncidentGroup[] {
    const meanings: Record<string, string> = {
      JOB_NO_RETRIES: "Une tâche automatique a épuisé ses tentatives : le travailleur n'a pas pu l'exécuter.",
      IO_MAPPING_ERROR: 'Les données entrantes ou sortantes de l’étape sont incorrectes.',
      CONDITION_ERROR: 'La condition d’une passerelle n’a pas pu être évaluée.',
      EXTRACT_VALUE_ERROR: 'Une valeur attendue est absente des données du processus.',
      UNHANDLED_ERROR_EVENT: 'Une erreur métier n’est interceptée par aucune étape.'
    };
    const map = new Map<string, CamundaIncident[]>();
    (i.items || []).forEach(x => map.set(x.errorType, [...(map.get(x.errorType) || []), x]));
    return [...map.entries()].map(([type, items]) => ({
      type, count: items.length, meaning: meanings[type] || 'Incident signalé par le moteur Camunda.', items
    })).sort((a, b) => b.count - a.count);
  }
}
