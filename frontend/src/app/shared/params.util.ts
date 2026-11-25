import { HttpParams } from '@angular/common/http';

type ParamValue = string | number | boolean | null | undefined;

/** Paramètres de requête : les valeurs vides (null, undefined, '', false) sont omises. */
export function toParams(values: Record<string, ParamValue>): HttpParams {
  let params = new HttpParams();
  Object.keys(values).forEach(key => {
    const v = values[key];
    if (v === null || v === undefined || v === '' || v === false) { return; }
    params = params.set(key, String(v));
  });
  return params;
}
