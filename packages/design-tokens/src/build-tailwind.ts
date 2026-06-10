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
          surface: 'var(--nkz-color-surface)',
          'surface-raised': 'var(--nkz-color-surface-raised)',
          'surface-sunken': 'var(--nkz-color-surface-sunken)',
          border: 'var(--nkz-color-border)',
          'border-strong': 'var(--nkz-color-border-strong)',
          'text-primary': 'var(--nkz-color-text-primary)',
          'text-secondary': 'var(--nkz-color-text-secondary)',
          'text-muted': 'var(--nkz-color-text-muted)',
          'text-on-accent': 'var(--nkz-color-text-on-accent)',
          accent: {
            base: 'var(--nkz-color-accent-base)',
            soft: 'var(--nkz-color-accent-soft)',
            strong: 'var(--nkz-color-accent-strong)',
          },
          success: 'var(--nkz-color-success)',
          'success-soft': 'var(--nkz-color-success-soft)',
          'success-strong': 'var(--nkz-color-success-strong)',
          warning: 'var(--nkz-color-warning)',
          'warning-soft': 'var(--nkz-color-warning-soft)',
          'warning-strong': 'var(--nkz-color-warning-strong)',
          danger: 'var(--nkz-color-danger)',
          'danger-soft': 'var(--nkz-color-danger-soft)',
          'danger-strong': 'var(--nkz-color-danger-strong)',
          info: 'var(--nkz-color-info)',
          'info-soft': 'var(--nkz-color-info-soft)',
          'info-strong': 'var(--nkz-color-info-strong)',
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
