import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// @testing-library/react only auto-registers this when `test.globals: true`
// is set; this config keeps globals off, so unmount explicitly to avoid
// leaked subscriptions (e.g. LayerRegistry listeners) bleeding into the next test.
afterEach(() => {
  cleanup();
});
