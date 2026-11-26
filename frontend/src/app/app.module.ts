import { NgModule } from '@angular/core';
import { BrowserModule } from '@angular/platform-browser';
import { HTTP_INTERCEPTORS, provideHttpClient, withInterceptorsFromDi, withXhr } from '@angular/common/http';
import { FormsModule, ReactiveFormsModule } from '@angular/forms';
import { NgChartsModule } from 'ng2-charts';

import { AppRoutingModule } from './app-routing.module';
import { AppComponent } from './app.component';
import { AuthInterceptor } from './core/interceptors/auth.interceptor';
import { NotFoundComponent } from './shared/not-found/not-found.component';
import { OrangeLogoComponent } from './shared/orange-logo/orange-logo.component';
import { LoginComponent } from './features/login/login.component';

import { DashboardComponent } from './features/dashboard/dashboard.component';
import { IncidentsComponent } from './features/incidents/incidents.component';
import { ApplicationsComponent } from './features/applications/applications.component';
import { InfrastructureComponent } from './features/infrastructure/infrastructure.component';
import { AiDecisionsComponent } from './features/ai-decisions/ai-decisions.component';
import { RemediationComponent } from './features/remediation/remediation.component';
import { HumanTasksComponent } from './features/human-tasks/human-tasks.component';
import { IncidentDetailComponent } from './features/incidents/incident-detail/incident-detail.component';
import { RegisterComponent } from './features/register/register.component';
import { ForgotPasswordComponent } from './features/forgot-password/forgot-password.component';
import { ResetPasswordComponent } from './features/reset-password/reset-password.component';
import { ActivateComponent } from './features/activate/activate.component';
import { UsersComponent } from './features/users/users.component';
import { MaintenanceComponent } from './features/maintenance/maintenance.component';
import { TeamsComponent } from './features/teams/teams.component';
import { TeamTasksComponent } from './features/team-tasks/team-tasks.component';
import { AuditTrailComponent } from './features/audit-trail/audit-trail.component';
import { ReportsComponent } from './features/reports/reports.component';
import { ReportViewComponent } from './features/reports/report-view.component';

@NgModule({ declarations: [
        AppComponent,
        LoginComponent,
        DashboardComponent,
        IncidentsComponent,
        ApplicationsComponent,
        InfrastructureComponent,
        AiDecisionsComponent,
        RemediationComponent,
        HumanTasksComponent,
        IncidentDetailComponent,
        RegisterComponent,
        ForgotPasswordComponent,
        ResetPasswordComponent,
        ActivateComponent,
        NotFoundComponent,
        UsersComponent,
        OrangeLogoComponent,
        MaintenanceComponent,
        TeamsComponent,
        TeamTasksComponent,
        AuditTrailComponent,
        ReportsComponent,
        ReportViewComponent
    ],
    bootstrap: [AppComponent], imports: [BrowserModule,
        FormsModule,
        ReactiveFormsModule,
        AppRoutingModule,
        NgChartsModule], providers: [
        { provide: HTTP_INTERCEPTORS, useClass: AuthInterceptor, multi: true },
        provideHttpClient(withXhr(), withInterceptorsFromDi())
    ] })
export class AppModule {}
