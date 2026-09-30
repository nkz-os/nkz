import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LayersCascadeSubmenu } from '../LayersCascadeSubmenu'

vi.mock('@/components/viewer/CoreLayerToggles', () => ({
  default: () => <div data-testid="core-layer-toggles">core-toggles</div>,
}))
vi.mock('@/components/SlotRenderer', () => ({
  SlotRenderer: ({ slot }: { slot: string }) => (
    <div data-testid="slot-renderer">{`slot:${slot}`}</div>
  ),
}))
vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({ t: (k: string) => k }),
}))

// Node 22 ships its own (empty) localStorage object that supersedes jsdom's,
// so we install a Map-backed stub for this suite.
beforeEach(() => {
  const store = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => (store.has(k) ? (store.get(k) as string) : null),
    setItem: (k: string, v: string) => {
      store.set(k, String(v))
    },
    removeItem: (k: string) => {
      store.delete(k)
    },
    clear: () => store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size
    },
  })
})

function setup(isOpen = true) {
  return render(
    <LayersCascadeSubmenu
      isOpen={isOpen}
      side="right"
      onMouseEnter={vi.fn()}
      onMouseLeave={vi.fn()}
    />
  )
}

describe('LayersCascadeSubmenu', () => {
  it('renders nothing visible when isOpen is false', () => {
    setup(false)
    expect(screen.queryByTestId('core-layer-toggles')).not.toBeInTheDocument()
  })

  it('renders only the base layers when open (modules live in the right panel)', () => {
    setup(true)
    expect(screen.getByTestId('core-layer-toggles')).toBeInTheDocument()
    expect(screen.queryByTestId('slot-renderer')).not.toBeInTheDocument()
  })

  it('persists Core group collapse state under nkz.viewer.layersSubmenu.core.open', () => {
    setup(true)
    const coreHeader = screen.getByRole('button', { name: /viewer\.layersGroupCore/i })
    fireEvent.click(coreHeader)
    expect(localStorage.getItem('nkz.viewer.layersSubmenu.core.open')).toBe('false')
    fireEvent.click(coreHeader)
    expect(localStorage.getItem('nkz.viewer.layersSubmenu.core.open')).toBe('true')
  })

})
