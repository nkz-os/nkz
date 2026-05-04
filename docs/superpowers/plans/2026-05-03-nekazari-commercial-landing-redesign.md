# Commercial Landing Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `CommercialLanding.tsx` (generic SaaS template) with a 12-block editorial landing that communicates open-core + managed-cloud positioning, per spec `2026-05-03-nekazari-commercial-landing-redesign.md`.

**Architecture:** Single-page CSR component composed of 12 focused sub-components. Each sub-component is a pure presentational file in `src/pages/landing/blocks/`. `CommercialLanding.tsx` becomes a thin orchestration layer (layout, scroll logic, auth). Existing `PricingCards`, `PartnerLogos`, `CookieBanner`, `NkzAttribution` are reused with wrapper adjustments. All new strings go into `common.json` under `landing.*`. No new npm dependencies.

**Tech Stack:** React 18 + TS 5 + Tailwind + lucide-react (icons) + existing i18n via `useI18n()`

**Spec:** `nkz/internal-docs/specs/2026-05-03-nekazari-commercial-landing-redesign.md`

---

## Files Map

| File | Action | Purpose |
|---|---|---|
| `apps/host/public/media/hero.mp4` | Create (copy) | Hero video from splash.mp4 |
| `apps/host/public/media/hero.webm` | Create (transcode) | VP9 variant |
| `apps/host/public/media/hero-poster.jpg` | Create (extract) | Static fallback |
| `apps/host/index.html` | Modify | Add JetBrains Mono from Google Fonts |
| `apps/host/src/index.css` | Modify | Reduced-motion rules, mono font utility |
| `apps/host/public/locales/es/common.json` | Modify | Add `landing_v2.*` keys (Spanish) |
| `apps/host/public/locales/en/common.json` | Modify | Add `landing_v2.*` keys (English) |
| `apps/host/src/pages/landing/blocks/HeroTopBar.tsx` | Create | Fixed top bar (transparent → white on scroll) |
| `apps/host/src/pages/landing/blocks/HeroSection.tsx` | Create | Hero with video, overlay, text, CTAs |
| `apps/host/src/pages/landing/blocks/ScrollIndicator.tsx` | Create | Bottom-center scroll indicator |
| `apps/host/src/pages/landing/blocks/ProofBand.tsx` | Create | 4-column metrics band |
| `apps/host/src/pages/landing/blocks/ProductAnchor.tsx` | Create | "Qué obtienes" + Cesium screenshot |
| `apps/host/src/pages/landing/blocks/OpenCoreSection.tsx` | Create | nkz-os vs Nekazari 2-column |
| `apps/host/src/pages/landing/blocks/EcosystemModules.tsx` | Create | 4×2 module grid |
| `apps/host/src/pages/landing/blocks/OpenStandards.tsx` | Create | Wordmarks band |
| `apps/host/src/pages/landing/blocks/FinalCTA.tsx` | Create | Dark CTA block |
| `apps/host/src/pages/landing/blocks/LandingFooter.tsx` | Create | Footer matching nkz-os.org |
| `apps/host/src/components/pricing/PricingCards.tsx` | Modify | Remove gradients, shadow-2xl, hover transforms; add Free tier |
| `apps/host/src/components/partners/PartnerLogos.tsx` | Modify | Grayscale default, color on hover, "CONFÍAN EN NEKAZARI" eyebrow |
| `apps/host/src/pages/landing/CommercialLanding.tsx` | Rewrite | Thin orchestrator composing all blocks |

---

### Task 0: Asset preparation (video variants + poster)

**Files:**
- Create: `apps/host/public/media/hero.mp4`
- Create: `apps/host/public/media/hero.webm`
- Create: `apps/host/public/media/hero-poster.jpg`

- [ ] **Step 1: Create media directory and copy splash.mp4**

```bash
mkdir -p apps/host/public/media
cp nkz-mobile/assets/splash.mp4 apps/host/public/media/hero.mp4
```

- [ ] **Step 2: Generate WebM variant and poster image**

No `ffmpeg` available in this environment. User must run:

```bash
cd /home/g/Documents/nekazari/nkz/apps/host/public/media
ffmpeg -i hero.mp4 -c:v libvpx-vp9 -b:v 1.5M -an hero.webm
ffmpeg -i hero.mp4 -ss 00:00:00.5 -frames:v 1 -q:v 3 hero-poster.jpg
```

Verify: `hero.webm` ≈ 2.5MB, `hero-poster.jpg` ≈ 120KB.

---

### Task 1: Add JetBrains Mono font to index.html

**File:** Modify `apps/host/index.html`

- [ ] **Step 1: Add JetBrains Mono link in `<head>`**

Add after the existing Inter `<link>` line (line 10):

```html
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
```

---

### Task 2: Add reduced-motion + mono utility to index.css

**File:** Modify `apps/host/src/index.css`

- [ ] **Step 1: Append rules at end of file**

```css
/* Landing: reduced-motion support */
@media (prefers-reduced-motion: reduce) {
  .hero-video {
    display: none !important;
  }
  .scroll-indicator-line {
    animation: none !important;
  }
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}

/* Landing: JetBrains Mono utility */
.font-mono-landing {
  font-family: 'JetBrains Mono', 'IBM Plex Mono', 'ui-monospace', 'SF Mono', monospace;
}
```

---

### Task 3: i18n — new landing_v2 strings (es + en)

**Files:**
- Modify: `apps/host/public/locales/es/common.json`
- Modify: `apps/host/public/locales/en/common.json`

- [ ] **Step 1: Add `landing_v2` block to `es/common.json`**

Find the closing `}` of the `"landing"` block (after the `"pricing"` nested object) and add a comma, then insert after `"landing"`:

