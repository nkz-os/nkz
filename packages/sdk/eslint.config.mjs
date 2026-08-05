// @nekazari/sdk is a published, external-module-consumed package — its
// public API types matter more than most. This config intentionally
// enables ONLY `@typescript-eslint/no-explicit-any` (as 'error', not the
// progressive 'warn' apps/host uses elsewhere) rather than a full
// recommended rule bundle: this package had no eslint config at all before
// this change, and pulling in `eslint:recommended` / `tseslint.configs.
// recommended` wholesale would start failing on pre-existing, unrelated
// issues (unused vars, @ts-ignore usage, etc.) that are out of scope for
// this any-ratchet. The few defensible generic-component `any`s are
// individually justified with a targeted eslint-disable-next-line comment.
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: ['dist', 'node_modules', 'coverage', 'eslint.config.mjs', 'vitest.config.ts'],
  },
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: tseslint.parser,
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: {
        // Pin explicitly: running eslint across multiple package dirs in one
        // invocation (e.g. `eslint packages/sdk/src packages/module-kit/src`)
        // makes typescript-eslint's parser see more than one candidate root
        // and refuse to guess.
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      '@typescript-eslint': tseslint.plugin,
    },
    linterOptions: {
      reportUnusedDisableDirectives: 'warn',
    },
    rules: {
      '@typescript-eslint/no-explicit-any': 'error',
    },
  },
);
