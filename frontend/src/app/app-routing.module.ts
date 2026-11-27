import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';
import { AuthGuard } from './core/guards/auth.guard';
import { RoleGuard } from './core/guards/role.guard';
import { NoAuthGuard } from './core/guards/no-auth.guard';















import { PermissionGuard } from './core/guards/permission.guard';






const ADMIN_ROLES  = ['ADMIN', 'SUPER_ADMIN'];

const routes: Routes = [
  // Public — redirect to dashboard if already logged in
  { path: 'login',           loadComponent: () => import('./features/login/login.component').then(m => m.LoginComponent),          canActivate: [NoAuthGuard] },
  { path: 'register',        loadComponent: () => import('./features/register/register.component').then(m => m.RegisterComponent),       canActivate: [NoAuthGuard] },
  { path: 'forgot-password', loadComponent: () => import('./features/forgot-password/forgot-password.component').then(m => m.ForgotPasswordComponent), canActivate: [NoAuthGuard] },
  { path: 'reset-password',  loadComponent: () => import('./features/reset-password/reset-password.component').then(m => m.ResetPasswordComponent),  canActivate: [NoAuthGuard] },
  { path: 'activate',        loadComponent: () => import('./features/activate/activate.component').then(m => m.ActivateComponent) },

  { path: '', redirectTo: 'dashboard', pathMatch: 'full' },

  // All authenticated users
  { path: 'dashboard',      loadComponent: () => import('./features/dashboard/dashboard.component').then(m => m.DashboardComponent),     canActivate: [AuthGuard] },
  { path: 'incidents',      loadComponent: () => import('./features/incidents/incidents.component').then(m => m.IncidentsComponent),     canActivate: [AuthGuard] },
  { path: 'incidents/:id',  loadComponent: () => import('./features/incidents/incident-detail/incident-detail.component').then(m => m.IncidentDetailComponent), canActivate: [AuthGuard] },
  { path: 'applications',   loadComponent: () => import('./features/applications/applications.component').then(m => m.ApplicationsComponent),  canActivate: [AuthGuard] },
  { path: 'infrastructure', loadComponent: () => import('./features/infrastructure/infrastructure.component').then(m => m.InfrastructureComponent), canActivate: [AuthGuard] },

  // ADMIN + SUPER_ADMIN only
  { path: 'ai-decisions', loadComponent: () => import('./features/ai-decisions/ai-decisions.component').then(m => m.AiDecisionsComponent), canActivate: [AuthGuard, RoleGuard], data: { roles: ADMIN_ROLES } },
  { path: 'remediation',  loadComponent: () => import('./features/remediation/remediation.component').then(m => m.RemediationComponent),  canActivate: [AuthGuard, RoleGuard], data: { roles: ADMIN_ROLES } },
  { path: 'tasks',        loadComponent: () => import('./features/human-tasks/human-tasks.component').then(m => m.HumanTasksComponent),   canActivate: [AuthGuard, RoleGuard], data: { roles: ADMIN_ROLES } },
  { path: 'users',        loadComponent: () => import('./features/users/users.component').then(m => m.UsersComponent),        canActivate: [AuthGuard, RoleGuard], data: { roles: ADMIN_ROLES } },

  // Modules « organisation » : l'accès suit les permissions calculées par le Backend (matrice par rôle existant).
  // Ce garde n'affiche que ce qui est utile : chaque appel d'API est de toute façon contrôlé côté Backend.
  { path: 'maintenance', loadComponent: () => import('./features/maintenance/maintenance.component').then(m => m.MaintenanceComponent), canActivate: [AuthGuard, PermissionGuard], data: { permissions: ['MAINTENANCE_VIEW'] } },
  { path: 'incidents/:id/maintenance', loadComponent: () => import('./features/incident-maintenance/incident-maintenance.component').then(m => m.IncidentMaintenanceComponent), canActivate: [AuthGuard, PermissionGuard], data: { permissions: ['MAINTENANCE_VIEW'] } },
  { path: 'teams',       loadComponent: () => import('./features/teams/teams.component').then(m => m.TeamsComponent),       canActivate: [AuthGuard, PermissionGuard], data: { permissions: ['TEAMS_VIEW'] } },
  { path: 'team-tasks',  loadComponent: () => import('./features/team-tasks/team-tasks.component').then(m => m.TeamTasksComponent),   canActivate: [AuthGuard, PermissionGuard], data: { permissions: ['TASKS_VIEW'] } },
  { path: 'audit-trail', loadComponent: () => import('./features/audit-trail/audit-trail.component').then(m => m.AuditTrailComponent),  canActivate: [AuthGuard, PermissionGuard], data: { permissions: ['AUDIT_VIEW_OWN', 'AUDIT_VIEW_ALL'] } },
  { path: 'reports',     loadComponent: () => import('./features/reports/reports.component').then(m => m.ReportsComponent),     canActivate: [AuthGuard, PermissionGuard], data: { permissions: ['REPORTS_VIEW'] } },

  { path: '404', loadComponent: () => import('./shared/not-found/not-found.component').then(m => m.NotFoundComponent) },
  { path: '**',  loadComponent: () => import('./shared/not-found/not-found.component').then(m => m.NotFoundComponent) }
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule]
})
export class AppRoutingModule {}
