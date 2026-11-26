import { Component, OnInit, OnDestroy, HostListener, ChangeDetectionStrategy } from '@angular/core';
import { Router, NavigationEnd } from '@angular/router';
import { Subscription, interval } from 'rxjs';
import { filter } from 'rxjs/operators';
import { SseService } from './core/services/sse.service';
import { AuthService } from './core/services/auth.service';
import { ApiService } from './core/services/api.service';
import { OrgPermissionsService } from './core/services/org-permissions.service';

export interface NavItem {
  label: string;
  icon: string;
  route: string;
  badge?: number;
}

export interface Toast {
  id: number;
  type: 'critical' | 'high' | 'info';
  title: string;
  message: string;
}

export interface AppNotif {
  id: number;
  title: string;
  severity: string;
  createdAt: string;
  read: boolean;
}

@Component({
    selector: 'app-root',
    templateUrl: './app.component.html',
    styleUrls: ['./app.component.scss'],
    changeDetection: ChangeDetectionStrategy.Eager,
    standalone: false
})
export class AppComponent implements OnInit, OnDestroy {
  currentTime = '';
  sseConnected = false;
  toasts: Toast[] = [];
  incidentBadge = 0;
  taskBadge = 0;
  // Dark theme
  isDark = false;
  // Notification center (email-triggering = CRITICAL/HIGH incidents)
  showNotifs = false;
  notifications: AppNotif[] = [];
  userName = 'Opérateur';
  userRole = 'Admin';
  userInitials = 'OP';
  private toastCounter = 0;
  private subs: Subscription[] = [];

  // Account settings dropdown/modal
  showUserMenu = false;
  showSettingsModal = false;
  settingsTab: 'password' | 'email' = 'password';

  // Change password form
  currentPassword = '';
  newPassword = '';
  confirmPassword = '';
  passwordLoading = false;
  passwordSuccess = '';
  passwordError = '';

  // Change email form
  emailCurrentPassword = '';
  newEmail = '';
  emailLoading = false;
  emailSuccess = '';
  emailError = '';

  navItems: NavItem[] = [
    { label: 'Dashboard',       icon: '📊', route: '/dashboard' },
    { label: 'Incidents',       icon: '🚨', route: '/incidents' },
    { label: 'Applications',    icon: '📱', route: '/applications' },
    { label: 'Infrastructure',  icon: '🖥️', route: '/infrastructure' },
    { label: 'Décisions de remédiation', icon: '⚖️', route: '/ai-decisions' },
    { label: 'Remediation',     icon: '🔧', route: '/remediation' },
    { label: 'Human Tasks',     icon: '👤', route: '/tasks' },
  ];

  constructor(
    public router: Router,
    private sseService: SseService,
    public authService: AuthService,
    private apiService: ApiService,
    public perms: OrgPermissionsService
  ) {}

  ngOnInit(): void {
    this.loadUserInfo();
    // Permissions des modules « organisation » (maintenance, équipes, tâches, audit, rapports) : menu adapté au rôle
    this.perms.ensureLoaded().subscribe();
    this.isDark = localStorage.getItem('helios_theme') === 'dark';
    this.applyTheme();
    this.refreshUserFromServer();
    this.seedNotifications();

    // Re-fetch user info on every navigation so switching accounts updates the navbar
    const navSub = this.router.events.pipe(
      filter(e => e instanceof NavigationEnd)
    ).subscribe(() => {
      if (this.authService.isLoggedIn()) {
        this.refreshUserFromServer();
        this.perms.ensureLoaded().subscribe();
      } else {
        this.loadUserInfo();
        this.perms.clear();
      }
    });
    this.subs.push(navSub);

    this.updateTime();
    const timeSub = interval(1000).subscribe(() => this.updateTime());
    this.subs.push(timeSub);

    const connSub = this.sseService.onConnected().subscribe(connected => {
      this.sseConnected = connected;
    });
    this.subs.push(connSub);

    const createdSub = this.sseService.onIncidentCreated().subscribe(incident => {
      const severity = (incident?.severity || '').toUpperCase();
      if (severity === 'CRITICAL' || severity === 'HIGH') {
        this.incidentBadge++;
        this.notifications.unshift({
          id: incident?.id ?? Date.now(),
          title: incident?.title || 'Nouvel incident détecté',
          severity,
          createdAt: incident?.createdAt || new Date().toISOString(),
          read: false
        });
        this.notifications = this.notifications.slice(0, 30);
        this.showToast(
          severity === 'CRITICAL' ? 'critical' : 'high',
          severity === 'CRITICAL' ? 'Incident Critique' : 'Incident Haute Sévérité',
          incident?.title || 'Nouvel incident détecté'
        );
      }
    });
    this.subs.push(createdSub);

    const updatedSub = this.sseService.onIncidentUpdated().subscribe(incident => {
      const severity = (incident?.severity || '').toUpperCase();
      if (severity === 'CRITICAL' || severity === 'HIGH') {
        this.showToast(
          severity === 'CRITICAL' ? 'critical' : 'high',
          'Incident Mis à Jour',
          incident?.title || 'Incident mis à jour'
        );
      }
    });
    this.subs.push(updatedSub);
  }

  private refreshUserFromServer(): void {
    if (!this.authService.isLoggedIn()) return;
    this.apiService.getMe().subscribe({
      next: (me: any) => {
        if (me?.firstName) localStorage.setItem('auth_first_name', me.firstName);
        if (me?.lastName)  localStorage.setItem('auth_last_name',  me.lastName);
        if (me?.username)  localStorage.setItem('auth_user', me.username);
        if (me?.role)      localStorage.setItem('auth_role', me.role);
        this.loadUserInfo();
      },
      error: () => {}
    });
  }

