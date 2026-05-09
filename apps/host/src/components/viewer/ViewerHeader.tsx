// =============================================================================
// Viewer Header - Floating Header for UnifiedViewer
// =============================================================================
// A glassmorphism header that floats over the map in the /entities page.
// Features:
// - Nekazari logo with dropdown menu on hover
// - Language selector and theme toggle on the right
// - Same navigation items as the sidebar
//
// This component replaces the solid Navigation bar in UnifiedViewer.

import React, { useState, useRef, useEffect } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/context/KeycloakAuthContext';
import { useModules } from '@/context/ModuleContext';
import { useI18n } from '@/context/I18nContext';
import { LanguageSelector } from '@/components/LanguageSelector';
import { useTheme } from '@/context/ThemeContext';
import {
    CORE_NAVIGATION_ITEMS,
    ADMIN_NAVIGATION_ITEMS,
    filterNavigationItemsByRoles,
    sortNavigationItemsByPriority,
} from '@/config/navigation';
import {
    ChevronDown,
    LogOut,
    Puzzle,
    Sun,
    Moon,
} from 'lucide-react';

// Glassmorphism styling
const surfaceStyles = {
    base: 'bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 shadow-lg',
    hover: 'hover:bg-slate-50 dark:hover:bg-slate-800',
};

// Module icon mapping (same as Sidebar)
const moduleIconMap: Record<string, React.ComponentType<{ className?: string }>> = {
    'puzzle': Puzzle,
    'default': Puzzle,
};

export interface ViewerHeaderProps {
    /** Toggle unified viewer layer manager */
    onToggleLayerManager?: () => void;
}

