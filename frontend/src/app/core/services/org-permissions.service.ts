import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { catchError, map, tap, finalize, shareReplay } from 'rxjs/operators';
import { environment } from '../../../environments/environment';
import { AuthService } from './auth.service';
import { MyPermissions, OrgPermission } from '../models/org.models';

/**
 * Permissions de l'utilisateur connecté sur les modules « organisation ». Elles viennent du Backend
 * ({@code GET /api/org/permissions/me}) : l'interface s'y adapte, mais le contrôle réel reste côté Backend
 * (un appel direct à l'API sans le droit requis est refusé).
 *
 * Le cache est lié à l'identité connectée (nom d'utilisateur + rôle mémorisés à la connexion) : une autre connexion,
 * ou un changement de rôle constaté à la reconnexion, provoque un rechargement.
 */
@Injectable({ providedIn: 'root' })
export class OrgPermissionsService {

  private base = environment.apiBaseUrl;
  private granted = new Set<string>();
  private loadedKey: string | null = null;
  private inflight: Observable<MyPermissions> | null = null;

  constructor(private http: HttpClient, private auth: AuthService) {}

  /** Nom d'utilisateur de la session courante (null si non connecté). */
  currentUsername(): string | null {
    return this.auth.isLoggedIn() ? this.auth.getUsername() : null;
  }

  private currentKey(): string | null {
    if (!this.auth.isLoggedIn()) { return null; }
    return `${(this.auth.getUsername() || '').toLowerCase()}|${this.auth.getRole() || ''}`;
  }

  /** Vrai si l'utilisateur connecté dispose de la permission (faux tant qu'elles ne sont pas chargées). */
  can(permission: OrgPermission): boolean {
    const key = this.currentKey();
    return key !== null && key === this.loadedKey && this.granted.has(permission);
  }

  canAny(...permissions: OrgPermission[]): boolean {
    return permissions.some(p => this.can(p));
  }

  /** Charge les permissions si nécessaire (nouvel utilisateur, première navigation). Émet {@code true} si disponibles. */
  ensureLoaded(): Observable<boolean> {
    const key = this.currentKey();
    if (key === null) {
      this.clear();
      return of(false);
    }
    if (key === this.loadedKey) {
      return of(true);
    }
    if (!this.inflight) {
      this.inflight = this.http.get<MyPermissions>(`${this.base}/api/org/permissions/me`).pipe(
        tap(me => {
          this.granted = new Set<string>(me.permissions);
          this.loadedKey = key;
        }),
        finalize(() => { this.inflight = null; }),
        shareReplay(1)
      );
    }
    return this.inflight.pipe(
      map(() => true),
      catchError(() => {
        this.clear();
        return of(false);
      })
    );
  }

  clear(): void {
    this.granted = new Set<string>();
    this.loadedKey = null;
  }
}
