import { Injectable } from '@angular/core';
import { ActivatedRouteSnapshot, CanActivate, Router, UrlTree } from '@angular/router';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';
import { AuthService } from '../services/auth.service';
import { OrgPermissionsService } from '../services/org-permissions.service';
import { OrgPermission } from '../models/org.models';

/**
 * Protège une route par permission (route.data['permissions'] : au moins une est requise).
 * Rappel : ce garde n'est qu'un confort d'affichage — le Backend refuse de toute façon (403) les appels non autorisés.
 */
@Injectable({ providedIn: 'root' })
export class PermissionGuard implements CanActivate {

  constructor(private perms: OrgPermissionsService, private auth: AuthService, private router: Router) {}

  canActivate(route: ActivatedRouteSnapshot): Observable<boolean | UrlTree> {
    const needed = (route.data['permissions'] || []) as OrgPermission[];
    return this.perms.ensureLoaded().pipe(
      map(loaded => {
        if (!this.auth.isLoggedIn()) { return this.router.createUrlTree(['/login']); }
        // Permissions indisponibles (erreur réseau/serveur) : on n'ouvre pas la page, sans déconnecter l'utilisateur.
        if (!loaded) { return this.router.createUrlTree(['/dashboard']); }
        if (needed.length === 0 || this.perms.canAny(...needed)) { return true; }
        return this.router.createUrlTree(['/dashboard']);
      })
    );
  }
}
