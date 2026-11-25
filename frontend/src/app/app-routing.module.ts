import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';
import { AuthGuard } from './core/guards/auth.guard';
import { RoleGuard } from './core/guards/role.guard';
import { NoAuthGuard } from './core/guards/no-auth.guard';
import { LoginComponent } from './features/login/login.component';
import { DashboardComponent } from './features/dashboard/dashboard.component';
import { IncidentsComponent } from './features/incidents/incidents.component';
import { ApplicationsComponent } from './features/applications/applications.component';
import { InfrastructureComponent } from './features/infrastructure/infrastructure.component';
import { AiDecisionsComponent } from './features/ai-decisions/ai-decisions.component';
import { RemediationComponent } from './features/remediation/remediation.component';
import { HumanTasksComponent } from './features/human-tasks/human-tasks.component';
import { FineTuningComponent } from './features/fine-tuning/fine-tuning.component';
import { IncidentDetailComponent } from './features/incidents/incident-detail/incident-detail.component';
import { NotFoundComponent } from './shared/not-found/not-found.component';
import { RegisterComponent } from './features/register/register.component';
import { ForgotPasswordComponent } from './features/forgot-password/forgot-password.component';
import { ResetPasswordComponent } from './features/reset-password/reset-password.component';
import { ActivateComponent } from './features/activate/activate.component';
import { UsersComponent } from './features/users/users.component';
import { PermissionGuard } from './core/guards/permission.guard';
import { MaintenanceComponent } from './features/maintenance/maintenance.component';
import { TeamsComponent } from './features/teams/teams.component';
import { TeamTasksComponent } from './features/team-tasks/team-tasks.component';
import { AuditTrailComponent } from './features/audit-trail/audit-trail.component';
import { ReportsComponent } from './features/reports/reports.component';

const ADMIN_ROLES  = ['ADMIN', 'SUPER_ADMIN'];
const SUPER_ONLY   = ['SUPER_ADMIN'];

const routes: Routes = [
  // Public — redirect to dashboard if already logged in
  { path: 'login',           component: LoginComponent,          canActivate: [NoAuthGuard] },
  { path: 'register',        component: RegisterComponent,       canActivate: [NoAuthGuard] },
  { path: 'forgot-password', component: ForgotPasswordComponent, canActivate: [NoAuthGuard] },
  { path: 'reset-password',  component: ResetPasswordComponent,  canActivate: [NoAuthGuard] },
  { path: 'activate',        component: ActivateComponent },

  { path: '', redirectTo: 'dashboard', pathMatch: 'full' },

  // All authenticated users
  { path: 'dashboard',      component: DashboardComponent,     canActivate: [AuthGuard] },
  { path: 'incidents',      component: IncidentsComponent,     canActivate: [AuthGuard] },
  { path: 'incidents/:id',  component: IncidentDetailComponent, canActivate: [AuthGuard] },
  { path: 'applications',   component: ApplicationsComponent,  canActivate: [AuthGuard] },
  { path: 'infrastructure', component: InfrastructureComponent, canActivate: [AuthGuard] },

  // ADMIN + SUPER_ADMIN only
  { path: 'ai-decisions', component: AiDecisionsComponent, canActivate: [AuthGuard, RoleGuard], data: { roles: ADMIN_ROLES } },
  { path: 'remediation',  component: RemediationComponent,  canActivate: [AuthGuard, RoleGuard], data: { roles: ADMIN_ROLES } },
  { path: 'tasks',        component: HumanTasksComponent,   canActivate: [AuthGuard, RoleGuard], data: { roles: ADMIN_ROLES } },
  { path: 'users',        component: UsersComponent,        canActivate: [AuthGuard, RoleGuard], data: { roles: ADMIN_ROLES } },

  // Modules « organisation » : l'accès suit les permissions calculées par le Backend (matrice par rôle existant).
  // Ce garde n'affiche que ce qui est utile : chaque appel d'API est de toute façon contrôlé côté Backend.
  { path: 'maintenance', component: MaintenanceComponent, canActivate: [AuthGuard, PermissionGuard], data: { permissions: ['MAINTENANCE_VIEW'] } },
  { path: 'teams',       component: TeamsComponent,       canActivate: [AuthGuard, PermissionGuard], data: { permissions: ['TEAMS_VIEW'] } },
  { path: 'team-tasks',  component: TeamTasksComponent,   canActivate: [AuthGuard, PermissionGuard], data: { permissions: ['TASKS_VIEW'] } },
  { path: 'audit-trail', component: AuditTrailComponent,  canActivate: [AuthGuard, PermissionGuard], data: { permissions: ['AUDIT_VIEW_OWN', 'AUDIT_VIEW_ALL'] } },
  { path: 'reports',     component: ReportsComponent,     canActivate: [AuthGuard, PermissionGuard], data: { permissions: ['REPORTS_VIEW'] } },

  // SUPER_ADMIN only
  { path: 'fine-tuning', component: FineTuningComponent, canActivate: [AuthGuard, RoleGuard], data: { roles: SUPER_ONLY } },

  { path: '404', component: NotFoundComponent },
  { path: '**',  component: NotFoundComponent }
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule]
})
export class AppRoutingModule {}
