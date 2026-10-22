import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { EntitlementGuard } from '../EntitlementGuard';

describe('<EntitlementGuard>', () => {
  it('renders children when tenant has the entitlement', () => {
    render(
      <EntitlementGuard required="open" tenantEntitlements={['open', 'tier-pro']}>
        <span>visible</span>
      </EntitlementGuard>
    );
    expect(screen.getByText('visible')).toBeInTheDocument();
  });

  it('renders fallback message when tenant lacks the entitlement', () => {
    render(
      <EntitlementGuard required="esdb-noncommercial" tenantEntitlements={['open']}>
        <span>hidden</span>
      </EntitlementGuard>
    );
    expect(screen.queryByText('hidden')).not.toBeInTheDocument();
    expect(screen.getByText(/entitlement required: esdb-noncommercial/i)).toBeInTheDocument();
  });

  it('renders custom fallback when provided', () => {
    render(
      <EntitlementGuard required="x" tenantEntitlements={[]} fallback={<em>denied</em>}>
        <span>hidden</span>
      </EntitlementGuard>
    );
    expect(screen.getByText('denied')).toBeInTheDocument();
  });
});
