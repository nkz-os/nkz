/** @type {import('tailwindcss').Config} */
// CT-only Tailwind config. ui-kit ships no compiled CSS of its own — consuming
// apps (e.g. apps/host) own the Tailwind build and scan ui-kit's src via their
// own `content` glob. The component-testing harness has no consuming app, so
// without this file `pnpm test:ct` renders every component with zero utility
// classes applied (not even Tailwind's own defaults).
export default {
  presets: [require('@nekazari/design-tokens/tailwind')],
  content: ['./src/**/*.{ts,tsx}', './playwright/**/*.{ts,tsx}'],
  safelist: [{ pattern: /-nkz-/ }],
  plugins: [],
}
