// =============================================================================
// Tenant ID Validation Utilities — Frontend (canonical, hyphen-based)
// =============================================================================
// Mirrors services/common/tenant_utils.py. Format rules (SOTA, K8s-native):
//   - canonical regex: ^[a-z0-9]+(?:-[a-z0-9]+)*$
//   - lowercase letters, digits, hyphens only
//   - NFD-transliterated for accents (á→a, ñ→n, ç→c, ...)
//   - whitespace and any non-alphanumeric collapse to a single '-'
//   - no leading/trailing '-', no consecutive '-'
//   - 3..47 chars (K8s namespace max 63 minus 'nekazari-tenant-' prefix)
//   - idempotent
//
// Keep this file in sync with the backend module — they MUST agree on the
// output for every input.

export const MIN_TENANT_ID_LENGTH = 3;
export const MAX_TENANT_ID_LENGTH = 47;
export const TENANT_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export interface TenantValidationResult {
  isValid: boolean;
  normalized?: string;
  /** i18n key (common namespace) for use with useI18n().t */
  errorKey?: string;
  errorParams?: Record<string, string | number>;
  /** Shown as secondary line when normalization differs from raw input */
  warningKey?: string;
  warningParams?: Record<string, string>;
}

/**
 * Normalize an arbitrary string into a canonical tenant ID.
 *
 * Returns an empty string if the input is null/whitespace/all-symbols —
 * callers should treat empty as a validation failure. Length bounds are
 * NOT enforced here (use `validateTenantId` for the full contract); this
 * function only performs the character-set normalization.
 */
export function normalizeTenantId(input: string): string {
  if (!input) return '';
  const trimmed = input.trim();
  if (!trimmed) return '';

  // Decompose unicode (NFD) and drop combining marks (accents). The Unicode
  // property escape \p{M} requires the `u` flag and matches all marks
  // (categories Mn, Mc, Me); we only need Mn for Latin accents but \p{M}
  // is the cleanest single regex and is also what unicodedata.category(c)
  // == 'Mn' approximates in the backend.
  const nfd = trimmed.normalize('NFD').replace(/\p{M}+/gu, '');
  const ascii = nfd.toLowerCase();

  // Collapse anything that is not [a-z0-9] into a single hyphen.
  const collapsed = ascii.replace(/[^a-z0-9]+/g, '-');

  // Strip leading/trailing hyphens (consecutive ones already collapsed).
  return collapsed.replace(/^-+|-+$/g, '');
}

/**
 * Validate a tenant ID candidate and return a structured result the UI can
 * render. The `normalized` field is always populated when the input is
 * non-empty so the UI can show a live preview even on failure.
 */
export function validateTenantId(input: string): TenantValidationResult {
  if (!input || !input.trim()) {
    return {
      isValid: false,
      errorKey: 'activation.tenant_name_empty',
    };
  }

  const normalized = normalizeTenantId(input);
  let warningKey: string | undefined;
  let warningParams: Record<string, string> | undefined;

  if (input.toLowerCase().trim() !== normalized && normalized) {
    warningKey = 'activation.tenant_name_normalize_hint';
    warningParams = { normalized };
  }

  if (!normalized) {
    return {
      isValid: false,
      errorKey: 'activation.tenant_name_invalid_chars',
      warningKey,
      warningParams,
    };
  }

  if (normalized.length < MIN_TENANT_ID_LENGTH) {
    return {
      isValid: false,
      normalized,
      errorKey: 'activation.tenant_name_too_short',
      errorParams: {
        min: MIN_TENANT_ID_LENGTH,
        current: normalized.length,
      },
      warningKey,
      warningParams,
    };
  }

  if (normalized.length > MAX_TENANT_ID_LENGTH) {
    return {
      isValid: false,
      normalized,
      errorKey: 'activation.tenant_name_too_long',
      errorParams: {
        max: MAX_TENANT_ID_LENGTH,
        current: normalized.length,
      },
      warningKey,
      warningParams,
    };
  }

  if (!TENANT_ID_PATTERN.test(normalized)) {
    // Defensive: unreachable given the collapse + strip above.
    return {
      isValid: false,
      normalized,
      errorKey: 'activation.tenant_name_invalid_chars',
      warningKey,
      warningParams,
    };
  }

  return {
    isValid: true,
    normalized,
    warningKey,
    warningParams,
  };
}

/**
 * Human-readable rule summary for inline UI hints. The wording matches the
 * backend's get_tenant_id_validation_rules().
 */
export function getTenantIdRules(): {
  minLength: number;
  maxLength: number;
  allowedChars: string;
  description: string;
} {
  return {
    minLength: MIN_TENANT_ID_LENGTH,
    maxLength: MAX_TENANT_ID_LENGTH,
    allowedChars: 'letras minúsculas, números y guiones (-)',
    description:
      `El identificador debe tener entre ${MIN_TENANT_ID_LENGTH} y ${MAX_TENANT_ID_LENGTH} caracteres. ` +
      'Solo se permiten letras minúsculas, números y guiones (-). ' +
      'Los espacios, acentos y caracteres especiales se convertirán automáticamente en guiones.',
  };
}

/**
 * Client-side uniqueness check stub. Actual uniqueness is enforced by the
 * backend (409 TENANT_EXISTS on POST /api/admin/tenants); this is kept so
 * existing UI code that wires up an async check has something to call.
 */
export function checkTenantIdUniqueness(): Promise<boolean> {
  return Promise.resolve(true);
}
