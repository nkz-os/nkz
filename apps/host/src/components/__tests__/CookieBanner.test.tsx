/**
 * CookieBanner — exercises consent UI branches for coverage (global branch threshold).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { CookieBanner } from '../CookieBanner';
const baseConsent = {
  hasAnswered: false,
  preferences: { necessary: true as const, analytics: false },
  analyticsEnabled: false,
  openPreferences: vi.fn(),
  closePreferences: vi.fn(),
  preferencesOpen: false,
  acceptAll: vi.fn(),
  rejectOptional: vi.fn(),
  saveCustom: vi.fn(),
};

const useCookieConsent = vi.fn(() => baseConsent);

vi.mock('@/context/CookieConsentContext', () => ({
  COOKIE_POLICY_NOTICE_VERSION: 1,
  useCookieConsent: () => useCookieConsent(),
}));

vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      if (key === 'cookies.policy_version' && opts && typeof opts.version === 'number') {
        return `notice-${opts.version}`;
      }
      return key;
    },
  }),
}));

describe('CookieBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useCookieConsent.mockImplementation(() => ({
      ...baseConsent,
      hasAnswered: false,
      preferencesOpen: false,
      preferences: { necessary: true, analytics: false },
    }));
  });

  it('renders nothing when answered and panel closed', () => {
    useCookieConsent.mockReturnValue({
      ...baseConsent,
      hasAnswered: true,
      preferencesOpen: false,
    });
    const { container } = render(<CookieBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('shows first-layer actions and rejects optional', () => {
    render(<CookieBanner />);
    fireEvent.click(screen.getByText('cookies.reject_optional'));
    expect(baseConsent.rejectOptional).toHaveBeenCalled();
  });

  it('opens configure panel, toggles analytics, saves', () => {
    render(<CookieBanner />);
    fireEvent.click(screen.getByText('cookies.configure'));
    const cb = screen.getByRole('checkbox', { name: 'cookies.category_analytics_title' });
    fireEvent.click(cb);
    fireEvent.click(screen.getByText('cookies.save_preferences'));
    expect(baseConsent.saveCustom).toHaveBeenCalledWith(true);
  });

  it('accepts all from first layer', () => {
    render(<CookieBanner />);
    fireEvent.click(screen.getByText('cookies.accept_all'));
    expect(baseConsent.acceptAll).toHaveBeenCalled();
  });

  it('shows overlay and closes when reopening preferences', () => {
    useCookieConsent.mockReturnValue({
      ...baseConsent,
      hasAnswered: true,
      preferencesOpen: true,
      preferences: { necessary: true, analytics: false },
    });
    render(<CookieBanner />);
    fireEvent.click(screen.getByRole('button', { name: 'cookies.close_overlay' }));
    expect(baseConsent.closePreferences).toHaveBeenCalled();
  });

  it('cancel from configure calls closePreferences when panel was opened from settings', async () => {
    useCookieConsent.mockReturnValue({
      ...baseConsent,
      hasAnswered: true,
      preferencesOpen: true,
      preferences: { necessary: true, analytics: false },
    });
    render(<CookieBanner />);
    await waitFor(() => {
      expect(screen.getByText('cookies.cancel')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('cookies.cancel'));
    expect(baseConsent.closePreferences).toHaveBeenCalled();
  });
});
