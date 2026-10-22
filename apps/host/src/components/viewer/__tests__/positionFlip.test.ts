import { describe, it, expect } from 'vitest'
import { computeSubmenuSide } from '../positionFlip'

describe('computeSubmenuSide', () => {
  it('returns "right" when there is enough space on the right', () => {
    expect(
      computeSubmenuSide({ triggerRight: 400, submenuWidth: 320, viewportWidth: 1280 })
    ).toBe('right')
  })

  it('returns "left" when the submenu would overflow the right edge', () => {
    expect(
      computeSubmenuSide({ triggerRight: 1100, submenuWidth: 320, viewportWidth: 1280 })
    ).toBe('left')
  })

  it('respects an 8 px safety margin', () => {
    expect(
      computeSubmenuSide({ triggerRight: 952, submenuWidth: 320, viewportWidth: 1280 })
    ).toBe('right')
    expect(
      computeSubmenuSide({ triggerRight: 953, submenuWidth: 320, viewportWidth: 1280 })
    ).toBe('left')
  })
})
