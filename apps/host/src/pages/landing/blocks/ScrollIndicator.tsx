import React from 'react';
import { useI18n } from '@/context/I18nContext';

export const ScrollIndicator: React.FC = () => {
  const { t } = useI18n();

  return (
    <div className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2 z-10">
      <span className="font-mono-landing text-[11px] uppercase tracking-[0.15em] text-white/50">
        {t('landing_v2.hero_scroll')}
      </span>
      <div className="scroll-indicator-line w-px h-8 bg-white/40 relative overflow-hidden">
        <div
          className="absolute top-0 left-0 w-full h-2 bg-white/70 rounded-full"
          style={{ animation: 'scrollDrop 1.5s ease-in-out infinite' }}
        />
      </div>
      <style>{`
        @keyframes scrollDrop {
          0% { transform: translateY(-100%); opacity: 0; }
          50% { opacity: 1; }
          100% { transform: translateY(32px); opacity: 0; }
        }
      `}</style>
    </div>
  );
};
