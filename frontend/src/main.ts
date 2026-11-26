import { provideZoneChangeDetection, importProvidersFrom } from "@angular/core";


import { HTTP_INTERCEPTORS, provideHttpClient, withXhr, withInterceptorsFromDi } from "@angular/common/http";
import { AuthInterceptor } from "./app/core/interceptors/auth.interceptor";
import { BrowserModule, bootstrapApplication } from "@angular/platform-browser";
import { FormsModule, ReactiveFormsModule } from "@angular/forms";
import { AppRoutingModule } from "./app/app-routing.module";

import { AppComponent } from "./app/app.component";


bootstrapApplication(AppComponent, {
    providers: [
        importProvidersFrom(BrowserModule, FormsModule, ReactiveFormsModule, AppRoutingModule),
        { provide: HTTP_INTERCEPTORS, useClass: AuthInterceptor, multi: true },
        provideZoneChangeDetection(),
        provideHttpClient(withXhr(), withInterceptorsFromDi())
    ]
})
  .catch(err => console.error(err));
