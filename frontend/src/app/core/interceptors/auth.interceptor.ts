import { Injectable } from '@angular/core';
import { HttpRequest, HttpHandler, HttpEvent, HttpInterceptor, HttpErrorResponse } from '@angular/common/http';
import { Observable, throwError, BehaviorSubject, filter, take, switchMap } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { AuthService } from '../services/auth.service';
import { Router } from '@angular/router';

@Injectable()
export class AuthInterceptor implements HttpInterceptor {

  private refreshing = false;
  private refreshSubject = new BehaviorSubject<string | null>(null);

  // Paths that must NOT receive the Authorization header (login endpoints)
  private readonly publicPaths = ['/api/auth/login', '/api/auth/2fa/verify', '/api/auth/refresh'];

  constructor(private auth: AuthService, private router: Router) {}

  intercept(req: HttpRequest<unknown>, next: HttpHandler): Observable<HttpEvent<unknown>> {
    const isPublic = this.publicPaths.some(p => req.url.includes(p));
    const token    = this.auth.getAccessToken();

    const hasBody = ['POST', 'PUT', 'PATCH'].includes(req.method);
    const headers: { [key: string]: string } = {};

    if (!isPublic && token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    if (hasBody && !req.headers.has('Content-Type')) {
      headers['Content-Type'] = 'application/json';
    }

    const authReq = Object.keys(headers).length
      ? req.clone({ setHeaders: headers })
      : req;

    return next.handle(authReq).pipe(
      catchError((error: HttpErrorResponse) => {
        if (error.status === 401 && !isPublic) {
          return this.handle401(authReq, next);
        }
        // If the canary endpoint (/api/auth/me) is forbidden, our token is
        // dead even if Spring returned 403 instead of 401. Force-logout so
        // the user lands on /login instead of a frozen page.
        if (error.status === 403 && req.url.includes('/api/auth/me')) {
          this.auth.logout();
          return throwError(() => error);
        }
        if (error.status === 403) {
          console.warn('Access denied — insufficient permissions');
        } else if (error.status === 0) {
          console.error('Network error — backend unreachable');
        } else if (error.status >= 500) {
          console.error(`Server error ${error.status}:`, error.error?.error ?? error.message);
        }
        return throwError(() => error);
      })
    );
  }

  private handle401(req: HttpRequest<unknown>, next: HttpHandler): Observable<HttpEvent<unknown>> {
    if (this.refreshing) {
      // Queue behind the in-flight refresh
      return this.refreshSubject.pipe(
        filter(t => t !== null), take(1),
        switchMap(newToken => next.handle(this.addBearer(req, newToken!)))
      );
    }

    this.refreshing = true;
    this.refreshSubject.next(null);

    return this.auth.refreshToken().pipe(
      switchMap(res => {
        this.refreshing = false;
        this.refreshSubject.next(res.accessToken);
        return next.handle(this.addBearer(req, res.accessToken));
      }),
      catchError(err => {
        this.refreshing = false;
        this.auth.logout();
        return throwError(() => err);
      })
    );
  }

  private addBearer(req: HttpRequest<unknown>, token: string): HttpRequest<unknown> {
    return req.clone({ setHeaders: { Authorization: `Bearer ${token}` } });
  }
}
