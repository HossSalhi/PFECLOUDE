import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { toParams } from '../../shared/params.util';
import {
  Maintenance, MaintenanceHistoryEntry, MaintenanceQuery, MaintenanceRequest, MaintenanceResource, Page
} from '../models/org.models';

/** Gestion de la maintenance (API {@code /api/maintenance}). Module déclaratif : aucune action sur la supervision. */
@Injectable({ providedIn: 'root' })
export class MaintenanceService {

  private base = `${environment.apiBaseUrl}/api/maintenance`;

  constructor(private http: HttpClient) {}

  list(q: MaintenanceQuery): Observable<Page<Maintenance>> {
    return this.http.get<Page<Maintenance>>(this.base, { params: toParams({
      status: q.status, resourceType: q.resourceType, resource: q.resource, responsibleId: q.responsibleId,
      from: q.from, to: q.to, q: q.q, page: q.page ?? 0, size: q.size ?? 25
    }) });
  }

  calendar(from: string, to: string): Observable<Maintenance[]> {
    return this.http.get<Maintenance[]>(`${this.base}/calendar`, { params: toParams({ from, to }) });
  }

  inProgress(): Observable<Maintenance[]> {
    return this.http.get<Maintenance[]>(`${this.base}/active`);
  }

  resources(): Observable<MaintenanceResource[]> {
    return this.http.get<MaintenanceResource[]>(`${this.base}/resources`);
  }

  get(id: number): Observable<Maintenance> {
    return this.http.get<Maintenance>(`${this.base}/${id}`);
  }

  history(id: number): Observable<MaintenanceHistoryEntry[]> {
    return this.http.get<MaintenanceHistoryEntry[]>(`${this.base}/${id}/history`);
  }

  create(body: MaintenanceRequest): Observable<Maintenance> {
    return this.http.post<Maintenance>(this.base, body);
  }

  update(id: number, body: MaintenanceRequest): Observable<Maintenance> {
    return this.http.put<Maintenance>(`${this.base}/${id}`, body);
  }

  start(id: number): Observable<Maintenance> {
    return this.http.post<Maintenance>(`${this.base}/${id}/start`, {});
  }

  complete(id: number): Observable<Maintenance> {
    return this.http.post<Maintenance>(`${this.base}/${id}/complete`, {});
  }

  cancel(id: number, reason: string | null): Observable<Maintenance> {
    return this.http.post<Maintenance>(`${this.base}/${id}/cancel`, { reason });
  }

  remove(id: number): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }
}
