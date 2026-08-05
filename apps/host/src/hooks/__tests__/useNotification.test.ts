import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useNotification } from '../useNotification';

const mockToast = {
  success: vi.fn(() => 'toast-id'),
  error: vi.fn(() => 'toast-id'),
  warning: vi.fn(() => 'toast-id'),
  info: vi.fn(() => 'toast-id'),
};

// Mock the ToastContext so the hook doesn't need a real <ToastProvider>
// tree while still verifying it's consumed correctly.
vi.mock('@/context/ToastContext', () => ({
  useToastContext: () => mockToast,
}));

describe('useNotification', () => {
  beforeEach(() => {
    mockToast.success.mockClear();
    mockToast.error.mockClear();
    mockToast.warning.mockClear();
    mockToast.info.mockClear();
  });

  it('calls the matching toast method for a success notification', () => {
    const { result } = renderHook(() => useNotification());

    act(() => {
      result.current.showNotification({ type: 'success', message: 'x' });
    });

    expect(mockToast.success).toHaveBeenCalledWith('x');
    expect(mockToast.error).not.toHaveBeenCalled();
    expect(mockToast.warning).not.toHaveBeenCalled();
    expect(mockToast.info).not.toHaveBeenCalled();
  });

  it.each([
    ['success', 'success'],
    ['error', 'error'],
    ['warning', 'warning'],
    ['info', 'info'],
  ] as const)('maps notification type %s to toast.%s', (type, method) => {
    const { result } = renderHook(() => useNotification());

    act(() => {
      result.current.showNotification({ type, message: `msg-${type}` });
    });

    expect(mockToast[method]).toHaveBeenCalledWith(`msg-${type}`);
    expect(mockToast[method]).toHaveBeenCalledTimes(1);
  });
});
