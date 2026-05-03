import React from 'react';
import { Globe } from 'lucide-react';
import { useAuth } from '@/context/KeycloakAuthContext';
import { useNavigate } from 'react-router-dom';

interface Props {
  isScrolled: boolean;
  language: string;
  supportedLanguages: Record<string, string>;
  showLanguageMenu: boolean;
  setShowLanguageMenu: (v: boolean) => void;
  onLanguageChange: (lang: string) => void;
}

export const HeroTopBar: React.FC<Props> = ({
  isScrolled,
  language,
  supportedLanguages,
  showLanguageMenu,
  setShowLanguageMenu,
  onLanguageChange,
}) => {
  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();

  const handleLogin = async () => {
    if (isAuthenticated) {
      navigate('/dashboard');
      return;
    }
    try {
      await login(true);
    } catch {
      const { getConfig } = await import('@/config/environment');
      const config = getConfig();
      const keycloakUrl = `${config.keycloak.url}/realms/${config.keycloak.realm}/protocol/openid-connect/auth`;
      const params = new URLSearchParams({
        client_id: config.keycloak.clientId,
        redirect_uri: `${window.location.origin}/dashboard`,
        response_type: 'code',
        scope: 'openid',
        prompt: 'login',
      });
      window.location.href = `${keycloakUrl}?${params.toString()}`;
    }
  };

  const textColor = isScrolled ? 'text-[#0E1A14]' : 'text-white';
  const bg = isScrolled
    ? 'bg-white/90 backdrop-blur-md border-b border-[rgba(14,26,20,0.06)]'
    : 'bg-transparent';

  return (
    <nav
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${bg}`}
      style={{ padding: '1.25rem 2rem' }}
    >
      <div className="max-w-[1200px] mx-auto flex items-center justify-between">
        <span className={`text-xl font-semibold tracking-tight ${textColor} transition-colors duration-300`}>
          NKZ
        </span>
        <div className="flex items-center gap-4">
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowLanguageMenu(!showLanguageMenu)}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${textColor} hover:opacity-70`}
            >
              <Globe className="h-4 w-4" />
              {supportedLanguages[language] || 'ES'}
            </button>
            {showLanguageMenu && (
              <>
                <div className="absolute right-0 mt-2 w-40 rounded-lg shadow-lg bg-white ring-1 ring-black/5 z-20 overflow-hidden">
                  {Object.entries(supportedLanguages).map(([code, name]) => (
                    <button
                      key={code}
                      onClick={() => onLanguageChange(code)}
                      className={`block w-full text-left px-4 py-2 text-sm transition-colors ${
                        language === code
                          ? 'bg-green-50 text-green-900 font-medium'
                          : 'text-gray-700 hover:bg-gray-50'
                      }`}
                    >
                      {name as string}
                    </button>
                  ))}
                </div>
                <div className="fixed inset-0 z-10" onClick={() => setShowLanguageMenu(false)} />
              </>
            )}
          </div>
          <button
            onClick={handleLogin}
            className={`text-sm font-medium transition-colors duration-300 ${textColor} hover:opacity-70`}
          >
            Iniciar sesión
          </button>
        </div>
      </div>
    </nav>
  );
};
