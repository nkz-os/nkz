// =============================================================================
// Module API Contract — Host Compatibility Checking
// =============================================================================
// Checks whether a module's declared API contract is compatible with the
// current host version. Uses simple semver range parsing (^, ~, exact)
// without external dependencies.
// =============================================================================

import type { ModuleApiContract, ModuleCompatibilityResult } from '@nekazari/sdk';

/** Current host API version. Bump on breaking changes to the module contract. */
export const HOST_API_VERSION = '2.0.0';

/**
 * Check if a module's declared API contract is compatible with the current host.
 * The module's `hostApiVersion` is a semver RANGE (e.g. "^2.0.0" or "~2.0.0").
 * We do a simple major.minor comparison without external dependencies.
 */
export function checkModuleContract(
  contract: ModuleApiContract | undefined | null,
  hostVersion: string = HOST_API_VERSION,
): ModuleCompatibilityResult {
  if (!contract) {
    return {
      compatible: false,
      reason: 'Module does not declare an API contract (api_contract in manifest.json)',
      hostVersion,
    };
  }

  const hostParts = hostVersion.split('.').map(Number);
  const cleanRange = contract.hostApiVersion.replace(/[^0-9.]/g, '');
  const rangeParts = cleanRange.split('.').map(Number);

  const hostMajor = hostParts[0] ?? 0;
  const hostMinor = hostParts[1] ?? 0;
  const rangeMajor = rangeParts[0] ?? 0;
  const rangeMinor = rangeParts[1] ?? 0;

  // caret (^) range: same major, >= range minor, patch >= range patch when minor matches
  if (contract.hostApiVersion.startsWith('^')) {
    const rangePatch = rangeParts[2] ?? 0;
    const hostPatch = hostParts[2] ?? 0;
    if (hostMajor !== rangeMajor || hostMinor < rangeMinor ||
        (hostMinor === rangeMinor && hostPatch < rangePatch)) {
      return {
        compatible: false,
        reason: `Module requires host API ${contract.hostApiVersion}, but host is ${hostVersion}`,
        contract,
        hostVersion,
      };
    }
  }
  // tilde (~) range: same major.minor, patch >= range patch
  else if (contract.hostApiVersion.startsWith('~')) {
    const rangePatch = rangeParts[2] ?? 0;
    const hostPatch = hostParts[2] ?? 0;
    if (hostMajor !== rangeMajor || hostMinor !== rangeMinor || hostPatch < rangePatch) {
      return {
        compatible: false,
        reason: `Module requires host API ${contract.hostApiVersion}, but host is ${hostVersion}`,
        contract,
        hostVersion,
      };
    }
  }
  // exact match
  else if (hostVersion !== cleanRange) {
    return {
      compatible: false,
      reason: `Module requires host API ${contract.hostApiVersion}, but host is ${hostVersion}`,
      contract,
      hostVersion,
    };
  }

  return { compatible: true, contract, hostVersion };
}