```json
  "landing_v2": {
    "eyebrow_open_core": "OPEN CORE · MANAGED CLOUD",
    "hero_h1": "El stack abierto del campo. Operado para ti.",
    "hero_sub": "Despliega nkz-os sin gestionar Kubernetes ni FIWARE. Multitenant, NGSI-LD nativo, infraestructura europea.",
    "hero_cta_primary": "Crear tenant gratuito",
    "hero_cta_secondary": "Solicitar demo",
    "hero_scroll": "Scroll",
    "proof_parcelas": "parcelas monitorizadas",
    "proof_plantas": "plantas industriales",
    "proof_observaciones": "observaciones /día",
    "proof_paises": "países",
    "product_eyebrow": "EL PRODUCTO",
    "product_h2": "Una consola operativa. Cualquier capa de datos.",
    "product_body": "Visualización 3D sobre CesiumJS con módulos cargados dinámicamente, drag-and-drop de capas geoespaciales y observaciones en tiempo real. Una única interfaz para todos los datos del campo.",
    "product_bullet_1": "Visualización 3D de parcelas, parcelarios, infraestructuras industriales",
    "product_bullet_2": "Capas dinámicas: Vegetation Prime, LiDAR, GIS Routing, DataHub",
    "product_bullet_3": "Dashboard multitenant con aislamiento estricto",
    "product_bullet_4": "Multi-idioma (es, en, ca, eu, fr, pt)",
    "opencore_eyebrow": "OPEN CORE",
    "opencore_h2": "Construido sobre nkz-os. Operado por nosotros.",
    "opencore_intro": "Nekazari opera nkz-os, el proyecto open-source que mantenemos en abierto. Puedes desplegarlo tú mismo en tu Kubernetes, o dejar que nosotros lo gestionemos para ti.",
    "opencore_oss_title": "nkz-os (Self-hosted)",
    "opencore_oss_1": "Open source · AGPL-3.0",
    "opencore_oss_2": "GitHub: nkz-os/nkz",
    "opencore_oss_3": "Comunidad + módulos abiertos",
    "opencore_oss_4": "Despliegue propio en K8s",
    "opencore_oss_5": "Gratis para siempre",
    "opencore_oss_cta": "Documentación",
    "opencore_cloud_title": "Nekazari (Cloud)",
    "opencore_cloud_1": "SaaS gestionado",
    "opencore_cloud_2": "Multitenant aislado",
    "opencore_cloud_3": "Mismos módulos + soporte SLA",
    "opencore_cloud_4": "Infra europea (DE/FR), backups",
    "opencore_cloud_5": "Free tier · Pro · Enterprise",
    "opencore_cloud_cta": "Crear tenant gratuito",
    "ecosystem_eyebrow": "ECOSISTEMA",
    "ecosystem_h2": "Un módulo para cada capa.",
    "ecosystem_sub": "Construidos sobre la misma base FIWARE. Disponibles en self-hosted y cloud.",
    "ecosystem_cta": "Ver todos los módulos en nkz-os.org",
    "ecosystem_badge_oss": "Open source",
    "ecosystem_badge_cloud": "Cloud",
    "ecosystem_mod_vegetation": "Índices vegetales sobre imágenes Sentinel-2.",
    "ecosystem_mod_lidar": "Ingestión SOTA de nubes de puntos LiDAR.",
    "ecosystem_mod_gis": "Rutas, geoanálisis y mapas de cultivo.",
    "ecosystem_mod_datahub": "Analítica de series temporales con uPlot.",
    "ecosystem_mod_iot": "Provisioning FIWARE estándar de dispositivos.",
    "ecosystem_mod_vpn": "Tailscale + Headscale para acceso seguro.",
    "ecosystem_mod_zulip": "Comunicaciones internas con OIDC integrado.",
    "ecosystem_mod_cue": "Configuración centralizada de la plataforma.",
    "standards_eyebrow": "STANDARDS",
    "standards_h2": "Sin lock-in. Sin formato propietario.",
    "partners_eyebrow": "CONFÍAN EN NEKAZARI",
    "cta_final_h2": "Empieza con un tenant gratuito. En menos de dos minutos.",
    "cta_final_primary": "Crear tenant gratuito",
    "cta_final_secondary": "o solicita una demo",
    "footer_tagline": "Plataforma SaaS sobre nkz-os.",
    "footer_col1_title": "Producto",
    "footer_col1_1": "Módulos",
    "footer_col1_2": "Pricing",
    "footer_col1_3": "Documentación",
    "footer_col1_4": "Estado",
    "footer_col2_title": "Open source",
    "footer_col2_1": "GitHub",
    "footer_col2_2": "nkz-os.org",
    "footer_col2_3": "Comunidad",
    "footer_col2_4": "Roadmap",
    "footer_col3_title": "Empresa",
    "footer_col3_1": "Sobre nosotros",
    "footer_col3_2": "Contacto",
    "footer_col3_3": "Privacidad",
    "footer_col3_4": "Términos",
    "footer_copyright": "© 2026 Nekazari · Powered by nkz-os · AGPL-3.0"
  }
```

- [ ] **Step 2: Add `landing_v2` block to `en/common.json`**

Same position, English values:

