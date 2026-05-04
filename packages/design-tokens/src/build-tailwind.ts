import { writeFileSync, mkdirSync } from 'fs';

const preset = `// @nekazari/design-tokens/tailwind — Tailwind CSS preset
// Auto-generated. Maps Tailwind utility classes to --nkz-* CSS custom properties.

module.exports = {
  theme: {
    extend: {
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
      borderRadius: {
        nkz: {
          xs: 'var(--nkz-radius-xs)',
          sm: 'var(--nkz-radius-sm)',
          md: 'var(--nkz-radius-md)',
          lg: 'var(--nkz-radius-lg)',
          xl: 'var(--nkz-radius-xl)',
          '2xl': 'var(--nkz-radius-2xl)',
          full: 'var(--nkz-radius-full)',
        },
      },
      boxShadow: {
        nkz: {
          sm: 'var(--nkz-shadow-sm)',
          md: 'var(--nkz-shadow-md)',
          lg: 'var(--nkz-shadow-lg)',
          xl: 'var(--nkz-shadow-xl)',
        },
      },
      spacing: {
        nkz: {
          tight: 'var(--nkz-space-tight)',
          inline: 'var(--nkz-space-inline)',
          stack: 'var(--nkz-space-stack)',
          section: 'var(--nkz-space-section)',
        },
      },
      zIndex: {
        nkz: {
          base: 'var(--nkz-z-base)',
          mapOverlay: 'var(--nkz-z-map-overlay)',
          toolbar: 'var(--nkz-z-toolbar)',
          rail: 'var(--nkz-z-rail)',
          header: 'var(--nkz-z-header)',
          popover: 'var(--nkz-z-popover)',
          tooltip: 'var(--nkz-z-tooltip)',
          modal: 'var(--nkz-z-modal)',
          toast: 'var(--nkz-z-toast)',
          loading: 'var(--nkz-z-loading)',
        },
      },
      transitionDuration: {
        nkz: {
          fast: 'var(--nkz-motion-fast)',
          normal: 'var(--nkz-motion-normal)',
          slow: 'var(--nkz-motion-slow)',
        },
      },
      transitionTimingFunction: {
        nkz: {
          default: 'var(--nkz-motion-ease-default)',
          spring: 'var(--nkz-motion-ease-spring)',
        },
      },
      fontSize: {
        nkz: {
          '2xs': ['var(--nkz-type-2xs-size)', { lineHeight: 'var(--nkz-type-2xs-line-height)', letterSpacing: 'var(--nkz-type-2xs-letter-spacing)', fontWeight: 'var(--nkz-type-2xs-weight)' }],
          xs:  ['var(--nkz-type-xs-size)',  { lineHeight: 'var(--nkz-type-xs-line-height)', letterSpacing: 'var(--nkz-type-xs-letter-spacing)', fontWeight: 'var(--nkz-type-xs-weight)' }],
          sm:  ['var(--nkz-type-sm-size)',  { lineHeight: 'var(--nkz-type-sm-line-height)', letterSpacing: 'var(--nkz-type-sm-letter-spacing)', fontWeight: 'var(--nkz-type-sm-weight)' }],
          base:['var(--nkz-type-base-size)',{ lineHeight: 'var(--nkz-type-base-line-height)', letterSpacing: 'var(--nkz-type-base-letter-spacing)', fontWeight: 'var(--nkz-type-base-weight)' }],
          md:  ['var(--nkz-type-md-size)',  { lineHeight: 'var(--nkz-type-md-line-height)', letterSpacing: 'var(--nkz-type-md-letter-spacing)', fontWeight: 'var(--nkz-type-md-weight)' }],
          lg:  ['var(--nkz-type-lg-size)',  { lineHeight: 'var(--nkz-type-lg-line-height)', letterSpacing: 'var(--nkz-type-lg-letter-spacing)', fontWeight: 'var(--nkz-type-lg-weight)' }],
          xl:  ['var(--nkz-type-xl-size)',  { lineHeight: 'var(--nkz-type-xl-line-height)', letterSpacing: 'var(--nkz-type-xl-letter-spacing)', fontWeight: 'var(--nkz-type-xl-weight)' }],
          '2xl':['var(--nkz-type-2xl-size)',{ lineHeight: 'var(--nkz-type-2xl-line-height)', letterSpacing: 'var(--nkz-type-2xl-letter-spacing)', fontWeight: 'var(--nkz-type-2xl-weight)' }],
          '3xl':['var(--nkz-type-3xl-size)',{ lineHeight: 'var(--nkz-type-3xl-line-height)', letterSpacing: 'var(--nkz-type-3xl-letter-spacing)', fontWeight: 'var(--nkz-type-3xl-weight)' }],
        },
      },
    },
  },
  plugins: [],
};
`;

mkdirSync('dist', { recursive: true });
writeFileSync('dist/tailwind-preset.js', preset.trimStart());
console.log('Generated dist/tailwind-preset.js');
