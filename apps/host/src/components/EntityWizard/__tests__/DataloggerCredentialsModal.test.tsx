import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useState } from 'react';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/services/dataloggersApi', () => ({ getDataloggerConfig: vi.fn() }));

import { DataloggerCredentialsModal } from '../DataloggerCredentialsModal';

function Host({ credentials }: { credentials: Record<string, unknown> | null }) {
  const [creds, setCreds] = useState(credentials);
  return creds ? (
    <DataloggerCredentialsModal isOpen onClose={() => setCreds(null)} datalogger={{ id: 'dl-1', name: 'Field A' }} credentials={creds} />
  ) : (
    <div data-testid="closed" />
  );
}

describe('DataloggerCredentialsModal', () => {
  it('shows host, port, username and the password once', () => {
    render(<Host credentials={{ configured: true, host: 'broker.example', port: 8883, username: 'dl_ab', password: 'pw-once' }} />);
    expect(screen.getByLabelText('dataloggers.credentials.password')).toHaveValue('pw-once');
    expect(screen.getByLabelText('dataloggers.credentials.username')).toHaveValue('dl_ab');
    expect(screen.getByLabelText('dataloggers.credentials.host')).toHaveValue('broker.example');
  });

  it('clears the password when the modal closes', () => {
    const { container } = render(<Host credentials={{ configured: true, host: 'h', port: 1, username: 'u', password: 'pw-once' }} />);
    fireEvent.click(screen.getByText('dataloggers.credentials.saved'));
    expect(screen.getByTestId('closed')).toBeInTheDocument();
    expect(container.innerHTML).not.toContain('pw-once');
  });

  it('configured false shows the reason, not credentials', () => {
    const { container } = render(<Host credentials={{ configured: false, reason: 'No external endpoint', username: 'dl_ab' }} />);
    expect(screen.getByText('No external endpoint')).toBeInTheDocument();
    expect(screen.queryByLabelText('dataloggers.credentials.password')).toBeNull();
    expect(container.innerHTML).not.toContain('dl_ab');
  });
});