```json
  "landing_v2": {
    "eyebrow_open_core": "OPEN CORE · MANAGED CLOUD",
    "hero_h1": "The open stack for the field. Operated for you.",
    "hero_sub": "Deploy nkz-os without managing Kubernetes or FIWARE. Multitenant, NGSI-LD native, European infrastructure.",
    "hero_cta_primary": "Create free tenant",
    "hero_cta_secondary": "Request demo",
    "hero_scroll": "Scroll",
    "proof_parcelas": "monitored parcels",
    "proof_plantas": "industrial plants",
    "proof_observaciones": "observations /day",
    "proof_paises": "countries",
    "product_eyebrow": "THE PRODUCT",
    "product_h2": "One operational console. Any data layer.",
    "product_body": "3D visualization on CesiumJS with dynamically loaded modules, drag-and-drop geospatial layers, and real-time observations. A single interface for all field data.",
    "product_bullet_1": "3D visualization of parcels, cadastral maps, industrial infrastructure",
    "product_bullet_2": "Dynamic layers: Vegetation Prime, LiDAR, GIS Routing, DataHub",
    "product_bullet_3": "Multitenant dashboard with strict isolation",
    "product_bullet_4": "Multi-language (es, en, ca, eu, fr, pt)",
    "opencore_eyebrow": "OPEN CORE",
    "opencore_h2": "Built on nkz-os. Operated by us.",
    "opencore_intro": "Nekazari operates nkz-os, the open-source project we maintain in the open. You can deploy it yourself on your Kubernetes, or let us manage it for you.",
    "opencore_oss_title": "nkz-os (Self-hosted)",
    "opencore_oss_1": "Open source · AGPL-3.0",
    "opencore_oss_2": "GitHub: nkz-os/nkz",
    "opencore_oss_3": "Community + open modules",
    "opencore_oss_4": "Self-deployed on K8s",
    "opencore_oss_5": "Free forever",
    "opencore_oss_cta": "Documentation",
    "opencore_cloud_title": "Nekazari (Cloud)",
    "opencore_cloud_1": "Managed SaaS",
    "opencore_cloud_2": "Isolated multitenant",
    "opencore_cloud_3": "Same modules + SLA support",
    "opencore_cloud_4": "EU infra (DE/FR), backups",
    "opencore_cloud_5": "Free tier · Pro · Enterprise",
    "opencore_cloud_cta": "Create free tenant",
    "ecosystem_eyebrow": "ECOSYSTEM",
    "ecosystem_h2": "One module for every layer.",
    "ecosystem_sub": "Built on the same FIWARE foundation. Available self-hosted and cloud.",
    "ecosystem_cta": "See all modules on nkz-os.org",
    "ecosystem_badge_oss": "Open source",
    "ecosystem_badge_cloud": "Cloud",
    "ecosystem_mod_vegetation": "Vegetation indices on Sentinel-2 imagery.",
    "ecosystem_mod_lidar": "SOTA LiDAR point cloud ingestion.",
    "ecosystem_mod_gis": "Routes, geo-analysis, and crop maps.",
    "ecosystem_mod_datahub": "Time-series analytics with uPlot.",
    "ecosystem_mod_iot": "Standard FIWARE device provisioning.",
    "ecosystem_mod_vpn": "Tailscale + Headscale for secure access.",
    "ecosystem_mod_zulip": "Internal communications with OIDC integration.",
    "ecosystem_mod_cue": "Centralized platform configuration.",
    "standards_eyebrow": "STANDARDS",
    "standards_h2": "No lock-in. No proprietary format.",
    "partners_eyebrow": "TRUSTED BY",
    "cta_final_h2": "Start with a free tenant. In under two minutes.",
    "cta_final_primary": "Create free tenant",
    "cta_final_secondary": "or request a demo",
    "footer_tagline": "SaaS platform powered by nkz-os.",
    "footer_col1_title": "Product",
    "footer_col1_1": "Modules",
    "footer_col1_2": "Pricing",
    "footer_col1_3": "Documentation",
    "footer_col1_4": "Status",
    "footer_col2_title": "Open source",
    "footer_col2_1": "GitHub",
    "footer_col2_2": "nkz-os.org",
    "footer_col2_3": "Community",
    "footer_col2_4": "Roadmap",
    "footer_col3_title": "Company",
    "footer_col3_1": "About",
    "footer_col3_2": "Contact",
    "footer_col3_3": "Privacy",
    "footer_col3_4": "Terms",
    "footer_copyright": "© 2026 Nekazari · Powered by nkz-os · AGPL-3.0"
  }
```

---

### Task 4: HeroTopBar component

**File:** Create `apps/host/src/pages/landing/blocks/HeroTopBar.tsx`

- [ ] **Step 1: Write the component**

```tsx
import React, { useState } from 'react';
import { Globe } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
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
  const { t } = useI18n();
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
        {/* NKZ Wordmark */}
        <span className={`text-xl font-semibold tracking-tight ${textColor} transition-colors duration-300`}>
          NKZ
        </span>

        {/* Right side: language + login */}
        <div className="flex items-center gap-4">
          {/* Language Selector */}
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

          {/* Login */}
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
```

---

### Task 5: ScrollIndicator component

**File:** Create `apps/host/src/pages/landing/blocks/ScrollIndicator.tsx`

- [ ] **Step 1: Write the component**

```tsx
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
          style={{
            animation: 'scrollDrop 1.5s ease-in-out infinite',
          }}
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
```

---

### Task 6: HeroSection component

**File:** Create `apps/host/src/pages/landing/blocks/HeroSection.tsx`

- [ ] **Step 1: Write the component**

