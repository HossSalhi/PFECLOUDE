import { Component } from '@angular/core';
import { Router } from '@angular/router';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'app-register',
  templateUrl: './register.component.html'
})
export class RegisterComponent {
  firstName = '';
  lastName = '';
  username = '';
  email = '';
  password = '';
  confirmPassword = '';
  showPassword = false;
  loading = false;
  error = '';
  success = '';

  constructor(private api: ApiService, protected router: Router) {}

  submit(): void {
    this.error = '';
    if (!this.firstName.trim() || !this.lastName.trim()) {
      this.error = 'Le prénom et le nom sont obligatoires.';
      return;
    }
    if (this.password !== this.confirmPassword) {
      this.error = 'Les mots de passe ne correspondent pas.';
      return;
    }
    if (this.password.length < 8) {
      this.error = 'Le mot de passe doit contenir au moins 8 caractères.';
      return;
    }
    this.loading = true;
    this.api.register(this.username, this.email, this.password, this.firstName.trim(), this.lastName.trim()).subscribe({
      next: (res) => {
        this.loading = false;
        this.success = res.message || 'Compte créé avec succès. Vérifiez votre email pour activer votre compte.';
      },
      error: (err) => {
        this.loading = false;
        this.error = err?.error?.error || 'Erreur lors de la création du compte.';
      }
    });
  }

  goLogin(): void { this.router.navigate(['/login']); }
}
