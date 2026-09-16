/// <reference types="vitest" />
import { defineConfig } from 'vitest/config'
import path from 'path'

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/__tests__/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'text-summary', 'lcov'],
      include: ['src/utils/**', 'src/hooks/**', 'src/components/**'],
      exclude: ['src/__tests__/**', 'node_modules/**'],
      thresholds: {
        // Raised 2026-02: urlNormalizer + nkzRuntime tests. Next target M1: 5% lines.
        // branches/functions recalibrated 2026-06 for vitest 3: AST-based V8
        // remapping changed the denominators (same suite passing measures
        // 37.35% branches / 12.84% functions vs 50/15 under vitest 2).
        // Recalibrated again 2026-09 for vitest 5: coverage-v8 5 reports EVERY
        // file under `include` (even never-imported UI components) instead of
        // only imported files, so the same 204-test suite now measures
        // 3.83% functions / 2.85% branches. The denominator is the whole
        // component tree, not the tested surface.
        statements: 1,
        branches: 2,
        functions: 3,
        lines: 1,
      },
    },
  },
})