```tsx
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import { ScrollIndicator } from './ScrollIndicator';

export const HeroSection: React.FC = () => {
  const { t } = useI18n();
  const navigate = useNavigate();

  return (
    <section className="relative min-h-screen flex items-end pb-24 overflow-hidden">
      {/* Video background */}
      <video
        autoPlay
        muted
        loop
        playsInline
        poster="/media/hero-poster.jpg"
        preload="metadata"
        className="hero-video absolute inset-0 w-full h-full object-cover hidden md:block"
        aria-hidden="true"
      >
        <source src="/media/hero.webm" type="video/webm" />
        <source src="/media/hero.mp4" type="video/mp4" />
      </video>

      {/* Mobile poster fallback */}
      <img
        src="/media/hero-poster.jpg"
        alt=""
        className="absolute inset-0 w-full h-full object-cover md:hidden"
        aria-hidden="true"
      />

      {/* Overlay gradient */}
      <div
        className="absolute inset-0 z-[1]"
        style={{
          background: 'linear-gradient(180deg, rgba(14,26,20,0.30) 0%, rgba(14,26,20,0.55) 50%, rgba(14,26,20,0.85) 100%)',
        }}
      />

      {/* Content */}
      <div className="relative z-[2] max-w-[1200px] mx-auto px-8 w-full">
        {/* Eyebrow */}
        <p className="font-mono-landing text-[13px] tracking-[0.15em] uppercase text-white/70 mb-4">
          {t('landing_v2.eyebrow_open_core')}
        </p>

        {/* H1 */}
        <h1
          className="text-white font-semibold mb-6 max-w-[14ch]"
          style={{
            fontFamily: "'Inter', sans-serif",
            fontSize: 'clamp(2.5rem, 6vw, 5.5rem)',
            lineHeight: 1.05,
            letterSpacing: '-0.03em',
          }}
        >
          {t('landing_v2.hero_h1')}
        </h1>

        {/* Sub */}
        <p
          className="text-white/85 mb-10 max-w-[56ch]"
          style={{
            fontFamily: "'Inter', sans-serif",
            fontSize: 'clamp(1.05rem, 1.4vw, 1.25rem)',
            lineHeight: 1.5,
            fontWeight: 400,
          }}
        >
          {t('landing_v2.hero_sub')}
        </p>

        {/* CTAs */}
        <div className="flex flex-col sm:flex-row items-start gap-4">
          <button
            onClick={() => navigate('/register')}
            className="inline-flex items-center px-7 py-3.5 bg-[#1F4D38] text-white font-semibold rounded-lg hover:bg-[#163A2A] hover:-translate-y-px transition-all duration-200"
            style={{ fontSize: '1rem' }}
          >
            {t('landing_v2.hero_cta_primary')}
          </button>
          <a
            href={`mailto:${(window as any).__ENV__?.SALES_EMAIL || 'info@nekazari.com'}`}
            className="inline-flex items-center gap-1.5 text-white font-medium hover:underline transition-all duration-200 group"
            style={{ fontSize: '1rem' }}
          >
            {t('landing_v2.hero_cta_secondary')}
            <ArrowRight className="h-4 w-4 group-hover:translate-x-0.5 transition-transform" />
          </a>
        </div>
      </div>

      {/* Scroll indicator */}
      <ScrollIndicator />
    </section>
  );
};
```

---

### Task 7: ProofBand component

**File:** Create `apps/host/src/pages/landing/blocks/ProofBand.tsx`

- [ ] **Step 1: Write the component**

```tsx
import React from 'react';
import { useI18n } from '@/context/I18nContext';

const METRICS = [
  { value: '127', key: 'landing_v2.proof_parcelas' },
  { value: '14', key: 'landing_v2.proof_plantas' },
  { value: '3.2M', key: 'landing_v2.proof_observaciones' },
  { value: '6', key: 'landing_v2.proof_paises' },
];

export const ProofBand: React.FC = () => {
  const { t } = useI18n();

  return (
    <section
      className="bg-[#FAFAF7]"
      style={{
        padding: '4rem 2rem',
        borderTop: '1px solid rgba(14,26,20,0.08)',
        borderBottom: '1px solid rgba(14,26,20,0.08)',
      }}
    >
      <div className="max-w-[1200px] mx-auto grid grid-cols-2 md:grid-cols-4 gap-8">
        {METRICS.map((m) => (
          <div key={m.key} className="text-center">
            <div
              className="font-mono-landing font-medium text-[#0E1A14] mb-1"
              style={{ fontSize: 'clamp(2rem, 3.5vw, 3rem)' }}
            >
              {m.value}
            </div>
            <div className="text-sm text-[#5B6660] leading-tight">{t(m.key)}</div>
          </div>
        ))}
      </div>
    </section>
  );
};
```

---

### Task 8: ProductAnchor component

**File:** Create `apps/host/src/pages/landing/blocks/ProductAnchor.tsx`

- [ ] **Step 1: Write the component**

```tsx
import React from 'react';
import { useI18n } from '@/context/I18nContext';

export const ProductAnchor: React.FC = () => {
  const { t } = useI18n();

  const bullets = [
    'landing_v2.product_bullet_1',
    'landing_v2.product_bullet_2',
    'landing_v2.product_bullet_3',
    'landing_v2.product_bullet_4',
  ];

  return (
    <section className="bg-white" style={{ padding: '8rem 2rem' }}>
      <div className="max-w-[1200px] mx-auto grid lg:grid-cols-2 gap-16 items-center">
        {/* Left: text */}
        <div>
          <p className="font-mono-landing text-[13px] tracking-[0.15em] uppercase text-[#5B6660] mb-4">
            {t('landing_v2.product_eyebrow')}
          </p>
          <h2
            className="text-[#0E1A14] font-semibold mb-6"
            style={{
              fontFamily: "'Inter', sans-serif",
              fontSize: 'clamp(2rem, 4vw, 3rem)',
              lineHeight: 1.15,
              letterSpacing: '-0.02em',
            }}
          >
            {t('landing_v2.product_h2')}
          </h2>
          <p className="text-[#5B6660] text-base leading-relaxed mb-6 max-w-[48ch]">
            {t('landing_v2.product_body')}
          </p>
          <ul className="space-y-3">
            {bullets.map((b) => (
              <li key={b} className="flex items-start gap-2 text-[#0E1A14] text-sm">
                <span className="text-[#1F4D38] font-medium mt-0.5">·</span>
                {t(b)}
              </li>
            ))}
          </ul>
        </div>

        {/* Right: screenshot placeholder */}
        <div className="relative">
          <div
            className="overflow-hidden bg-[#FAFAF7] flex items-center justify-center"
            style={{
              borderRadius: '12px',
              boxShadow:
                '0 30px 60px -20px rgba(14,26,20,0.25), 0 0 0 1px rgba(14,26,20,0.06)',
              aspectRatio: '16/9',
            }}
          >
            <p className="text-[#5B6660] text-sm">
              Screenshot Cesium viewer — pendiente de captura
            </p>
          </div>
        </div>
      </div>
    </section>
  );
};
```

**Note:** The screenshot image is a blocking asset per spec §16. The placeholder `<p>` must be replaced with an `<img>` once the screenshot is generated. The `aspectRatio: '16/9'` preserves layout.

