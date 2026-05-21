export type SubmenuSide = 'left' | 'right'

export interface ComputeSubmenuSideArgs {
  triggerRight: number
  submenuWidth: number
  viewportWidth: number
}

const SAFETY_MARGIN_PX = 8

/**
 * Decide which side a cascade submenu should open on so it doesn't clip
 * the viewport right edge. Pure, framework-agnostic, easy to unit-test.
 */
export function computeSubmenuSide({
  triggerRight,
  submenuWidth,
  viewportWidth,
}: ComputeSubmenuSideArgs): SubmenuSide {
  return triggerRight + submenuWidth + SAFETY_MARGIN_PX > viewportWidth
    ? 'left'
    : 'right'
}
