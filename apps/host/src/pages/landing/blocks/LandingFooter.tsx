import React from 'react';
import { Globe } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import { NkzAttribution } from '@/components/attribution/NkzAttribution';

interface Props {
  language: string;
  supportedLanguages: Record<string, string>;
  showLanguageMenu: boolean;
  setShowLanguageMenu: (v: boolean) => void;
  onLanguageChange: (lang: string) => void;
}

export const LandingFooter: React.FC<Props> = ({
  language,
  supportedLanguages,
  showLanguageMenu,
  setShowLanguageMenu,
  onLanguageChange,
}) => {
  const { t } = useI18n();

  return (
    <footer className="bg-[#0E1A14] text-[#A8B1AC]" style={{ padding: '5rem 2rem 3rem' }}>
      <div className="max-w-[1200px] mx-auto">
        <div className="mb-12">
          <span className="text-[#FAFAF7] text-xl font-semibold">NKZ</span>
          <p className="text-sm mt-2">{t('landing_v2.footer_tagline')}</p>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-8 mb-12">
          <div>
            <h4 className="text-[#FAFAF7] font-medium text-sm mb-4">{t('landing_v2.footer_col1_title')}</h4>
            <ul className="space-y-2 text-sm">
              <li><a href="https://nkz-os.org/modules" className="hover:text-[#FAFAF7] transition-colors">{t('landing_v2.footer_col1_1')}</a></li>
              <li><a href="#pricing" className="hover:text-[#FAFAF7] transition-colors">{t('landing_v2.footer_col1_2')}</a></li>
              <li><a href="https://nkz-os.org/docs" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col1_3')}</a></li>
              <li><a href="https://nkz-os.org/status" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col1_4')}</a></li>
            </ul>
          </div>

          <div>
            <h4 className="text-[#FAFAF7] font-medium text-sm mb-4">{t('landing_v2.footer_col2_title')}</h4>
            <ul className="space-y-2 text-sm">
              <li><a href="https://github.com/nkz-os/nkz" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col2_1')}</a></li>
              <li><a href="https://nkz-os.org" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col2_2')}</a></li>
              <li><a href="https://nkz-os.org/community" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col2_3')}</a></li>
              <li><a href="https://nkz-os.org/roadmap" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col2_4')}</a></li>
            </ul>
          </div>

          <div>
            <h4 className="text-[#FAFAF7] font-medium text-sm mb-4">{t('landing_v2.footer_col3_title')}</h4>
            <ul className="space-y-2 text-sm">
              <li><a href="https://nkz-os.org/about" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col3_1')}</a></li>
              <li><a href={`mailto:${(window as any).__ENV__?.SUPPORT_EMAIL || 'info@nekazari.com'}`} className="hover:text-[#FAFAF7] transition-colors">{t('landing_v2.footer_col3_2')}</a></li>
              <li><a href="https://nkz-os.org/privacy" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col3_3')}</a></li>
              <li><a href="https://nkz-os.org/terms" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col3_4')}</a></li>
            </ul>
          </div>

          <div />
        </div>

        <div className="border-t border-[rgba(168,177,172,0.15)] pt-6 flex flex-col sm:flex-row items-center justify-between gap-4 text-xs">
          <span>{t('landing_v2.footer_copyright')}</span>
          <div className="flex items-center gap-4">
            <NkzAttribution variant="commercial" />
            <div className="relative">
              <button
                onClick={() => setShowLanguageMenu(!showLanguageMenu)}
                className="inline-flex items-center gap-1 text-[#A8B1AC] hover:text-[#FAFAF7] transition-colors"
              >
                <Globe className="h-3.5 w-3.5" />
                {supportedLanguages[language] || 'ES'}
              </button>
              {showLanguageMenu && (
                <>
                  <div className="absolute bottom-full right-0 mb-2 w-36 rounded-lg shadow-lg bg-white ring-1 ring-black/5 z-20 overflow-hidden">
                    {Object.entries(supportedLanguages).map(([code, name]) => (
                      <button
                        key={code}
                        onClick={() => onLanguageChange(code)}
                        className={`block w-full text-left px-3 py-2 text-xs transition-colors ${
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
          </div>
        </div>
      </div>
    </footer>
  );
};
