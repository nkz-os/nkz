import { writeFileSync, mkdirSync } from 'fs';

// =============================================================================
// Tailwind CSS Preset Builder
// =============================================================================
// Generates a Tailwind preset that maps utility classes to --nkz-* CSS custom
// properties. Tailwind v3 only deep-nests colors; all other theme keys MUST be
// flat with the nkz- prefix baked into the key name. e.g.:
//   borderRadius: { 'nkz-sm': '...', 'nkz-md': '...' }  ← correct
//   borderRadius: { nkz: { sm: '...', md: '...' } }      ← broken (zero CSS)

const preset = `// @nekazari/design-tokens/tailwind — Tailwind CSS preset
// AUTO-GENERATED from tokens.config.ts. Do not edit directly.
// Maps Tailwind utility classes to --nkz-* CSS custom properties.
// Usage in tailwind.config.js: presets: [require('@nekazari/design-tokens/tailwind')]

module.exports = {
  theme: {
    extend: {
      // Colors — Tailwind v3 handles nesting here via flattenColorPalette
      colors: {
        nkz: {
          canvas: 'var(--nkz-color-canvas)',
          surface: 'rgb(var(--nkz-color-surface-rgb) / <alpha-value>)',
          'surface-raised': 'rgb(var(--nkz-color-surface-raised-rgb) / <alpha-value>)',
          'surface-sunken': 'rgb(var(--nkz-color-surface-sunken-rgb) / <alpha-value>)',
          'surface-inverse': 'rgb(var(--nkz-color-surface-inverse-rgb) / <alpha-value>)',
          border: 'var(--nkz-color-border)',
          'border-strong': 'var(--nkz-color-border-strong)',
          'text-primary': 'rgb(var(--nkz-color-text-primary-rgb) / <alpha-value>)',
          'text-secondary': 'rgb(var(--nkz-color-text-secondary-rgb) / <alpha-value>)',
          'text-muted': 'rgb(var(--nkz-color-text-muted-rgb) / <alpha-value>)',
          // 'muted' is a deprecated alias of 'text-muted' (canonical). The host
          // alone calls it that 531 times and no token ever answered, so muted
          // text has been rendering in the inherited colour. Prefer 'text-muted'.
          muted: 'rgb(var(--nkz-color-text-muted-rgb) / <alpha-value>)',
          // Alias de compatibilidad (D4). Introducidos por el reemplazo en lote del
          // commit ae33bca3 (2026-06-04), que cambió bg-gray-50/bg-gray-100 por esta
          // familia sin que existiera variable detrás: 311 usos sin pintar nada.
          // gray-100 #F3F4F6 ≈ surface-sunken #F5F5F4 · gray-50 #F9FAFB ≈ canvas #FAFAF9
          'bg': 'var(--nkz-color-canvas)',
          'bg-secondary': 'rgb(var(--nkz-color-surface-sunken-rgb) / <alpha-value>)',
          'bg-muted': 'rgb(var(--nkz-color-surface-sunken-rgb) / <alpha-value>)',
          'text-on-accent': 'rgb(var(--nkz-color-text-on-accent-rgb) / <alpha-value>)',
          'text-on-inverse': 'rgb(var(--nkz-color-text-on-inverse-rgb) / <alpha-value>)',
          accent: {
            base: 'rgb(var(--nkz-color-accent-base-rgb) / <alpha-value>)',
            soft: 'rgb(var(--nkz-color-accent-soft-rgb) / <alpha-value>)',
            // 'light' is a deprecated alias of 'soft' (canonical); kept so existing
            // module code using nkz-*-light still resolves. Prefer 'soft' in new code.
            light: 'rgb(var(--nkz-color-accent-soft-rgb) / <alpha-value>)',
            strong: 'rgb(var(--nkz-color-accent-strong-rgb) / <alpha-value>)',
          },
          success: 'rgb(var(--nkz-color-success-rgb) / <alpha-value>)',
          'success-soft': 'rgb(var(--nkz-color-success-soft-rgb) / <alpha-value>)',
          // 'success-light' is a deprecated alias of 'success-soft' (canonical); kept so
          // existing module code using nkz-success-light still resolves. Prefer '-soft' in new code.
          'success-light': 'rgb(var(--nkz-color-success-soft-rgb) / <alpha-value>)',
          'success-strong': 'rgb(var(--nkz-color-success-strong-rgb) / <alpha-value>)',
          warning: 'rgb(var(--nkz-color-warning-rgb) / <alpha-value>)',
          'warning-soft': 'rgb(var(--nkz-color-warning-soft-rgb) / <alpha-value>)',
          // 'warning-light' is a deprecated alias of 'warning-soft' (canonical); kept so
          // existing module code using nkz-warning-light still resolves. Prefer '-soft' in new code.
          'warning-light': 'rgb(var(--nkz-color-warning-soft-rgb) / <alpha-value>)',
          'warning-strong': 'rgb(var(--nkz-color-warning-strong-rgb) / <alpha-value>)',
          danger: 'rgb(var(--nkz-color-danger-rgb) / <alpha-value>)',
          'danger-soft': 'rgb(var(--nkz-color-danger-soft-rgb) / <alpha-value>)',
          // 'danger-light' is a deprecated alias of 'danger-soft' (canonical); kept so
          // existing module code using nkz-danger-light still resolves. Prefer '-soft' in new code.
          'danger-light': 'rgb(var(--nkz-color-danger-soft-rgb) / <alpha-value>)',
          'danger-strong': 'rgb(var(--nkz-color-danger-strong-rgb) / <alpha-value>)',
          info: 'rgb(var(--nkz-color-info-rgb) / <alpha-value>)',
          'info-soft': 'rgb(var(--nkz-color-info-soft-rgb) / <alpha-value>)',
          // 'info-light' is a deprecated alias of 'info-soft' (canonical); kept so
          // existing module code using nkz-info-light still resolves. Prefer '-soft' in new code.
          'info-light': 'rgb(var(--nkz-color-info-soft-rgb) / <alpha-value>)',
          'info-strong': 'rgb(var(--nkz-color-info-strong-rgb) / <alpha-value>)',
        },
      },

      // Border radius — FLAT keys (NOT nested under nkz:)
      borderRadius: {
        'nkz-xs': 'var(--nkz-radius-xs)',
        'nkz-sm': 'var(--nkz-radius-sm)',
        'nkz-md': 'var(--nkz-radius-md)',
        'nkz-lg': 'var(--nkz-radius-lg)',
        'nkz-xl': 'var(--nkz-radius-xl)',
        'nkz-2xl': 'var(--nkz-radius-2xl)',
        'nkz-full': 'var(--nkz-radius-full)',
      },

      // Box shadow — FLAT keys
      boxShadow: {
        'nkz-sm': 'var(--nkz-shadow-sm)',
        'nkz-md': 'var(--nkz-shadow-md)',
        'nkz-lg': 'var(--nkz-shadow-lg)',
        'nkz-xl': 'var(--nkz-shadow-xl)',
        'nkz-inset-highlight': 'var(--nkz-shadow-inset-highlight)',
      },

      // Spacing — FLAT keys so gap-nkz-stack, p-nkz-section etc. work
      spacing: {
        'nkz-tight': 'var(--nkz-space-tight)',
        'nkz-inline': 'var(--nkz-space-inline)',
        'nkz-stack': 'var(--nkz-space-stack)',
        'nkz-section': 'var(--nkz-space-section)',
      },

      // Z-index — FLAT keys
      zIndex: {
        'nkz-base': 'var(--nkz-z-base)',
        'nkz-map-overlay': 'var(--nkz-z-map-overlay)',
        'nkz-toolbar': 'var(--nkz-z-toolbar)',
        'nkz-rail': 'var(--nkz-z-rail)',
        'nkz-header': 'var(--nkz-z-header)',
        'nkz-popover': 'var(--nkz-z-popover)',
        'nkz-tooltip': 'var(--nkz-z-tooltip)',
        'nkz-modal': 'var(--nkz-z-modal)',
        'nkz-toast': 'var(--nkz-z-toast)',
        'nkz-loading': 'var(--nkz-z-loading)',
      },

      // Transition duration — FLAT keys
      transitionDuration: {
        'nkz-fast': 'var(--nkz-motion-fast)',
        'nkz-normal': 'var(--nkz-motion-normal)',
        'nkz-slow': 'var(--nkz-motion-slow)',
        'nkz-reduced': 'var(--nkz-motion-reduced)',
      },

      // Transition timing function — FLAT keys
      transitionTimingFunction: {
        'nkz-default': 'var(--nkz-motion-ease-default)',
        'nkz-spring': 'var(--nkz-motion-ease-spring)',
      },

      // Font size — FLAT keys with tuple [size, { lineHeight, letterSpacing, fontWeight }]
      fontSize: {
        'nkz-2xs': ['var(--nkz-type-2xs-size)', {
          lineHeight: 'var(--nkz-type-2xs-line-height)',
          letterSpacing: 'var(--nkz-type-2xs-letter-spacing)',
          fontWeight: 'var(--nkz-type-2xs-weight)',
        }],
        'nkz-xs': ['var(--nkz-type-xs-size)', {
          lineHeight: 'var(--nkz-type-xs-line-height)',
          letterSpacing: 'var(--nkz-type-xs-letter-spacing)',
          fontWeight: 'var(--nkz-type-xs-weight)',
        }],
        'nkz-sm': ['var(--nkz-type-sm-size)', {
          lineHeight: 'var(--nkz-type-sm-line-height)',
          letterSpacing: 'var(--nkz-type-sm-letter-spacing)',
          fontWeight: 'var(--nkz-type-sm-weight)',
        }],
        'nkz-base': ['var(--nkz-type-base-size)', {
          lineHeight: 'var(--nkz-type-base-line-height)',
          letterSpacing: 'var(--nkz-type-base-letter-spacing)',
          fontWeight: 'var(--nkz-type-base-weight)',
        }],
        'nkz-md': ['var(--nkz-type-md-size)', {
          lineHeight: 'var(--nkz-type-md-line-height)',
          letterSpacing: 'var(--nkz-type-md-letter-spacing)',
          fontWeight: 'var(--nkz-type-md-weight)',
        }],
        'nkz-lg': ['var(--nkz-type-lg-size)', {
          lineHeight: 'var(--nkz-type-lg-line-height)',
          letterSpacing: 'var(--nkz-type-lg-letter-spacing)',
          fontWeight: 'var(--nkz-type-lg-weight)',
        }],
        'nkz-xl': ['var(--nkz-type-xl-size)', {
          lineHeight: 'var(--nkz-type-xl-line-height)',
          letterSpacing: 'var(--nkz-type-xl-letter-spacing)',
          fontWeight: 'var(--nkz-type-xl-weight)',
        }],
        'nkz-2xl': ['var(--nkz-type-2xl-size)', {
          lineHeight: 'var(--nkz-type-2xl-line-height)',
          letterSpacing: 'var(--nkz-type-2xl-letter-spacing)',
          fontWeight: 'var(--nkz-type-2xl-weight)',
        }],
        'nkz-3xl': ['var(--nkz-type-3xl-size)', {
          lineHeight: 'var(--nkz-type-3xl-line-height)',
          letterSpacing: 'var(--nkz-type-3xl-letter-spacing)',
          fontWeight: 'var(--nkz-type-3xl-weight)',
        }],
      },
    },
  },
  plugins: [],
};
`;

mkdirSync('dist', { recursive: true });
writeFileSync('dist/tailwind-preset.js', preset.trimStart());
console.log('Generated dist/tailwind-preset.js');
