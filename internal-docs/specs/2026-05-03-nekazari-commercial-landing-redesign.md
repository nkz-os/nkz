# Spec — Rediseño Landing Comercial Nekazari (CommercialLanding.tsx)

**Fecha**: 2026-05-03
**Autor**: brainstorming session
**Estado**: aprobado, pendiente de plan de implementación
**Archivo target**: `nkz/apps/host/src/pages/landing/CommercialLanding.tsx`
**Live**: `https://nekazari.robotika.cloud/` (modo `commercial`, confirmado vía `/api/public/platform-settings`)

---

## 1. Objetivo

Reemplazar la landing comercial actual (estética genérica de plantilla SaaS 2022, con orbs flotantes, sparkles y triple CTA) por una landing que:

1. Refleje el posicionamiento real del producto: **open core (`nkz-os`) + managed cloud (`Nekazari`)**, modelo Vercel/Sentry/Supabase/Red Hat aplicado a agritech/industria/medio ambiente.
2. Encarne la analogía interna **"el Android de la agricultura"** sin enunciarla — la sección de módulos del ecosistema y el guiño explícito al OSS la hacen evidente.
3. Sea coherente de familia con `nkz-os.org` (mismo wordmark, paleta y código tipográfico) pero con identidad propia más rica y comercial, ya que dirige a clientes pagantes y no a la comunidad OSS.
4. Convierta: una CTA primaria por bloque, free tier visible, demo enterprise como link secundario.

## 2. Fuera de alcance

- Rediseño de `OSSLanding.tsx` (este spec solo cubre `CommercialLanding.tsx`).
- Rediseño de `nkz-os.org` (proyecto Astro independiente, fuera del repo `nkz`).
- Cambios en backend `/api/public/platform-settings` o en el switching `commercial` ↔ `oss`.
- Componentes ya existentes que se mantienen: `PricingCards`, `PartnerLogos`, `CookieBanner`, `NkzAttribution`, `KeycloakAuthContext`, i18n via `useI18n`. Se reutilizan tal cual; cualquier ajuste interno a esos componentes se trata como subtarea opcional.
- Selector de idioma: se rediseña visualmente pero no se cambia su mecánica.

## 3. Arquitectura de información

Orden de bloques top-to-bottom:

```
1. Top bar (fixed)
2. Hero cinematográfico con splash.mp4
3. Proof band (métricas reales)
4. "Qué obtienes con Nekazari" — screenshot del Cesium viewer
5. Powered by nkz-os — open core vs managed cloud (dos columnas)
6. Módulos del ecosistema — grid 4×2
7. Construido sobre estándares abiertos — wordmarks técnicos
8. Pricing — 3 tiers (componente existente, ajuste estético)
9. Partners / Customers — logos (componente existente)
10. CTA final — bloque oscuro
11. Footer — coherente con nkz-os.org
12. Cookie banner (existente, sin cambios)
```

Cambios respecto a la landing actual:
- **Eliminado**: orbs flotantes blurred, `Sparkles` icon, gradiente verde de fondo, gradiente verde en `bg-clip-text` del título, marco `rounded-3xl border-4 border-white` en imagen, trust badges como píldoras.
- **Añadido**: hero con video, proof band, sección "Powered by nkz-os", sección "estándares abiertos", scroll indicator.
- **Reordenado**: pricing y partners se mantienen pero después de "Powered by" y "Módulos" para que la diferenciación open-core llegue antes que el precio.

## 4. Hero — sección crítica

### 4.1 Composición

Full viewport height (`min-height: 100vh`, content top-anchored o bottom-anchored según test, default bottom-left).

