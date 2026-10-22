import type { AuthInfo, PlanTier } from '../hooks/types';

export interface MockFixtures {
  moduleId: string;
  auth?: AuthInfo;
  tenantPlan?: PlanTier;
  i18n?: { lang?: string; translations?: Record<string, Record<string, string>> };
}
