// Flat config port of the legacy .eslintrc.cjs — format migration only,
// rule parity is intentional (no severity changes, no new rules).
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';

export default tseslint.config(
  {
    ignores: ['dist', 'node_modules', 'eslint.config.mjs'],
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
    ],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.es2020,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    linterOptions: {
      // replaces the legacy --report-unused-disable-directives CLI flag
      reportUnusedDisableDirectives: 'warn',
    },
    rules: {
      // React
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',

      // TypeScript - progressive strictness (warn first, error later)
      '@typescript-eslint/no-explicit-any': 'warn',
      // caughtErrors: 'none' — parity with legacy .eslintrc.cjs (tseslint v8
      // flipped the default to 'all', which flags unused catch params)
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' },
      ],
      '@typescript-eslint/no-empty-function': 'off',
      '@typescript-eslint/ban-ts-comment': 'warn',
      // parity with legacy .eslintrc.cjs — not part of v7 recommended
      '@typescript-eslint/no-unused-expressions': 'off',

      // Code quality
      'no-console': ['warn', { allow: ['warn', 'error'] }], // prefer logger; allow console.warn/error as escape hatch
      'no-debugger': 'error',
      'prefer-const': 'warn',
      'no-var': 'error',
      eqeqeq: ['warn', 'smart'],
    },
  },
);
