import { Component, ChangeDetectionStrategy } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { AuthService } from '../../core/services/auth.service';
import { OrangeLogoComponent } from '../../shared/orange-logo/orange-logo.component';
import { FormsModule } from '@angular/forms';

@Component({
    selector: 'app-login',
    templateUrl: './login.component.html',
    changeDetection: ChangeDetectionStrategy.Eager,
    imports: [OrangeLogoComponent, FormsModule, RouterLink]
})
export class LoginComponent {
  username  = '';
  password  = '';
  totpCode  = '';
  tempToken = '';

  step: 'credentials' | '2fa' = 'credentials';
  loading      = false;
  error        = '';
  showPassword = false;
  qrCodeDataUri = '';

  constructor(private auth: AuthService, private router: Router) {}

  submitCredentials(): void {
    if (!this.username || !this.password) { return; }
    this.loading = true;
    this.error   = '';

    this.auth.login({ username: this.username, password: this.password }).subscribe({
      next: res => {
        this.loading = false;
        if (res.requires2fa && res.tempToken) {
          this.tempToken    = res.tempToken;
          this.qrCodeDataUri = (res as any).qrCodeDataUri || '';
          this.step = '2fa';
        } else {
          this.router.navigate(['/dashboard']);
        }
      },
      error: err => {
        this.loading = false;
        this.error = err?.error?.error ?? 'Login failed — check your credentials';
      }
    });
  }

  submitTotp(): void {
    if (!this.totpCode || this.totpCode.length !== 6) { return; }
    this.loading = true;
    this.error   = '';

    this.auth.verify2fa({ tempToken: this.tempToken, code: this.totpCode }).subscribe({
      next: () => {
        this.loading = false;
        this.router.navigate(['/dashboard']);
      },
      error: err => {
        this.loading = false;
        this.totpCode = '';
        this.error = err?.error?.error ?? 'Invalid code — try again';
      }
    });
  }

  backToCredentials(): void {
    this.step = 'credentials';
    this.tempToken = '';
    this.totpCode  = '';
    this.error     = '';
  }
}
