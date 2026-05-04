// tokens.config.ts — canonical token values for ALL profiles
// Build scripts read this to generate CSS, JS objects, and Tailwind preset.

export type TokenProfile = 'page' | 'viewer' | 'viewer-light' | 'field' | 'hmi';

export interface TokenColors {
  canvas: string;
  surface: string;
  surfaceRaised: string;
  surfaceSunken: string;
  border: string;
  borderStrong: string;
  textPrimary: string;
  textSecondary: string;
  textMuted: string;
  textOnAccent: string;
  accentBase: string;
  accentSoft: string;
  accentStrong: string;
  success: string;
  successSoft: string;
  successStrong: string;
  warning: string;
  warningSoft: string;
  warningStrong: string;
  danger: string;
  dangerSoft: string;
  dangerStrong: string;
  info: string;
  infoSoft: string;
  infoStrong: string;
}

export interface TokenType {
  size: string;
  lineHeight: string;
  letterSpacing: string;
  fontWeight: string;
}

export interface TokenRadii {
  xs: string;
  sm: string;
  md: string;
  lg: string;
  xl: string;
  '2xl': string;
  full: string;
}

export interface TokenShadows {
  sm: string;
  md: string;
  lg: string;
  xl: string;
  insetHighlight?: string;
}

export interface TokenMotion {
  fast: string;
  normal: string;
  slow: string;
  easeDefault: string;
  easeSpring: string;
  reduced: string;
}

export interface TokenZIndex {
  base: number;
  mapOverlay: number;
  toolbar: number;
  rail: number;
  header: number;
  popover: number;
  tooltip: number;
  modal: number;
  toast: number;
  loading: number;
}

export interface TokenSpace {
  tight: string;
  inline: string;
  stack: string;
  section: string;
}

export interface TokenProfileDefinition {
  colors: TokenColors;
  type: Record<'2xs' | 'xs' | 'sm' | 'base' | 'md' | 'lg' | 'xl' | '2xl' | '3xl', TokenType>;
  radii: TokenRadii;
  shadows: TokenShadows;
  motion: TokenMotion;
  zIndex: TokenZIndex;
  space: TokenSpace;
  glass: string;
}

// Type scale shared across profiles (size varies per profile)
const typeScalePage = {
  '2xs': { size: '11px', lineHeight: '14px', letterSpacing: '0', fontWeight: '400' },
  'xs':  { size: '12px', lineHeight: '16px', letterSpacing: '0', fontWeight: '400' },
  'sm':  { size: '13px', lineHeight: '20px', letterSpacing: '0', fontWeight: '400' },
  'base':{ size: '14px', lineHeight: '22px', letterSpacing: '0', fontWeight: '400' },
  'md':  { size: '16px', lineHeight: '24px', letterSpacing: '0', fontWeight: '500' },
  'lg':  { size: '18px', lineHeight: '26px', letterSpacing: '-0.011em', fontWeight: '600' },
  'xl':  { size: '22px', lineHeight: '30px', letterSpacing: '-0.011em', fontWeight: '600' },
  '2xl': { size: '28px', lineHeight: '36px', letterSpacing: '-0.011em', fontWeight: '700' },
  '3xl': { size: '36px', lineHeight: '44px', letterSpacing: '-0.011em', fontWeight: '700' },
};

const typeScaleViewer = {
  ...typeScalePage,
  'base': { size: '13px', lineHeight: '20px', letterSpacing: '0', fontWeight: '400' },
};

const typeScaleField = {
  '2xs': { size: '13px', lineHeight: '16px', letterSpacing: '0', fontWeight: '400' },
  'xs':  { size: '14px', lineHeight: '18px', letterSpacing: '0', fontWeight: '400' },
  'sm':  { size: '15px', lineHeight: '22px', letterSpacing: '0', fontWeight: '400' },
  'base':{ size: '16px', lineHeight: '24px', letterSpacing: '0', fontWeight: '500' },
  'md':  { size: '18px', lineHeight: '26px', letterSpacing: '0', fontWeight: '600' },
  'lg':  { size: '22px', lineHeight: '28px', letterSpacing: '-0.011em', fontWeight: '600' },
  'xl':  { size: '26px', lineHeight: '32px', letterSpacing: '-0.011em', fontWeight: '700' },
  '2xl': { size: '32px', lineHeight: '40px', letterSpacing: '-0.011em', fontWeight: '700' },
  '3xl': { size: '40px', lineHeight: '48px', letterSpacing: '-0.011em', fontWeight: '700' },
};

