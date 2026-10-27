import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

import { DataloggerRotationNotice } from '../DataloggerRotationNotice';

describe('DataloggerRotationNotice', () => {
  it('tells the user to regenerate the password in Settings and closes', () => {
    const onClose = vi.fn();
    render(<DataloggerRotationNotice onClose={onClose} />);
    expect(screen.getByText('dataloggers.needs_rotation.message')).toBeInTheDocument();
    expect(screen.getByText('dataloggers.needs_rotation.open_settings')).toHaveAttribute('href', '/settings');
    fireEvent.click(screen.getByText('dataloggers.needs_rotation.understood'));
    expect(onClose).toHaveBeenCalled();
  });
});
