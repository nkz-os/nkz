// =============================================================================
// Theme Toggle Component
// =============================================================================
// Button component for toggling between light, dark, and system theme

import React from 'react';
import { Moon, Sun, Monitor } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
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
      return 'Sistema';
    }
    return resolvedTheme === 'dark' ? 'Oscuro' : 'Claro';
  };

  if (variant === 'compact') {
    return (
      <Button
        onClick={toggleTheme}
        className={`inline-flex items-center justify-center w-full rounded-md border border-nkz-border dark:border-gray-600 shadow-sm px-3 py-2 bg-white dark:bg-gray-800 text-sm font-medium text-gray-700 dark:text-gray-200 hover:bg-nkz-bg-secondary dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500 transition-all duration-200 ${className}`}
        aria-label={`Cambiar tema (${getLabel()})`}
        title={`Tema actual: ${getLabel()}`}
      >
        {getIcon()}
        <span className="ml-2 text-xs">{getLabel()}</span>
      </Button>
    );
  }

  return (
    <Button
      onClick={toggleTheme}
      className={`relative inline-flex items-center justify-center w-10 h-10 rounded-md border border-nkz-border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 hover:bg-nkz-bg-secondary dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500 transition-all duration-200 ${className}`}
      aria-label={`Cambiar tema (${getLabel()})`}
      title={`Tema actual: ${getLabel()}. Click para cambiar.`}
    >
      <div className="absolute inset-0 flex items-center justify-center transition-all duration-300 transform">
        {resolvedTheme === 'dark' ? (
          <Moon className="h-5 w-5 text-gray-700 dark:text-gray-200" />
        ) : (
          <Sun className="h-5 w-5 text-gray-700 dark:text-gray-200" />
        )}
      </div>
      {theme === 'system' && (
        <span className="absolute -top-1 -right-1 w-2 h-2 bg-nkz-info rounded-full ring-2 ring-white dark:ring-gray-800" />
      )}
    </Button>
  );
};

