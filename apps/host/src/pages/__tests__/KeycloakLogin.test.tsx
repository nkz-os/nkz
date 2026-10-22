/**
 * Component test: KeycloakLogin (critical auth entry point).
 * Verifies that the login page renders title, status message and retry button.
 * No username/password inputs — auth is delegated to Keycloak.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import KeycloakLogin from '../KeycloakLogin'

const mockLogin = vi.fn().mockResolvedValue(undefined)

vi.mock('@/context/KeycloakAuthContext', () => ({
  useAuth: () => ({
    isAuthenticated: false,
    isLoading: false,
    login: mockLogin,
  }),
}))

// i18n isn't initialized in this test environment — mock t() to echo the key,
// matching the convention used by other component tests in this app (see
// MapSearchLupa.test.tsx / CoreTimelineControls.test.tsx).
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

function renderWithRouter(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('KeycloakLogin', () => {
  it('renders login UI: title, status and retry button', () => {
    renderWithRouter(<KeycloakLogin />)
    expect(screen.getByRole('heading', { name: 'auth.connecting_to_keycloak' })).toBeInTheDocument()
    expect(screen.getByText('auth.redirecting_to_keycloak')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'auth.retry_login_button' })).toBeInTheDocument()
  })

  it('renders forgot password link', () => {
    renderWithRouter(<KeycloakLogin />)
    expect(screen.getByRole('link', { name: 'auth.forgot_password_link' })).toBeInTheDocument()
  })
})
