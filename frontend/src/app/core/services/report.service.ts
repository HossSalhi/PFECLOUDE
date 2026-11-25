import { Injectable } from '@angular/core';
import { HttpClient, HttpResponse } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { toParams } from '../../shared/params.util';
import {
  GenerateReportRequest, Page, ReportContent, ReportDetail, ReportFrequency, ReportItem, ReportSchedule, ReportType,
  ReportTypeInfo
} from '../models/org.models';

/** Rapports (API {@code /api/reports}) : génération, consultation, statistiques, plannings, exports PDF et CSV. */
@Injectable({ providedIn: 'root' })
export class ReportService {

  private base = `${environment.apiBaseUrl}/api/reports`;

  constructor(private http: HttpClient) {}

  types(): Observable<ReportTypeInfo[]> {
    return this.http.get<ReportTypeInfo[]>(`${this.base}/types`);
  }

  list(type: ReportType | '' | undefined, page = 0, size = 20): Observable<Page<ReportItem>> {
    return this.http.get<Page<ReportItem>>(this.base, { params: toParams({ type, page, size }) });
  }

  get(id: number): Observable<ReportDetail> {
    return this.http.get<ReportDetail>(`${this.base}/${id}`);
  }

  generate(body: GenerateReportRequest): Observable<ReportItem> {
    return this.http.post<ReportItem>(`${this.base}/generate`, body);
  }

  /** Statistiques d'activité de la plateforme calculées à la demande (non enregistrées). */
  statistics(from: string, to: string, compare: boolean): Observable<ReportContent> {
    return this.http.get<ReportContent>(`${this.base}/statistics`, { params: toParams({ from, to, compare }) });
  }

  export(id: number, format: 'pdf' | 'csv'): Observable<HttpResponse<Blob>> {
    return this.http.get(`${this.base}/${id}/export`, {
      params: toParams({ format }), responseType: 'blob', observe: 'response'
    });
  }

  // ── Plannings ──────────────────────────────────────────────────────────────

  schedules(): Observable<ReportSchedule[]> {
    return this.http.get<ReportSchedule[]>(`${this.base}/schedules`);
  }

  createSchedule(type: ReportType, frequency: ReportFrequency): Observable<ReportSchedule> {
    return this.http.post<ReportSchedule>(`${this.base}/schedules`, { type, frequency, enabled: true });
  }

  toggleSchedule(id: number, enabled: boolean): Observable<ReportSchedule> {
    return this.http.patch<ReportSchedule>(`${this.base}/schedules/${id}`, { enabled });
  }

  deleteSchedule(id: number): Observable<void> {
    return this.http.delete<void>(`${this.base}/schedules/${id}`);
  }
}
