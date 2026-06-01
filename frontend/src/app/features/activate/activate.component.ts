import { Component, OnInit } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'app-activate',
  templateUrl: './activate.component.html'
})
export class ActivateComponent implements OnInit {
  token = '';
  loading = false;
  error = '';
  success = '';

  constructor(private api: ApiService, private route: ActivatedRoute, private router: Router) {}

  ngOnInit(): void {
    this.token = this.route.snapshot.queryParamMap.get('token') || '';
    if (this.token) {
      this.activate();
    }
  }

  activate(): void {
    if (!this.token) {
      this.error = 'Token manquant.';
      return;
    }
    this.loading = true;
    this.error = '';
    this.api.activateAccount(this.token).subscribe({
      next: (res) => {
        this.loading = false;
        this.success = res.message || 'Compte activé avec succès.';
      },
      error: (err) => {
        this.loading = false;
        this.error = err?.error?.error || 'Token invalide ou déjà utilisé.';
      }
    });
  }

  goLogin(): void { this.router.navigate(['/login']); }
}
