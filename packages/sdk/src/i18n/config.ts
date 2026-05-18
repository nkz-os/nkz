/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

// =============================================================================
// i18n Singleton — shared i18next instance
// =============================================================================
// This module provides the canonical i18next singleton shared across the host
// and all remote modules. The host bootstraps the instance (apps/host/src/i18n/init.ts)
// before React mounts; modules import { i18n } to register their namespaces via
// addResourceBundle() at import time.
//
// initI18n() and unwrapI18nPlugin() were moved to the host (2026-05-18) to
// eliminate defensive CJS-interop unwrapping that was only needed when the SDK
// was loaded through Module Federation chunking.

import i18n from 'i18next';

export type SupportedLanguage = 'es' | 'en' | 'ca' | 'eu' | 'fr' | 'pt';

export interface I18nConfig {
  defaultLanguage?: SupportedLanguage;
  fallbackLanguage?: SupportedLanguage;
  supportedLanguages?: SupportedLanguage[];
  loadPath?: string;
  namespaces?: string[];
  debug?: boolean;
}

/**
 * Change the current language
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

// Export the i18n instance for direct access (modules use this for addResourceBundle)
export { i18n };