---

### Task 9: OpenCoreSection component

**File:** Create `apps/host/src/pages/landing/blocks/OpenCoreSection.tsx`

- [ ] **Step 1: Write the component**

```tsx
import React from 'react';
import { ArrowRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useI18n } from '@/context/I18nContext';

const ossItems = [
  'landing_v2.opencore_oss_1',
  'landing_v2.opencore_oss_2',
  'landing_v2.opencore_oss_3',
  'landing_v2.opencore_oss_4',
  'landing_v2.opencore_oss_5',
];

const cloudItems = [
  'landing_v2.opencore_cloud_1',
  'landing_v2.opencore_cloud_2',
  'landing_v2.opencore_cloud_3',
  'landing_v2.opencore_cloud_4',
  'landing_v2.opencore_cloud_5',
];

export const OpenCoreSection: React.FC = () => {
  const { t } = useI18n();
  const navigate = useNavigate();

  return (
    <section className="bg-white" style={{ padding: '8rem 2rem' }}>
      <div className="max-w-[1200px] mx-auto">
        <p className="font-mono-landing text-[13px] tracking-[0.15em] uppercase text-[#5B6660] mb-4">
          {t('landing_v2.opencore_eyebrow')}
        </p>
        <h2
          className="text-[#0E1A14] font-semibold mb-6 max-w-[20ch]"
          style={{
            fontFamily: "'Inter', sans-serif",
            fontSize: 'clamp(2rem, 4vw, 3rem)',
            lineHeight: 1.15,
            letterSpacing: '-0.02em',
          }}
        >
          {t('landing_v2.opencore_h2')}
        </h2>
        <p className="text-[#5B6660] text-base leading-relaxed mb-12 max-w-[64ch]">
          {t('landing_v2.opencore_intro')}
        </p>

        {/* Two columns */}
        <div className="grid md:grid-cols-2 gap-0">
          {/* Left: nkz-os */}
          <div className="pr-0 md:pr-12">
            <h3
              className="text-[#0E1A14] font-semibold mb-6"
              style={{ fontFamily: "'Inter', sans-serif", fontSize: '22px' }}
            >
              {t('landing_v2.opencore_oss_title')}
            </h3>
            <ul className="space-y-3.5 mb-8">
              {ossItems.map((k) => (
                <li key={k} className="text-[#0E1A14] text-[15px]">
                  {t(k)}
                </li>
              ))}
            </ul>
            <a
              href="https://nkz-os.org"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-[#1F4D38] font-medium text-sm hover:underline group"
            >
              {t('landing_v2.opencore_oss_cta')}
              <ArrowRight className="h-4 w-4 group-hover:translate-x-0.5 transition-transform" />
            </a>
          </div>

          {/* Divider */}
          <div className="hidden md:block w-px bg-[rgba(14,26,20,0.08)] mx-0 absolute left-1/2 self-stretch" />

          {/* Right: Nekazari Cloud */}
          <div className="pt-8 md:pt-0 md:pl-12 border-t md:border-t-0 border-[rgba(14,26,20,0.08)]">
            <h3
              className="text-[#0E1A14] font-semibold mb-6"
              style={{ fontFamily: "'Inter', sans-serif", fontSize: '22px' }}
            >
              {t('landing_v2.opencore_cloud_title')}
            </h3>
            <ul className="space-y-3.5 mb-8">
              {cloudItems.map((k) => (
                <li key={k} className="text-[#0E1A14] text-[15px]">
                  {t(k)}
                </li>
              ))}
            </ul>
            <button
              onClick={() => navigate('/register')}
              className="inline-flex items-center gap-1.5 text-[#1F4D38] font-medium text-sm hover:underline group"
            >
              {t('landing_v2.opencore_cloud_cta')}
              <ArrowRight className="h-4 w-4 group-hover:translate-x-0.5 transition-transform" />
            </button>
          </div>
        </div>
      </div>
    </section>
  );
};
```

**Fix:** The divider approach above is flawed — `absolute` inside a grid. Replace the grid layout with a flex-based approach. In the actual implementation, use:

```tsx
<div className="max-w-[1200px] mx-auto relative flex flex-col md:flex-row">
  {/* Left column */}
  <div className="flex-1 md:pr-16">...</div>
  {/* Divider */}
  <div className="hidden md:block w-px bg-[rgba(14,26,20,0.08)] self-stretch" />
  {/* Right column */}
  <div className="flex-1 md:pl-16 pt-8 md:pt-0 border-t md:border-t-0 border-[rgba(14,26,20,0.08)]">...</div>
</div>
```

---

### Task 10: EcosystemModules component

**File:** Create `apps/host/src/pages/landing/blocks/EcosystemModules.tsx`

- [ ] **Step 1: Write the component**

```tsx
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

        {/* Grid */}
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

        {/* CTA */}
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
```

---

### Task 11: OpenStandards component

**File:** Create `apps/host/src/pages/landing/blocks/OpenStandards.tsx`

- [ ] **Step 1: Write the component**

```tsx
import React from 'react';
import { useI18n } from '@/context/I18nContext';

const STANDARDS = 'FIWARE NGSI-LD · Smart Data Models · Keycloak (OIDC) · Kubernetes · TimescaleDB · MQTT · OAuth 2.0 · MinIO (S3)';

export const OpenStandards: React.FC = () => {
  const { t } = useI18n();

  return (
    <section className="bg-[#FAFAF7]" style={{ padding: '4rem 2rem' }}>
      <div className="max-w-[1200px] mx-auto text-center">
        <p className="font-mono-landing text-[13px] tracking-[0.15em] uppercase text-[#5B6660] mb-4">
          {t('landing_v2.standards_eyebrow')}
        </p>
        <h2
          className="text-[#0E1A14] font-semibold mb-8"
          style={{ fontSize: '28px', letterSpacing: '-0.02em' }}
        >
          {t('landing_v2.standards_h2')}
        </h2>
        <p
          className="font-mono-landing text-lg text-[#5B6660] leading-relaxed max-w-[48ch] mx-auto"
        >
          {STANDARDS}
        </p>
      </div>
    </section>
  );
};
```

