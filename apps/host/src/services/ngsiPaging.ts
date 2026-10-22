// =============================================================================
// NGSI-LD list paging
// =============================================================================
// Orion-LD answers `GET /ngsi-ld/v1/entities?type=X` with a default page of 20
// and no hint that more exist, so a list call without `limit` silently drops
// everything past the 20th entity. Full-list callers go through this helper.
import { logger } from '@/utils/logger';

/** Orion-LD's maximum page; larger values are rejected with 400. */
export const NGSI_MAX_PAGE = 1000;
/** Safety cap on one list call; beyond it the list is truncated with a warning. */
export const NGSI_MAX_ITEMS = 10_000;

/**
 * Fetch every page of an NGSI-LD entity list. `fetchPage` returns one page for
 * the given offset/limit. Stops on a short page or at `maxItems`.
 *
 * Errors from `fetchPage` propagate: a partial list is never returned as if it
 * were complete.
 */
export async function fetchAllPages<T>(
  fetchPage: (offset: number, limit: number) => Promise<T[]>,
  opts?: { pageSize?: number; maxItems?: number; label?: string },
): Promise<T[]> {
  const pageSize = opts?.pageSize ?? NGSI_MAX_PAGE;
  const maxItems = opts?.maxItems ?? NGSI_MAX_ITEMS;
  const label = opts?.label ?? 'entities';

  // A non-positive page size can never produce a short page: fail loudly instead of looping.
  if (!Number.isInteger(pageSize) || pageSize < 1) {
    throw new RangeError(`fetchAllPages: pageSize must be a positive integer, got ${pageSize}`);
  }

  const all: T[] = [];
  let offset = 0;

  for (;;) {
    const page = await fetchPage(offset, pageSize);
    for (const item of page) all.push(item);

    if (all.length >= maxItems) {
      logger.warn(
        `[ngsiPaging] ${label}: list truncated at ${maxItems} items; more may exist`,
      );
      return all.length > maxItems ? all.slice(0, maxItems) : all;
    }
    if (page.length < pageSize) return all;

    offset += pageSize;
  }
}
