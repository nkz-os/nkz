// =============================================================================
// Language Selector Component - Reusable Language Switcher
// =============================================================================

import React, { useState, useEffect, useRef } from 'react';
import { Globe } from 'lucide-react';
import { useTranslation, changeLanguage, getCurrentLanguage, getSupportedLanguages, SupportedLanguage } from '@nekazari/sdk';
import { Button } from '@nekazari/ui-kit';

interface LanguageSelectorProps {
  className?: string;
  variant?: 'default' | 'compact' | 'iconOnly';
}

export const LanguageSelector: React.FC<LanguageSelectorProps> = ({
  className = '',
  variant = 'default'
}) => {
  const { i18n } = useTranslation();
  const [language, setLanguage] = useState<SupportedLanguage>(getCurrentLanguage());
  const supportedLanguages = getSupportedLanguages();
  const [showMenu, setShowMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Listen for language changes
  useEffect(() => {
    const handleLanguageChanged = (lng: string) => {
      setLanguage(lng.split('-')[0] as SupportedLanguage);
    };

    if (typeof i18n.on === 'function') {
      i18n.on('languageChanged', handleLanguageChanged);
    }
    return () => {
      if (typeof i18n.off === 'function') {
        i18n.off('languageChanged', handleLanguageChanged);
      }
    };
  }, [i18n]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setShowMenu(false);
      }
    };

    if (showMenu) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [showMenu]);

  const handleLanguageChange = async (lang: string) => {
    await changeLanguage(lang as SupportedLanguage);
    setShowMenu(false);
  };

  if (variant === 'iconOnly') {
    return (
      <div className={`relative inline-block ${className}`} ref={menuRef}>
        <Button
          type="button"
          onClick={() => setShowMenu(!showMenu)}
          title={supportedLanguages[language] || supportedLanguages['es']}
          aria-label={supportedLanguages[language] || 'Idioma'}
          variant="ghost"
          size="sm"
          className="inline-flex items-center justify-center w-9 h-9 rounded-md border border-nkz-border dark:border-nkz-border shadow-sm bg-white dark:bg-nkz-surface text-nkz-text-secondary hover:bg-nkz-bg-secondary dark:hover:bg-nkz-surface-raised"
        >
          <Globe className="h-4 w-4" />
        </Button>
        {showMenu && (
          <div className="absolute right-0 mt-2 w-48 rounded-md shadow-lg bg-white dark:bg-nkz-surface ring-1 ring-black ring-opacity-5 z-20">
            {Object.entries(supportedLanguages).map(([code, name]) => (
              <Button
                key={code}
                variant="ghost"
                size="sm"
                onClick={() => handleLanguageChange(code)}
                className={`block w-full text-left px-4 py-2 text-sm ${
                  language === code
                    ? 'bg-nkz-accent-soft text-nkz-accent-strong'
                    : 'text-nkz-text-secondary hover:bg-nkz-bg-secondary dark:hover:bg-nkz-surface-raised'
                }`}
              >
                {(name as React.ReactNode)}
              </Button>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (variant === 'compact') {
    return (
      <div className={`relative inline-block ${className}`} ref={menuRef}>
        <Button
          type="button"
          onClick={() => setShowMenu(!showMenu)}
          variant="ghost"
          size="sm"
          className="inline-flex items-center justify-center w-full rounded-md border border-nkz-border shadow-sm px-3 py-2 bg-white dark:bg-nkz-surface text-nkz-text-secondary hover:bg-nkz-bg-secondary dark:hover:bg-nkz-surface-raised"
        >
          <Globe className="h-4 w-4 mr-2" />
          <span className="text-xs">{supportedLanguages[language] || supportedLanguages['es']}</span>
        </Button>
        {showMenu && (
          <div className="absolute right-0 mt-2 w-48 rounded-md shadow-lg bg-white dark:bg-nkz-surface ring-1 ring-black ring-opacity-5 z-20">
            {Object.entries(supportedLanguages).map(([code, name]) => (
              <Button
                key={code}
                variant="ghost"
                size="sm"
                onClick={() => handleLanguageChange(code)}
                className={`block w-full text-left px-4 py-2 text-sm ${
                  language === code
                    ? 'bg-nkz-accent-soft text-nkz-accent-strong'
                    : 'text-nkz-text-secondary hover:bg-nkz-bg-secondary dark:hover:bg-nkz-surface-raised'
                }`}
              >
                {(name as React.ReactNode)}
              </Button>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={`relative inline-block text-left ${className}`} ref={menuRef}>
      <Button
        type="button"
        onClick={() => setShowMenu(!showMenu)}
        variant="ghost"
        size="sm"
        className="inline-flex items-center justify-center w-full rounded-md border border-nkz-border shadow-sm px-4 py-2 bg-white dark:bg-nkz-surface text-nkz-text-secondary hover:bg-nkz-bg-secondary dark:hover:bg-nkz-surface-raised"
      >
        <Globe className="h-5 w-5 mr-2" />
        {supportedLanguages[language] || supportedLanguages['es']}
      </Button>
      {showMenu && (
        <div className="absolute right-0 mt-2 w-48 rounded-md shadow-lg bg-white dark:bg-nkz-surface ring-1 ring-black ring-opacity-5 z-20">
          {Object.entries(supportedLanguages).map(([code, name]) => (
            <Button
              key={code}
              variant="ghost"
              size="sm"
              onClick={() => handleLanguageChange(code)}
              className={`block w-full text-left px-4 py-2 text-sm ${
                language === code
                  ? 'bg-nkz-accent-soft text-nkz-accent-strong'
                  : 'text-nkz-text-secondary hover:bg-nkz-bg-secondary dark:hover:bg-nkz-surface-raised'
              }`}
            >
              {(name as React.ReactNode)}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
};

export default LanguageSelector;