---

### Task 12: FinalCTA component

**File:** Create `apps/host/src/pages/landing/blocks/FinalCTA.tsx`

- [ ] **Step 1: Write the component**

```tsx
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';

export const FinalCTA: React.FC = () => {
  const { t } = useI18n();
  const navigate = useNavigate();

  return (
    <section className="bg-[#0E1A14]" style={{ padding: '8rem 2rem' }}>
      <div className="max-w-[1200px] mx-auto text-center">
        <h2
          className="text-white font-semibold mb-8"
          style={{
            fontFamily: "'Inter', sans-serif",
            fontSize: 'clamp(2rem, 5vw, 3rem)',
            lineHeight: 1.15,
            letterSpacing: '-0.02em',
          }}
        >
          {t('landing_v2.cta_final_h2')}
        </h2>

        <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
          <button
            onClick={() => navigate('/register')}
            className="inline-flex items-center px-7 py-3.5 bg-white text-[#0E1A14] font-semibold rounded-lg hover:-translate-y-px transition-all duration-200"
            style={{ fontSize: '1rem' }}
          >
            {t('landing_v2.cta_final_primary')}
          </button>
          <a
            href={`mailto:${(window as any).__ENV__?.SALES_EMAIL || 'info@nekazari.com'}`}
            className="inline-flex items-center gap-1.5 text-white/70 font-medium hover:text-white transition-colors group"
            style={{ fontSize: '1rem' }}
          >
            {t('landing_v2.cta_final_secondary')}
            <ArrowRight className="h-4 w-4 group-hover:translate-x-0.5 transition-transform" />
          </a>
        </div>
      </div>
    </section>
  );
};
```

---

### Task 13: LandingFooter component

**File:** Create `apps/host/src/pages/landing/blocks/LandingFooter.tsx`

- [ ] **Step 1: Write the component**

```tsx
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
        {/* Top row: wordmark + tagline */}
        <div className="mb-12">
          <span className="text-[#FAFAF7] text-xl font-semibold">NKZ</span>
          <p className="text-sm mt-2">{t('landing_v2.footer_tagline')}</p>
        </div>

        {/* Columns */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-8 mb-12">
          {/* Producto */}
          <div>
            <h4 className="text-[#FAFAF7] font-medium text-sm mb-4">{t('landing_v2.footer_col1_title')}</h4>
            <ul className="space-y-2 text-sm">
              <li><a href="https://nkz-os.org/modules" className="hover:text-[#FAFAF7] transition-colors">{t('landing_v2.footer_col1_1')}</a></li>
              <li><a href="#pricing" className="hover:text-[#FAFAF7] transition-colors">{t('landing_v2.footer_col1_2')}</a></li>
              <li><a href="https://nkz-os.org/docs" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col1_3')}</a></li>
              <li><a href="https://nkz-os.org/status" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col1_4')}</a></li>
            </ul>
          </div>

          {/* Open source */}
          <div>
            <h4 className="text-[#FAFAF7] font-medium text-sm mb-4">{t('landing_v2.footer_col2_title')}</h4>
            <ul className="space-y-2 text-sm">
              <li><a href="https://github.com/nkz-os/nkz" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col2_1')}</a></li>
              <li><a href="https://nkz-os.org" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col2_2')}</a></li>
              <li><a href="https://nkz-os.org/community" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col2_3')}</a></li>
              <li><a href="https://nkz-os.org/roadmap" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col2_4')}</a></li>
            </ul>
          </div>

          {/* Empresa */}
          <div>
            <h4 className="text-[#FAFAF7] font-medium text-sm mb-4">{t('landing_v2.footer_col3_title')}</h4>
            <ul className="space-y-2 text-sm">
              <li><a href="https://nkz-os.org/about" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col3_1')}</a></li>
              <li><a href={`mailto:${(window as any).__ENV__?.SUPPORT_EMAIL || 'info@nekazari.com'}`} className="hover:text-[#FAFAF7] transition-colors">{t('landing_v2.footer_col3_2')}</a></li>
              <li><a href="https://nkz-os.org/privacy" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col3_3')}</a></li>
              <li><a href="https://nkz-os.org/terms" className="hover:text-[#FAFAF7] transition-colors" target="_blank" rel="noopener noreferrer">{t('landing_v2.footer_col3_4')}</a></li>
            </ul>
          </div>

          {/* Empty spacer for 4-col grid balance */}
          <div />
        </div>

        {/* Bottom bar */}
        <div className="border-t border-[rgba(168,177,172,0.15)] pt-6 flex flex-col sm:flex-row items-center justify-between gap-4 text-xs">
          <span>{t('landing_v2.footer_copyright')}</span>
          <div className="flex items-center gap-4">
            <NkzAttribution variant="commercial" />
            {/* Language selector */}
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
```

---

### Task 14: Update PricingCards (remove gradients, add Free tier)

**File:** Modify `apps/host/src/components/pricing/PricingCards.tsx`

- [ ] **Step 1: Rewrite PricingCards**

Replace the entire file:

