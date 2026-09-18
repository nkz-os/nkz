// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { ThemeProvider } from '../ThemeProvider';

afterEach(cleanup);

describe('ThemeProvider — subárbol, no raíz global', () => {
  it('no escribe data-theme en documentElement', () => {
    render(
      <ThemeProvider profile="viewer">
        <span>x</span>
      </ThemeProvider>,
    );
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });

  it('delimita el perfil a su subárbol', () => {
    const { getByTestId } = render(
      <ThemeProvider profile="page">
        <ThemeProvider profile="viewer">
          <span data-testid="inner">x</span>
        </ThemeProvider>
      </ThemeProvider>,
    );
    expect(getByTestId('inner').closest('[data-theme]')?.getAttribute('data-theme')).toBe('viewer');
  });
});
