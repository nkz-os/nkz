/**
 * Locale parity guard for the host app.
 *
 * i18next falls back to the Spanish locale (`fallbackLng: 'es'`), so a key that
 * exists in `es` but not in `en` is silently rendered in Spanish on an English
 * page, and a key that exists nowhere is rendered as the raw key. Neither
 * produces an error, so this test is the only thing that notices.
 *
 * Scope on purpose:
 *  - `es` -> `en` is asserted for the whole `common.json`.
 *  - ca/eu/fr/pt are NOT asserted for full parity: they still have known gaps
 *    that are being completed separately, so a blanket check would fail by
 *    design. Instead, a fixed list of keys that were added together with their
 *    translations is asserted in all six locales; it cannot be tripped by the
 *    pre-existing gaps.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const LOCALES_DIR = resolve(__dirname, '../../public/locales');
const LANGUAGES = ['es', 'en', 'ca', 'eu', 'fr', 'pt'] as const;

type JsonTree = { [key: string]: string | JsonTree };

function load(lang: string, namespace: string): JsonTree {
  const file = resolve(LOCALES_DIR, lang, `${namespace}.json`);
  return JSON.parse(readFileSync(file, 'utf8')) as JsonTree;
}

/** Flattens nested objects into dot-separated keys, the way i18next resolves them. */
function flatten(tree: JsonTree, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'string') {
      out[path] = value;
    } else {
      Object.assign(out, flatten(value, path));
    }
  }
  return out;
}

function placeholders(text: string): string[] {
  return (text.match(/\{\{\s*[\w.]+\s*\}\}/g) ?? []).map((p) => p.replace(/\s+/g, '')).sort();
}

/**
 * Keys that are rendered by static `t('...')` calls in reachable components and
 * were absent from `es` (raw key shown, or only an inline English default).
 * They live in `common.json`.
 */
const COMMON_KEYS_IN_ALL_LOCALES: readonly string[] = [
  // Forgot-password page (public route)
  'forgot_password.title',
  'forgot_password.subtitle',
  'forgot_password.email',
  'forgot_password.email_placeholder',
  'forgot_password.email_help',
  'forgot_password.email_required',
  'forgot_password.send_link',
  'forgot_password.sending',
  'forgot_password.email_sent',
  'forgot_password.email_sent_message',
  'forgot_password.check_spam',
  'forgot_password.back_to_login',
  'forgot_password.error',
  // Plan selector (limits management) and module cards
  'select_plan',
  'no_description_available',
  // Agronomic panel parcel button
  'weather.agro_panel.select_parcel',
  // Tenant user management
  'settings.users.limit_reached',
  'settings.users.platform_only_roles_warning',
  'settings.users.usage_hint',
  // Platform admin panel
  'admin.activation_codes',
  'admin.all_platform_users',
  'admin.all_platform_users_desc',
  'admin.assign_button',
  'admin.assign_role',
  'admin.assign_user_button',
  'admin.assign_user_title',
  'admin.change',
  'admin.config_saved',
  'admin.config_tab',
  'admin.contact_email',
  'admin.contact_info',
  'admin.contact_phone',
  'admin.create_tenant_button',
  'admin.create_tenant_error',
  'admin.create_tenant_required',
  'admin.create_tenant_title',
  'admin.create_user_button',
  'admin.create_user_title',
  'admin.current_tenant',
  'admin.expiration_date',
  'admin.limits_hint',
  'admin.limits_read_only',
  'admin.new_tenant',
  'admin.no_activations',
  'admin.no_tenant_results',
  'admin.no_tenants',
  'admin.no_user_results',
  'admin.no_users',
  'admin.no_users_global',
  'admin.owner_email',
  'admin.owner_password',
  'admin.plan',
  'admin.plan_change_limits_warning',
  'admin.resource_limits',
  'admin.search_tenants',
  'admin.search_users',
  'admin.search_users_placeholder',
  'admin.status_suspended',
  'admin.tenant_config',
  'admin.tenant_name',
  'admin.type_to_search',
  'admin.user_assigned',
  'admin.users_count',
  'admin.users_tab',
];

/**
 * `navigation.*` keys are resolved by `useI18n().t` into the `navigation`
 * namespace (`navigation.json`), not into `common.json`.
 */
const NAVIGATION_KEYS_IN_ALL_LOCALES: readonly string[] = [
  'admin_badge',
  'manage_modules',
  'section_admin',
  'section_modules',
];

describe('locale parity: es -> en (common.json)', () => {
  const es = flatten(load('es', 'common'));
  const en = flatten(load('en', 'common'));

  it('every Spanish key exists in English', () => {
    const missing = Object.keys(es).filter((key) => !(key in en));
    expect(missing, `Keys in es/common.json missing from en/common.json:\n${missing.join('\n')}`).toEqual([]);
  });

  it('keeps {{interpolation}} placeholders identical between es and en', () => {
    const mismatched = Object.keys(es)
      .filter((key) => key in en)
      .filter((key) => placeholders(es[key]).join('|') !== placeholders(en[key]).join('|'))
      .map((key) => `${key}: es=${placeholders(es[key]).join(',')} en=${placeholders(en[key]).join(',')}`);
    expect(mismatched, `Placeholder mismatch:\n${mismatched.join('\n')}`).toEqual([]);
  });
});

describe('locale parity: keys added with full translations (all languages)', () => {
  const english = flatten(load('en', 'common'));

  for (const lang of LANGUAGES) {
    it(`${lang}/common.json has every added key`, () => {
      const flat = flatten(load(lang, 'common'));
      const missing = COMMON_KEYS_IN_ALL_LOCALES.filter((key) => !(key in flat));
      expect(missing, `Keys missing from ${lang}/common.json:\n${missing.join('\n')}`).toEqual([]);
    });

    it(`${lang}/navigation.json has every navigation key`, () => {
      const flat = flatten(load(lang, 'navigation'));
      const missing = NAVIGATION_KEYS_IN_ALL_LOCALES.filter((key) => !(key in flat));
      expect(missing, `Keys missing from ${lang}/navigation.json:\n${missing.join('\n')}`).toEqual([]);
    });

    it(`${lang}/common.json keeps placeholders of the added keys`, () => {
      const flat = flatten(load(lang, 'common'));
      const mismatched = COMMON_KEYS_IN_ALL_LOCALES.filter((key) => key in flat && key in english).filter(
        (key) => placeholders(flat[key]).join('|') !== placeholders(english[key]).join('|'),
      );
      expect(mismatched, `Placeholder mismatch vs en in ${lang}:\n${mismatched.join('\n')}`).toEqual([]);
    });
  }
});