```
┌──────────────────────────────────────────────────────────┐
│ [NKZ wordmark]                  [es▾]  [Iniciar sesión] │  ← top bar
│                                                           │
│                                                           │
│  ╔════════════════════════════════════════════════════╗  │
│  ║                                                    ║  │
│  ║         splash.mp4 (autoplay muted loop)           ║  │
│  ║         girl + campo + risa, fade-out con          ║  │
│  ║         "NKZ" wordmark al final del loop           ║  │
│  ║                                                    ║  │
│  ╚════════════════════════════════════════════════════╝  │
│                                                           │
│  OPEN CORE · MANAGED CLOUD                                │  ← eyebrow mono uppercase
│                                                           │
│  El stack abierto del campo.                              │  ← H1 editorial
│  Operado para ti.                                         │
│                                                           │
│  Despliega nkz-os sin gestionar Kubernetes ni FIWARE.     │  ← sub
│  Multitenant, NGSI-LD nativo, infraestructura europea.    │
│                                                           │
│  [ Crear tenant gratuito ]   Solicitar demo →             │  ← 1 primaria + 1 link
│                                                           │
│                              │                            │  ← scroll indicator
│                            Scroll                         │
└──────────────────────────────────────────────────────────┘
```

### 4.2 Video (splash.mp4)

- **Source**: `/home/g/Documents/nekazari/nkz-mobile/assets/splash.mp4` (4.18 MB, MP4 H.264). Hay que **copiarlo a `nkz/apps/host/public/media/hero.mp4`** (no referenciar desde el repo móvil — son repos independientes).
- **Encoding adicional**: generar variante WebM VP9 (`hero.webm`, target ~2.5MB) y poster JPEG del primer frame (`hero-poster.jpg`, ~120KB) para fallback y CLS-zero. Comando de referencia:
  ```bash
  ffmpeg -i splash.mp4 -c:v libvpx-vp9 -b:v 1.5M -an hero.webm
  ffmpeg -i splash.mp4 -ss 00:00:00.5 -frames:v 1 -q:v 3 hero-poster.jpg
  ```
- **HTML**:
  ```tsx
  <video
    autoPlay muted loop playsInline
    poster="/media/hero-poster.jpg"
    preload="metadata"
    className="absolute inset-0 w-full h-full object-cover"
    aria-hidden="true"
  >
    <source src="/media/hero.webm" type="video/webm" />
    <source src="/media/hero.mp4" type="video/mp4" />
  </video>
  ```
- **Mobile fallback** (`< 768px`): no autoplay del video, mostrar `hero-poster.jpg` como `<img>`. Decisión: ahorrar batería/datos en móvil.
- **`prefers-reduced-motion`**: desactivar autoplay, mostrar poster estático.

### 4.3 Overlay y legibilidad

Capa oscura sobre el video — sin esta capa el texto no se lee:

```css
background: linear-gradient(
  180deg,
  rgba(14, 26, 20, 0.30) 0%,
  rgba(14, 26, 20, 0.55) 50%,
  rgba(14, 26, 20, 0.85) 100%
);
```

Tope superior más claro (deja respirar el video y que se vea la cara/risa); fondo más oscuro donde aterriza el texto.

### 4.4 Contenido

- **Eyebrow** (texto pequeño sobre el H1): `OPEN CORE · MANAGED CLOUD`, font mono, `letter-spacing: 0.15em`, uppercase, color `rgba(255,255,255,0.7)`, peso 500, 12-13px.
- **H1**: `El stack abierto del campo. Operado para ti.` — `font-family: 'Inter Display', 'Inter', sans-serif`, peso 600, `font-size: clamp(2.5rem, 6vw, 5.5rem)`, `line-height: 1.05`, `letter-spacing: -0.03em`, color blanco. Punto final intencional (no es slogan, es declaración).
- **Sub**: `Despliega nkz-os sin gestionar Kubernetes ni FIWARE. Multitenant, NGSI-LD nativo, infraestructura europea.` — `font-family: 'Inter'`, peso 400, `font-size: clamp(1.05rem, 1.4vw, 1.25rem)`, `line-height: 1.5`, color `rgba(255,255,255,0.85)`, `max-width: 56ch`.
- **CTA primaria**: botón sólido fondo verde NKZ `#1F4D38`, texto blanco, peso 600, `padding: 0.875rem 1.75rem`, `border-radius: 0.5rem` (8px, NO pills, NO 16px+), hover: `#163A2A` + leve `translateY(-1px)`. Sin gradientes. Sin sombras grandes.
- **Link secundario**: texto blanco peso 500, subrayado en hover (no `border-b` permanente), flecha `→` que se desplaza 2px en hover.