const typeScaleHmi = {
  '2xs': { size: '14px', lineHeight: '18px', letterSpacing: '0.04em', fontWeight: '500' },
  'xs':  { size: '16px', lineHeight: '20px', letterSpacing: '0.04em', fontWeight: '500' },
  'sm':  { size: '18px', lineHeight: '24px', letterSpacing: '0.04em', fontWeight: '600' },
  'base':{ size: '20px', lineHeight: '28px', letterSpacing: '0.02em', fontWeight: '600' },
  'md':  { size: '24px', lineHeight: '32px', letterSpacing: '0.01em', fontWeight: '700' },
  'lg':  { size: '28px', lineHeight: '36px', letterSpacing: '0', fontWeight: '700' },
  'xl':  { size: '34px', lineHeight: '42px', letterSpacing: '0', fontWeight: '700' },
  '2xl': { size: '42px', lineHeight: '52px', letterSpacing: '0', fontWeight: '700' },
  '3xl': { size: '52px', lineHeight: '64px', letterSpacing: '0', fontWeight: '700' },
};

const radii: TokenRadii = {
  xs: '4px', sm: '6px', md: '8px', lg: '12px', xl: '16px', '2xl': '20px', full: '9999px',
};

const radiiField: TokenRadii = {
  xs: '6px', sm: '8px', md: '12px', lg: '16px', xl: '20px', '2xl': '24px', full: '9999px',
};

const space: TokenSpace = {
  tight: '4px', inline: '8px', stack: '12px', section: '24px',
};

const spaceHmi: TokenSpace = {
  tight: '8px', inline: '16px', stack: '24px', section: '48px',
};

const zIndex: TokenZIndex = {
  base: 0, mapOverlay: 10, toolbar: 20, rail: 30,
  header: 40, popover: 50, tooltip: 100, modal: 1000, toast: 2000, loading: 3000,
};

const motion: TokenMotion = {
  fast: '120ms', normal: '200ms', slow: '320ms',
  easeDefault: 'cubic-bezier(0.4, 0, 0.2, 1)',
  easeSpring: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
  reduced: '0ms',
};

const motionHmi: TokenMotion = {
  fast: '80ms', normal: '120ms', slow: '200ms',
  easeDefault: 'ease', easeSpring: 'ease', reduced: '0ms',
};

const semanticColors = {
  success: '#10B981', successSoft: '#D1FAE5', successStrong: '#059669',
  warning: '#F59E0B', warningSoft: '#FEF3C7', warningStrong: '#D97706',
  danger:  '#EF4444', dangerSoft: '#FEE2E2', dangerStrong: '#DC2626',
  info:    '#3B82F6', infoSoft: '#DBEAFE',   infoStrong: '#2563EB',
};

const semanticColorsField = {
  success: '#059669', successSoft: '#D1FAE5', successStrong: '#047857',
  warning: '#D97706', warningSoft: '#FEF3C7', warningStrong: '#B45309',
  danger:  '#DC2626', dangerSoft: '#FEE2E2',  dangerStrong: '#B91C1C',
  info:    '#2563EB', infoSoft: '#DBEAFE',    infoStrong: '#1D4ED8',
};

const semanticColorsHmi = {
  success: '#34D399', successSoft: '#064E3B', successStrong: '#6EE7B7',
  warning: '#FBBF24', warningSoft: '#78350F', warningStrong: '#FDE68A',
  danger:  '#F87171', dangerSoft: '#7F1D1D',  dangerStrong: '#FCA5A5',
  info:    '#60A5FA', infoSoft: '#1E3A5F',    infoStrong: '#93C5FD',
};

const defaultAccent = { accentBase: '#10B981', accentSoft: '#D1FAE5', accentStrong: '#059669' };

const shadowsPage = {
  sm: '0 1px 2px rgba(0,0,0,0.04)',
  md: '0 2px 8px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)',
  lg: '0 8px 24px rgba(0,0,0,0.08), 0 2px 6px rgba(0,0,0,0.04)',
  xl: '0 24px 60px rgba(0,0,0,0.12)',
};

const shadowsViewer = {
  sm: '0 1px 3px rgba(0,0,0,0.45)',
  md: '0 4px 14px rgba(0,0,0,0.55), 0 1px 3px rgba(0,0,0,0.4)',
  lg: '0 16px 40px rgba(0,0,0,0.65), 0 4px 12px rgba(0,0,0,0.4)',
  xl: '0 32px 80px rgba(0,0,0,0.75)',
  insetHighlight: 'inset 0 1px 0 rgba(255,255,255,0.06)',
};

