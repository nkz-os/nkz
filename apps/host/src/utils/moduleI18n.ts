// =============================================================================
// Module name / description in the UI language
// =============================================================================
// The API returns, per module, the plain `label` / `displayName` / `description`
// plus `labelI18n` / `descriptionI18n` maps ({ es, en, ca, eu, fr, pt }). These
// pure helpers pick the text for a language and fall back in a fixed order, so
// every place that shows a module name or description resolves it the same way.

export type I18nMap = Record<string, string>;

/** The fields of a module definition (or marketplace row) the helpers read. */
export interface ModuleTextSource {
  id?: string;
  name?: string;
  displayName?: string;
  /** Marketplace rows are snake_case. */
  display_name?: string;
  label?: string;
  description?: string;
  labelI18n?: I18nMap;
  descriptionI18n?: I18nMap;
}

const asText = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() !== '' ? value : undefined;

/**
 * Validate a translation map received from the API: a plain object whose
 * non-blank string entries are kept. Anything else, or a map left empty,
 * yields undefined so callers fall back to the plain text.
 */
export function sanitizeI18nMap(value: unknown): I18nMap | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const clean: I18nMap = {};
  for (const [lang, text] of Object.entries(value as Record<string, unknown>)) {
    const kept = asText(text);
    if (kept !== undefined) clean[lang] = kept;
  }
  return Object.keys(clean).length > 0 ? clean : undefined;
}

/** Text for `lang` in `map`: the exact tag first (`pt-br`), then its primary subtag (`pt`). */
function pick(map: I18nMap | undefined, lang: string | undefined): string | undefined {
  if (!map || typeof map !== 'object' || !lang) return undefined;
  const tag = lang.trim().toLowerCase().replace(/_/g, '-');
  if (!tag) return undefined;
  const primary = tag.split('-')[0];
  return asText(map[tag]) ?? asText(map[primary]);
}

/** Module name for `lang`: translation, then label, displayName, name, id. */
export function moduleLabel(module: ModuleTextSource, lang: string | undefined): string {
  return (
    pick(module.labelI18n, lang) ??
    asText(module.label) ??
    asText(module.displayName) ??
    asText(module.display_name) ??
    asText(module.name) ??
    asText(module.id) ??
    ''
  );
}

/** Module description for `lang`: translation, then the plain description. */
export function moduleDescription(
  module: ModuleTextSource,
  lang: string | undefined,
): string | undefined {
  return pick(module.descriptionI18n, lang) ?? asText(module.description);
}
