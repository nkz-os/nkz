import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { RegistrationWizard } from '../RegistrationWizard'

const post = vi.fn()
vi.mock('axios', () => ({ default: { create: () => ({ post: (...a: unknown[]) => post(...a) }) } }))
vi.mock('@/context/KeycloakAuthContext', () => ({ useAuth: () => ({ login: vi.fn() }) }))
vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (k: string) => k, language: 'en' }) }))
vi.mock('@/components/TermsAcceptance', () => ({
  TermsAcceptance: (p: Record<string, unknown>) => (
    <button
      type="button"
      onClick={() => Object.values(p).forEach((v) => typeof v === 'function' && v(true))}
    >
      accept-terms
    </button>
  ),
}))

const PASSWORD = 'Abcdef1!x'
const fail = (status: number, data: object = {}): Promise<unknown> =>
  Promise.reject({ response: { status, data } })
const calls = (path: string) => post.mock.calls.filter((c) => c[0] === path)

function type(id: string, value: string) {
  fireEvent.change(document.getElementById(id) as HTMLInputElement, { target: { value } })
}

function submit() {
  fireEvent.submit(document.querySelector('form') as HTMLFormElement)
}

function accountStep(method: 'otp' | 'code') {
  render(<MemoryRouter><RegistrationWizard defaultMethod={method} /></MemoryRouter>)
  type('reg-email', 'a@example.test')
  type('reg-firstname', 'Name')
  type('reg-lastname', 'Surname')
  type('reg-org', 'Farm One')
  submit()
}

function securityStep() {
  type('reg-password', PASSWORD)
  type('reg-confirm', PASSWORD)
  fireEvent.click(screen.getByText('accept-terms'))
  submit()
}

describe('RegistrationWizard', () => {
  beforeEach(() => {
    post.mockReset()
    post.mockImplementation((path: string) =>
      path.endsWith('check-tenant')
        ? Promise.resolve({ data: { available: true } })
        : Promise.resolve({ data: { success: true } }))
  })

  it('asks for the password before activating a code, and sends it', async () => {
    accountStep('code')
    await waitFor(() => expect(document.getElementById('reg-code')).toBeTruthy())
    type('reg-code', 'NEKAAAABBBBCCCC')
    submit()
    expect(calls('/webhook/activate')).toHaveLength(0)
    await screen.findByText('accept-terms')
    securityStep()
    await waitFor(() => expect(calls('/webhook/activate')).toHaveLength(1))
    expect(calls('/webhook/activate')[0][1]).toMatchObject({ password: PASSWORD, code: 'NEK-AAAA-BBBB-CCCC' })
  })

  it('does not require a free name for an activation code', async () => {
    post.mockImplementation((path: string) =>
      Promise.resolve({ data: path.endsWith('check-tenant') ? { available: false } : { success: true } }))
    accountStep('code')
    await waitFor(() => expect(document.getElementById('reg-code')).toBeTruthy())
    type('reg-code', 'NEKAAAABBBBCCCC')
    submit()
    expect(await screen.findByText('accept-terms')).toBeInTheDocument()
  })

  it('sends no code for a taken name', async () => {
    post.mockImplementation((path: string) =>
      Promise.resolve({ data: path.endsWith('check-tenant') ? { available: false } : { success: true } }))
    accountStep('otp')
    fireEvent.click(await screen.findByText('registration.send_otp'))
    expect(await screen.findByText('registration.org_name_taken')).toBeInTheDocument()
    expect(calls('/webhook/register/request-otp')).toHaveLength(0)
  })

  it.each([[403], [503]])('says registration is unavailable on %s', async (status) => {
    post.mockImplementation(() => fail(status))
    accountStep('otp')
    fireEvent.click(await screen.findByText('registration.send_otp'))
    expect(await screen.findByText('registration.unavailable')).toBeInTheDocument()
  })

  it('tells a taken name from a taken email on register', async () => {
    for (const [code, message] of [['TENANT_EXISTS', 'registration.org_name_taken'], ['EMAIL_EXISTS', 'registration.email_exists']]) {
      post.mockImplementation((path: string) => {
        if (path.endsWith('check-tenant')) return Promise.resolve({ data: { available: true } })
        if (path.endsWith('request-otp')) return Promise.resolve({ data: { success: true } })
        return fail(409, { error_code: code })
      })
      accountStep('otp')
      fireEvent.click(await screen.findByText('registration.send_otp'))
      await waitFor(() => expect(document.getElementById('reg-otp')).toBeTruthy())
      type('reg-otp', '123456')
      submit()
      await screen.findByText('accept-terms')
      securityStep()
      expect((await screen.findAllByText(message)).length).toBeGreaterThan(0)
      cleanup()
    }
  })
})
