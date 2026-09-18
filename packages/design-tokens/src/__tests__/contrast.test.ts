import { describe, it, expect } from 'vitest';
import { contrastRatio } from '../contrast';
import { profiles } from '../tokens.config';

// Se validan TODOS los perfiles, no solo los emitidos a CSS: `hmi` no llega al
// web (jsOnlyProfiles) pero sí lo consume la app móvil vía build-native.ts, y
// sus colores son hex opaco, así que son medibles. Dejarlo fuera sería cubrir
// el navegador y no el móvil.
const ALL_PROFILES = Object.keys(profiles) as (keyof typeof profiles)[];

// TRINQUETE DE FALLOS CONOCIDOS. Medidos el 2026-09-18 sobre el árbol actual.
// NO es una alfombra: cada entrada cita su defecto y su ratio. El suite queda
// verde, pero cualquier regresión NUEVA rompe al instante. Arreglar un defecto
// = borrar su entrada de aquí y ver el test pasar.
//   D17 — textMuted incumple AA (531 usos solo en el host)
//   D15 — `viewer` reutiliza los semánticos de `page`, afinados para fondo claro
const KNOWN_AA_FAILURES: Record<string, string[]> = {
  'page':         ['textMuted'],                                                  // 2.35:1 — D17
  'viewer-light': ['textMuted'],                                                  // 2.34:1 — D17
  'field':        ['textMuted'],                                                  // 4.40:1 — D17
  'viewer':       ['successStrong', 'warningStrong', 'dangerStrong', 'infoStrong'], // 2.18–2.91:1 — D15
};
const known = (p: string, t: string) => (KNOWN_AA_FAILURES[p] ?? []).includes(t);

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
      if (known(profile, token)) {
        // Fallo conocido: se ASERTA que sigue fallando. Si alguien lo arregla,
        // este test rompe y obliga a quitar la entrada del trinquete.
        expect(ratio, `${token}/${surface}: ¿arreglado? quita la entrada de KNOWN_AA_FAILURES`).toBeLessThan(4.5);
      } else {
        expect(ratio, `${token} sobre ${surface}`).toBeGreaterThanOrEqual(4.5);
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
    expect(contrastRatio(c['textOnAccent'], c['accentBase'])).toBeGreaterThanOrEqual(4.5);
  });
});
