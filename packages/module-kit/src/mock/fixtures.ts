import type { MockFixtures } from './types';

/** Default in-memory fixture used by MockProvider when no `fixtures` prop is passed. */
export const DEFAULT_MOCK_FIXTURES: Required<Omit<MockFixtures, 'i18n' | 'tenantPlan'>> & {
  i18n: { lang: string; translations: Record<string, Record<string, string>> };
  tenantPlan: 'pro';
} = {
  moduleId: 'mock',
  auth: {
    user: { id: 'dev-user', email: 'dev@nekazari.test', name: 'Dev User' },
    tenantId: 'dev-tenant',
    tenantName: 'Dev Tenant',
    roles: ['Farmer', 'TenantAdmin'],
    isAuthenticated: true,
  },
  tenantPlan: 'pro',
  i18n: {
    lang: 'en',
    translations: {
      en: {},
      es: {},
    },
  },
};
