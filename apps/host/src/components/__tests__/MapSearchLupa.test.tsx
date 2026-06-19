import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MapSearchLupa } from '../MapSearchLupa';

const mockUseGeocoder = vi.fn();
vi.mock('@/hooks/useGeocoder', () => ({
  useGeocoder: (...args: any[]) => mockUseGeocoder(...args),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d || _k }),
}));

const baseResult = { label: 'Pamplona', lat: 42.8, lon: -1.6, bbox: null, type: 'city', countryCode: 'ES' };

describe('MapSearchLupa', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseGeocoder.mockReturnValue({ results: [], loading: false, error: null, search: vi.fn() });
  });

  it('starts collapsed, expands on click, picks a result', () => {
    mockUseGeocoder.mockReturnValue({
      results: [baseResult],
      loading: false,
      error: null,
      search: vi.fn(),
    });
    const onPick = vi.fn();
    render(<MapSearchLupa onPick={onPick} />);

    expect(screen.queryByRole('textbox')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    expect(screen.getByRole('textbox')).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Pam' } });
    fireEvent.click(screen.getByText('Pamplona'));
    expect(onPick).toHaveBeenCalledWith(baseResult);
  });

  it('shows no results message when query has no matches', () => {
    const onPick = vi.fn();
    render(<MapSearchLupa onPick={onPick} />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'zzz' } });
    expect(screen.getByText('map.search_no_results')).toBeInTheDocument();
  });

  it('shows loading indicator', () => {
    mockUseGeocoder.mockReturnValue({
      results: [], loading: true, error: null, search: vi.fn(),
    });
    render(<MapSearchLupa onPick={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    expect(screen.getByText('...')).toBeInTheDocument();
  });

  it('shows error message', () => {
    mockUseGeocoder.mockReturnValue({
      results: [], loading: false, error: 'Boom', search: vi.fn(),
    });
    render(<MapSearchLupa onPick={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    expect(screen.getByText('map.search_error')).toBeInTheDocument();
  });

  it('closes on Escape key', () => {
    mockUseGeocoder.mockReturnValue({
      results: [baseResult],
      loading: false,
      error: null,
      search: vi.fn(),
    });
    render(<MapSearchLupa onPick={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' });
    expect(screen.queryByRole('textbox')).toBeNull();
  });
});
