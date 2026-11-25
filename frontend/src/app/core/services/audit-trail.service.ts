import { Injectable } from '@angular/core';
import { HttpClient, HttpResponse } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { toParams } from '../../shared/params.util';
import { AuditEntry, AuditMeta, AuditQuery, Page } from '../models/org.models';

/** Journal d'audit des activités (API {@code /api/audit-trail}) : consultation et export CSV, jamais d'écriture. */
@Injectable({ providedIn: 'root' })
export class AuditTrailService {

  private base = `${environment.apiBaseUrl}/api/audit-trail`;

  constructor(private http: HttpClient) {}

  private filters(q: AuditQuery) {
    return {
      username: q.username, module: q.module, action: q.action, result: q.result,
      from: q.from, to: q.to, q: q.q
    };
  }

  search(q: AuditQuery): Observable<Page<AuditEntry>> {
    return this.http.get<Page<AuditEntry>>(this.base, {
      params: toParams({ ...this.filters(q), page: q.page ?? 0, size: q.size ?? 25 })
    });
  }

  get(id: number): Observable<AuditEntry> {
    return this.http.get<AuditEntry>(`${this.base}/${id}`);
  }

  meta(): Observable<AuditMeta> {
    return this.http.get<AuditMeta>(`${this.base}/meta`);
  }

  exportCsv(q: AuditQuery): Observable<HttpResponse<Blob>> {
    return this.http.get(`${this.base}/export`, {
      params: toParams(this.filters(q)), responseType: 'blob', observe: 'response'
    });
  }
}
