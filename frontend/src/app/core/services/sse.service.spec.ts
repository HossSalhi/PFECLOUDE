import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { SseService } from './sse.service';
import { AuthService } from './auth.service';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: any; onerror: any; onmessage: any;
  constructor(public url: string, public opts?: any) { FakeEventSource.instances.push(this); }
  addEventListener(): void {}
  close(): void {}
}

describe('SseService (accès par ticket)', () => {
  let http: HttpTestingController;
  const realES = (window as any).EventSource;
  let loggedIn = true;

  function create(): SseService {
    TestBed.configureTestingModule({
    imports: [],
    providers: [{ provide: AuthService, useValue: { isLoggedIn: () => loggedIn } }, provideHttpClient(withInterceptorsFromDi()), provideHttpClientTesting()]
});
    http = TestBed.inject(HttpTestingController);
    return TestBed.inject(SseService);
  }

  beforeEach(() => { FakeEventSource.instances = []; (window as any).EventSource = FakeEventSource; loggedIn = true; });
  afterEach(() => { (window as any).EventSource = realES; });

  it("demande un ticket (cookie HttpOnly) puis ouvre le flux avec les cookies, sans ticket ni jeton dans l'URL", () => {
    const s = create();
    const req = http.expectOne(r => r.url.endsWith('/api/events/ticket'));
    expect(req.request.method).toBe('POST');
    expect(req.request.withCredentials).toBe(true);
    req.flush({ expiresInSeconds: 30 });
    expect(FakeEventSource.instances.length).toBe(1);
    expect(FakeEventSource.instances[0].url.endsWith('/api/events/stream')).toBe(true);
    expect(FakeEventSource.instances[0].url).not.toContain('ticket');
    expect(FakeEventSource.instances[0].opts).toEqual({ withCredentials: true });
    expect(FakeEventSource.instances[0].url).not.toContain('Bearer');
    s.ngOnDestroy();
  });

  it("n'ouvre aucune connexion et ne demande rien tant que l'utilisateur n'est pas connecté", () => {
    loggedIn = false;
    const s = create();
    http.expectNone(r => r.url.endsWith('/api/events/ticket'));
    expect(FakeEventSource.instances.length).toBe(0);
    s.ngOnDestroy();
  });

  it('en cas de refus du ticket, ne se connecte pas et signale la déconnexion', () => {
    const s = create();
    let state: boolean | undefined;
    s.onConnected().subscribe(v => state = v);
    http.expectOne(r => r.url.endsWith('/api/events/ticket')).flush('refus', { status: 403, statusText: 'Forbidden' });
    expect(FakeEventSource.instances.length).toBe(0);
    expect(state).toBe(false);
    s.ngOnDestroy();
  });
});