### 4.5 Top bar

- Fixed, full-width, padding `1.25rem 2rem`.
- **Estado sobre hero (transparente)**: `background: transparent`, `NKZ` wordmark blanco, links blancos.
- **Estado scrolled (>50vh)**: `background: rgba(255,255,255,0.92)`, `backdrop-filter: blur(8px)`, `border-bottom: 1px solid rgba(14,26,20,0.06)`, wordmark `#0E1A14`, links `#0E1A14`.
- Transición de estado: `transition: background 0.3s, color 0.3s`.

### 4.6 Scroll indicator

Bottom-center del hero. Línea vertical fina (1px × 32px) `rgba(255,255,255,0.4)` con animación de "drop" de 1.5s loop, label pequeño `Scroll` (mono, uppercase, 11px) encima.

## 5. Proof band

Justo debajo del hero, fondo blanco `#FAFAF7`, padding `4rem 2rem`, hairline arriba y abajo `1px solid rgba(14,26,20,0.08)`.

Layout: 4 columnas en desktop, 2×2 en tablet, stack en móvil.

```
127         14          3.2M           6
parcelas    plantas     observaciones  países
monitorizadas industriales /día
```

- **Números**: `font-family: 'JetBrains Mono'` o `'IBM Plex Mono'`, peso 500, `font-size: clamp(2rem, 3.5vw, 3rem)`, color `#0E1A14`.
- **Labels**: `Inter`, peso 400, 14px, color `#5B6660`.
- Métricas reales — vienen del backend o se hardcodean por ahora con valor fijo. **TODO en plan**: decidir fuente de los números (estático vs `/api/public/platform-stats` futuro).

## 6. "Qué obtienes con Nekazari" — anchor de producto

Sección con screenshot del Cesium viewer. Esto reemplaza la "imagen genérica" actual.

Layout: bipartido con texto a la izquierda, screenshot a la derecha en desktop; stacked en móvil.

- **Eyebrow**: `EL PRODUCTO`
- **H2**: `Una consola operativa. Cualquier capa de datos.`
- **Cuerpo**: 2-3 frases breves describiendo el viewer (Cesium, módulos cargados, drag-n-drop de capas, observaciones en tiempo real).
- **Lista corta** (sin tarjetas, solo texto con bullets `·`):
  - Visualización 3D de parcelas, parcelarios, infraestructuras industriales
  - Capas dinámicas: Vegetation Prime, LiDAR, GIS Routing, DataHub
  - Dashboard multitenant con aislamiento estricto
  - Multi-idioma (es, en, ca, eu, fr, pt)

- **Screenshot**: real, del Cesium viewer en estado pleno. Si no existe captura adecuada en el repo, **bloquea implementación** — necesita generarse antes. Encuadre 16:9 o 4:3, marco glass sutil:
  ```css
  border-radius: 12px;
  box-shadow: 0 30px 60px -20px rgba(14,26,20,0.25),
              0 0 0 1px rgba(14,26,20,0.06);
  ```
- Sin `border-4 border-white`. Sin `rounded-3xl`. Sin gradiente decorativo detrás.

## 7. Powered by nkz-os — diferenciación open core / managed cloud

**La sección clave** del posicionamiento. Sin ella, la página no comunica el modelo de negocio real.

Fondo blanco, padding generoso (`8rem 2rem`).

- **Eyebrow**: `OPEN CORE`
- **H2**: `Construido sobre nkz-os. Operado por nosotros.`
- **Párrafo introductorio** (2-3 frases):
  > Nekazari opera **nkz-os**, el proyecto open-source que mantenemos en abierto. Puedes desplegarlo tú mismo en tu Kubernetes, o dejar que nosotros lo gestionemos para ti.

Layout dos columnas con divisor vertical (1px hairline), igual peso visual:

| **nkz-os (Self-hosted)** | **Nekazari (Cloud)** |
|---|---|
| Open source · AGPL-3.0 | SaaS gestionado |
| GitHub: `nkz-os/nkz` | Multitenant aislado |
| Comunidad + módulos abiertos | Mismos módulos + soporte SLA |
| Despliegue propio en K8s | Infra europea (DE/FR), backups |
| Gratis para siempre | Free tier · Pro · Enterprise |
| `Documentación →` (link a nkz-os.org) | `Crear tenant gratuito →` (CTA) |

- Tipografía: títulos de columna `Inter Display` peso 600 22px, items `Inter` peso 400 15px con espaciado `0.875rem` entre líneas.
- Sin iconos por línea. Es texto editorial honesto, no comparativa de marketing con checkmarks verdes.

## 8. Módulos del ecosistema — el "AOSP" visible

Sección que refuerza la analogía Android. Grid de los módulos reales del ecosistema.

- **Eyebrow**: `ECOSISTEMA`
- **H2**: `Un módulo para cada capa.`
- **Sub**: `Construidos sobre la misma base FIWARE. Disponibles en self-hosted y cloud.`

Grid de 4 columnas × 2 filas en desktop, 2×4 en tablet, 1 columna en móvil.

Módulos a listar (los que están en producción según `CLAUDE.md` / `MEMORY.md`):

1. Vegetation Prime — índices vegetales (NDVI, GNDVI, custom)
2. LiDAR — ingestión SOTA, tilesets
3. GIS Routing — rutas y geoanálisis
4. DataHub — analítica de series temporales
5. IoT — provisioning FIWARE estándar
6. VPN — Tailscale + Headscale
7. Zulip — comunicaciones
8. CUE — configuración (Phase 1)

Cada tarjeta:
```
┌────────────────────────────┐
│  [icon mono · 32px]        │
│                            │
│  Vegetation Prime          │  ← Inter Display 18px peso 600
│                            │
│  Índices vegetales sobre   │  ← Inter 14px peso 400 #5B6660
│  imágenes Sentinel-2.      │     2 líneas máx
│                            │
│  Open source ·  Cloud      │  ← badge mono 11px ambos
│                            │
└────────────────────────────┘
```

- Sin `hover:scale-105`, sin `hover:shadow-2xl`, sin `transform`. Hover sutil: `border-color: rgba(14,26,20,0.18)` + `background: #FAFAF7` (cambio mínimo).
- `border: 1px solid rgba(14,26,20,0.08)`, `border-radius: 8px`, padding `1.5rem`.
- Click: navega a `nkz-os.org/modules/<id>` (link cross-domain — el detalle de cada módulo vive en el OSS site, no replicar aquí).

CTA al pie: `Ver todos los módulos en nkz-os.org →` (link, no botón).

## 9. Construido sobre estándares abiertos

Banda compacta, fondo `#FAFAF7`, padding `4rem 2rem`.

- **Eyebrow**: `STANDARDS`
- **H2** (más pequeño que otros, 28px): `Sin lock-in. Sin formato propietario.`

Wordmarks o text-only (preferir text-only para no caer en logo soup):

```
FIWARE NGSI-LD · Smart Data Models · Keycloak (OIDC)
Kubernetes · TimescaleDB · MQTT · OAuth 2.0 · MinIO (S3)
```

- Tipografía mono, 16-18px, peso 400, color `#5B6660`, separadores `·` con espacios.
- Si el día de mañana se prefieren wordmarks reales, dejar el componente preparado para sustituir; arrancar con texto.

## 10. Pricing

Reutiliza `<PricingCards />` existente. Cambios estéticos a aplicar dentro de ese componente:

- Quitar gradientes y `shadow-2xl` si los hubiera.
- Mantener 3 tiers: Free / Pro / Enterprise.
- Tier Free destacado con un hairline accent (no badge "Most Popular" si lo tuviera).
- Acción primaria del tier Free: `Crear cuenta gratis`. Pro: `Suscribirse`. Enterprise: `Hablar con ventas`.

