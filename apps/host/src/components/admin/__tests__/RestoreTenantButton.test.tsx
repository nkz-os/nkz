import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { RestoreTenantButton } from '../RestoreTenantButton'

const post = vi.fn()
const warning = vi.fn()
vi.mock('@/services/api', () => ({ default: { post: (...a: unknown[]) => post(...a) } }))
vi.mock('@/context/ToastContext', () => ({ useToastContext: () => ({ warning, error: vi.fn() }) }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }))

async function restore(data: object) {
  post.mockResolvedValue({ data })
  const onRestored = vi.fn()
  render(<RestoreTenantButton tenantId="acme" onRestored={onRestored} />)
  fireEvent.click(screen.getByText('admin.restore_tenant'))
  fireEvent.click(screen.getByText('admin.confirm_restore'))
  await waitFor(() => expect(onRestored).toHaveBeenCalled())
}

describe('RestoreTenantButton', () => {
  beforeEach(() => warning.mockReset())

  it('says nothing when everything reopened', async () => {
    await restore({ accounts: 'ok', ingestion: 'ok' })
    expect(warning).not.toHaveBeenCalled()
  })

  it.each([['failed', 'ok'], ['ok', 'failed']])('warns when accounts=%s ingestion=%s', async (accounts, ingestion) => {
    await restore({ accounts, ingestion })
    expect(warning).toHaveBeenCalledWith('admin.restore_partial')
  })
})