const shadowsField = {
  sm: '0 1px 3px rgba(0,0,0,0.12)',
  md: '0 4px 12px rgba(0,0,0,0.16)',
  lg: '0 12px 32px rgba(0,0,0,0.20)',
  xl: '0 24px 48px rgba(0,0,0,0.28)',
};

const shadowsHmi = {
  sm: '0 2px 4px rgba(0,0,0,0.6)',
  md: '0 4px 12px rgba(0,0,0,0.7)',
  lg: '0 8px 24px rgba(0,0,0,0.8)',
  xl: '0 16px 48px rgba(0,0,0,0.9)',
};

export const profiles: Record<TokenProfile, TokenProfileDefinition> = {
  page: {
    colors: {
      canvas: '#FAFAF9',
      surface: '#FFFFFF',
      surfaceRaised: '#FFFFFF',
      surfaceSunken: '#F5F5F4',
      border: '#E7E5E4',
      borderStrong: '#D6D3D1',
      textPrimary: '#18181B',
      textSecondary: '#52525B',
      textMuted: '#A1A1AA',
      textOnAccent: '#FFFFFF',
      ...defaultAccent,
      ...semanticColors,
    },
    type: typeScalePage,
    radii,
    shadows: shadowsPage,
    motion, zIndex, space,
    glass: 'none',
  },

  viewer: {
    colors: {
      canvas: 'transparent',
      surface: 'rgba(15, 23, 42, 0.78)',
      surfaceRaised: 'rgba(30, 41, 59, 0.88)',
      surfaceSunken: 'rgba(51, 65, 85, 0.55)',
      border: 'rgba(148, 163, 184, 0.14)',
      borderStrong: 'rgba(148, 163, 184, 0.28)',
      textPrimary: '#F8FAFC',
      textSecondary: '#CBD5E1',
      textMuted: '#94A3B8',
      textOnAccent: '#FFFFFF',
      ...defaultAccent,
      ...semanticColors,
    },
    type: typeScaleViewer,
    radii,
    shadows: shadowsViewer,
    motion, zIndex, space,
    glass: 'backdrop-filter: blur(12px) saturate(180%); border: 1px solid var(--nkz-color-border);',
  },

  'viewer-light': {
    colors: {
      canvas: 'transparent',
      surface: 'rgba(248, 250, 252, 0.85)',
      surfaceRaised: 'rgba(255, 255, 255, 0.92)',
      surfaceSunken: 'rgba(241, 245, 249, 0.7)',
      border: 'rgba(15, 23, 42, 0.10)',
      borderStrong: 'rgba(15, 23, 42, 0.18)',
      textPrimary: '#0F172A',
      textSecondary: '#475569',
      textMuted: '#94A3B8',
      textOnAccent: '#FFFFFF',
      ...defaultAccent,
      ...semanticColors,
    },
    type: typeScaleViewer,
    radii,
    shadows: shadowsPage,
    motion, zIndex, space,
    glass: 'backdrop-filter: blur(12px) saturate(180%); border: 1px solid var(--nkz-color-border);',
  },

  field: {
    colors: {
      canvas: '#FFFFFF',
      surface: '#FFFFFF',
      surfaceRaised: '#FFFFFF',
      surfaceSunken: '#F5F5F4',
      border: '#D6D3D1',
      borderStrong: '#A8A29E',
      textPrimary: '#0C0A09',
      textSecondary: '#44403C',
      textMuted: '#78716C',
      textOnAccent: '#FFFFFF',
      ...defaultAccent,
      ...semanticColorsField,
    },
    type: typeScaleField,
    radii: radiiField,
    shadows: shadowsField,
    motion: { ...motion, normal: '150ms', slow: '250ms' },
    zIndex, space,
    glass: 'none',
  },

  hmi: {
    colors: {
      canvas: '#0F0F0F',
      surface: '#1A1A1A',
      surfaceRaised: '#242424',
      surfaceSunken: '#0A0A0A',
      border: '#333333',
      borderStrong: '#525252',
      textPrimary: '#FDE68A',
      textSecondary: '#FBBF24',
      textMuted: '#D97706',
      textOnAccent: '#0F0F0F',
      accentBase: '#F59E0B',
      accentSoft: '#78350F',
      accentStrong: '#FBBF24',
      ...semanticColorsHmi,
    },
    type: typeScaleHmi,
    radii,
    shadows: shadowsHmi,
    motion: motionHmi,
    zIndex, space: spaceHmi,
    glass: 'none',
  },
};

export const cssProfiles: TokenProfile[] = ['page', 'viewer', 'viewer-light', 'field'];
export const jsOnlyProfiles: TokenProfile[] = ['hmi'];
