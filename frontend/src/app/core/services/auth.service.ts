import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { Observable, tap } from 'rxjs';
import { environment } from '../../../environments/environment';

export interface LoginRequest  { username: string; password: string; }
export interface TotpRequest   { tempToken: string; code: string; }
export interface LoginResponse {
  requires2fa:  boolean;
  tempToken:    string | null;
  accessToken:  string | null;
  refreshToken: string | null;
  username:     string | null;
  role:         string | null;
  firstName:    string | null;
  lastName:     string | null;
}

const ACCESS_KEY     = 'auth_access';
const REFRESH_KEY    = 'auth_refresh';
const USER_KEY       = 'auth_user';
const ROLE_KEY       = 'auth_role';
const FIRST_NAME_KEY = 'auth_first_name';
const LAST_NAME_KEY  = 'auth_last_name';

@Injectable({ providedIn: 'root' })
export class AuthService {

  private base = environment.apiBaseUrl;

  constructor(private http: HttpClient, private router: Router) {}

  login(req: LoginRequest): Observable<LoginResponse> {
    return this.http.post<LoginResponse>(`${this.base}/api/auth/login`, req).pipe(
      tap(res => {
        if (!res.requires2fa && res.accessToken) {
          this.storeTokens(res.accessToken, res.refreshToken, res.username, res.role, res.firstName, res.lastName);
        }
      })
    );
  }

  verify2fa(req: TotpRequest): Observable<LoginResponse> {
    return this.http.post<LoginResponse>(`${this.base}/api/auth/2fa/verify`, req).pipe(
      tap(res => {
        if (res.accessToken) {
          this.storeTokens(res.accessToken, res.refreshToken, res.username, res.role, res.firstName, res.lastName);
        }
      })
    );
  }

  logout(): void {
    const token = this.getAccessToken();
    if (token) {
      // Fire-and-forget — don't wait for the server
      this.http.post(`${this.base}/api/auth/logout`, {}).subscribe({ error: () => {} });
    }
    this.clearTokens();
    this.router.navigate(['/login']);
  }

  refreshToken(): Observable<{ accessToken: string }> {
    const refresh = localStorage.getItem(REFRESH_KEY) ?? '';
    return this.http.post<{ accessToken: string }>(
      `${this.base}/api/auth/refresh`, {},
      { headers: { Authorization: `Bearer ${refresh}` } }
    ).pipe(
      tap(res => localStorage.setItem(ACCESS_KEY, res.accessToken))
    );
  }

  isLoggedIn(): boolean {
    return !!this.getAccessToken();
  }

  getAccessToken(): string | null {
    return localStorage.getItem(ACCESS_KEY);
  }

  getUsername(): string | null {
    return localStorage.getItem(USER_KEY);
  }

  getRole(): string | null {
    return localStorage.getItem(ROLE_KEY);
  }

  getFirstName(): string | null {
    return localStorage.getItem(FIRST_NAME_KEY);
  }

  getLastName(): string | null {
    return localStorage.getItem(LAST_NAME_KEY);
  }

  isUser(): boolean      { return this.getRole() === 'USER'; }
  isAdmin(): boolean     { return this.getRole() === 'ADMIN'; }
  isSuperAdmin(): boolean { return this.getRole() === 'SUPER_ADMIN'; }
  isAtLeastAdmin(): boolean { return ['ADMIN', 'SUPER_ADMIN'].includes(this.getRole() || ''); }

  private storeTokens(access: string, refresh: string | null,
                      username: string | null, role: string | null,
                      firstName: string | null, lastName: string | null): void {
    localStorage.setItem(ACCESS_KEY, access);
    if (refresh)    localStorage.setItem(REFRESH_KEY, refresh);
    if (username)   localStorage.setItem(USER_KEY, username);
    if (role)       localStorage.setItem(ROLE_KEY, role);
    if (firstName)  localStorage.setItem(FIRST_NAME_KEY, firstName);
    if (lastName)   localStorage.setItem(LAST_NAME_KEY, lastName);
  }

  private clearTokens(): void {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(ROLE_KEY);
    localStorage.removeItem(FIRST_NAME_KEY);
    localStorage.removeItem(LAST_NAME_KEY);
  }
}
