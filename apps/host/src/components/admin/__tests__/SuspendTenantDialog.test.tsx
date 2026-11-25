import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SuspendTenantDialog } from '../SuspendTenantDialog'

const post = vi.fn()
const get = vi.fn()
const warning = vi.fn()
vi.mock('@/services/api', () => ({
  default: { post: (...a: unknown[]) => post(...a), get: (...a: unknown[]) => get(...a) },
}))
vi.mock('@/context/ToastContext', () => ({ useToastContext: () => ({ warning, error: vi.fn() }) }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }))

async function suspend(data: object) {
  get.mockResolvedValue({
    data: { tenant_id: 'acme', tenant_name: 'Acme', systems: {}, estimated_impact: '', warnings: [] },
  })
  post.mockResolvedValue({ data })
  const onSuspended = vi.fn()
  render(
    <SuspendTenantDialog tenantId="acme" tenantName="Acme" onClose={vi.fn()} onSuspended={onSuspended} />,
  )
  fireEvent.change(await screen.findByPlaceholderText('Acme'), { target: { value: 'Acme' } })
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: /admin\.suspend_tenant/ }))
  await waitFor(() => expect(onSuspended).toHaveBeenCalled())
}

describe('SuspendTenantDialog', () => {
  beforeEach(() => { warning.mockReset(); post.mockReset() })

  it('says nothing when everything closed, and waits long enough', async () => {
    await suspend({ accounts: 'ok', ingestion: 'ok' })
    expect(warning).not.toHaveBeenCalled()
    expect(post.mock.calls[0][0]).toBe('/api/admin/tenants/acme/suspend')
    expect(post.mock.calls[0][2]).toEqual({ timeout: 300000 })
  })

  it.each([['failed', 'ok'], ['ok', 'failed']])('warns when accounts=%s ingestion=%s', async (accounts, ingestion) => {
    await suspend({ accounts, ingestion })
    expect(warning).toHaveBeenCalledWith('admin.suspend_partial')
  })
})
