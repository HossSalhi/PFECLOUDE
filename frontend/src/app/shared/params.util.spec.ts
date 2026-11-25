import { toParams } from './params.util';

describe('params.util', () => {

  it('omet les valeurs vides, nulles, indéfinies ou fausses', () => {
    const params = toParams({ a: 'x', b: '', c: null, d: undefined, e: false, f: 0, g: true });
    expect(params.keys().sort()).toEqual(['a', 'f', 'g']);
    expect(params.get('a')).toBe('x');
    expect(params.get('f')).toBe('0');
    expect(params.get('g')).toBe('true');
  });

  it('conserve les caractères spéciaux pour un encodage correct par HttpClient', () => {
    expect(toParams({ q: 'a&b=c' }).get('q')).toBe('a&b=c');
  });
});
