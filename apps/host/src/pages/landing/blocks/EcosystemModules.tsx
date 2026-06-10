import React from 'react';
import { ArrowRight, Leaf, Mountain, Route, BarChart3, Radio, Shield, MessageCircle, Settings } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';

interface Module {
  id: string;
  name: string;
  descKey: string;
  icon: React.ReactNode;
  url: string;
}

const MODULES: Module[] = [
  { id: 'vegetation', name: 'Vegetation Prime', descKey: 'landing_v2.ecosystem_mod_vegetation', icon: <Leaf className="h-8 w-8" />, url: 'https://nkz-os.org/modules/vegetation' },
  { id: 'lidar', name: 'LiDAR', descKey: 'landing_v2.ecosystem_mod_lidar', icon: <Mountain className="h-8 w-8" />, url: 'https://nkz-os.org/modules/lidar' },
  { id: 'gis-routing', name: 'GIS Routing', descKey: 'landing_v2.ecosystem_mod_gis', icon: <Route className="h-8 w-8" />, url: 'https://nkz-os.org/modules/gis-routing' },
  { id: 'datahub', name: 'DataHub', descKey: 'landing_v2.ecosystem_mod_datahub', icon: <BarChart3 className="h-8 w-8" />, url: 'https://nkz-os.org/modules/datahub' },
  { id: 'iot', name: 'IoT', descKey: 'landing_v2.ecosystem_mod_iot', icon: <Radio className="h-8 w-8" />, url: 'https://nkz-os.org/modules/iot' },
  { id: 'vpn', name: 'VPN', descKey: 'landing_v2.ecosystem_mod_vpn', icon: <Shield className="h-8 w-8" />, url: 'https://nkz-os.org/modules/vpn' },
  { id: 'zulip', name: 'Zulip', descKey: 'landing_v2.ecosystem_mod_zulip', icon: <MessageCircle className="h-8 w-8" />, url: 'https://nkz-os.org/modules/zulip' },
  { id: 'cue', name: 'CUE', descKey: 'landing_v2.ecosystem_mod_cue', icon: <Settings className="h-8 w-8" />, url: 'https://nkz-os.org/modules/cue' },
];

export const EcosystemModules: React.FC = () => {
  const { t } = useI18n();

  return (
    <section className="bg-[#FAFAF7]" style={{ padding: '8rem 2rem' }}>
      <div className="max-w-[1200px] mx-auto">
        <p className="font-mono-landing text-[13px] tracking-[0.15em] uppercase text-[#5B6660] mb-4">
          {t('landing_v2.ecosystem_eyebrow')}
        </p>
        <h2
          className="text-[#0E1A14] font-semibold mb-4"
          style={{
            fontFamily: "'Inter', sans-serif",
            fontSize: 'clamp(2rem, 4vw, 3rem)',
            lineHeight: 1.15,
            letterSpacing: '-0.02em',
          }}
        >
          {t('landing_v2.ecosystem_h2')}
        </h2>
        <p className="text-[#5B6660] text-base mb-12">
          {t('landing_v2.ecosystem_sub')}
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8 mb-10">
          {MODULES.map((mod) => (
            <a
              key={mod.id}
              href={mod.url}
              target="_blank"
              rel="noopener noreferrer"
              className="block p-6 rounded-lg border border-[rgba(14,26,20,0.08)] hover:border-[rgba(14,26,20,0.18)] hover:bg-[#FAFAF7] transition-all duration-200"
              style={{ textDecoration: 'none' }}
            >
              <div className="text-[#1F4D38] mb-4">{mod.icon}</div>
              <h3
                className="text-[#0E1A14] font-semibold mb-2"
                style={{ fontFamily: "'Inter', sans-serif", fontSize: '18px' }}
              >
                {mod.name}
              </h3>
              <p className="text-[#5B6660] text-sm leading-relaxed mb-4 line-clamp-2">
                {t(mod.descKey)}
              </p>
              <div className="flex gap-2">
                <span className="font-mono-landing text-[11px] uppercase tracking-[0.1em] text-[#5B6660] border border-[rgba(14,26,20,0.08)] rounded px-2 py-0.5">
                  {t('landing_v2.ecosystem_badge_oss')}
                </span>
                <span className="font-mono-landing text-[11px] uppercase tracking-[0.1em] text-[#5B6660] border border-[rgba(14,26,20,0.08)] rounded px-2 py-0.5">
                  {t('landing_v2.ecosystem_badge_cloud')}
                </span>
              </div>
            </a>
          ))}
        </div>

        <a
          href="https://nkz-os.org/modules"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-[#1F4D38] font-medium text-sm hover:underline group"
        >
          {t('landing_v2.ecosystem_cta')}
          <ArrowRight className="h-4 w-4 group-hover:translate-x-0.5 transition-transform" />
        </a>
      </div>
    </section>
  );
};
