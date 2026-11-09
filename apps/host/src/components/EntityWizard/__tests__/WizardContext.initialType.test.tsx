import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { WizardProvider, useWizard } from '../WizardContext';

const withType = (type: string) => ({ children }: { children: ReactNode }) => (
  <WizardProvider initialEntityType={type}>{children}</WizardProvider>
);

describe('WizardProvider preset type', () => {
  it('keeps a creatable preset type', () => {
    const { result } = renderHook(() => useWizard(), { wrapper: withType('WeatherObserved') });
    expect(result.current.entityType).toBe('WeatherObserved');
    expect(result.current.formData).not.toBeNull();
  });

  it('falls back to the type picker for a hidden type', () => {
    const { result } = renderHook(() => useWizard(), { wrapper: withType('LivestockAnimal') });
    expect(result.current.entityType).toBeNull();
    expect(result.current.formData).toBeNull();
  });
});
