import { beforeAll } from 'vitest';

beforeAll(() => {
  // WASM not available in Node — skip markers for WASM-dependent tests
  (globalThis as any).__VITEST_WASM__ = false;
});
