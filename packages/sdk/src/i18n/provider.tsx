/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

// =============================================================================
// i18n Re-exports
// =============================================================================
// NekazariI18nProvider was removed (2026-05-18) — the host now initializes
// i18next directly in main.tsx (apps/host/src/i18n/init.ts) and wraps the
// app in <I18nextProvider> from react-i18next.
//
// This file remains as a convenience re-export barrel for hooks and utilities.

export { useTranslation, Trans, Translation } from 'react-i18next';
export { changeLanguage, getCurrentLanguage, getSupportedLanguages } from './config';
export type { SupportedLanguage } from './config';
