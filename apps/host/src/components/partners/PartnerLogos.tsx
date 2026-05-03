import React from 'react';
import { useI18n } from '@/context/I18nContext';

interface Partner {
  name: string;
  url: string;
  logo: string;
}

/**
 * Reads partner list from window.__ENV__.PARTNERS_JSON (JSON string)
 * or falls back to a single "Powered by FIWARE" placeholder.
 *
 * Example env value:
 *   PARTNERS_JSON='[{"name":"Acme","url":"https://acme.example","logo":"/modules/assets/logo-acme.png"}]'
 */
function getPartners(): Partner[] {
  try {
    const raw = (window as any).__ENV__?.PARTNERS_JSON;
    if (!raw) return [];
    const parsed = Array.isArray(raw) ? raw : JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.length > 0) return parsed;
  } catch { /* ignore malformed JSON */ }
  return [];
}

export const PartnerLogos: React.FC = () => {
  const { t } = useI18n();
  const partners = React.useMemo(() => getPartners(), []);

  if (partners.length === 0) return null;

  return (
    <div className="w-full bg-white py-16">
      <div className="max-w-[1200px] mx-auto px-8">
        <p className="font-mono-landing text-center text-[13px] tracking-[0.15em] uppercase text-[#5B6660] mb-10">
          {t('landing_v2.partners_eyebrow')}
        </p>
        <div className="flex flex-wrap justify-center items-center gap-12 md:gap-24">
          {partners.map((partner, index) => (
            <a
              key={index}
              href={partner.url}
              target="_blank"
              rel="noopener noreferrer"
              title={partner.name}
              className="group block"
            >
              <img
                src={partner.logo}
                alt={partner.name}
                loading="lazy"
                className="max-h-16 w-auto object-contain opacity-60 grayscale group-hover:grayscale-0 group-hover:opacity-100 transition-all duration-300"
              />
            </a>
          ))}
        </div>
      </div>
    </div>
  );
};
