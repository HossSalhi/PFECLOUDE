import { Component, OnInit, ChangeDetectionStrategy } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { ApiService } from '../../core/services/api.service';
import { FormsModule } from '@angular/forms';

@Component({
    selector: 'app-reset-password',
    templateUrl: './reset-password.component.html',
    changeDetection: ChangeDetectionStrategy.Eager,
    imports: [FormsModule]
})
export class ResetPasswordComponent implements OnInit {
  token = '';
  newPassword = '';
  confirmPassword = '';
  showPassword = false;
  loading = false;
  error = '';
  success = '';

  constructor(private api: ApiService, private route: ActivatedRoute, private router: Router) {}

  ngOnInit(): void {
    this.token = this.route.snapshot.queryParamMap.get('token') || '';
  }

  submit(): void {
    this.error = '';
    if (this.newPassword !== this.confirmPassword) {
      this.error = 'Les mots de passe ne correspondent pas.';
      return;
    }
    if (this.newPassword.length < 8) {
      this.error = 'Le mot de passe doit contenir au moins 8 caractères.';
      return;
    }
    this.loading = true;
    this.api.resetPassword(this.token, this.newPassword).subscribe({
      next: (res) => {
        this.loading = false;
        this.success = res.message || 'Mot de passe réinitialisé.';
      },
      error: (err) => {
        this.loading = false;
        this.error = err?.error?.error || 'Token invalide ou expiré.';
      }
    });
  }

  goLogin(): void { this.router.navigate(['/login']); }
}