  private loadUserInfo(): void {
    const firstName = this.authService.getFirstName() || '';
    const lastName  = this.authService.getLastName()  || '';
    const username  = this.authService.getUsername()  || 'Opérateur';
    const role      = this.authService.getRole()      || 'ADMIN';

    this.userName  = firstName && lastName ? `${firstName} ${lastName}` : username;
    this.userRole  = role;

    if (firstName && lastName) {
      this.userInitials = (firstName[0] + lastName[0]).toUpperCase();
    } else if (firstName) {
      this.userInitials = firstName.substring(0, 2).toUpperCase();
    } else {
      this.userInitials = username.substring(0, 2).toUpperCase();
    }
  }

  logout(): void {
    this.showUserMenu = false;
    this.authService.logout();
  }

  toggleUserMenu(): void {
    this.showUserMenu = !this.showUserMenu;
  }

  openSettings(tab: 'password' | 'email' = 'password'): void {
    this.showUserMenu = false;
    this.settingsTab = tab;
    this.showSettingsModal = true;
    this.resetForms();
  }

  closeSettings(): void {
    this.showSettingsModal = false;
    this.resetForms();
  }

  private resetForms(): void {
    this.currentPassword = '';
    this.newPassword = '';
    this.confirmPassword = '';
    this.passwordLoading = false;
    this.passwordSuccess = '';
    this.passwordError = '';
    this.emailCurrentPassword = '';
    this.newEmail = '';
    this.emailLoading = false;
    this.emailSuccess = '';
    this.emailError = '';
  }

  submitChangePassword(): void {
    this.passwordError = '';
    this.passwordSuccess = '';
    if (this.newPassword !== this.confirmPassword) {
      this.passwordError = 'Les mots de passe ne correspondent pas.';
      return;
    }
    if (this.newPassword.length < 8) {
      this.passwordError = 'Le nouveau mot de passe doit contenir au moins 8 caractères.';
      return;
    }
    this.passwordLoading = true;
    this.apiService.changePassword(this.currentPassword, this.newPassword).subscribe({
      next: () => {
        this.passwordLoading = false;
        this.passwordSuccess = 'Mot de passe modifié avec succès.';
        this.currentPassword = '';
        this.newPassword = '';
        this.confirmPassword = '';
      },
      error: (err: any) => {
        this.passwordLoading = false;
        this.passwordError = err?.error?.error || 'Erreur lors de la modification.';
      }
    });
  }

  submitChangeEmail(): void {
    this.emailError = '';
    this.emailSuccess = '';
    if (!this.newEmail) {
      this.emailError = 'Veuillez saisir un nouvel email.';
      return;
    }
    this.emailLoading = true;
    this.apiService.changeEmail(this.emailCurrentPassword, this.newEmail).subscribe({
      next: (res: any) => {
        this.emailLoading = false;
        this.emailSuccess = 'Email modifié avec succès.';
        this.emailCurrentPassword = '';
        this.newEmail = '';
      },
      error: (err: any) => {
        this.emailLoading = false;
        this.emailError = err?.error?.error || 'Erreur lors de la modification.';
      }
    });
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.showUserMenu = false;
    this.showNotifs = false;
    if (this.showSettingsModal) this.closeSettings();
  }

  private showToast(type: 'critical' | 'high' | 'info', title: string, message: string): void {
    const id = ++this.toastCounter;
    const toast: Toast = { id, type, title, message };
    this.toasts = [toast, ...this.toasts].slice(0, 5);
    setTimeout(() => {
      this.toasts = this.toasts.filter(t => t.id !== id);
    }, 5000);
  }

  private updateTime(): void {
    const now = new Date();
    this.currentTime = now.toLocaleTimeString('fr-FR', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });
  }

  isActive(route: string): boolean {
    return this.router.url === route || this.router.url.startsWith(route + '/');
  }

  // ── Dark theme ──────────────────────────────────────────────────────────
  private applyTheme(): void {
    const root = document.documentElement;
    if (this.isDark) { root.classList.add('dark'); } else { root.classList.remove('dark'); }
  }

  toggleTheme(): void {
    this.isDark = !this.isDark;
    localStorage.setItem('helios_theme', this.isDark ? 'dark' : 'light');
    this.applyTheme();
  }

  // ── Notification center ─────────────────────────────────────────────────
  private seedNotifications(): void {
    if (!this.authService.isLoggedIn()) return;
    this.apiService.getIncidents(0, 20).subscribe({
      next: (page: any) => {
        const lastSeen = Number(localStorage.getItem('helios_notif_seen') || 0);
        this.notifications = (page?.content || [])
          .filter((i: any) => ['CRITICAL', 'HIGH'].includes((i.severity || '').toUpperCase()))
          .slice(0, 15)
          .map((i: any) => ({
            id: i.id,
            title: i.title,
            severity: (i.severity || '').toUpperCase(),
            createdAt: i.createdAt,
            read: new Date(i.createdAt).getTime() <= lastSeen
          }));
      },
      error: () => {}
    });
  }

  get unreadNotifCount(): number {
    return this.notifications.filter(n => !n.read).length;
  }

  toggleNotifs(event?: Event): void {
    if (event) { event.stopPropagation(); }
    this.showNotifs = !this.showNotifs;
    this.showUserMenu = false;
    if (this.showNotifs) {
      localStorage.setItem('helios_notif_seen', String(Date.now()));
      this.notifications = this.notifications.map(n => ({ ...n, read: true }));
    }
  }

  openNotif(n: AppNotif): void {
    this.showNotifs = false;
    this.router.navigate(['/incidents', n.id]);
  }

  notifTime(iso: string): string {
    if (!iso) return '';
    return new Date(iso).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }
}
