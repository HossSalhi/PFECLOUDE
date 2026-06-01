import { NgModule } from '@angular/core';
import { BrowserModule } from '@angular/platform-browser';
import { HttpClientModule, HTTP_INTERCEPTORS } from '@angular/common/http';
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
import { FineTuningComponent } from './features/fine-tuning/fine-tuning.component';
import { IncidentDetailComponent } from './features/incidents/incident-detail/incident-detail.component';
import { RegisterComponent } from './features/register/register.component';
import { ForgotPasswordComponent } from './features/forgot-password/forgot-password.component';
import { ResetPasswordComponent } from './features/reset-password/reset-password.component';
import { ActivateComponent } from './features/activate/activate.component';
import { UsersComponent } from './features/users/users.component';

@NgModule({
  declarations: [
    AppComponent,
    LoginComponent,
    DashboardComponent,
    IncidentsComponent,
    ApplicationsComponent,
    InfrastructureComponent,
    AiDecisionsComponent,
    RemediationComponent,
    HumanTasksComponent,
    FineTuningComponent,
    IncidentDetailComponent,
    RegisterComponent,
    ForgotPasswordComponent,
    ResetPasswordComponent,
    ActivateComponent,
    NotFoundComponent,
    UsersComponent,
    OrangeLogoComponent
  ],
  imports: [
    BrowserModule,
    HttpClientModule,
    FormsModule,
    ReactiveFormsModule,
    AppRoutingModule,
    NgChartsModule
  ],
  providers: [
    { provide: HTTP_INTERCEPTORS, useClass: AuthInterceptor, multi: true }
  ],
  bootstrap: [AppComponent]
})
export class AppModule {}
