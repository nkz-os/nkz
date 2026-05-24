import type { ReactNode } from 'react';

export type EntitlementGuardProps = {
  required: string;
  tenantEntitlements: string[];
  fallback?: ReactNode;
  children: ReactNode;
};

export function EntitlementGuard({ required, tenantEntitlements, fallback, children }: EntitlementGuardProps) {
  if (tenantEntitlements.includes(required)) return <>{children}</>;
  if (fallback !== undefined) return <>{fallback}</>;
  return (
    <div className="nkz-entitlement-guard" role="status">
      Entitlement required: {required}
    </div>
  );
}
