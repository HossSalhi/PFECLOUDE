import { Component } from '@angular/core';
import { Router } from '@angular/router';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'app-forgot-password',
  templateUrl: './forgot-password.component.html'
})
export class ForgotPasswordComponent {
  email = '';
  loading = false;
  error = '';
  success = '';
  resetToken = '';

  constructor(private api: ApiService, public router: Router) {}

  submit(): void {
    this.error = '';
    this.loading = true;
    this.api.forgotPassword(this.email).subscribe({
      next: (res) => {
        this.loading = false;
        this.success = res.message || 'Email envoyé.';
        this.resetToken = res.resetToken || '';
      },
      error: () => {
        this.loading = false;
        this.error = 'Une erreur est survenue. Veuillez réessayer.';
      }
    });
  }

  goLogin(): void { this.router.navigate(['/login']); }
}
