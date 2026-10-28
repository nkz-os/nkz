import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { EnvNotice } from '../EnvNotice';

const useAuth = vi.fn();

vi.mock('@/context/KeycloakAuthContext', () => ({ useAuth: () => useAuth() }));
vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (k: string) => k }) }));

const setFlag = (v?: string) => {
  window.__ENV__ = v === undefined ? {} : { VITE_PLATFORM_ENV_NOTICE: v };
};

describe('EnvNotice', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    useAuth.mockReturnValue({ isAuthenticated: true });
    setFlag('development');
  });
  afterEach(() => {
    vi.restoreAllMocks();
    delete window.__ENV__;
  });

  it('renders nothing when the flag is missing', () => {
    setFlag();
    const { container } = render(<EnvNotice />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when the flag has another value', () => {
    setFlag('production');
    const { container } = render(<EnvNotice />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when not authenticated', () => {
    useAuth.mockReturnValue({ isAuthenticated: false });
    const { container } = render(<EnvNotice />);
    expect(container.firstChild).toBeNull();
  });

  it('renders the strip when enabled and authenticated', () => {
    render(<EnvNotice />);
    expect(screen.getByRole('status')).toBeTruthy();
    expect(screen.getByText('layout.env_notice.message')).toBeTruthy();
  });

  it('collapses to a DEV badge, persists, and expands again', () => {
    const { unmount } = render(<EnvNotice />);
    fireEvent.click(screen.getByLabelText('layout.env_notice.collapse'));
    expect(screen.getByText('DEV')).toBeTruthy();
    expect(screen.queryByText('layout.env_notice.message')).toBeNull();
    expect(window.sessionStorage.getItem('nkz_env_notice_collapsed')).toBe('1');
    unmount();

    render(<EnvNotice />);
    expect(screen.getByText('DEV')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('layout.env_notice.expand'));
    expect(screen.getByText('layout.env_notice.message')).toBeTruthy();
  });

  it('does not crash when storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    render(<EnvNotice />);
    fireEvent.click(screen.getByLabelText('layout.env_notice.collapse'));
    expect(screen.getByText('DEV')).toBeTruthy();
  });
});