```tsx
import React from 'react';
import { useI18n } from '@/context/I18nContext';
import { Check } from 'lucide-react';

export const PricingCards: React.FC = () => {
  const { t } = useI18n();

  return (
    <div className="max-w-[1200px] mx-auto px-8 py-24" id="pricing">
      <div className="text-center mb-16">
        <h2 className="text-4xl font-bold text-[#0E1A14] mb-4">
          {t('landing.pricing.title') || 'Planes adaptados a tu terreno'}
        </h2>
        <p className="text-xl text-[#5B6660] max-w-3xl mx-auto">
          {t('landing.pricing.subtitle') || 'Comienza gratis durante 45 días y mejora cuando lo necesites.'}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-8 max-w-5xl mx-auto">
        {/* Free Tier */}
        <div className="bg-white rounded-lg border border-[rgba(14,26,20,0.08)] p-8">
          <h3 className="text-2xl font-bold text-[#0E1A14] mb-2">Free</h3>
          <div className="flex items-baseline mb-4">
            <span className="text-4xl font-extrabold text-[#0E1A14]">0€</span>
            <span className="text-xl text-[#5B6660] ml-2">/mes</span>
          </div>
          <p className="text-[#5B6660] mb-6">Para empezar sin compromiso.</p>
          <ul className="space-y-3 mb-8">
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">1 parcela · 1 usuario</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">Módulos esenciales</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">Soporte comunidad</span></li>
          </ul>
          <a
            href="/register"
            className="block w-full py-3 px-6 text-center rounded-lg bg-white text-[#1F4D38] font-semibold border-2 border-[#1F4D38] hover:bg-[#FAFAF7] transition-colors"
          >
            Crear cuenta gratis
          </a>
        </div>

        {/* Pro Tier */}
        <div className="bg-white rounded-lg border border-[rgba(14,26,20,0.08)] p-8">
          <h3 className="text-2xl font-bold text-[#0E1A14] mb-2">{t('landing.pricing.pro.title') || 'Pro'}</h3>
          <div className="flex items-baseline mb-4">
            <span className="text-4xl font-extrabold text-[#0E1A14]">{t('landing.pricing.pro.price') || '49€'}</span>
            <span className="text-xl text-[#5B6660] ml-2">{t('landing.pricing.pro.period') || '/mes'}</span>
          </div>
          <p className="text-[#5B6660] mb-6">{t('landing.pricing.pro.desc') || 'For professional agronomists and mid-size farms.'}</p>
          <ul className="space-y-3 mb-8">
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.pro.feat1') || '45-day free trial'}</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.pro.feat2') || 'Up to 500 hectares and 5 users'}</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.pro.feat3') || 'All modules included'}</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.pro.feat4') || 'Priority support'}</span></li>
          </ul>
          <a
            href="/register"
            className="block w-full py-3 px-6 text-center rounded-lg bg-[#1F4D38] text-white font-semibold hover:bg-[#163A2A] transition-colors"
          >
            {t('landing.pricing.pro.cta') || 'Start Free Trial'}
          </a>
        </div>

        {/* Enterprise Tier */}
        <div className="bg-white rounded-lg border border-[rgba(14,26,20,0.08)] p-8">
          <h3 className="text-2xl font-bold text-[#0E1A14] mb-2">{t('landing.pricing.ent.title') || 'Enterprise'}</h3>
          <div className="flex items-baseline mb-4">
            <span className="text-4xl font-extrabold text-[#0E1A14]">{t('landing.pricing.ent.price') || 'Custom'}</span>
          </div>
          <p className="text-[#5B6660] mb-6">{t('landing.pricing.ent.desc') || 'For cooperatives, large estates and institutions.'}</p>
          <ul className="space-y-3 mb-8">
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.ent.feat1') || 'Unlimited hectares and users'}</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.ent.feat2') || 'All modules included (AI, Lidar, etc.)'}</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.ent.feat3') || 'Dedicated tenant isolation'}</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.ent.feat4') || 'SLA and account manager'}</span></li>
          </ul>
          <a
            href={`mailto:${(window as any).__ENV__?.SALES_EMAIL || 'sales@example.com'}`}
            className="block w-full py-3 px-6 text-center rounded-lg bg-white text-[#0E1A14] border-2 border-[rgba(14,26,20,0.18)] font-semibold hover:border-[#0E1A14] transition-colors"
          >
            {t('landing.pricing.ent.cta') || 'Contact Sales'}
          </a>
        </div>
      </div>
    </div>
  );
};
```

**Key changes:**
- Removed `bg-gradient-to-r` from buttons → flat `bg-[#1F4D38]`
- Removed `shadow-xl`, `hover:scale-105`, `hover:-translate-y-2`
- Removed "Most Popular" badge
- Removed `rounded-3xl` → `rounded-lg`
- Added Free tier (3-cols instead of 2)
- `border-[rgba(14,26,20,0.08)]` instead of `border-green-500`

---

### Task 15: Update PartnerLogos (grayscale + eyebrow)

**File:** Modify `apps/host/src/components/partners/PartnerLogos.tsx`

- [ ] **Step 1: Update styling and add eyebrow key**

Replace the component return:

```tsx
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
```

**Key changes:**
- Removed `transform hover:scale-110`
- Added `grayscale` default, `group-hover:grayscale-0`
- Changed eyebrow from `t('landing.partners_title')` to `t('landing_v2.partners_eyebrow')`
- Changed opacity from 0.7 to 0.6
- No H2, just eyebrow (logos speak per spec)

---

### Task 16: Rewrite CommercialLanding.tsx as thin orchestrator

**File:** Rewrite `apps/host/src/pages/landing/CommercialLanding.tsx`

- [ ] **Step 1: Replace entire file**

