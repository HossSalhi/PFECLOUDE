import { HttpResponse } from '@angular/common/http';

/** Nom de fichier proposé par le Backend (en-tête Content-Disposition), ou {@code fallback}. */
export function filenameFrom(resp: HttpResponse<Blob>, fallback: string): string {
  const header = resp.headers.get('Content-Disposition') || '';
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header);
  return match ? decodeURIComponent(match[1]) : fallback;
}

/** Enregistre un fichier téléchargé (la requête passe par HttpClient : le jeton d'authentification est ajouté). */
export function saveBlob(resp: HttpResponse<Blob>, fallbackName: string): void {
  const blob = resp.body;
  if (!blob) { return; }
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filenameFrom(resp, fallbackName);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.setTimeout(() => window.URL.revokeObjectURL(url), 2000);
}
