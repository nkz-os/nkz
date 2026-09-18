/** @type {import('tailwindcss').Config} */
// Config solo para CT. viewer-kit no publica CSS compilado: las apps consumidoras
// son las que hacen el build de Tailwind y escanean su src. El harness de
// component testing no tiene app consumidora, así que sin este fichero
// `test:ct` renderiza cada componente sin NINGUNA clase de utilidad.
// Preset y safelist deben coincidir con apps/host/tailwind.config.js, o las
// capturas retratarían algo que no es producción.
export default {
  presets: [require('@nekazari/design-tokens/tailwind')],
  // Espeja apps/host/tailwind.config.js:11. Sin declararlo, Tailwind v3 usa
  // 'media' y las variantes dark: responderían a prefers-color-scheme en vez
  // de a la clase .dark, que es lo que produce producción (ThemeContext.tsx).
  darkMode: 'class',
  content: ['./src/**/*.{ts,tsx}', './playwright/**/*.{ts,tsx}'],
  safelist: [{ pattern: /-nkz-/ }],
  plugins: [],
}