export const ViewerHeader: React.FC<ViewerHeaderProps> = ({ onToggleLayerManager: _onToggleLayerManager }) => {
    const { user, logout, hasAnyRole: _hasAnyRole } = useAuth();
    const { modules } = useModules();
    const { t } = useI18n();
    const { resolvedTheme, toggleTheme } = useTheme();
    const isLight = resolvedTheme === 'light';
    const navigate = useNavigate();
    const location = useLocation();
    const [isMenuOpen, setIsMenuOpen] = useState(false);
    const menuRef = useRef<HTMLDivElement>(null);
    const menuTimeoutRef = useRef<NodeJS.Timeout | null>(null);

    // User roles
    const userRoles = user?.roles || [];
    const isPlatformAdmin = userRoles.includes('PlatformAdmin');

    // Get navigation items
    const coreItems = sortNavigationItemsByPriority(
        filterNavigationItemsByRoles(CORE_NAVIGATION_ITEMS, userRoles, isPlatformAdmin)
    );
    const adminItems = sortNavigationItemsByPriority(
        filterNavigationItemsByRoles(ADMIN_NAVIGATION_ITEMS, userRoles, isPlatformAdmin)
    );

    // Safe modules array
    const safeModules = Array.isArray(modules) ? modules.filter(m => m?.id && m?.routePath) : [];

    // Handle menu open on hover
    const handleMouseEnter = () => {
        if (menuTimeoutRef.current) {
            clearTimeout(menuTimeoutRef.current);
            menuTimeoutRef.current = null;
        }
        setIsMenuOpen(true);
    };

    const handleMouseLeave = () => {
        menuTimeoutRef.current = setTimeout(() => {
            setIsMenuOpen(false);
        }, 150);
    };

    // Cleanup timeout on unmount
    useEffect(() => {
        return () => {
            if (menuTimeoutRef.current) {
                clearTimeout(menuTimeoutRef.current);
            }
        };
    }, []);

    const handleLogout = () => {
        logout();
        navigate('/');
    };

    const isActive = (path: string) => {
        return location.pathname === path || location.pathname.startsWith(path + '/');
    };

    return (
        <>
            {/* Left: Logo and Navigation Menu */}
            <div
                ref={menuRef}
                className="absolute top-4 left-4 z-50"
                onMouseEnter={handleMouseEnter}
                onMouseLeave={handleMouseLeave}
            >
                {/* Logo Button: click → dashboard, hover opens menu */}
                <button
                    type="button"
                    onClick={() => {
                        setIsMenuOpen(false);
                        navigate('/dashboard');
                    }}
                    className={`flex items-center gap-2 px-4 py-2.5 rounded-xl ${surfaceStyles.base} ${surfaceStyles.hover} transition-all duration-300 group`}
                >
                    <img
                        src="/nkz-os-logo.svg"
                        alt="Nekazari"
                        className="h-7 w-auto dark:invert"
                    />
                    <ChevronDown
                        className={`w-4 h-4 text-slate-500 dark:text-slate-400 transition-transform duration-300 ${isMenuOpen ? 'rotate-180' : ''
                            }`}
                    />
                </button>

                {/* Dropdown Menu */}
                <div
                    className={`group absolute top-full left-0 mt-2 min-w-[320px] rounded-xl ${surfaceStyles.base} overflow-visible transition-all duration-300 origin-top-left ${isMenuOpen
                        ? 'opacity-100 scale-100 translate-y-0'
                        : 'opacity-0 scale-95 -translate-y-2 pointer-events-none'
                        }`}
                >
                    {/* User Info */}
                    <div className="px-4 py-3 border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800">
                        <div className="flex items-center gap-3">
                            <div className="w-10 h-10 rounded-full bg-gradient-to-br from-green-400 to-emerald-500 flex items-center justify-center text-white font-bold shadow-md">
                                {user?.email?.charAt(0).toUpperCase() || 'U'}
                            </div>
                            <div className="flex-1 min-w-0">
                                <p className="text-sm font-semibold text-slate-800 dark:text-slate-100 truncate">
                                    {user?.email || 'Usuario'}
                                </p>
                                <p className="text-xs text-slate-500 dark:text-slate-400 capitalize">
                                    {isPlatformAdmin ? 'Administrador' : userRoles[0] || 'Usuario'}
                                </p>
                            </div>
                        </div>
                    </div>

                    {/* Core Navigation */}
                    <div className="py-2">
                        <div className="px-4 py-1">
                            <span className="text-xs font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                                Principal
                            </span>
                        </div>
                        {coreItems.map((item) => {
                            const Icon = item.icon;
                            const active = isActive(item.path);
                            return (
                                <Link
                                    key={item.path}
                                    to={item.path}
                                    onClick={() => setIsMenuOpen(false)}
                                    className={`flex items-center gap-3 px-4 py-2.5 mx-2 rounded-lg transition-all ${active
                                        ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
                                        : 'text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800'
                                        }`}
                                >
                                    <Icon className={`w-5 h-5 ${active ? 'text-green-600 dark:text-green-400' : 'text-slate-500'}`} />
                                    <span className="font-medium">
                                        {t(item.label)}
                                    </span>
                                </Link>
                            );
                        })}
                    </div>

                    {/* Addons Section (only if modules exist) */}
                    {safeModules.length > 0 && (
                        <div className="py-2 border-t border-slate-200 dark:border-slate-700">
                            <div className="px-4 py-1">
                                <span className="text-xs font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                                    Addons
                                </span>
                            </div>
                            <div className="max-h-64 overflow-y-auto px-1 pb-1">
                            {safeModules.map((module) => {
                                const Icon = moduleIconMap[module.icon || 'default'] || Puzzle;
                                const active = isActive(module.routePath);
                                const emoji = module.metadata?.icon;
                                const hasEmoji = emoji && typeof emoji === 'string' && emoji.length <= 2;

                                return (
                                    <Link
                                        key={module.id}
                                        to={module.routePath}
                                        onClick={() => setIsMenuOpen(false)}
                                        className={`flex items-center gap-3 px-4 py-2.5 mx-2 rounded-lg transition-all ${active
                                            ? 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400'
                                            : 'text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800'
                                            }`}
                                    >
                                        {hasEmoji ? (
                                            <span className="w-5 h-5 flex items-center justify-center text-base">{emoji}</span>
                                        ) : (
                                            <Icon className={`w-5 h-5 ${active ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500'}`} />
                                        )}
                                        <span className="font-medium truncate">
                                            {module.label || module.displayName || module.name}
                                        </span>
                                    </Link>
                                );
                            })}
                            </div>
                        </div>
                    )}

                    {/* Admin/Settings Section */}
                    {adminItems.length > 0 && (
                        <div className="py-2 border-t border-slate-200 dark:border-slate-700">
                            {adminItems.map((item) => {
                                const Icon = item.icon;
                                const active = isActive(item.path);
                                return (
                                    <Link
                                        key={item.path}
                                        to={item.path}
                                        onClick={() => setIsMenuOpen(false)}
                                        className={`flex items-center gap-3 px-4 py-2.5 mx-2 rounded-lg transition-all ${active
                                            ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
                                            : 'text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800'
                                            }`}
                                    >
                                        <Icon className={`w-5 h-5 ${active ? 'text-blue-600 dark:text-blue-400' : 'text-slate-500'}`} />
                                        <span className="font-medium">
                                            {t(item.label)}
                                        </span>
                                    </Link>
                                );
                            })}
                        </div>
                    )}

                    {/* Logout */}
                    <div className="py-2 border-t border-slate-200 dark:border-slate-700">
                        <button
                            onClick={handleLogout}
                            className="flex items-center gap-3 px-4 py-2.5 mx-2 rounded-lg w-[calc(100%-16px)] text-left text-slate-600 dark:text-slate-400 hover:bg-red-50 dark:hover:bg-red-900/20 hover:text-red-600 dark:hover:text-red-400 transition-all"
                        >
                            <LogOut className="w-5 h-5" />
                            <span className="font-medium">{t('layout.logout', { defaultValue: 'Cerrar sesión' })}</span>
                        </button>
                    </div>

                    {/* Controls: Theme + Language */}
                    <div className="flex items-center gap-2 px-3 py-2.5 border-t border-slate-200 dark:border-slate-700">
                        <button
                            onClick={toggleTheme}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                        >
                            {isLight ? (
                                <Sun className="w-4 h-4" />
                            ) : (
                                <Moon className="w-4 h-4" />
                            )}
                            <span>{isLight ? 'Claro' : 'Oscuro'}</span>
                        </button>
                        <LanguageSelector variant="compact" />
                    </div>
                </div>
            </div>
        </>
    );
};

export default ViewerHeader;
