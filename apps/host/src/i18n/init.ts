/**
 * Host i18n bootstrap — initializes the shared i18next singleton before React mounts.
 *
 * Previously this lived in @nekazari/sdk (initI18n + unwrapI18nPlugin) and ran
 * inside NekazariI18nProvider's useEffect. Moving it to the host's main bundle
 * eliminates the defensive unwrapI18nPlugin — imports are direct, not routed
 * through Module Federation chunking.
 *
 * Modules still import { i18n } from '@nekazari/sdk' and call addResourceBundle()
 * on this same singleton instance.
 */
import HttpBackend from 'i18next-http-backend';
import { initReactI18next } from 'react-i18next';
import { i18n, type SupportedLanguage } from '@nekazari/sdk';
import { hostI18nConfig } from '../config/hostI18nConfig';

let initialized = false;

export async function initHostI18n(): Promise<void> {
  if (initialized || i18n.isInitialized) return;
  initialized = true;

  const supportedLangs = hostI18nConfig.supportedLanguages ?? ['es', 'en'];
  const storedLang = localStorage.getItem('language') as SupportedLanguage | null;
  const browserLang = navigator.language.split('-')[0] as SupportedLanguage;
  const detectedLang: string =
    storedLang ||
    (supportedLangs.includes(browserLang)
      ? browserLang
      : hostI18nConfig.defaultLanguage ?? 'es');

  await i18n
    .use(HttpBackend)
    .use(initReactI18next)
    .init({
      lng: detectedLang,
      fallbackLng: hostI18nConfig.fallbackLanguage ?? 'es',
      supportedLngs: supportedLangs,
      ns: hostI18nConfig.namespaces,
      defaultNS: 'common',

      backend: {
        loadPath: hostI18nConfig.loadPath,
        crossDomain: false,
      },

      react: {
        useSuspense: false,
      },

      interpolation: {
        escapeValue: false,
      },

      debug: hostI18nConfig.debug ?? false,
      load: 'languageOnly',
      cleanCode: true,
    });

  if (!storedLang) {
    localStorage.setItem('language', detectedLang);
  }
}
