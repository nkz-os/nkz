/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

// =============================================================================
// i18n Configuration - Centralized i18next Setup
// =============================================================================
// This module provides centralized i18next configuration for the NKZ platform.
// All modules (Host, Weather, NDVI, etc.) share the same i18next instance
// but can load their own translation namespaces.

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import HttpBackendRaw from 'i18next-http-backend';

// =============================================================================
// MF 2.0 / CJS interop unwrap
// =============================================================================
// Under Module Federation 2.0 chunking + tsup's CJS interop, the default
// export of CJS plugins (i18next-http-backend, i18next-browser-languagedetector,
// ...) arrives at the consumer wrapped: `{ default: ActualPlugin }` instead of
// `ActualPlugin`. i18next.use() inspects `module.type` (the i18next plugin
// contract: 'backend' | 'languageDetector' | 'i18nFormat' | 'postProcessor' |
// 'logger' | '3rdParty') and throws "You are passing a wrong module!" if
// absent, bricking init.
//
// Walk the `.default` chain until we find an object/function whose `.type` is
// a string — that's the actual i18next plugin. Direct/unwrapped imports pass
// through untouched on the first iteration; one or more layers of wrapping
// are flattened. Note: HttpBackend's default is a class (typeof === 'function',
// NOT 'object'), so the type check must accept both.
function unwrapI18nPlugin<T>(mod: T): T {
  let cur: any = mod;
  for (let depth = 0; depth < 5; depth++) {
    if (cur && typeof cur.type === 'string') return cur as T;
    if (cur && cur.default && cur.default !== cur) {
      cur = cur.default;
      continue;
    }
    break;
  }
  return mod;
}

const HttpBackend = unwrapI18nPlugin(HttpBackendRaw);

export type SupportedLanguage = 'es' | 'en' | 'ca' | 'eu' | 'fr' | 'pt';

export interface I18nConfig {
  defaultLanguage?: SupportedLanguage;
  fallbackLanguage?: SupportedLanguage;
  supportedLanguages?: SupportedLanguage[];
  loadPath?: string;
  namespaces?: string[];
  debug?: boolean;
}

const DEFAULT_CONFIG: Required<I18nConfig> = {
  defaultLanguage: 'es',
  fallbackLanguage: 'es',
  supportedLanguages: ['es', 'en', 'ca', 'eu', 'fr', 'pt'],
  loadPath: '/locales/{{lng}}/{{ns}}.json',
  namespaces: ['common'],
  debug: false,
};

/** Serialize concurrent init calls (StrictMode / unstable `config` identity in consumers). */
let initI18nPromise: Promise<void> | null = null;

async function runI18nInit(config: I18nConfig): Promise<void> {
  if (i18n.isInitialized) return;

  const finalConfig = { ...DEFAULT_CONFIG, ...config };

  const storedLang = localStorage.getItem('language') as SupportedLanguage | null;
  const browserLang = navigator.language.split('-')[0] as SupportedLanguage;
  const detectedLang =
    storedLang ||
    (finalConfig.supportedLanguages.includes(browserLang) ? browserLang : finalConfig.defaultLanguage);

  // Manual detection above already resolves the language (localStorage > browser >
  // configured default) and we pass it as `lng`. i18next-browser-languagedetector
  // was redundant and, under Module Federation 2.0 chunking, the host receives its
  // default export wrapped in a namespace object — i18next.use() then rejects it
  // with "You are passing a wrong module!" and bricks the landing page.
  await i18n
    .use(HttpBackend)
    .use(initReactI18next)
    .init({
      lng: detectedLang,
      fallbackLng: finalConfig.fallbackLanguage,
      supportedLngs: finalConfig.supportedLanguages,
      ns: finalConfig.namespaces,
      defaultNS: 'common',

      backend: {
        loadPath: finalConfig.loadPath,
        crossDomain: false,
      },

      react: {
        useSuspense: false,
      },

      interpolation: {
        escapeValue: false,
      },

      debug: finalConfig.debug,

      load: 'languageOnly',
      cleanCode: true,
    });

  if (!storedLang) {
    localStorage.setItem('language', detectedLang);
  }
}

/**
 * Initialize i18next with the provided configuration
 *
 * @param config Configuration options for i18next
 * @returns Promise that resolves when i18next is initialized
 */
export async function initI18n(config: I18nConfig = {}): Promise<void> {
  if (i18n.isInitialized) return;
  if (!initI18nPromise) {
    initI18nPromise = runI18nInit(config).finally(() => {
      initI18nPromise = null;
    });
  }
  await initI18nPromise;
}

/**
 * Change the current language
 * 
 * @param language Language code to switch to
 */
export async function changeLanguage(language: SupportedLanguage): Promise<void> {
  await i18n.changeLanguage(language);
  localStorage.setItem('language', language);
}

/**
 * Get current language
 */
export function getCurrentLanguage(): SupportedLanguage {
  return ((i18n.language || 'es').split('-')[0] as SupportedLanguage) || 'es';
}

/**
 * Get supported languages with display names
 */
export function getSupportedLanguages(): Record<SupportedLanguage, string> {
  return {
    es: 'Español',
    en: 'English',
    ca: 'Català',
    eu: 'Euskera',
    fr: 'Français',
    pt: 'Português',
  };
}

// Export the i18n instance for direct access if needed
export { i18n };