Si el componente no permite estos ajustes sin invasión, abrir subspec mínima en plan de implementación. Por defecto: **no rediseñar `PricingCards` en este spec**, solo eliminar wrappers/sombras a su alrededor en `CommercialLanding`.

## 11. Partners / Customers

Reutiliza `<PartnerLogos />` existente. Aplicar:

- Fondo blanco, padding `4rem 2rem`.
- Logos en escala de grises por defecto (`filter: grayscale(1) opacity(0.6)`), color en hover.
- Eyebrow `CONFÍAN EN NEKAZARI`, sin H2 (los logos hablan).

## 12. CTA final

Bloque oscuro, fondo `#0E1A14`, padding `8rem 2rem`. Sin `bg-grid-pattern`, sin gradientes verdes.

```
                Empieza con un tenant gratuito.
                En menos de dos minutos.

                [ Crear tenant gratuito ]
                                    o solicita una demo →
```

- **H2**: `Empieza con un tenant gratuito. En menos de dos minutos.` Inter Display peso 600, 48px desktop, blanco.
- **CTA primaria**: botón blanco, texto `#0E1A14`, mismo estilo que la del hero pero invertido.
- **Link**: `solicita una demo →`, blanco semitransparente.

## 13. Footer

Coherente en estructura con `nkz-os.org` para que el visitante perciba familia.

```
NKZ
Plataforma SaaS sobre nkz-os.

Producto                Open source            Empresa
─────────              ─────────              ─────────
Módulos                 GitHub                 Sobre nosotros
Pricing                 nkz-os.org             Contacto
Documentación           Comunidad              Privacidad
Estado                  Roadmap                Términos

────────────────────────────────────────────────
© 2026 Nekazari · Powered by nkz-os · AGPL-3.0
[NKZ attribution]                          [es▾]
```

- Fondo `#0E1A14`, texto `#A8B1AC` body, `#FAFAF7` headers.
- Tipografía `Inter` 14px body, 12px copyright.
- Mantiene `<NkzAttribution variant="commercial" />` y `__ENV__.COMPANY_URL` / `SUPPORT_EMAIL` que ya usa la actual.

## 14. Sistema visual

### Tipografía

| Rol | Familia | Pesos | Detalle |
|---|---|---|---|
| Display (H1, H2 grandes) | `Inter Display` o `Inter` | 600 | `letter-spacing: -0.03em` en H1, `-0.02em` en H2 |
| UI / body | `Inter` | 400, 500, 600 | `letter-spacing: -0.01em` en sub-headlines |
| Mono (eyebrows, métricas) | `JetBrains Mono` o `IBM Plex Mono` | 400, 500 | uppercase + `letter-spacing: 0.15em` para eyebrows |

Si `Inter Display` no es viable (licencia / weight no disponible), usar `Inter` con tracking ajustado (`-0.04em` en H1 grande). Mono: si no hay JetBrains/Plex, fallback `'ui-monospace', 'SF Mono', monospace`.

### Paleta

```css
--ink: #0E1A14;          /* texto sobre claro, fondos oscuros */
--bone: #FAFAF7;         /* fondo de bandas (#FFFFFF excesivamente blanco) */
--white: #FFFFFF;        /* fondos puros, hero overlay */
--green-nkz: #1F4D38;    /* CTA primaria, accent */
--green-nkz-hover: #163A2A;
--mute: #5B6660;         /* textos secundarios */
--hairline: rgba(14, 26, 20, 0.08);
--hairline-strong: rgba(14, 26, 20, 0.18);
```

**Cero gradientes** (excepción: overlay del hero sobre el video). **Cero `bg-clip-text`**. **Cero blurs flotantes decorativos**.

### Espaciado y tipografía macro

- Container max-width: `1200px` (no `max-w-7xl` Tailwind = 1280, queremos un pelín más estrecho para columnas más cómodas).
- Padding lateral: `2rem` desktop, `1.25rem` mobile.
- Padding vertical de bandas: `8rem 0` desktop, `4rem 0` mobile.
- Gap entre items en grids: `2rem`.

### Sombras

