// Utilitaires de dates partagés par les modules « organisation ».
// Le Backend échange des LocalDateTime ISO SANS fuseau ("2026-11-25T10:00:00") : on les traite comme heure locale.

const pad = (n: number): string => String(n).padStart(2, '0');

/** Date/heure lisible (fr) : « 25/11/2026 10:30 », ou « — » si absente. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) { return '—'; }
  const d = new Date(value);
  if (isNaN(d.getTime())) { return value; }
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Date seule (fr) : « 25/11/2026 ». Accepte « 2026-11-25 » comme « 2026-11-25T10:00:00 ». */
export function formatDate(value: string | null | undefined): string {
  if (!value) { return '—'; }
  const d = new Date(value.length === 10 ? value + 'T00:00:00' : value);
  if (isNaN(d.getTime())) { return value; }
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
}

/** « 2026-11-25T10:30:00 » (heure locale, sans fuseau) pour les paramètres envoyés au Backend. */
export function toLocalIso(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Valeur d'un <input type="datetime-local"> : « 2026-11-25T10:30 ». */
export function toDatetimeLocalValue(iso: string | null | undefined): string {
  if (!iso) { return ''; }
  const d = new Date(iso);
  if (isNaN(d.getTime())) { return ''; }
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** « 2026-11-25 » (valeur d'un <input type="date">). */
export function toDateValue(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Début de journée locale pour une valeur <input type="date">, au format envoyé au Backend. */
export function startOfDayIso(dateValue: string): string {
  return `${dateValue}T00:00:00`;
}

/** Début du jour SUIVANT (borne de fin exclusive d'une période inclusive jusqu'à {@code dateValue}). */
export function endOfDayExclusiveIso(dateValue: string): string {
  const d = new Date(dateValue + 'T00:00:00');
  d.setDate(d.getDate() + 1);
  return `${toDateValue(d)}T00:00:00`;
}

/** Message d'erreur lisible à partir d'une erreur HTTP du Backend ({ error, status, … }). */
export function errorMessage(err: any, fallback = 'Une erreur est survenue.'): string {
  if (!err) { return fallback; }
  if (err.status === 403) { return "Action non autorisée pour votre rôle."; }
  const body = err.error;
  if (body && typeof body === 'object' && typeof body.error === 'string') { return body.error; }
  if (typeof body === 'string' && body.length > 0 && body.length < 300) { return body; }
  if (typeof err.message === 'string' && err.message.length > 0 && err.message.length < 300) { return err.message; }
  return fallback;
}
