// =============================================================================
// Theme Toggle Component
// =============================================================================
// Button component for toggling between light, dark, and system theme

import React from 'react';
import { Moon, Sun, Monitor } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { useI18n } from '@/context/I18nContext';
import { Button } from '@nekazari/ui-kit';

interface ThemeToggleProps {
  variant?: 'default' | 'compact';
  className?: string;
}

export const ThemeToggle: React.FC<ThemeToggleProps> = ({
  variant = 'default',
  className = '',
}) => {
  const { theme, resolvedTheme, toggleTheme } = useTheme();
  const { t } = useI18n();

  const getIcon = () => {
    if (theme === 'system') {
      return <Monitor className="h-4 w-4" />;
    }
    return resolvedTheme === 'dark' ? (
      <Moon className="h-4 w-4" />
    ) : (
      <Sun className="h-4 w-4" />
    );
  };

  const getLabel = () => {
    if (theme === 'system') {
      return t('theme.system');
    }
    return resolvedTheme === 'dark' ? t('theme.dark') : t('theme.light');
  };

  if (variant === 'compact') {
    return (
      <Button
        variant="secondary"
        onClick={toggleTheme}
        className={`w-full shadow-sm ${className}`}
        aria-label={t('theme.toggle_aria', { label: getLabel() })}
        title={t('theme.current_title', { label: getLabel() })}
      >
        {getIcon()}
        <span className="ml-2 text-xs">{getLabel()}</span>
      </Button>
    );
  }

  return (
    <Button
      variant="secondary"
      onClick={toggleTheme}
      className={`relative w-10 h-10 ${className}`}
      aria-label={t('theme.toggle_aria', { label: getLabel() })}
      title={t('theme.current_title', { label: getLabel() })}
    >
      <div className="absolute inset-0 flex items-center justify-center transition-all duration-300 transform">
        {resolvedTheme === 'dark' ? (
          <Moon className="h-5 w-5" />
        ) : (
          <Sun className="h-5 w-5" />
        )}
      </div>
      {theme === 'system' && (
        <span className="absolute -top-1 -right-1 w-2 h-2 bg-nkz-info rounded-full ring-2 ring-white dark:ring-gray-800" />
      )}
    </Button>
  );
};