- Single elevación: `box-shadow: 0 1px 2px rgba(14,26,20,0.04), 0 4px 8px rgba(14,26,20,0.04)`.
- Único caso de sombra mayor: el screenshot del Cesium viewer (sección §6) — `0 30px 60px -20px rgba(14,26,20,0.25)`.
- **Prohibido**: `shadow-2xl` Tailwind, sombras verdes/coloreadas.

### Motion

- Transiciones por defecto: `transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1)`.
- Hover sobre tarjetas de módulo: cambio de `border-color` y `background` solamente. No `transform`.
- Animaciones decorativas: solo el scroll indicator del hero.
- `prefers-reduced-motion: reduce`: desactivar autoplay del video, scroll indicator estático, transiciones a `0.01s`.

## 15. Integración del video

### 15.1 Pipeline de assets

1. Copiar `nkz-mobile/assets/splash.mp4` → `nkz/apps/host/public/media/hero.mp4`.
2. Generar `hero.webm` (VP9, ~1.5-2.5MB) y `hero-poster.jpg` (primer frame, ~120KB).
3. Verificar que termina con el wordmark NKZ legible (lo confirma el usuario). Si el wordmark sale en negro y el overlay es oscuro, ajustar overlay (top más claro) o pedir versión sin wordmark final para evitar redundancia con el wordmark de la top bar.
4. Audio: forzar `muted` siempre.

### 15.2 Performance

- `preload="metadata"` (no `auto`) — el video carga al entrar viewport via Intersection Observer, no al render inicial.
- Total payload del hero: target < 3MB (poster + WebM).
- LCP candidate: el poster JPEG, no el video. Asegurar que `hero-poster.jpg` está en el `<picture>` o como `<img>` con `fetchpriority="high"` en el fallback mobile.

### 15.3 Accesibilidad

- `aria-hidden="true"` en el `<video>` (es decoración).
- El contenido textual del hero NO depende del video — si falla, todo se lee igual.
- Contraste del texto blanco sobre overlay oscuro: verificar AA (mínimo 4.5:1) sobre el percentil 80 de luminancia del video.

## 16. Activos requeridos antes de implementar

Lista bloqueante. La implementación no avanza sin estos:

| Asset | Estado | Acción |
|---|---|---|
| `splash.mp4` original | ✅ existe en `nkz-mobile/assets/` | copiar y transcodificar |
| `hero.webm` | ❌ falta | generar con ffmpeg (15.1.2) |
| `hero-poster.jpg` | ❌ falta | extraer con ffmpeg (15.1.2) |
| Screenshot real Cesium viewer (1920×1080 mín, módulos cargados, light theme si existe) | ❓ por confirmar | si no existe, generar con sesión de captura sobre tenant demo |
| Métricas reales del proof band | ❓ definir | placeholder estático con valores actuales o endpoint futuro |
| Iconos mono de los 8 módulos (32px) | ❓ por confirmar | usar `lucide-react` consistente con el resto del repo, mapping definido en plan |
| Wordmark NKZ blanco y negro | ✅ existe (top bar actual lo usa) | reutilizar |

## 17. Notas técnicas

- **Stack**: React 18 + TS 5 + Tailwind (existente). No introducir librerías nuevas. Si se necesita Inter Display y no se puede importar de Google Fonts (privacy), self-host en `public/fonts/` con `font-display: swap`.
- **i18n**: todo string nuevo se añade a `nkz/apps/host/public/locales/{es,en,ca,eu,fr,pt}/landing.json` (o ampliar `common.json` según patrón existente). Mínimo `es` + `en` antes de merge.
- **Routing**: la página sigue siendo `<CommercialLanding />` montada por `<Landing />` cuando `landing_mode === 'commercial'`. No cambiar el switch.
- **Auth**: el botón "Iniciar sesión" del top bar invoca `useAuth().login()` igual que ahora. La CTA "Crear tenant gratuito" navega a `/register`. Sin cambios en el flujo.
- **Server-side rendering**: la landing es CSR (Vite), el video es client-side por naturaleza. No requiere SSR.

