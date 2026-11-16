import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, act, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { SdkNavigateBridge } from '../SdkNavigateBridge';

type SdkGlobal = { navigate?: (path: string) => void; i18n?: unknown };
const sdk = () => (window as unknown as { __NKZ_SDK__?: SdkGlobal }).__NKZ_SDK__;

const Where = () => {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname + loc.search}</div>;
};

const mount = () =>
  render(
    <MemoryRouter initialEntries={['/fast']}>
      <SdkNavigateBridge />
      <Where />
    </MemoryRouter>,
  );

beforeEach(() => {
  delete (window as unknown as { __NKZ_SDK__?: SdkGlobal }).__NKZ_SDK__;
});
afterEach(() => {
  delete (window as unknown as { __NKZ_SDK__?: SdkGlobal }).__NKZ_SDK__;
});

describe('SdkNavigateBridge', () => {
  it('navigates inside the app, keeping the query string', () => {
    mount();
    act(() => sdk()!.navigate!('/gis-routing?view=work&parcel=p1'));
    expect(screen.getByTestId('where').textContent).toBe('/gis-routing?view=work&parcel=p1');
  });

  it('keeps what is already on the global', () => {
    const i18n = { t: () => 'x' };
    (window as unknown as { __NKZ_SDK__?: SdkGlobal }).__NKZ_SDK__ = { i18n };
    mount();
    expect(sdk()!.i18n).toBe(i18n);
    expect(typeof sdk()!.navigate).toBe('function');
  });

  it('ignores anything that is not an in-app path', () => {
    mount();
    for (const bad of ['https://example.org/x', '//example.org/x', 'relative', '', 42 as unknown as string]) {
      act(() => sdk()!.navigate!(bad));
    }
    expect(screen.getByTestId('where').textContent).toBe('/fast');
  });

  it('removes navigate on unmount', () => {
    const { unmount } = mount();
    unmount();
    expect(sdk()?.navigate).toBeUndefined();
  });
});