```tsx
import React, { useState, useEffect } from 'react';
import { useI18n } from '@/context/I18nContext';
import { CookieBanner } from '@/components/CookieBanner';
import { PricingCards } from '@/components/pricing/PricingCards';
import { PartnerLogos } from '@/components/partners/PartnerLogos';

import { HeroTopBar } from './blocks/HeroTopBar';
import { HeroSection } from './blocks/HeroSection';
import { ProofBand } from './blocks/ProofBand';
import { ProductAnchor } from './blocks/ProductAnchor';
import { OpenCoreSection } from './blocks/OpenCoreSection';
import { EcosystemModules } from './blocks/EcosystemModules';
import { OpenStandards } from './blocks/OpenStandards';
import { FinalCTA } from './blocks/FinalCTA';
import { LandingFooter } from './blocks/LandingFooter';

export const CommercialLanding: React.FC = () => {
  const { t, setLanguage, language, supportedLanguages } = useI18n();
  const [showLanguageMenu, setShowLanguageMenu] = useState(false);
  const [isScrolled, setIsScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > window.innerHeight * 0.5);
    };
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  const handleLanguageChange = async (lang: string) => {
    await setLanguage(lang as any);
    setShowLanguageMenu(false);
  };

  return (
    <div className="min-h-screen bg-white">
      <CookieBanner />

      <HeroTopBar
        isScrolled={isScrolled}
        language={language}
        supportedLanguages={supportedLanguages}
        showLanguageMenu={showLanguageMenu}
        setShowLanguageMenu={setShowLanguageMenu}
        onLanguageChange={handleLanguageChange}
      />

      {/* Block 2: Hero */}
      <HeroSection />

      {/* Block 3: Proof band */}
      <ProofBand />

      {/* Block 4: Product anchor */}
      <ProductAnchor />

      {/* Block 5: Open Core */}
      <OpenCoreSection />

      {/* Block 6: Ecosystem modules */}
      <EcosystemModules />

      {/* Block 7: Open standards */}
      <OpenStandards />

      {/* Block 8: Pricing */}
      <PricingCards />

      {/* Block 9: Partners */}
      <PartnerLogos />

      {/* Block 10: Final CTA */}
      <FinalCTA />

      {/* Block 11: Footer */}
      <LandingFooter
        language={language}
        supportedLanguages={supportedLanguages}
        showLanguageMenu={showLanguageMenu}
        setShowLanguageMenu={setShowLanguageMenu}
        onLanguageChange={handleLanguageChange}
      />
    </div>
  );
};
```

**Scrolling threshold:** Changed from `50px` to `50% of viewport height` per spec §4.5 (top bar transitions after scrolling past half the hero).

---

### Task 17: Drop fallback i18n keys in remaining 4 locales

**Files:** Modify `apps/host/public/locales/{ca,eu,fr,pt}/common.json`

- [ ] **Step 1: Copy English `landing_v2` block into each locale file**

For each of `ca`, `eu`, `fr`, `pt`: add the English `landing_v2` block as a fallback. This ensures `t('landing_v2.*')` returns English strings (not the raw key) in unsupported languages.

---

### Task 18: Verification

**Manual verification checklist:**

- [ ] **Step 1: Start dev server**

```bash
cd nkz/apps/host && pnpm dev
```

- [ ] **Step 2: Visual checks**
  - Scroll through all 12 blocks: Hero → Proof → Product → OpenCore → Modules → Standards → Pricing → Partners → FinalCTA → Footer
  - Top bar transitions from transparent → white after scrolling past 50vh
  - Cookie banner appears
  - Language selector works
  - Login button works
  - CTA buttons navigate to `/register`
  - No horizontal overflow at 360px, 768px, 1024px, 1440px, 1920px
  - Video autoplays on desktop, poster renders on mobile (< 768px)

- [ ] **Step 3: Reduced motion**
  - Enable `prefers-reduced-motion: reduce` in DevTools
  - Verify: video hidden, no animations

- [ ] **Step 4: Run type-check**

```bash
cd nkz/apps/host && pnpm tsc --noEmit
```

- [ ] **Step 5: Run build**

```bash
cd nkz/apps/host && pnpm build
```

---

## Spec Coverage Self-Review

| Spec § | Requirement | Covered by |
|---|---|---|
| §3 | 12-block IA order | Task 16 (CommercialLanding.tsx) |
| §4.1 | Hero composition (video, overlay, text, CTAs) | Task 6 (HeroSection) |
| §4.2 | Video sources, poster, mobile fallback | Task 6, Task 0 |
| §4.3 | Overlay gradient | Task 6 |
| §4.4 | Hero content (eyebrow, H1, sub, CTAs) | Task 6 |
| §4.5 | Top bar (transparent → white) | Task 4 (HeroTopBar) |
| §4.6 | Scroll indicator | Task 5 (ScrollIndicator) |
| §5 | Proof band (4 metrics) | Task 7 (ProofBand) |
| §6 | Product anchor + Cesium screenshot | Task 8 (ProductAnchor) |
| §7 | Open core vs managed cloud (2-col) | Task 9 (OpenCoreSection) |
| §8 | Ecosystem modules grid (4×2) | Task 10 (EcosystemModules) |
| §9 | Open standards band | Task 11 (OpenStandards) |
| §10 | Pricing (3 tiers, no gradients) | Task 14 (PricingCards rewrite) |
| §11 | Partners (grayscale, eyebrow) | Task 15 (PartnerLogos update) |
| §12 | Final CTA (dark block) | Task 12 (FinalCTA) |
| §13 | Footer (nkz-os.org coherent) | Task 13 (LandingFooter) |
| §14 | Visual system (fonts, palette, shadows, motion) | Tasks 1-2 (fonts, CSS) + all components inline |
| §15 | Video pipeline (assets, perf, a11y) | Task 0 (assets), Task 6 (perf/a11y) |
| §16 | Blocking assets | Task 0 (video), Task 8 note (screenshot placeholder) |
| §17 | Tech notes (i18n, routing, auth) | Tasks 3, 16 |
| §18.1-10 | Acceptance criteria | Task 18 (verification) |

**Gaps identified:**
- Cesium screenshot is placeholder — blocking asset per spec §16, needs capture
- WebM/poster generation needs ffmpeg (not installed) — user must run
- Remaining 4 locales get English fallback (acceptable per spec §18.7)

---

## Execution Handoff

Plan complete. The implementation creates 10 new files in `blocks/`, rewrites `CommercialLanding.tsx` and `PricingCards.tsx`, updates `PartnerLogos.tsx`, adds i18n keys, and adds font/CSS utilities. Total: ~15 files changed.

**Two execution options:**

1. **Subagent-Driven (recommended)** — Fresh subagent per task, review between tasks, fastest iteration
2. **Inline Execution** — Execute tasks sequentially in this session with checkpoints