## 18. Criterios de aceptación

Para considerar el rediseño completado:

1. La página live en `https://nekazari.robotika.cloud/` muestra la nueva estructura completa (12 bloques de §3).
2. Hero reproduce el video en desktop con overlay correcto, poster en mobile, todo responsive desde 360px hasta 1920px sin overflow horizontal.
3. Lighthouse Performance ≥ 85 en mobile, ≥ 95 en desktop. LCP < 2.5s en 4G simulado.
4. WCAG AA en contraste para todos los textos sobre fondo claro y oscuro.
5. `prefers-reduced-motion` honored (video pausado, sin animaciones).
6. Cero referencias a las clases eliminadas: `Sparkles`, `bg-clip-text`, `bg-grid-pattern`, `animate-pulse delay-1000`, `rounded-3xl border-4 border-white`, orbs blurred.
7. Strings en `es` y `en` mínimo. Otros 4 idiomas pueden tener fallback temporal a `en`.
8. Sección "Powered by nkz-os" tiene link funcional a `https://nkz-os.org/`.
9. La grid de módulos enlaza a las páginas correspondientes en `nkz-os.org/modules/...`.
10. Cookie banner sigue apareciendo y funcional.

## 19. Preguntas abiertas

1. **Métricas reales del proof band**: ¿valor estático actualizado mensualmente en el código, o endpoint público `/api/public/platform-stats`? Decidir en plan.
2. **Wordmark final del video**: si el splash.mp4 termina con el wordmark NKZ visible y la top bar también muestra el wordmark NKZ, hay redundancia. ¿Cropping del video para que termine antes del wordmark, o aceptar la doble exposición como refuerzo de marca?
3. **Pricing**: ¿los nombres de tiers están finalizados (Free / Pro / Enterprise) o requieren ajuste comercial?
4. **Partners**: ¿hay nuevos logos que añadir / quitar respecto al `<PartnerLogos />` actual?
5. **Inter Display**: ¿usar Google Fonts (privacy implications) o self-host? Self-host añade ~100KB pero evita el third-party request.

---

## Anexo A — Referencias visuales

- **Vercel.com**: hero con video/animación, top bar transparente → solid, eyebrow + H1 editorial, CTAs minimal.
- **Sentry.io**: open core / managed cloud diferenciación, sección dedicada honesta sobre el OSS.
- **Supabase.com**: ecosistema de módulos, palette neutral con un accent verde, tipografía técnica.
- **Linear.app**: spacing y restraint, hover states minimalistas.
- **Stripe.com**: jerarquía tipográfica, single CTA primaria por bloque.

## Anexo B — Inventario de cambios contra la versión actual

| Bloque actual | Acción |
|---|---|
| `bg-gradient-to-br from-green-50 via-white to-blue-50` | Eliminar — fondo `bone` o `white` plano |
| 3 orbs `blur-3xl animate-pulse` | Eliminar |
| `Sparkles` icon animado | Eliminar |
| `Shield` icon en logo cuadro verde | Eliminar — usar wordmark NKZ tipográfico |
| H1 `bg-clip-text` gradiente verde | Eliminar — texto sólido blanco/ink |
| Imagen `/NKZ_landing_Page.png` con `border-4 border-white` | Reemplazar por screenshot real Cesium viewer en marco glass §6 |
| Triple CTA (Try free / Access / View Pricing) | Reducir a 1 primaria + 1 link |
| Trust badges píldoras | Eliminar — sustituir por proof band §5 |
| Features grid 6 cards iconos verdes | Reemplazar por sección Módulos §8 con módulos reales |
| CTA section verde gradiente | Cambiar por CTA dark §12 |
| Footer 4 columnas actual | Rediseñar §13 coherente con nkz-os.org |
| `useEffect` scroll listener | Mantener (mismo mecanismo, top bar transparente → solid) |
| `useI18n`, `useAuth`, `<CookieBanner />`, `<PricingCards />`, `<PartnerLogos />`, `<NkzAttribution />` | Mantener intactos, solo cambiar wrappers/spacing |
