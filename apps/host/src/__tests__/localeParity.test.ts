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
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
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
  // Theme toggle (navigation)
  'theme.system',
  'theme.light',
  'theme.dark',
  'theme.toggle_aria',
  'theme.current_title',
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
  // Platform admin control center tabs (AdminManagement). `admin.nek_codes` and
  // the root `limits` are reused for two of the eight tabs.
  'admin.tenants_tab',
  'admin.nek_codes',
  'limits',
  'admin.terms_tab',
  'admin.platform_apis_tab',
  'admin.platform_tab',
  'admin.logs_tab',
  'admin.assets_tab',
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

describe('locale parity: entity wizard (all languages)', () => {
  const es = flatten(load('es', 'common'));
  const wizardKeys = Object.keys(es).filter((key) => key.startsWith('wizard.'));

  for (const lang of LANGUAGES) {
    it(`${lang}/common.json has every wizard key with the Spanish placeholders`, () => {
      const flat = flatten(load(lang, 'common'));
      const problems = wizardKeys.filter(
        (key) => !(key in flat) || placeholders(flat[key]).join('|') !== placeholders(es[key]).join('|'),
      );
      expect(problems, `Wizard keys missing or with wrong placeholders in ${lang}:\n${problems.join('\n')}`).toEqual([]);
    });
  }
});

describe('English locale contains no Spanish text', () => {
  // Spanish-only characters. A leftover Spanish value in `en` is invisible to the
  // es -> en parity check above (the key exists), so scan the values instead.
  const SPANISH_ONLY = /[ñáéíóúü¿¡]/;

  for (const namespace of ['common', 'layout', 'navigation']) {
    it(`en/${namespace}.json has no Spanish-only characters`, () => {
      const offenders = Object.entries(flatten(load('en', namespace)))
        .filter(([, value]) => SPANISH_ONLY.test(value))
        .map(([key, value]) => `${key}: ${value}`);
      expect(offenders, `Spanish text in en/${namespace}.json:\n${offenders.join('\n')}`).toEqual([]);
    });
  }
});

/**
 * `useI18n().t('<ns>.<key>')` is not a plain lookup: a leading `common.`,
 * `layout.` or `navigation.` segment is stripped and used as the i18next
 * namespace, so `t('layout.cookie_settings')` reads `cookie_settings` from
 * `layout.json`, not `layout.cookie_settings` from `common.json`. A key that only
 * exists at the "plain" path is rendered as the bare key in every language.
 */
describe('useI18n namespace-prefixed keys resolve where the wrapper looks', () => {
  const SRC_DIR = resolve(__dirname, '..');
  const NAMESPACES = ['common', 'layout', 'navigation'] as const;
  const CALL = /(?<![\w.$])t\(\s*(['"])((?:common|layout|navigation)\.[^'"\n]+?)\1\s*[,)]/g;

  function sourceFiles(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) {
        return name === '__tests__' || name === 'node_modules' ? [] : sourceFiles(path);
      }
      return /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name) ? [path] : [];
    });
  }

  it('every static t("<ns>.<key>") in useI18n components exists in <ns>.json (es)', () => {
    const catalogue = Object.fromEntries(NAMESPACES.map((ns) => [ns, flatten(load('es', ns))]));
    const unresolved: string[] = [];
    for (const file of sourceFiles(SRC_DIR)) {
      const text = readFileSync(file, 'utf8');
      if (!text.includes('useI18n(')) continue;
      for (const match of text.matchAll(CALL)) {
        const [namespace, ...rest] = match[2].split('.');
        const key = rest.join('.');
        const flat = catalogue[namespace];
        const found = key in flat || ['_one', '_other', '_zero', '_few', '_many', '_two'].some((s) => `${key}${s}` in flat);
        if (!found) unresolved.push(`${file.slice(SRC_DIR.length + 1)}: t('${match[2]}') -> ${namespace}.json "${key}"`);
      }
    }
    expect(unresolved, `Keys the wrapper cannot resolve:\n${unresolved.join('\n')}`).toEqual([]);
  });

  for (const lang of LANGUAGES) {
    it(`${lang} has the previously unresolved keys in the wrapper's namespaces`, () => {
      const layout = flatten(load(lang, 'layout'));
      const common = flatten(load(lang, 'common'));
      expect('cookie_settings' in layout, `${lang}/layout.json: cookie_settings`).toBe(true);
      expect('saved_successfully' in common, `${lang}/common.json: root saved_successfully`).toBe(true);
    });
  }
});
