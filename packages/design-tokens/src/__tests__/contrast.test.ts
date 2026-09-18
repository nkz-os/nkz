import { describe, it, expect } from 'vitest';
import { contrastRatio } from '../contrast';
import { profiles } from '../tokens.config';

// Se validan TODOS los perfiles, no solo los emitidos a CSS: `hmi` no llega al
// web (jsOnlyProfiles) pero sí lo consume la app móvil vía build-native.ts, y
// sus colores son hex opaco, así que son medibles. Dejarlo fuera sería cubrir
// el navegador y no el móvil.
const ALL_PROFILES = Object.keys(profiles) as (keyof typeof profiles)[];

// TRINQUETE DE FALLOS CONOCIDOS. Medidos el 2026-09-18 sobre el árbol actual.
// Claves por PAR "token/fondo" (no solo token): un token puede cumplir AA
// contra una superficie y fallar contra otra — ver D17/field abajo, donde
// `surface` y `surfaceRaised` SÍ cumplen y solo `surfaceSunken` falla.
// NO es una alfombra: cada entrada cita su defecto y su ratio. El suite queda
// verde, pero cualquier regresión NUEVA rompe al instante. Arreglar un defecto
// = borrar su(s) entrada(s) de aquí y ver el test pasar.
//
//   D17 — textMuted incumple AA (531 usos solo en el host). `field` cumple AA
//   contra `surface`/`surfaceRaised` (4.7976:1); solo falla contra
//   `surfaceSunken` (4.3977:1). `page` y `viewer-light` fallan en las tres.
//
//   D15 — `viewer` reutiliza los semánticos de `page`, afinados para fondo claro.
//
//   D18 — RESUELTO 2026-09-18: `accentBase`/`accentStrong` se oscurecieron un
//   paso (emerald-700/800); textOnAccent/accentBase ya cumple AA en los
//   cuatro perfiles y se retiró de este trinquete.
const KNOWN_AA_FAILURES: Record<string, string[]> = {
  'page': [
    'textMuted/surface',        // 2.5629:1 — D17
    'textMuted/surfaceRaised',  // 2.5629:1 — D17
    'textMuted/surfaceSunken',  // 2.3493:1 — D17
  ],
  'viewer-light': [
    'textMuted/surface',        // 2.4506:1 — D17
    'textMuted/surfaceRaised',  // 2.5640:1 — D17
    'textMuted/surfaceSunken',  // 2.3405:1 — D17
  ],
  'field': [
    'textMuted/surfaceSunken',  // 4.3977:1 — D17 (surface/surfaceRaised cumplen AA: 4.7976:1)
  ],
  'viewer': [
    'successStrong/surface',        // 3.2554:1 — D15
    'successStrong/surfaceRaised',  // 2.6676:1 — D15
    'successStrong/surfaceSunken',  // 3.6785:1 — D15
    'warningStrong/surface',        // 3.5551:1 — D15
    'warningStrong/surfaceRaised',  // 2.9131:1 — D15
    'warningStrong/surfaceSunken',  // 4.0172:1 — D15
    'dangerStrong/surface',         // 2.7593:1 — D15
    'dangerStrong/surfaceRaised',   // 2.2610:1 — D15
    'dangerStrong/surfaceSunken',   // 3.1179:1 — D15
    'infoStrong/surface',           // 2.6639:1 — D15
    'infoStrong/surfaceRaised',     // 2.1829:1 — D15
    'infoStrong/surfaceSunken',     // 3.0101:1 — D15
  ],
};
const known = (p: string, pair: string) => (KNOWN_AA_FAILURES[p] ?? []).includes(pair);

describe('contrastRatio', () => {
  it('da 21 para negro sobre blanco', () => {
    expect(contrastRatio('#000000', '#FFFFFF')).toBeCloseTo(21, 1);
  });
  it('da 1 para un color consigo mismo', () => {
    expect(contrastRatio('#10B981', '#10B981')).toBeCloseTo(1, 5);
  });
});

// Solo superficies opacas: `canvas` es `transparent` en viewer/viewer-light y
// no se puede medir contraste contra ella.
const SURFACES = ['surface', 'surfaceRaised', 'surfaceSunken'] as const;
const TEXT_TOKENS = ['textPrimary', 'textSecondary', 'textMuted'] as const;
const STRONG = ['successStrong', 'warningStrong', 'dangerStrong', 'infoStrong'] as const;

describe.each(ALL_PROFILES)('perfil %s', (profile) => {
  const c = profiles[profile].colors as Record<string, string>;

  it.each([...TEXT_TOKENS, ...STRONG])('%s alcanza AA 4.5:1 sobre todas las superficies', (token) => {
    for (const surface of SURFACES) {
      const ratio = contrastRatio(c[token], c[surface]);
      const pair = `${token}/${surface}`;
      if (known(profile, pair)) {
        // Fallo conocido: se ASERTA que sigue fallando. Si alguien lo arregla,
        // este test rompe y obliga a quitar la entrada del trinquete.
        expect(ratio, `${pair}: ¿arreglado? quita la entrada de KNOWN_AA_FAILURES`).toBeLessThan(4.5);
      } else {
        expect(ratio, pair).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it.each([
    ['successStrong', 'successSoft'],
    ['warningStrong', 'warningSoft'],
    ['dangerStrong', 'dangerSoft'],
    ['infoStrong', 'infoSoft'],
  ])('%s alcanza AA 4.5:1 sobre %s', (fg, bg) => {
    expect(contrastRatio(c[fg], c[bg])).toBeGreaterThanOrEqual(4.5);
  });

  it('textOnAccent alcanza AA 4.5:1 sobre accentBase', () => {
    const ratio = contrastRatio(c['textOnAccent'], c['accentBase']);
    const pair = 'textOnAccent/accentBase';
    if (known(profile, pair)) {
      // Fallo conocido: se ASERTA que sigue fallando. Si alguien lo arregla,
      // este test rompe y obliga a quitar la entrada del trinquete.
      expect(ratio, `${pair}: ¿arreglado? quita la entrada de KNOWN_AA_FAILURES`).toBeLessThan(4.5);
    } else {
      expect(ratio, pair).toBeGreaterThanOrEqual(4.5);
    }
  });
});
