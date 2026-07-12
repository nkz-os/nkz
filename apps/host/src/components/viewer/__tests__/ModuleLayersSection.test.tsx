import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { LayerRegistry } from '@nekazari/sdk'
import { ModuleLayersSection } from '../ModuleLayersSection'

// Real LayerRegistry singleton (per spec: mock the registry via the real
// LayerRegistry, reset() between tests) — no mock of @nekazari/sdk itself.
vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({ t: (k: string) => k }),
}))

// jsdom has no ResizeObserver; @radix-ui/react-use-size (used by ui-kit's
// Slider, rendered for supportsOpacity layers) needs one to mount.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  LayerRegistry.reset()
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
})

afterEach(() => {
  LayerRegistry.reset()
  vi.unstubAllGlobals()
})

describe('ModuleLayersSection', () => {
  it('renders an empty hint when no layers are registered', () => {
    render(<ModuleLayersSection />)
    expect(screen.getByText('viewer.moduleLayers.empty')).toBeInTheDocument()
  })

  it('renders registered layers, resolving titleKey through t() and grouping by group', () => {
    LayerRegistry.registerViewerLayers('weather-map', [
      { id: 'weather-temp', titleKey: 'weatherMap.layers.temperature', group: 'Weather' },
    ])

    render(<ModuleLayersSection />)

    expect(screen.getByText('weatherMap.layers.temperature')).toBeInTheDocument()
    expect(screen.getByText('Weather')).toBeInTheDocument()
  })

  it('falls back to moduleId as the group when no group is declared', () => {
    LayerRegistry.registerViewerLayers('soil', [
      { id: 'soil-texture', titleKey: 'soil.layers.texture' },
    ])

    render(<ModuleLayersSection />)

    expect(screen.getByText('soil')).toBeInTheDocument()
  })

  it('toggling the switch calls LayerRegistry.setVisible with the layer id and next value', () => {
    LayerRegistry.registerViewerLayers('soil', [
      { id: 'soil-texture', titleKey: 'soil.layers.texture', defaultVisible: true },
    ])
    const setVisibleSpy = vi.spyOn(LayerRegistry, 'setVisible')

    render(<ModuleLayersSection />)

    fireEvent.click(screen.getByRole('switch'))

    expect(setVisibleSpy).toHaveBeenCalledWith('soil-texture', false)
  })

  it('hides the opacity slider when supportsOpacity is false and shows it when true', () => {
    LayerRegistry.registerViewerLayers('vegetation', [
      { id: 'veg-ndvi', titleKey: 'vegetation.layers.ndvi', supportsOpacity: false },
      { id: 'veg-savi', titleKey: 'vegetation.layers.savi', supportsOpacity: true },
    ])

    render(<ModuleLayersSection />)

    expect(screen.queryByTestId('module-layer-opacity-veg-ndvi')).not.toBeInTheDocument()
    expect(screen.getByTestId('module-layer-opacity-veg-savi')).toBeInTheDocument()
  })

  it('reacts to late registrations without polling (subscribe-driven)', () => {
    render(<ModuleLayersSection />)
    expect(screen.queryByText('crop.layers.stress')).not.toBeInTheDocument()

    act(() => {
      LayerRegistry.registerViewerLayers('crop-health', [
        { id: 'crop-stress', titleKey: 'crop.layers.stress' },
      ])
    })

    expect(screen.getByText('crop.layers.stress')).toBeInTheDocument()
  })
})
