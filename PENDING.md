# Nekazari — Pending Tasks

> 🔴 **MANDATO CRÍTICO E INAMOVIBLE: ESTÁNDAR ESTRICTO FIWARE NGSI-LD** 🔴
> 1. **CERO ESCRITURAS DIRECTAS (TIMESERIES/TELEMETRÍA):** ~~Risk-worker, sensors, telemetry-worker, weather-worker migrados (2026-06-04 / 2026-06-11, PRs #431, #433, ParcelWeatherEngine).~~
> 2. **CERO ENTIDADES INVENTADAS:** PROHIBIDO crear modelos de datos propios. Usar OBLIGATORIAMENTE diccionarios FIWARE Smart Data Models (ej. `AgriParcel`). Si no existe, usar entidad genérica o añadir como `Property`.
> 3. **NGSI-LD ESTRICTO:** Toda petición a Orion-LD DEBE incluir `@context` en el body (con `application/ld+json`) o en el header `Link` (con `application/json`). Usar `OrionClient` (async) o `SyncOrionClient` (sync) de `nkz-platform-sdk` para inyección automática de headers.

> **📋 INSTRUCCIÓN PARA AGENTES:** Este documento tiene dos secciones. **Solo la parte superior (antes de la línea `ARCHIVE`) contiene trabajo pendiente.** Todo lo que aparece después de la línea `═══ ARCHIVE ═══` ya está completado y solo se conserva como referencia histórica. **No lo interpretes como trabajo por hacer.**

Living document. Add items here as they surface; close them with date and commit ref.

---

## ✅ Session 2026-06-22 — catastro revival + buildings 3D + lidar PNOA auto + MDS surface

> Implementación completa. Spec: `internal-docs-local/2026-06-22-spec-catastro-lidar-mds-design.md`. Plan: `internal-docs-local/2026-06-22-plan-catastro-lidar-mds.md`.

### Catastro Revival (nkz-module-cadastral-spain)
- **FIWARE compliance**: SyncOrionClient SDK, HMAC auth, entity-manager routing, webhook subscription ✅
- **3D Buildings**: DGC BuildingPart WFS, Navarra IDENA buildings (3D geom/attrs/text fallback), API endpoints, Cesium layer with toggle ✅
- Commits: `80dd3d0` `c78d30d` `b3dd21e` `82a665a` `e45459c`

### LiDAR PNOA Auto (nkz-module-lidar)
- **PNOA coverage index**: 135 tiles (peninsula + canarias), has_coverage() now returns True ✅
- **MDS terrain tiles**: phase_e pipeline via geolibre, /terrain/{layer_id}/ endpoints ✅
- Commits: `2a9f2ca` `5254e2c` `3af5edf`

### MDS Surface Layer (nkz-module-eu-elevation)
- **lidar_mds provider type** in terrainFactory, ElevationLayer, AdminControl, i18n ✅
- Commit: `fb12de4`

### Deploy & final state
- [x] Build + push Docker images for cadastral-spain and lidar
- [x] Update ArgoCD — cadastral app pointing to catastro-sp-module-nekazari (correct repo)
- [x] Create ArgoCD app for eu-elevation
- [x] catastro: replicas 0→1 (un-parked)
- [x] lidar: k8s SHA bumped for PNOA coverage + MDS + multi-strategy downloader
- [x] PNOA LAZ URL investigation: IGN/CNIG no expone URLs directas simples. Se implementó PNOADownloader con multi-estrategia (direct URL → CNIG API → fallback con caché). La cobertura se detecta correctamente. La descarga automática es best-effort; si falla, el usuario puede subir .laz manualmente como antes.
- [ ] **Future**: si se descubre endpoint EPT/3D Tiles del IGN o Navarra, integrar como fuente directa

---

## 🧭 Sesión 2026-06-21 — rotaciones / montikobs / crop-health zonal (consolidado)

> Volcado de los workstreams abiertos al final de la sesión 2026-06-21 (P1+SP3 cerrados; auditoría montikobs; crop-health #1/#2; diseño zonal). Memorias: `crop-plan-rotations-2026-06-20`, `commit-style-english-concise`. Detalle operativo de la auditoría: secciones de abajo.

### ✅ Cerrado esta sesión
- **P1 — contrato `AgronomicValue`** (SDK 0.6.0 PyPI): DONE+DEPLOYED+VERIFIED. PRs nkz#662, bioorch#26, crop#18, gitops#38, crop#19. Ambos módulos por ArgoCD.
- **SP3 — panel "Plan & Acciones"** (bioorch frontend): IMPLEMENTADO, PR bioorch#27 **mergeado por el owner**. Falta deploy FE (MinIO) + verificación e2e en vivo.
- **crop-health #2** (queries/DB): PR crop#20 **mergeado**. Quitado read directo a TimescaleDB en `context_client` + dual has/ref en 9 reads.

### 🔴 Montikobs / provisioning de parcelas (operativo — NO empezado a arreglar)
Diagnóstico completo (auditoría 2026-06-21): parcela `montikobs` (`urn:…AgriParcel:80ed26c9…`, tenant montiko) creada OK en Orion con geometría, pero **nada derivado** (sin AgriSoil/WeatherObserved/CropHealthAssessment).
- [ ] **Limpieza PENDIENTE DE CONFIRMAR (bucle teardown 502/503 corriendo cada 25s):** `DELETE FROM tenant_parcel_modules WHERE tenant_id='montiko' AND parcel_id='urn:ngsi-ld:AgriParcel:62a6e83b-b32c-452e-82db-be4aa62aa4d6';` (7 filas de un parcel viejo borrado; retry_count 5580). El clasificador exige confirmación explícita del owner.
- [ ] **Causa raíz #1: ningún módulo tiene `auto_provision=true`** → el reconcile nunca aprovisiona parcelas nuevas. (Los 7 rows del parcel viejo eran activación manual histórica.)
- [ ] **Causa raíz #2: solo `soil` tiene `setup_parcel_url`**; los otros 6 (weather, weather-map, crop-health, vegetation-prime, field-operations, parcels) no → teardown/activate 502.
- [ ] **soil da 503** (servicio no responde / endpoint) — revisar.
- [ ] **Modelo de activación correcto (decidido en la sesión) = 3 granularidades:**
  - **Por parcela (push, una vez):** `soil` (AgriSoil de geometría) + `weather` (estación virtual del centroide; hoy `tenant_weather_locations` montiko vacío) → marcar `auto_provision=true` + cablear `setup_parcel_url`.
  - **Por tenant (scheduled, auto-descubre parcelas):** `weather-map` (gated por `COG_TENANTS`, **hoy VACÍO** → no genera nada; enrolar montiko), `vegetation-prime`, `crop-health`. NO son targets de setup-por-parcela.
  - **Deliberada del usuario:** `bioorchestrator` (assign-crop), `field-operations`.
- [ ] **crop-health lista desde `CropHealthAssessment`** (`assessments.py:303`) → parcela sin assessment no se lista. Debe listar desde `AgriParcel`.
- Specs/planes previos relacionados: `docs/superpowers/specs/2026-06-17-parcel-lifecycle-orchestration-design.md`.

### 🟡 weather vs weather-map (aclarado — NO redundantes)
- `weather` = forecast puntual diario (Open-Meteo, centroide). `weather-map` = **downscaling físico por terreno** (DEM + pendiente/aspect → raster 10m; helada/ET₀ por elevación; `AgriParcelZone` por elevación+aspect, sensor-aware). El COG es cada 5 días (`COG_INTERVAL_DAYS`), el cálculo ET₀ es diario on-demand (`/forecast/et0`). **NO mover COG a crop-health.**
- [ ] **Redundancia real a limpiar:** `weather-map /forecast/et0` duplica el módulo `weather` (centroid-proxy a Open-Meteo) → quitar ese endpoint.

### 🟢 crop-health zonal (Spec 1 EN CURSO esta sesión)
Decisión: crop-health sintetiza **por ZONA (vector), no por píxel**. Consume `AgriParcelZone` (que weather-map ya escribe; terreno elevación+aspect; tiene `sensorNearby`/`sensorDistanceM` por zona), agrega inputs por zona, corre riesgo por zona, emite `CropHealthAssessment` por zona (`…:{parcel}-{zoneId}-{date}`) + rollup parcela. Fallback sin zonas = parcela entera. **Arregla estructuralmente el bug IoT last-write-wins** (cada sensor → su zona). vegetation-health NO crea zonas hoy (futuro: precedencia VRA NDVI).
- [ ] **Spec 1 — backend zonal crop-health: spec+plan LISTOS, pendiente EJECUTAR.** Spec `docs/superpowers/specs/2026-06-21-crop-health-zonal-synthesis-design.md` (revisado: 3 fixes incorporados — consolidación multi-sensor intra-zona, tipo separado `CropHealthZoneAssessment` para back-compat, webhook recompute determinista). Plan `docs/superpowers/plans/2026-06-21-crop-health-zonal-synthesis.md` (8 tasks TDD). Auto-contenidos → ejecutable por agente fresco.
- [ ] **Spec 2 — visor zonal** (aparte, superficie producto → owner): polígonos coloreados por riesgo + hover ("falta riego"/"riesgo pulgón") sobre el visor unificado.
- [ ] **Bug IoT last-write-wins** (data-loss; varios sensores sobreescriben el mismo CropHealthAssessment) — se resuelve dentro de Spec 1.

### Higiene de la sesión
- [ ] Limpiar ramas locales mergeadas (nkz, bioorch, crop-health, gitops). SDK editable `--break-system-packages` 0.6.0 en `~/.local` (benigno). Ver `internal-docs-local/2026-06-21-pendings-sesion.md`.

---

## 🟢 Sensor Health & Calibration System (2026-06-21)

> Implementado y revisado. Spec: `internal-docs-local/NKZ_SENSOR_HEALTH_SPEC.md`. Plan: `docs/superpowers/plans/2026-06-21-sensor-health-system.md`. Impl: 16 commits nkz + 12 commits nkz-module-datahub.

### Pendientes documentados (mejoras futuras)
- [x] **Toggle raw/calibrado funcional en gráficas** — implementado end-to-end. Reader expone raw_measurements + quality_flag, BFF pasa raw_values, worker procesa rawMeasurements, PanelChart renderiza overlay. (nkz#681 + datahub main 2026-06-22)
- [ ] **Integración Odoo** — marcado como opcional en spec. Endpoint `PATCH /api/entities/sensors/{id}/reliability` listo. Falta webhook/subscription que conecte Alert entities → Odoo Work Orders.
- [x] **sensor_profiles health defaults** — migración `090_sensor_profiles_health_defaults.sql` + API expuesta + health_defaults añadido al DeviceProfile de MongoDB + wizard auto-rellena desde el perfil seleccionado. (commits e56fb9c, f4b9591 2026-06-21)
- [x] `quality_flag='stale'` — se setea automáticamente al cerrar un período de calibración (POST `/api/entities/sensors/{id}/calibration`). Datos del período anterior se marcan stale. (commit 35d208a 2026-06-21)

---

## 🌐 i18n Audit — SidebarShell / ModuleGroup / weather-map / soil (2026-06-21)

> Auditoría de traducciones en el visor unificado (`/entities`). Los paneles laterales del visor se ven siempre en español aunque el usuario cambie a euskera/inglés. Causas identificadas y pendientes abajo. Contexto en memoria `internal-docs-local/2026-06-21-i18n-audit-viewer.md` (si se escribe).

### 🔴 viewer-kit: SidebarShell + ModuleGroup — hardcoded Spanish, 0 i18n

Los componentes `SidebarShell` y `ModuleGroup` del paquete `@nekazari/viewer-kit` tienen strings hardcodeados en español sin ningún sistema de internacionalización:

| Archivo | Strings | Estado |
|---------|---------|--------|
| `packages/viewer-kit/src/viewer/SidebarShell.tsx` | `"Expandir panel"` / `"Cerrar panel"` / `"Abrir panel"` (title + tooltip, líneas 147-192) | ❌ Hardcodeado ES |
| `packages/viewer-kit/src/viewer/ModuleGroup.tsx` | `title="Ocultar"` (línea 98) | ❌ Hardcodeado ES |

No existe ningún `import` de i18n, `t()`, ni ficheros de locale en todo el paquete. **Es la causa principal** de que los botones de toggle de panel no se traduzcan nunca.

- [x] **Fix SidebarShell i18n:** nuevo interface `SidebarLabels` con 6 props opcionales + defaults en español. El host pasa `t('viewer.sidebar.*')` via prop `labels`. PR #672 merged + deployed ✅
- [x] **Decisión:** props-based approach (sin dependencia de SDK en viewer-kit). ✅

### 🟡 weather-map — bridge deprecado, registra idiomas de forma no fiable

| Aspecto | Detalle |
|---------|--------|
| Ficheros locale | ✅ Existen: `src/locales/{en,es,eu,ca,fr,pt}.json` (20 líneas c/u, todas traducidas) |
| Mecanismo de registro | ❌ `window.__NKZ_SDK__` (patrón deprecado, AGENTS.md §9) |
| Namespace | `'weather-map'` (no `'common'` como la mayoría) |

```typescript
// Actual (deprecado — falla si __NKZ_SDK__ no está disponible al cargar el módulo):
const nkzSdk = (window as any).__NKZ_SDK__;
if (nkzSdk?.i18n) { nkzSdk.i18n.addResources('eu', 'weather-map', eu); }

// Corrección necesaria:
import { i18n } from '@nekazari/sdk';
i18n.addResourceBundle('eu', 'weather-map', eu, true, true);
```

- [x] **Migrar weather-map i18n.ts** a `import { i18n } from '@nekazari/sdk'` + `addResourceBundle(..., 'weather-map', ...)`. Commit `5c0b5be` a main ✅
- [x] **Verificar namespace:** los componentes usan `useTranslation('weather-map')` con claves como `t('controls.metric')` — coincide con el namespace registrado. ✅

### 🟢 soil — i18n correcto, EU completo

| Aspecto | Detalle |
|---------|--------|
| Ficheros locale | ✅ No tiene ficheros separados — las traducciones están en el propio `i18n.ts` como objetos JS |
| Mecanismo de registro | ✅ `import { i18n as sdkI18n } from '@nekazari/sdk'` (patrón correcto) |
| Traducciones EU | ✅ Objeto `eu` completo con todas las claves traducidas |

Sin incidencias. El módulo soil debería traducirse correctamente al cambiar de idioma.

---

## 💧 NKZ Water Studio (hydrology)

- [ ] **DEMs muy llanos — vigilar drenaje conectado (2026-06-21).** `flow_accum_full_workflow` (geolibre-wasm 0.4.4) produce acumulación degenerada (max≈1, sin red de cauces) cuando el relieve del DEM es menor que el ruido / el breaching no deja paso de flujo continuo. **No es bug del engine** (verificado: con pendiente real acumula bien a cualquier tamaño) — es física del terreno. Acción: al integrar DEMs reales (Fase 1), detectar el caso (p. ej. `max(accum) ≈ n_celdas_de_su_propia_celda` o por debajo de un umbral) y degradar con `dataFidelity` + aviso, en vez de devolver una red vacía silenciosamente. Parcelas de regadío en plano y vegas son el caso típico. Ver memoria `geolibre-wasm-d8-trap-2026-06-21` y `nkz-module-hydrology/backend/app/services/geolibre_engine.py`.

## 🛡️ Endurecimiento pre-relanzamiento — valoración profesional (2026-06-18)

> Derivado de la revisión de la plataforma (sesión 2026-06-18). Veredicto: **arquitectura de nivel profesional, endurecimiento en fase temprana**. Lista para showcase/pitch; para producción con datos reales hace falta esta pasada antes de "volver a la carga". Contexto en memoria `fiware-seal-audit-2026-06-17` y `parcel-lifecycle-orchestration-2026-06-17`.

### P0 — Seguridad / correctness crítica (antes de prod)
- [x] **Verificación de aislamiento multitenant** — PR #645. 13 tests (normalize_tenant_id, Orion isolation, PG isolation, static analysis). 2026-06-20.
- [x] **Revisión de seguridad independiente** — ejecutada 2026-06-20. 6 hallazgos (0 Critical, 3 Medium, 3 Low). Handover en `internal-docs-local/2026-06-20-HANDOVER-security-review.md`.
  - ✅ H1: `.gitignore` hardening → 14 módulos actualizados (`chore: harden .gitignore for secrets`)
  - ✅ H2: CORS hardcodeado en connectivity → migrado a ConfigMap (connectivity module `c68e470`)
  - ✅ H4: bare `except:` en modules.py, entities.py → logueados (nkz PR #657)
  - ✅ H5: X-FIWARE-Compliant gate → eliminada dependencia de cabecera, validación por cadena OIDC (nkz PR #657)
  - ✅ H6: fallback `"free"` → `"basic"` en parcel_activation.py (nkz PR #657)
  - [ ] **H3 — NetworkPolicy restrictiva para /internal/** — PRIORITARIO (2026-06-20). Cluster usa `allow-same-namespace` (permite todo intra-ns). Migrar a per-service policies y añadir NP que restrinja acceso a `/internal/` endpoints solo a entity-manager + api-gateway. Ver `internal-docs-local/2026-06-20-security-review-checklist.md` H3.
  - [ ] **crop-health: 2 lecturas directas a TimescaleDB pendientes (2026-06-21).** Tras el fix #2 (rama `fix/crop-health-audit-queries-db`) quedan 2 `asyncpg.connect(weather_db)` en `app/api/assessments.py` (endpoints `/assessments/history` y `/assessments/correlation`): leen `telemetry_events` directo (viola la política zero-direct-timeseries-read). NO tienen ruta SDK limpia: `TimescaleClient` y `timeseries-reader` son **por-entidad** (`/entities/<id>/data`), y estos endpoints quieren TODOS los `CropHealthAssessment` de una parcela por `entity_type+parcelId` → requiere capacidad nueva en timeseries-reader (query por type+parcel) o usar la API temporal de Orion. Refactor mayor, no incluido en el fix #2 limpio (ese sí quitó el fallback directo-DB de `context_client.py` y cableó dual has/ref en 9 reads).
  - [ ] **H7 — Módulos FastAPI NO verifican HMAC (gap platform-wide, mitigado por NetworkPolicy) (2026-06-21).** Hallazgo al verificar el "plan de auditoría crop-health" de otro agente. El `require_auth` del SDK (`nkz_platform_sdk/auth.py`) **solo lee `X-Tenant-ID` + valida formato — NO verifica `X-Auth-Signature`**. El gateway SÍ envía la firma (`fiware_api_gateway.py:836`), pero ningún módulo FastAPI la mira → quien alcance un pod de módulo saltándose el gateway puede suplantar tenant con solo poner `X-Tenant-ID`. **Severidad: HIGH defense-in-depth, NO "puerta abierta"** (lo frena la NetworkPolicy; ligado a H3). crop-health además tiene deviation propia: middleware JWKS custom (`app/middleware/auth.py:73-79`) en vez del patrón estándar. **Fix correcto = (b) añadir verificación HMAC opcional al `require_auth` del SDK** (message `token|tenant|ts`, `hmac.compare_digest`, ventana 300s — copiar de `common/auth_middleware.verify_hmac_signature`), gated por `REQUIRE_HMAC_SIGNATURE` + `HMAC_SECRET` cableado en todos los módulos; luego cada módulo (crop-health el primero) adopta `require_auth` y retira su auth custom. Alternativa (a) crop-health-only = parche inconsistente, descartada. **El plan del otro agente proponía migrar a `require_auth` para "ganar HMAC" — INCORRECTO, `require_auth` no verifica HMAC.** Decidir scope antes de implementar.
- [x] **Cerrar el footgun sistémico del falso-cero** — PR #645. Helper `safe_count_entities()`/`safe_query_entities()` en `common/orion_safe_query.py`. Nunca devuelve 0/[] en error, siempre -1/None centinela. 19 tests. 2026-06-20.

### P1 — Robustez operativa
- [x] **Revivir los bootstraps de subscriptions** — PR #645. Entity-manager ahora llama `ensure_subscriptions_for_all_tenants()` en background thread al arranque. 2026-06-20.
- [x] **Activar motor reconcile/cascade** — PR #646. `RECONCILE_BACKSTOP_ENABLED=true`. 2026-06-20.
- [ ] **Item 5 (teardown en otros módulos)** — baja prioridad (el backstop ya cubre la garantía). No empezado.

### P2 — Calidad / CI / tests
- [x] **Subir cobertura de CI a TODOS los servicios** — PR #645. Nuevo job `backend-unit-tests` en CI que ejecuta ~270 tests y bloquea el build si fallan. 2026-06-20.
- [x] **Sellar de verdad los módulos**: 29/29 compliant (0 pending). Fix api-gateway X-FIWARE-Compliant header proxy (PR #638). Batch publish 6 módulos via push trigger + 4 sellados manualmente (intelligence, vpn, odoo, simulation). 2026-06-19.
- [x] **Higiene del hook `check-fiware-compliance.sh`**: fix subshell bug (pipe→while) + patrón execute acotado + añadido INSERT check para execute(). 2026-06-20 (nkz PR #648).

### P2 — Armonización NGSI-LD (cosmética)
- [x] Ver sección dedicada abajo + handover `internal-docs-local/2026-06-17-ngsi-ld-inventory-fiware-seal.md` (no bloquea el sello). Completado PR #626 (2026-06-18).

### P3 — Decisiones / follow-ups
- [ ] **Bizkaia SSL-skip** en catastro (`verify=verify_ssl`, endpoint gov externo): proveer bundle de CA vs aceptarlo. No bloquea el sello (no es Orion).
- [ ] **Activation gate** (rechazar servir módulos no-`compliant`): construir + activar SOLO tras sellar todos (si no, rompe los `pending`).
- [ ] **Test-en-prod containment**: guard anti-prod en la suite de compliance local (`tests/fiware-compliance/`, untracked) — doc `internal-docs-local/2026-06-18-test-in-prod-urn-test-ctx-finding.md`.
- [ ] **Sostenibilidad / bus factor** (no técnico): 30 módulos + móvil + infra para poco equipo. Reducir superficie o sumar manos antes de escalar usuarios.

---

## 🌡️ Fenología GDD por parcela (crop-health) — diseño CERRADO, ejecución pendiente (2026-06-20)

> Origen: cimiento crop-health (fenología) desplegado 2026-06-19. Al consumirlo se descubrió que **el GDD está MUERTO en prod**. Diseño de la solución ya decidido (brainstorm 2026-06-20). Detalle completo: handover `internal-docs-local/2026-06-20-handover-weather-gdd-per-parcel.md`; specs `docs/superpowers/specs/2026-06-20-phenology-gdd-seeding-design.md` y `2026-06-19-crop-health-foundation-e2e-design.md`. Memoria `crop-health-foundation-spec-plan-2026-06-19`.

### 🔴 Sub-proyecto 1 — GDD por parcela (PREREQUISITO; desbloquea toda la fenología) — 🟢 IMPLEMENTADO (2026-06-20)
> **Causa raíz:** `crop-health._fetch_gdd` (pipeline.py:1299) llama a `timeseries-reader-service:5000/api/weather/gdd` → **404** (endpoint documentado pero NUNCA implementado). → `gdd=None` siempre → `derive_stage_from_gdd` nunca corre → fenología no se computa en prod.
> **DECISIÓN de arquitectura (cerrada):** el dato diario por parcela YA existe en `weather_observations` (weather-worker escribe Tmin/Tmax/Tavg diarios por parcela desde Open-Meteo en sus coords; lo sirve timeseries-reader). El GDD primario NO es weather-map (sus COGs son snapshots cada 5 días, para heatmap visual). El píxel/zonal es fase 2.

**Endpoint implementado en timeseries-reader** (PR #643 pendiente de merge):
- `GET /api/weather/gdd` con método `simple_avg_capped` (base_temp + upper_cutoff por cultivo)
- KNN espacial (PostGIS `<->`) sobre `weather_observations`
- Resolución por lat/lon o parcel_id (Orion lookup fallback)
- Tests: 10 en `services/tests/test_weather_gdd.py`
- Response builder aislado en `services/timeseries-reader/gdd_response.py` (rompe taint CodeQL)

**Lado crop-health (4 commits en main):**
- `StageTable` en schemas con `base_temp`, `upper_cutoff`, `gdd_method`
- `context_client.get_phenology_stages` devuelve `StageTable` + extrae `baseTemp` de BioOrch
- `_fetch_gdd` pasa `parcel_id` + `base_temp` + `upper_cutoff` (desde StageTable del cultivo)
- Caché TTLCache añadida a `get_phenology_stages`
- `project_stage_timeline`/`evaluate_phenology_progress` compatibles con `dict | StageTable`
- 171 tests pasando

**Arquitectura a futuro:** Cuando weather-map implemente GDD zonal por píxel (Fase 2), crop-health podrá apuntar a weather-map o usar fallback. El contrato HTTP es idéntico.

### 🟡 Sub-proyecto 1b — generador COG de weather-map (DESACOPLADO del GDD primario) — ✅ HECHO (2026-06-20)
- [x] **A**: `run_cog_generator.py` ahora lee `COG_TENANTS` env var (commit `886587e`)
- [x] **B**: `fetch_tenant_parcels` (sources.py) incluye `Link` @context header (commit `886587e`)

### 🟢 Sub-proyecto 2 — seeding fenología GDD (provenance-first) — BLOQUEADO en el método de SP1
> Spec escrito. Datos válidos pero su data flow depende de la fuente/método GDD del SP1 (los valores deben ser consistentes con `simple_avg_capped`).
- [ ] Poblar `stage_detection` en `bioorch/backend/data/phenology_sources.yaml` (hoy `{}`) con `base_temp_c`+`upper_cutoff_temp_c`+`gdd_method`+rangos GDD por fase, **provenance-first (regla §2.3: nada inventado, todo con fuente citable; validación del agrónomo)**. Cultivos: trigo, cebada, lenteja, garbanzo, almorta, trigo sarraceno, olivo (arbequina/arroñiz), almendro, guisante, brócoli (+maíz). Consolidar los 4 vivos (trigo/olivo/almendro/vid) en el YAML para no regresarlos.
- [ ] Tweak `seed_phenology.py`: persistir `source` de la fase GDD (hoy solo en params).

### 🔵 Fase 2 — GDD zonal sub-parcela (para parcelas grandes/heterogéneas) — ✅ IMPLEMENTADO (2026-06-20)
> `nkz-module-weather-map` commit `886587e`. Pipeline completo: DEM → clustering por elevación+aspecto → downscaling diario por zona (lapse rate) → `AgriParcelZone` entities en Orion-LD → TimescaleDB vía suscripción. Endpoint `GET /api/weather-map/zones/{parcel_id}`. CronJob `zone-accumulator` diario. Detalle: `internal-docs-local/plans/2026-06-20-zonal-parcel-storage-phase2-plan.md`.

---

## Auditoría NGSI-LD plataforma-wide — preparación sello FIWARE (2026-06-17)

**Objetivo:** la plataforma va a auditoría para el sello "fiware" → armonizar el acceso a Orion en TODOS los servicios hacia un único layer NGSI-LD conforme, antes del audit. El sello audita el protocolo en el cable + Smart Data Models, no el SDK interno (ver memoria `fiware-seal-audit-2026-06-17`).

> **INVENTARIO HECHO (2026-06-17): 0 violaciones de protocolo, 0 verify=False, 0 sin tenant → NO bloquea el sello. La armonización es COSMÉTICA (deferible).** Findings + HANDOVER (~14 ficheros, orden sugerido, checklist) en `internal-docs-local/2026-06-17-ngsi-ld-inventory-fiware-seal.md`. Tareas abajo = ese refactor cosmético.

- [x] **Inventariar** todos los puntos de acceso a Orion por servicio — COMPLETED 2026-06-17 (handover en `internal-docs-local/handovers/`)
- [x] **Armonizar** 14+ callers a `inject_fiware_headers()` — COMPLETED 2026-06-17 (PR #626). SDK 0.5.0 publicado en PyPI, compliance CI en 6 módulos.
- [x] **Verificar** — COMPLETED 2026-06-17 (compliance CI en monorepo + 6 módulos, 0 manual headers en monorepo)
- [ ] Tarea separada del motor de ciclo de vida de parcelas (ese ya es conforme — usa `inject_fiware_headers`). No bloqueante para el motor.

**Referencia:** memoria `parcel-lifecycle-orchestration-2026-06-17`, spec §13.

## ArgoCD stuck apps — 5 OOS sin drift (2026-06-11) — RESUELTO PARCIALMENTE (2026-06-20)

**Diagnóstico:** 3 causas raíz identificadas y corregidas:
- **6 certificados cert-manager OOS**: causados por `issuerRef` sin `group: cert-manager.io` y manifests redundantes (Ingress-shim auto-crea los certs). Fix: eliminados manifests de git (`gitops-config`), añadido `ignoreDifferences` global para cert-manager Certificates en argocd-cm.
- **VPN deployment OOS**: imagen `:latest` en clúster vs `@sha256:` en git. Fix: force-sync.
- **core-auth Job OOS**: esperado para Jobs completados. ignoreDifferences añadido.

**Estado actual:**
- ✅ **core-networking**: Synced
- ✅ **core-services**: Synced
- ✅ **vpn**: Synced
- ⏳ **core-auth**: OutOfSync (Job completado, aceptable)
- ⏳ **headscale**: OutOfSync (2 recursos excluidos del sync a propósito, aceptable)

**Referencia:** sesión 2026-06-20, commits en `gitops-config` (6f4f14d, 1e79550), PR #645 en nkz.

## GitOps convergence — manifests de módulos en `gitops-config` (2026-06-21)

> **Mandato para agentes:** la fuente de verdad de **todos** los manifiestos de despliegue de módulos (Deployment, CronJob, Service, NetworkPolicy, Ingress, ConfigMap) debe vivir en **`gitops-config/overlays/modules/<módulo>/`**, sincronizada por **una app ArgoCD por módulo**. El CI del repo del módulo solo construye y publica la imagen (SHA); gitops-config fija el pin `@sha256:`.
>
> **Confusión habitual (crop-health 2026-06-15):** tener ArgoCD apuntando al `k8s/` del repo del módulo **no es** el patrón objetivo. Eso elimina `kubectl apply` manual pero mantiene **dos planos de verdad** (manifest en repo público del módulo + ConfigMap en gitops-config) y dificulta rollback centralizado. El modelo canónico es **bioorchestrator** (todo el overlay en gitops-config).

### Por qué converger (no negociable en prod)

- Sin shell de producción para desplegar: rollback = `git revert` del pin SHA → ArgoCD reconcilia.
- Una app ArgoCD por recurso/módulo → evita sync-loops (regla CLAUDE.md).
- `selfHeal` coherente: parches manuales en el cluster se revierten solos.
- Pin `@sha256:` centralizado; nunca `:latest` en producción.
- Secretos → SealedSecrets en gitops-config (nunca plaintext en repos de módulo).

### División canónica (dos planos, una fuente de manifests)

| Plano | Repo | Responsabilidad |
|-------|------|-----------------|
| **Build** | `nkz-module-*` / `nekazari-module-*` | Código, tests, CI → imagen GHCR taggeada por commit SHA o semver |
| **Deploy** | `gitops-config` | Manifiestos K8s con pin `@sha256:`, env prod, Ingress, NP, ConfigMaps |

El `k8s/` del repo del módulo, tras migración, queda como **plantilla/documentación** (placeholders `YOUR_DOMAIN`) o se elimina para no tener dos fuentes de verdad.

### Patrón canónico — bioorchestrator (copiar este modelo)

```
gitops-config/overlays/modules/bioorchestrator/
  deployment.yaml          # pin @sha256:
  service.yaml
  network-policy.yaml
  neo4j-statefulset.yaml
  bioorchestrator-sealed-secret.yaml
  bioorchestrator-ingress.yaml

nkz/gitops/modules/bioorchestrator.yaml
  source.repoURL: gitops-config
  source.path: overlays/modules/bioorchestrator
```

Deploy flow: merge imagen en CI → PR en gitops-config bump SHA → ArgoCD auto-sync. **Cero SSH.**

### Inventario actual (audit 2026-06-21)

Leyenda: **✅ canónico** | **🟡 ArgoCD desde repo módulo** (deuda) | **🟠 split config/deployment** | **🔴 manual / sin ArgoCD** | **⚪ core** (nkz `core-services`, fuera de scope módulo)

| Módulo | Manifiestos deployment | Config overlay | App ArgoCD | Estado | Migrar |
|--------|------------------------|----------------|------------|--------|--------|
| **bioorchestrator** | `gitops-config/overlays/modules/bioorchestrator/` | (mismo overlay) | `bioorchestrator` | ✅ Canónico | — |
| **crop-health** | `nekazari-module-crop-health/k8s/` | `gitops-config/.../crop-health/` | `crop-health` + `crop-health-config` | 🟠 Split (2 apps) | **P0** |
| **weather-map** | `nkz-module-weather-map/k8s/` | — | ❌ ninguna | 🔴 `kubectl apply` manual | **P0** |
| **field-operations** | `nkz-module-field-operations/k8s/` | `gitops-config/.../field-operations/` | ❌ ninguna | 🔴 Manual (config sí en gitops) | **P0** |
| **connectivity** | `nkz-module-connectivity/k8s/` | `gitops-config/.../connectivity/` | ❌ ninguna | 🔴 Manual | **P1** |
| **greenhouse-dt** | `nkz-module-greenhouse-dt/k8s/` | `gitops-config/.../greenhouse-dt/` | `greenhouse-dt` + `greenhouse-dt-config` | 🟠 Split | **P1** |
| **vegetation-prime** | `vegetation-health-nkz/k8s/` | `gitops-config/.../vegetation-prime/` | `vegetation-prime` + config app | 🟠 Split | **P1** |
| **soil-module** | `nkz-module-soil/k8s/` | `gitops-config/.../soil/` | `soil-module` | 🟡 Repo módulo | **P1** |
| **gis-routing** | `nkz-module-gis-routing/k8s/` | `gitops-config/.../gis-routing/` | `gis-routing` + config app | 🟠 Split | **P2** |
| **cadastral (catastro-sp)** | `catastro-sp-module-nekazari/k8s/` | `gitops-config/.../catastro-sp/` | `cadastral` + config app | 🟠 Split | **P2** |
| **cue** | `nkz-module-cue/k8s/` | `gitops-config/.../cue/` | `cue` + config app | 🟠 Split | **P2** |
| **lidar** | `nkz-module-lidar/k8s/` | `gitops-config/.../lidar/` | `lidar` + config app | 🟠 Split | **P2** |
| **eu-elevation** | `nkz-module-eu-elevation/k8s/` | `gitops-config/.../eu-elevation/` | ❌ config only | 🔴 Deployment fuera gitops | **P2** |
| **carbon** | `nkz-module-carbon/k8s/` | — | `carbon` | 🟡 Repo módulo | **P2** |
| **datahub** | `nkz-module-data-hub/k8s/` | `gitops-config/.../datahub/` | `datahub` + `datahub-config` | 🟠 Split | **P2** |
| **odoo** | `nkz-module-odoo/k8s/` | `gitops-config/.../odoo/` | `odoo` + `odoo-config` | 🟠 Split | **P2** |
| **zulip** | `nkz-module-zulip/k8s/` | `gitops-config/.../zulip/` | `zulip` + `zulip-config` | 🟠 Split | **P2** |
| **robotics** | `nkz-module-robotics/k8s/` | — | `robotics` | 🟡 Repo módulo | **P3** |
| **n8n** | `n8n-module-nkz/k8s/` | — | `n8n` | 🟡 Repo módulo | **P3** |
| **vpn** | `nkz-module-vpn/k8s/` | `gitops-config/.../` (vpn-config) | `vpn` + config app | 🟠 Split | **P3** |
| **billing** | `nkz-module-billing/k8s/` | — | `billing` | 🟡 Repo módulo | **P3** |
| **backup** | `nkz-module-backup/k8s/` | `gitops-config/.../backup/` | ❌ config only | 🔴 | **P3** |
| **agrienergy** | `nkz/k8s/core/services/agrienergy-deployment.yaml` | `gitops-config/.../agrienergy/` | `core-services` | ⚪ Core (decidir si mover) | **P3** |
| **intelligence** | `nkz/k8s/core/services/intelligence-service-deployment.yaml` | — | `core-services` | ⚪ Core | **P3** |
| **biorefinery, hydrology** | `k8s/` en repo módulo | — | ❌ | 🔴 Sin app ArgoCD | **P3** (si en prod) |

> Re-auditar esta tabla al migrar cada módulo. Si un módulo no está en prod, marcar `N/A` y no crear app.

### Checklist de migración (por módulo — seguir en orden)

- [ ] **GITOPS-1 — Copiar manifests** de `<module>/k8s/` → `gitops-config/overlays/modules/<module>/`. Sustituir dominios/IPs reales (ya deben estar en gitops-config; el repo del módulo usa `YOUR_DOMAIN`).
- [ ] **GITOPS-2 — Pin imagen** `@sha256:` (nunca `:latest`). Comentario con commit SHA de la imagen.
- [ ] **GITOPS-3 — Unificar apps ArgoCD:** una sola Application apuntando a `gitops-config/overlays/modules/<module>/`. Eliminar app duplicada `*-config` si el ConfigMap pasa al mismo overlay (patrón bioorchestrator).
- [ ] **GITOPS-4 — Actualizar** `nkz/gitops/modules/<module>.yaml`: `repoURL: gitops-config`, `path: overlays/modules/<module>`.
- [ ] **GITOPS-5 — Secretos:** migrar a SealedSecrets en gitops-config; eliminar `secret.yaml` plaintext del repo del módulo.
- [ ] **GITOPS-6 — Deprecar** `k8s/` del repo del módulo: README "template only, deploy via gitops-config" o borrar manifests duplicados.
- [ ] **GITOPS-7 — Verificar:** `argocd app get <module>` → Synced + Healthy; rollout OK; rollback test (revert SHA pin).
- [ ] **GITOPS-8 — Actualizar AGENTS.md** tabla deploy (eliminar filas "manual kubectl apply" del módulo migrado).

### Orden sugerido (impacto operativo)

1. **P0 — bloquean despliegue sin SSH:** `weather-map`, `crop-health`, `field-operations`
2. **P1 — agronomic lifecycle + split apps:** `greenhouse-dt`, `vegetation-prime`, `soil-module`, `connectivity`
3. **P2 — resto con ArgoCD desde repo módulo o split:** gis-routing, cue, lidar, eu-elevation, datahub, odoo, zulip, cadastral, carbon
4. **P3 — baja prioridad / core / no prod:** robotics, n8n, vpn, billing, backup, biorefinery, hydrology; evaluar si agrienergy/intelligence salen de `core-services` a overlay propio

### Anti-patrones (no hacer)

- ❌ `kubectl apply -f k8s/` en producción (excepto hotfix temporal hasta migrar).
- ❌ Dos apps ArgoCD (`<module>` + `<module>-config`) apuntando a repos distintos para el mismo módulo — unificar.
- ❌ ArgoCD app con `targetRevision: HEAD` al repo del módulo como destino permanente.
- ❌ `:latest` o short SHA (7 chars) en manifests de prod.
- ❌ Valores prod (dominios, IPs) en repos públicos de módulo — solo en gitops-config.

**Referencia canónica:** `gitops-config/overlays/modules/bioorchestrator/` + `nkz/gitops/modules/bioorchestrator.yaml`.

---

## Módulos soil ↔ crop-health ↔ bioorchestrator — conectividad reparada (2026-06-11) — DONE

- [x] **Root cause:** 3 módulos tenían `SOIL_MODULE_URL`/`SOIL_API_URL` apuntando a nombres de servicio DNS incorrectos (`nkz-soil-service:5000`, `soil-api-service:5000`). El servicio real es `soil-module-service:8000`. El core (`nkz`) ya tenía el fix, los módulos en repos independientes no.
- [x] **crop-health:** `config.py` + `k8s/backend-deployment.yaml` — `517dbfa`. Rollout verificado en prod, conectividad soil OK.
- [x] **bioorchestrator:** `soil_client.py` (URL + endpoint + parseo NGSI-LD) — `fa701dc`. `ingestion/orion.py` (Link header + tenant) — `c25fe52`. gitops-config deployment + SHA bump — `59c395e`. Rollout verificado, conectividad OK.
- [x] **soil backend:** imagen reconstruida con layers USDA + fix Ksat Saxton-Rawls (`1336ba5`, ksat=0 en no-arenosos) — `63108aa`. 8 capas GIS servidas. ArgoCD `Synced/Healthy`.
- [x] **soil frontend:** `SoilProfileCard` (perfil vertical con barras USDA), capa `soil-usda-texture` en manifiesto, i18n 6 idiomas — OIDC publish automático.
- [x] **ArgoCD consolidation:** 4 apps redundantes eliminadas de `gitops-config/gitops/config/` (`bioorchestrator-config`, `datahub-config`, `odoo-config`, `zulip-config` — ya existían en `nkz/gitops/modules/`). `nekazari-config-root` → Synced. `bioorchestrator-config` → deleted.
- [x] **README soil:** Data License Boundary (JRC raw vs derived), Cross-Module Integration table, slots catalog.

## cue-iuws-poller / risk-evaluation-batch — future egress needed (2026-06-13)

Ambos módulos funcionan sin internet hoy (pull de imágenes va por host network, no afecta NetworkPolicy).
- `cue-iuws-poller`: poller IUWS → necesitará egress HTTPS para llamar a IUWS API
- `risk-evaluation-batch`: evaluador de riesgos → necesitará egress HTTPS para fuentes externas

Cuando toque activar esa funcionalidad, habrá que añadir NetworkPolicy de egress (como `cue-iuws-external-apis` y `risk-worker-external-apis`), tanto en el repo del módulo como en gitops-config si son policies baseline.

---

## Plan post-v1.2.0 Semana 1 — Confianza operativa (2026-06-11) — IN PROGRESS

> Plan: `internal-docs-local/2026-06-10-plan-accion-post-v120.md`

### #1 Alertas con destinatario (Telegram) — DONE+VERIFIED e2e (2026-06-11)
- [x] Reglas Prometheus: certs <15d + `PodNotReady` 5m (PR #22) + labels `namespace` en agregadas (PR #24)
- [x] `AlertmanagerConfig` Telegram con chatID real — gitops-config PR #23 merged
- [x] Secret `alertmanager-telegram` creado en server (usuario)
- [x] Selector ya estaba: `alertmanagerConfigSelector: {}` = selecciona todo (sin helm upgrade)
- [x] NetworkPolicy `alertmanager-notifications-egress` — notify fallaba con connection refused (PR #25)
- [x] **E2E verificado**: "Notify success" en logs + mensajes llegando al móvil del owner (confirmado)
- [ ] **USUARIO**: rotar token del bot (@BotFather /revoke) — quedó expuesto en salida de sesión — y `kubectl -n nekazari delete secret alertmanager-telegram && create` con el nuevo
- [ ] Verificar regla certs: ¿Prometheus scrapea `certmanager_*`? (si no, ServiceMonitor)

### #2 Check sintético cada 5 min — DONE+VERIFIED (2026-06-11)
- [x] CronJob + alertas + app ArgoCD `core-monitoring` (PR #22) — verificado en cluster: 4/4 checks OK cada 5 min
- [x] Fix hairpin: `hostNetwork: true` (el hairpin pod→IP pública vía svclb rechazaba conexiones erráticamente) — PR #24
- [x] Bucle completo demostrado: jobs fallidos → `SyntheticCheckFailing` → Telegram
- [ ] Opcional: PAT read-only → Secret `synthetic-check-pat` (key `token`) activa el check de API autenticada
- [ ] Hallazgo colateral: `resolve-url` anuncia `manifest.json` que NO existe en MinIO (los reales: `remoteEntry.js`, `mf-manifest.json`) — revisar consumidores de `module_csp.py`

### #5 Cola Dependabot — BLOCKED (permisos de merge)
- [ ] **USUARIO**: mergear nkz #528 (dompurify, verde) y #527 (esbuild 0.25 security, verde) — el clasificador denegó el merge al agente; checks requeridos en "expected", puede necesitar `@dependabot rebase`
- [ ] #526 NO auto-mergear: lockfile pnpm roto (`--frozen-lockfile` falla en apps/host) y lleva majors encubiertos (vite 5→6 en host, i18next-http-backend 2→3) → moverlo a la revisión conjunta de majors (#520/#485/#521-523)

### #3 Simulacro restore — DONE+VERIFIED (2026-06-11 tarde)
- [x] **Incidente previo descubierto y arreglado**: backups IONOS **18 días fallando en silencio** (desde el baseline NP del 2026-05-24; egress rechazado + `pip install` con stderr a /dev/null → crash-loop sin logs). Fix: nkz PR #549 (CronJobs versionados, label `component=backup-sftp`, creds desde Secrets, fallos visibles, backoffLimit 3) + gitops-config PR #28 (NP `backup-sftp-egress` DNS+443+22 + alertas `BackupJobFailed`/`PostgresBackupStalled`/`MongoBackupStalled`). Subida e2e verificada (SUCCESS + ficheros en remoto). sftp-cleanup también verificado.
- [x] **Drill**: ns temporal `restore-drill`, restore PG 9 DBs (TimescaleDB: restoring=on + post_restore, **2m04s**) + Mongo 14 DBs (**3s**), paridad EXACTA con prod (tenants/módulos/obs/telemetry/entidades Orion). Acta: `internal-docs-local/2026-06-11-acta-restore-drill.md`. Drill v2 pendiente: MinIO + Keycloak sobre DB restaurada.

### #4 Ticket GitHub GC e7ea9bf — USUARIO

## Plan post-v1.2.0 — #6 DECIDIDO (2026-06-11, owner)

**10 módulos core** (sustituye la propuesta de 6): weather, vegetation, soil, crop-health, **bioorchestrator**, gis-routing (*owner con dudas — confirmar antes de invertir fuerte*), visor/entities, **connectivity** (sensores externos — CRÍTICO), **agrivolt** (parque agrivoltaico sobre olivo contratado), **datahub**.
- Requisito: ≥1 tenant con acceso a Odoo (som comunitats, comunidades energéticas) + agrivolt, TODO bajo el mismo tenant.
- Listón: core "pulidísimos, nivel top". Resto → etiqueta community + feature-freeze (ejecución PENDIENTE).
- Killer features: catálogo en `KILLER FEATURES.txt` (riego = recomendada) — PARA DESPUÉS.
- [ ] Ejecutar etiquetado community + feature-freeze en marketplace — mecanismo propuesto en la auditoría: `category='community'` (SQL 1 línea) + PR host para el badge (Modules.tsx NO renderiza category hoy → superficie de producto, merge del owner)
- [x] **Auditoría de los 10 core HECHA (2026-06-11 tarde)**: `internal-docs-local/2026-06-11-auditoria-10-core.md`. Orden de pulido APROBADO por owner: connectivity → weather → agrienergy → datahub → gis (tras decisión owner). Correcciones post-auditoría: el puntero FE raíz ES el diseño canónico (publicPath; los versionados de bioorch/datahub son el esquema viejo — verificar que aún resuelven) y weather-worker YA estaba migrado a Orion.

## Auditoría trío + weather (2026-06-12 noche) — HECHA, pulido pendiente de orden

> Informe completo: `internal-docs-local/2026-06-12-auditoria-bioorch-crop-soil-weather.md` (5 auditorías paralelas, suites ejecutadas)

- [x] **🔴 P1 — soil: fuga licencia JRC en `/point/texture`** — resuelto PR #15 (commit e0634ae, 2026-06-13). Raw fractions suprimidas cuando `redistributable=False`. Productos derivados (USDA class, Saxton-Rawls) servidos como obras derivadas.
- [ ] **🔴 P2 — sweep tenant `-`→`_` SISTÉMICO** (4ª-5ª aparición de la clase): weather-worker (×4 normalizadores; estaciones virtuales de allotarra van a tenant fantasma — explica DBs huérfanas underscore) + telemetry-worker subscription_manager + bioorch (auth middleware + clientes sin tenant = cross-tenant) + crop-health (fiware_publisher). [M]
- [ ] **🔴 P3 — weather-map hardening** (módulo de 1 día): SIN auth + tenant por query param (escritura cross-tenant), Orion crudo, `weatherStats` inventado, sin LICENSE, CI `|| true`, data manifest vacío, CSS BEM fantasma, soil-lookup roto (siempre loam). 133 tests de física verdes como base. [M]
- [ ] **P4 — bioorch polish** (L): CI gate (quitar `|| true`), SDK Orion (~15 sites + PATCH ld+json→json+Link que rompe clear_crop_assignment), suscripción AgriCrop por tenant (hoy muerta), Timescale DSN fail-fast, HTTPException import, AGPL headers, SlotShell, i18next ^23, dao.py 2.8k LOC sin tests. **Secreto en disco**: `nkz-bioorchestrator-knowledge/HANDOVER.md` con DeepSeek key + creds Magento → mover/rotar.
- [x] **P5 — crop-health polish — DESPLEGADO+VERIFICADO (2026-06-15)**. SDK 0.4.4 en PyPI (nkz#592); crop#14 mergeado; imagen `df63b3a8` (bcfd206) desplegada, rollout OK, pod sano. **ArgoCD parcial** (nkz#593: app `crop-health` Synced/Healthy desde repo módulo — fin del `kubectl apply` manual, pero **deuda GitOps**: Deployment sigue en `nekazari-module-crop-health/k8s/`, ConfigMap en gitops-config; ver sección *GitOps convergence*). KEYCLOAK_URL interno verificado. **Redis auth ARREGLADO+DESPLEGADO** (crop#15, imagen `078e50c4`, vía ArgoCD — `Redis connected`+sliding window OK). Dedup sub verificado: no hay sub previa, nada que hacer hasta 1ª activación de parcela. Hecho: SDK Orion en las **5** superficies (publisher+context_client+sources+assessments+pipeline incl. aggregate @context fix), SubscriptionRegistrar DeviceMeasurement en setup-parcel, `except: pass`→logging, config os.getenv→Settings, seed_agricrop `--tenant` requerido. Detalle: `internal-docs-local/2026-06-14-crop-health-5-sdk-conformance-{spec,plan}.md`.
- [ ] **P6 — entradas weather → AMPLIADO a arquitectura** (L; INVESTIGAR primero). Brief: `internal-docs-local/2026-06-15-handover-6-weather-inputs-architecture.md`. Surgió: (a) **posible bug vivo** — `weather_observations` es legacy sin escritores (path vigente = `WeatherObserved` en Orion→Timescale vía weather-worker `orion_writer`/ParcelWeatherEngine) pero ≥5 servicios la LEEN (weather-api/agro_status, risk-worker, timeseries-reader, entity-manager) → confirmar y migrar lectores; (b) **decisión owner**: evaluar usar el weather **pixel-level/raster** de `nkz-module-weather-map` (zonal-stats §5) como entrada de crop-health/risk/bioorch. Items originales (mapping soilMoisture/gusts/GDD, tests `_parse_openmeteo_response`/orion_writer=0, FORECAST 14d descartado, ON CONFLICT dedup) se re-evalúan tras decidir arquitectura. **⚠️ RE-INVESTIGADO 2026-06-15 tarde — el diagnóstico+decisión anterior eran FALSOS** (queries Orion sin `@context` devolvían 0 por fallo de expansión de tipo, no por falta de datos). Doc CORREGIDO: `internal-docs-local/2026-06-15-weather-parcels-findings-CORRECTED.md` (el `-weather-inputs-findings.md` queda SUPERSEDED). Verdad con @context: montiko tiene **3 AgriParcel + 3 WeatherObserved** en Orion; los 3 AgriParcelRecord eran **fotos de campo**, no weather-map (weather-map nunca corrió: su `fetch_tenant_parcels` también consulta sin @context). **Se anula** la "decisión de adoptar weather-map como fuente". **PROBLEMA REAL #1 (transversal, prioritario): parcelas** — espejo PostGIS `cadastral_parcels` VACÍO en todos los tenants (Orion tiene los AgriParcel pero ninguna suscripción enruta AgriParcel→catastro `/orion-notification`, está a telemetry-worker → el sync Orion→PG es código muerto en prod) + 3 caminos de escritura con fuente-de-verdad contradictoria (catastro PG-only / catastro orion_sync Orion-truth / entity-manager PG→Orion / FE escribe directo a Orion) + 3 esquemas de id. **PROBLEMA REAL #2 (weather, re-encuadrado): split Orion WeatherObserved vs `weather_observations` legacy** — sin la falsa urgencia. **Siguiente**: auditar/arreglar parcelas (decidir Orion=SoT, revivir o retirar espejo, unificar id), luego re-encuadrar weather (cadena real de consumo, migrar lectores, pulido #6).

### Parcela = fuente única de verdad — BACKEND DESPLEGADO+VERIFICADO (2026-06-16)
Spec/plan: `internal-docs-local/2026-06-15-parcel-single-source-of-truth-{design,plan}.md`. Memoria: `parcel-single-source-of-truth-2026-06-16.md`.
- **Arquitectura (owner)**: Orion `AgriParcel`=SoT; entity-manager=único escritor (`POST/PATCH/DELETE /api/entities/parcels` + zonas + dedup por cadastralReference + UUID); `cadastral_parcels`=read-model por suscripción+reconcile; enforcement duro en gateway (NO activado aún).
- **DESPLEGADO**: PRs nkz #600 (backend), #601/#603/#604/#607 (pins entity-manager, vivo `sha256:13e71bc`), #602 (migr 082 ROUND), #605 (migr 083 nullable). Migraciones 082+083 aplicadas a prod (OK owner). Suscripciones de proyección creadas montiko/platform/allotarra. **Pipeline espejo VERIFICADO E2E** (crear uuid→fila correcta→borrar→limpio).
- **3 bugs preexistentes/integración arreglados en vivo**: trigger `ROUND(double,int)` (082), claves JSON-LD expandidas en notificaciones + cadastral_reference NOT NULL (parse fix), municipality/province NOT NULL (083), tenant duplicado en receiverInfo (#606).
- [ ] **PENDIENTE — cutover supervisado (NO autónomo)**: (a) T11 FE `parcelApi.ts` writes→API entity-manager (superficie producto, merge owner, verificar en navegador); (b) T12 catastro `cadastral_api.py`→entity-manager + retirar `orion_sync.py`; (c) borrar las 5 parcelas legacy `{ts}-{rand}`; (d) **activar enforcement gateway AL FINAL** (bump pin gateway línea 45) tras verificar FE. Follow-up menor: paginación reconcile (cap 1000).

## Pulido módulos core (orden aprobado 2026-06-11) — IN PROGRESS

- [x] **connectivity DONE+VERIFIED (2026-06-11)**: PR #3 merged — i18n 6 locales (ca/eu/fr/pt nuevos) publicado y vivo (deployed_version 9cdf3a94, manifest 200); tests 22→30 (reglas cross-tenant público/privado, include_public, wrong-type) en CI; backend pineado por digest (era v1.0.1+Always) con rollout verificado; frontend-deployment.yaml muerto eliminado.
- [x] **weather (a) pinning DONE+VERIFIED (2026-06-11)**: nkz PR #550 merged + apply + rollout — weather-api y weather-worker por digest (eran `:latest`+Always).
- [x] **weather (b) suite propia DONE+VERIFIED (2026-06-12)**: nkz PR #552 merged — 119 tests en `services/tests/` (CI-blocking vía Backend Unit Tests): agro_status 77 (matriz semáforos, PTF, triángulo USDA, fusión sensores, downscaling), auth 16, routers 26 (TestClient, Orion/DB/Open-Meteo mock; fail-safe DB caída→200 degradado; persist agroStatus NGSI-LD compliant). Suite completa 220 passed (baseline 101) + entity-manager 167. `fastapi` añadido a requirements-test.txt.
- [x] **BUG REAL Ksat Saxton-Rawls FIXED en 2 repos (2026-06-12, aprobado owner)**: la fórmula desviada del paper daba **ksat=0 en todo suelo no arenoso → grupo hidrológico D siempre → recovery_hours inflados (60h vs 18-36h)**. Fix per paper (Eqs. 5/15/16/18, valores = Tabla 3) en `weather-api/app/services/agro_status.py` (nkz #552) y `nkz-module-soil/pedotransfer/saxton_rawls.py` (soil PR #14, mergeado por owner/basabot + pin api `03108a` del owner; agente bumpeó pin worker `984e86` — el cálculo de ingesta vive en el worker). **Follow-up**: ksat/hydrologicGroup cacheados/emitidos pre-fix quedan obsoletos hasta re-ingesta o expiración de caché del provider.
- [x] **weather (c) deploy del fix Ksat DONE+VERIFIED (2026-06-12, apply autorizado por owner)**: PR #555 merged + `kubectl apply` + rollout OK; e2e en prod: `_saxton_rawls_2006(40,20,1.0)` → ksat 7.98 en pod weather-api Y en pod soil-worker (ArgoCD sincronizó el pin `984e86` solo).
- [x] **weather (d) ASSUMPTION unidades workability — RESUELTO empíricamente (2026-06-12 noche)**: (1) la única humedad de suelo en prod es de estaciones virtuales WeatherObserved (`soilMoistureTop/Sub`, unitCode M3, ~0.156) = **volumétrica 0–1, consistente** con FC/PWP — el miedo a % queda refutado para los datos existentes; (2) el camino texture-aware es **código muerto hoy**: la fusión busca `payload.soil_moisture|moisture` a nivel raíz pero los payloads reales son `{measurements:{soilMoistureTop…}}` camelCase → soil_moisture siempre None → siempre heurístico precip/humedad → **no hay bug activo posible**. Follow-up (decisión owner): cablear `measurements.soilMoistureTop` activaría el semáforo texture-aware con unidades correctas (cambio de comportamiento del semáforo).
- [x] **soil follow-up re-ingesta — RESUELTO: innecesaria (2026-06-12 noche)**: no existe ksat/hydrologicGroup obsoleto persistido en ningún sitio — la ProviderCache (Redis) guarda fetches CRUDOS pre-PTF (texturas, inputs); API (`reading.py`) y worker (`_apply_pedotransfer`) calculan Saxton-Rawls EN VIVO y ambos pods llevan el fix; montiko tiene 0 entidades AgriSoilExtended persistidas; sin columnas ksat en PG. **Verificado en prod**: `/point/texture` montiko → clay-loam, ksat 2.67 mm/h, grupo C (pre-fix habría sido ksat≈0 → D).
- [x] **agrienergy picos DONE+VERIFIED (2026-06-11 noche)**: pin por digest (nkz PR #551, sincronizado por ArgoCD core-services — OJO: la fuente del deployment es `nkz/k8s/core/services/`, el k8s/ del repo del módulo es manifest viejo) + locales ca/eu/fr/pt (PR #6 del módulo, publicado `2b555aa2`).
- [x] **agrienergy suite real + conformidad SDK DONE+VERIFIED (2026-06-12)**: módulo PR #7 merged (140 tests, era 6; CI gate ya existente) + SDK PR nkz #556 (`append_entity_attrs`, 0.4.2 en PyPI) + pin/env PR nkz #564, desplegado vía ArgoCD y verificado e2e en prod. **6 fixes reales**: (1) tenant `-`→`_` en el cliente Orion hand-rolled (leía/escribía el tenant Orion equivocado, p.ej. allotarra) — migrado a SDK OrionClient; (2) `/notify` exigía JWT que Orion no envía → lazo cerrado MUERTO en prod (verificado: existían 0 suscripciones agrienergy) — ahora tenant por header (patrón telemetry-worker), gateway externo sigue dando 401; (3) guard de idempotencia (±0.01°, azimut circular) rompe el bucle de auto-notificación; (4) split intención/estado: con `refDevice`, tilt/azimuth solo se escriben tras MQTT OK → fallo MQTT contado en `errors` y reintentado en la siguiente notificación; (5) SubscriptionRegistrar ensure-on-use vía /parks (primer consumidor del SDK) — 3 suscripciones activas creadas+verificadas en Orion para `asociacion-allotarra`, re-ensure idempotente (created 0/skipped 3); (6) **3 bugs de geometría en ShadowEngine**: espejo N/S del rayo solar (las sombras caían HACIA el sol), crash con proyección degenerada (panel vertical+cénit), y modelo self-shading inter-fila (cotangente por tangente + gating sobre sombra de suelo — nunca detectaba sombreado a espaciados reales).
- [ ] **agrienergy follow-ups** (sesión 2026-06-12): (a) defaults mágicos de radiación en /notify (ghi=800/dni=600/dhi=200 inventados si WeatherObserved no trae radiación) → alimentar de weather-api; (b) fast-ack con BackgroundTasks en /notify (patrón telemetry-worker); (c) migrar middleware JWT a `require_auth()` del SDK (deuda ya listada en MF2); (d) ASSUMPTION: self-shading inter-fila independiente del azimut del panel (comentado en shadow_engine.py) — confirmar precisión aceptable; (e) limitación json-logic documentada: dict de 1 clave en reglas custom = operador → el futuro editor visual (AGE-6) debe emitir siempre tilt+azimuth (o `azimuth: null`); (f) integración con flujo setup-parcel canónico para suscripciones; (g) suite frontend vitest.
- [x] **datahub CI DONE+VERIFIED (2026-06-11 noche)**: PR #16 — Validate ahora corre vitest (22) + pytest (6, faltaba pytest-asyncio: los async fallaban en silencio). Bug real arreglado al encender la suite: `_fetch_from_parcel_weather_api` omitía `_source`/`_downscaling` en ventanas vacías. Publicado `231e28af`.
- [x] **datahub suite del worker DONE+VERIFIED (2026-06-12)**: PR #17 merged + publicado (deployed_version `7ad60a3`, manifest 200). Extracción `src/workers/pipeline.ts` (verbatim verificado byte a byte) + 43 tests (65 total: matemática pura + harness de protocolo con self fake pineando invariantes V2.1: xs monótono, NaN solo en bridges, transfer list, cache-clone, retry-on-empty, epochs mixtos, taxonomía errores). **2 bugs reales confirmados+arreglados (aprobado owner)**: (A) cache key sin policy → resize de viewport servía datos procesados con el threshold viejo (288 pts en vez de ~2000); (B) el bridge NaN de un outage real sobrevivía al downsampling solo por suerte de alineación de buckets del LTTB → la línea cruzaba outages (violaba criterio aceptación 3). Spec actualizado a V2.2 en internal-docs (V2.1 Superseded; el doc estaba desfasado: URN passthrough/Strangler, retry-on-empty, epochs mixtos, ratio como media, concurrencia documentada).
- [ ] **datahub follow-ups** (sesión 2026-06-12, pre-existentes encontrados en review final): (a) `PanelSeriesRail` compara clave de 3 partes contra la cache key completa del worker → el lookup de mini-stats per-series está muerto desde V2.1; (b) `useWorkerSeries.release()` sin call sites (RELEASE_SERIES solo se ejercita en tests); (c) descartados con razón (no re-litigar sin evidencia nueva, ver spec V2.2): AbortController y TTL de cache; (d) suite del panel/hook React; (e) contrato Intelligence predict (doc también antiguo, revisar).
- [x] **Punteros FE bioorch/datahub — VERIFICADOS (2026-06-12 noche)**: datahub re-publicado por el flujo canónico hoy (deployed_version `7ad60a3`); bioorchestrator tiene puntero raíz canónico (`/modules/bioorchestrator/mf-manifest.json` 200), remoteEntry 200 y los chunks del publicPath versionado (`98b996f8…/assets/…`) también 200. Nada que re-publicar.
- [x] **Tenant `asociacion-allotarra` REPARADO** (usuario lo creó desde admin UI 2026-06-11 13:30; verificado: fila tenants enterprise/active nivel 3, api_key, orion-asociacion-allotarra 28 entidades, limits en fila tenants). Usuario ya había instalado weather/vegetation-prime/lidar el 06-07. Agente añadió `agrienergy` + `odoo-erp` (INSERT canónico, installed_by=idi@allotarra.eu).
- [x] **Odoo allotarra APROVISIONADO (2026-06-11 17:15, autorizado por owner)**: webhook → 200 `provisioned`, DB `nkz_odoo_asociacion-allotarra` clonada (guiones OK), `energy_communities` (som comunitats) **installed**, paridad exacta con montiko. Usuario idi@allotarra.eu se creará en su primer login OAuth (igual que g.abrego en montiko). Falta solo verificación visual del owner entrando como idi@allotarra.eu.
- [ ] Pulido módulo odoo (no-core): `OdooClient` falla con `Authentication failed` contra DBs recién clonadas (install extras + create_user) — probable ODOO_ADMIN_PASSWORD vs password del template. Hoy inocuo (template trae lo necesario; user via OAuth), pero el código miente menos si se arregla.
- [ ] Limpieza: 5 filas huérfanas en `tenant_installed_modules` del tenant legacy `asociacionallotarra` (sin guión, 2026-05-29) + DBs Mongo `orion-asociacion_allotarra`/`orion-asociacionallotarra` + DB PG `n8n_asociacionallotarra` — decidir si purgar
- [x] `farmer1@example.com` (seed demo del bootstrap) DESHABILITADO 2026-06-11 vía kcadm (verificado enabled=f). OJO: `realm-import-job` podría re-crearlo/re-habilitarlo si se re-ejecuta — vigilar tras próximos despliegues de auth
- [ ] `nekazari@example.com` (tenant `platform`, password=admin-password del Secret, creado por `realm-import-job`) — decidir si sigue habilitado o se renombra a correo real del owner
- [x] Reglas de certificados OPERATIVAS (2026-06-11): ServiceMonitor (gitops #26) + NP `prometheus-scrape-egress` a cert-manager:9402 (gitops #27 — el egress baseline bloqueaba el scrape cross-namespace). Verificado: up=1, 15 series `certmanager_*`
- [x] ~~OTRO AGENTE~~ **HECHO 2026-06-11 tarde** (el otro agente no llegó a trabajar): (a) `PodNotReady` solo `Pending|Running` (gitops #29, verificado vivo en cluster), (b) NP egress backups/sftp-cleanup (gitops #28), (c) 6 jobs Failed + pods Error huérfanos borrados, (d) TTLs OK en todos los CronJobs (tenant-reaper sin TTL, acotado por historyLimit 3). La sospecha era CIERTA: 18 días sin backups — arreglado+verificado (ver #3)

## Plan post-v1.2.0 Semana 2 — adelantado (2026-06-11, sesión autónoma)

### #9 CODEOWNERS superficie de producto — DONE (con matiz importante)
- [x] `.github/CODEOWNERS` en nkz (components/, public/locales/, src/config/) — PR #538 merged
- [x] Ruleset `product-surface-human-review` (id 17536677): require code-owner review en main, bypass admin solo-PR
- [x] **Verificado empíricamente**: bloquea a actores sin bypass (Dependabot, colaboradores) PERO el bypass de admin vía API es SILENCIOSO — `gh pr merge` con token del owner mergea sin avisar (test PR #541). Mitigación: regla dura en CLAUDE.md (agentes nunca mergean superficie de producto; nunca `--admin`)
- [ ] Residuo cosmético: línea en blanco al final de `CesiumMap.tsx` (commit de verificación #541 en main) — quitar en cualquier PR futuro de host

### #10 ESLint flat config — DONE (PR #539, auto-merge armado)
- [x] eslint 9 + typescript-eslint v8 + react-hooks 5 + react-refresh 0.5.2; paridad exacta (404 warnings/0 errors = baseline main); pin retirado de dependabot.yml
- [ ] Verificación definitiva: próxima tanda Dependabot propondrá react-refresh 0.5.x y debe pasar lint

### #7 Suites rojas a verde + CI-blocking — DONE en 3 frentes
- [x] **entity-manager**: 21 failed → 167 passed (18 weather obsoletos eliminados, 2 fix mock helpers, 1 fix 410 purge). Suite añadida al job requerido Backend Unit Tests — PR #542 (auto-merge armado)
- [x] **crop-health**: 12 failed → 119 passed (headers gateway en clients, mock VegetationIndex realista). Workflow Tests NUEVO (antes el CI no corría tests) — PR #12 MERGED
- [x] **vegetation-health**: 3 errores colección → 49 passed 6/6 ficheros (conftest env, shapely.ops, load_bands no-op, run_tests.sh aislamiento por proceso). Job backend-tests REACTIVADO en ci.yml (estaba "disabled by request") — PR #16 MERGED
- [ ] Follow-up: weather-api NO tiene suite propia (sus rutas salieron de entity-manager sin tests)
- [ ] Follow-up: vegetation-health local requiere python 3.11 (Docker) — numpy 1.24.3 no compila en 3.12

## Vegetation Health — estabilización (2026-06-12)

> Commits: `812ac0d`..`a6b0614`. Auditoría completa + fixes desplegados vía ArgoCD.

### Bugs corregidos
- [x] **Rate limits irrisorios**: `DEFAULT_DAILY_JOBS_LIMIT` 5→100, `DOWNLOAD` 3→30, `CALC` 20→150, `MONTHLY_HA` 10→500
- [x] **Fencepost scheduler**: `sub.next_run_at = now + delta` acumula deriva de microsegundos. Tras 1 iteración, `next_run_at=02:00:00.991` pero el beat dispara a `02:00:00.884` → la suscripción parece "no vencida" por 107ms. Las daily nunca se ejecutaban. Fix: `datetime.combine((now+delta).date(), time(2,0))`
- [x] **Reaper sin retry**: Jobs stuck >1h o perdidos se marcaban `failed` permanentemente. Ahora: reset a `pending` con `retry_count` (3 intentos antes de fail definitivo)
- [x] **Sin autoretry en tasks**: `download_sentinel2_scene` y `calculate_vegetation_index` no reintentaban ante 500/504 de Copernicus. Ahora: `autoretry_for=(Exception,)` con 3 reintentos y backoff
- [x] **Cloud threshold 30%**: Demasiado restrictivo para clima atlántico (Galicia, País Vasco). Subido a 50%
- [x] **SAR sin entrada manual**: Solo se activaba vía subscription scheduler nocturno. Nuevo endpoint `POST /api/vegetation/sar/analyze` + botón en UI
- [x] **SAVI sí se calculaba** (no era bug de SAVI): Los jobs no llegaban a ejecutarse por rate limiting. Con los nuevos límites, SAVI aparece

### Lecciones aprendidas
- **YAML comments in `tags: |` block**: `docker/build-push-action` interpreta líneas con `#` como tags inválidas → build failure
- **GHCR package naming on repo rename**: `github.repository` cambia → CI sube a nuevo package. Si k8s reference el legacy, el tag no existe. Solución: dual-tag en CI o cambiar k8s
- **Full SHA vs short SHA**: CI tagea imágenes con `${{ github.sha }}` (40 chars). k8s pin con short SHA (7 chars) no resuelve. Usar full SHA o `@sha256:` digest
- **In-place pod patching**: Editar archivos vía `kubectl exec` + `sed` se pierde al reiniciar el pod (vuelve a la imagen Docker). Usar solo para hotfix temporal; el fix permanente requiere nueva imagen + ArgoCD

---

## Module Publish System — OIDC (2026-06-03) — DONE

> **Status:** Deployed. ARC removed. OIDC JWT auth in api-gateway. Reusable workflow on main.

- [x] OIDC JWT validation in api-gateway (`fiware_api_gateway.py:internal_module_ci`)
- [x] Reusable workflow `_publish-module.yml@main` with OIDC token request (`jq -r .value`)
- [x] Traefik IngressRoute `publish-internal` (priority 200) + middleware `gh-actions-ipwhitelist`
- [x] NetworkPolicy `api-gateway-oidc-jwks-egress` for api-gateway → GitHub JWKS
- [x] ARC removed: controller, scale-set, `arc-system` ns, NetworkPolicies, GitHub App secret
- [x] gis-routing `ci/canonical-publish` merged to main (OIDC publish + drop `deploy-module.sh`)
- [x] E2E verified: carbon 201, gis-routing 201 + pointer flip + manifest served
- [x] Docs: AGENTS.md §7, CURRENT_STATE.md, PENDING.md
- [ ] **Follow-up:** add `permissions: id-token: write` to all module repos
- [x] **Follow-up:** rotate `INTERNAL_SERVICE_SECRET` (rotado por usuario)
- [x] **Follow-up:** add rate-limiting to `/api/internal/modules/*/publish` (implementado: 10 req/min sliding window, `fiware_api_gateway.py`)

---

## Maintenance & Audit (2026-05-29) — DONE

> **Session scope**: i18n module descriptions, marketplace UI fixes, VPN module recovery, cluster audit.
> **PRs**: nkz#372 (merged), nkz#374 (dollar-quoting fix, open), nkz#375 (closed — superseded), gitops-config@079dab7 (main)

### Module i18n descriptions — DONE
- [x] **Backend** (`modules.py`): `_resolve_description()` helper with `Accept-Language` support, `/api/modules/me` now returns `description` + `icon_url`, `/api/modules/marketplace` resolves i18n descriptions
- [x] **Frontend** (`ModuleContext.tsx`): added `description` + `icon_url` to `ModuleDefinition`
- [x] **Frontend** (`Modules.tsx`): fixed icon rendering (URL vs emoji), fixed description display (`module.metadata?.description` → `module.description`), filter installed modules from marketplace section
- [x] **Migration 078**: backfill `description_i18n` in `marketplace_modules.metadata` for 17 modules (dollar-quoting fix in PR #374)
- [x] **17 modules**: `description_i18n` added to `manifest.json` (es, en, eu, fr, pt, ca)
- [x] **Template**: `module-template/manifest.json` + `registration.sql` updated
- [x] **Module repos**: all pushed to main
- [x] **Deploy**: entity-manager image `sha256:4ff4a93e` rolled out, migration 078 applied (16/17 modules — backup missing from DB), frontend host rebuilt + MinIO upload

### VPN module (headscale + tailscale) — DONE
- [x] **Diagnosis**: NetworkPolicies block egress TCP 443; tailscale hairpin NAT to `vpn.robotika.cloud` (server public IP)
- [x] **argocd-repo-server**: init container `copyutil` crash-loop — fixed `ln -s` → `ln -sf` + pod recreated
- [x] **headscale**: self-hosted DERP server (region 999, zero egress), no external internet dependency
- [x] **tailscale-subnet-router**: changed `login_server` from `https://vpn.robotika.cloud` → `http://headscale-service.nekazari.svc.cluster.local:8080`
- [x] **GitOps**: `gitops-config/overlays/core/vpn/headscale-overlay.yaml` updated (DERP server enabled + internal URL), pushed to main

### Pending (not urgent)
- [ ] `backup` module: INSERT row in `marketplace_modules` (missing — manifest exists but not registered)
- [ ] `soil`, `datahub`, `vpn`: modules without `manifest.json` — description_i18n to be added via separate SQL migration
- [ ] `core-auth` ArgoCD app: OutOfSync / Degraded (preexisting)
- [ ] `headscale`, `vpn` ArgoCD apps: OutOfSync (drift from manual patches, will self-resolve on next sync)

---

---

## v1.1.0 Release (2026-05-26) — DONE

> **Tag:** `v1.1.0` — [GitHub Release](https://github.com/nkz-os/nkz/releases/tag/v1.1.0)
> **PR:** #348. Helm chart images pinned, templates support `@sha256:` digest, `UPGRADE.md` + `README.md` added, CHANGELOG updated.
> **Incident 2026-05-26:** `:latest` in frontend-host caused complete landing page outage (cross-pod hashed asset 404s). SHA pinned in gitops, CLAUDE.md strengthened.

- [x] Pin all NKZ images in Helm chart (`values.yaml`)
- [x] MinIO pinned to `RELEASE.2025-09-07T16-13-09Z`
- [x] Deployment templates support `@sha256:` digest + `pullPolicy` (9 templates)
- [x] `UPGRADE.md` + Helm chart `README.md`
- [x] CHANGELOG.md v1.1.0 section
- [x] Git tag + GitHub Release
- [x] EM-DEF-3 closed (entity-manager pinned)
- [x] O2 closed (version pinning for GHCR images)

## Phase 1 Soil Platform Data Fabric — Follow-ups (2026-05-24 PM)

| # | Status | Notes |
|---|--------|-------|
| **FU-1** — bioorchestrator CI workflow | **Done 2026-05-24** | `nkz-os/nekazari-module-bioorchestrator@8b941c2`: first `build-push.yml` (test + backend image to GHCR on push to main, tags `latest+sha+semver`). Until now every backend deploy required a manual `docker build + push + edit deployment.yaml`. |
| **FU-2** — SealedSecrets for soil + bioorch | **Done 2026-05-24** | `bitnami/sealed-secrets-controller:v0.24.5` was already in `kube-system` (98d). Generated SealedSecrets with `kubeseal v0.24.5`: `nkz-os/nkz-module-soil@190580c k8s/sealed-secret.yaml` (pg-dsn + MinIO creds) and `nkz-os/gitops-config@30a6b70 overlays/modules/bioorchestrator/bioorchestrator-sealed-secret.yaml` (6 keys; replaced manual-apply plaintext). ArgoCD `soil-module` reverted to `selfHeal=true,prune=true` with no overrides. |
| **FU-3** — ESDB raster bbox reprojection EPSG:3035→4326 | **Done 2026-05-24** | `nkz-os/nkz-module-soil@237b4d3`: `esdb_raster_loader.py` uses `rasterio.warp.transform_bounds`, removed best-effort try/except. Verified: 15 ESDB raster rows now hold lon/lat polygons (e.g. AWC_SUB extent ≈ -64.4…74.0 lon × 13.0…72.6 lat). |
| **FU-4** — `lucas_texture_all` backfill | **Inert (Done 2026-05-24)** | Migration `007_lucas_texture_backfill.sql` lands and is idempotent. Reality: published `LUCAS-SOIL-2018.csv` does not include sand/silt/clay columns at all, so the topsoil loader leaves those fields NULL and the backfill SELECT matches zero rows. The migration stays in place as living documentation; will start producing rows once a 2025 texture loader is wired in (when `LUCAS_Text_All_10032025.csv` is published). Optional alt source: SoilGrids or ESDB raster sampling — deferred to a Phase 4 data-quality sprint. |
| **FU-5** — Ingress routes for /api/v1/soil, /api/capability, /api/graph | **Done 2026-05-24** | Three dedicated Ingress objects in `nkz-os/gitops-config@9b8483a` `overlays/core/networking/`: `soil-api-service` (with `soil-strip-api` middleware to strip `/api`), `bioorch-capability-graph` (no CORS middleware — that combination on `nekazari-ingress` was returning 502 before the request reached the backend). NP fix in `nkz-os/nkz#337` (port 8420 for Traefik→bioorch, applied + merged). Verified externally: `/api/v1/soil/capabilities → 200`, `/api/capability/catalog → 401`, `/api/graph/soil-data → 401` (auth-gated, routes OK). |
| **FU-housekeeping** — Move applied NetworkPolicies to gitops-config | **PR open 2026-05-24** | `nkz-os/gitops-config@cda9e6c` holds `overlays/core/network-policies/` (3 manifests) + new ArgoCD app `core-network-policies` (Synced + Healthy, NPs now `argocd.argoproj.io/tracking-id`-annotated). PR `nkz-os/nkz#340` removes the duplicates from `nkz/k8s/common/network-policies/` and adds a README explaining the split (project templates vs deployment overlay). No runtime change. |
| **FU-pending-human** — `nkz-os/bioorchestrator-backend` GHCR package visibility | **Done by user 2026-05-24** | Package made public via web UI; CI on bioorch can now push `:latest` without 403. |
| **T38** — Production smoke after Phase 1 | **PASS 2026-05-24** | Loaded: topsoil 18984, bulk_density 6172, erosion 861, organic 1012, esdb_raster_index 15. KNN coverage on perturbed agricultural points = 100% within 5 km, avg 2.05 km (success criterion ≥99%). All three T38 endpoints reachable from external ingress. Details in memory `phase-1-soil-fabric-followups-2026-05-24`. |

---

## Security Audit (2026-05-19)

| # | Status | Notes |
|---|--------|-------|
| **SEC-1.1** — AEMET JWT in `apikey_aemet.txt` | **Done 2026-05-19** | Local file deleted; sha256-confirmed identical to live `weather-secrets/aemet-api-key` Secret. `.gitignore` hardened (`apikey_*.txt`, `*.apikey`, `*_api_key.txt`). Pending **user action**: rotate token at AEMET portal, then `kubectl patch secret weather-secrets` + restart weather-worker / weather-api. |
| **SEC-1.2 (MQTT broker pwd)** | **Done 2026-05-19** | `mosquitto-credentials/admin-password` rotated in cluster (Secret + password file + SIGHUP). Leaked literal `d6cc8be9a0c6b0e16a5f3818` confirmed REJECTED (CONNACK 5). `iot-agent` user unaffected. Runbook in memory `security-rotation-2026-05-19.md`. |
| **SEC-1.2 (DaTaK code)** | **Done 2026-05-19** | `nkz-os/datak@6b9058e` on main: `remote_diag.py` requires `DATAK_REMOTE_{HOST,USER,PASSWORD}` env vars; `backend/app/config.py` drops empty yaml values so env vars take effect + fail-fast guard rejecting `digital_twin.enabled=true` without `DATAK_DIGITAL_TWIN_PASSWORD`; `configs/gateway.yaml` sanitized; `.gitignore` deduped. No history rewrite (rotation makes leaked values inert). |
| **SEC-1.2 (DaTaK SSH `agrivolt`)** | **Blocked — host offline** | Pending: rotate `agrivolt` SSH password on the DaTaK host (and prefer SSH-key auth replacing password auth) when the PC returns to the network. |
| **SEC-1.3** — NodePort MQTT 31883 plaintext | **False positive — closed via doc cleanup** | Multi-vantage probe (from server itself + on-host `ss -tln` + `lsof`) confirms nothing listens on 1883/8883/31883. Audit was based on stale ConfigMap doc + 53d-old memory. `MQTT_EXTERNAL_PORT` updated `31883`→`8883` (SOTA mTLS port per `mosquitto-deployment.yaml`) with explicit contract comment, applied in cluster, `sdm-integration` restarted. nkz repo: branch `security/sec-1-hardening` pushed, **PR pending merge**: https://github.com/nkz-os/nkz/pull/new/security/sec-1-hardening |
| **SEC-1.4** — Dev compose weak passwords | **Open** | `catastro-sp-module-nekazari/docker-compose.yml` and similar dev compose files use `POSTGRES_PASSWORD=modulepass` etc. Low severity if compose stays local-only. Audit sweep + harden as part of public-readiness work. |
| **SEC-1-FOLLOWUP** — Mosquitto Secret↔password-file sync gap | **Open** | After today's rotation Secret and password file are aligned, but a PVC reset would drop all users (init-container does not seed from Secret). Long-term: init-container that materialises passwords file from `mosquitto-credentials` Secret on every pod start. |
| **SEC-1-FOLLOWUP** — External MQTT ingestion not published | **Open — architectural decision** | Cluster does not expose 8883/1883 externally. Decision: publish 8883 via Traefik TCP+TLS passthrough (SOTA), or stop advertising an external endpoint from `sdm-integration`. Until decided, IoT clients reading `MQTT_EXTERNAL_HOST:MQTT_EXTERNAL_PORT` cannot connect. |
| **MOB-1** — audit verification | **Mostly false positives (Done 2026-05-19)** | After source verification, all of MOB-1.2 (log gating with `__DEV__`), MOB-1.3 (`lastPacketTime` → `useRef`, deps clean, `hasStartedRef` guard), MOB-1.4 (MapContainer with NetInfo, MapLibre default, Cesium opt-in with thermal warning, `<ErrorBoundary>`), and the minor items (tamagui removed, AuthContext typed, ModuleWebView + `NKZ_AUTH_INJECTION` wired, i18n coverage at `t()`) were already implemented. Only MOB-1.1 was genuinely open. |
| **MOB-1.1** — SQLite at-rest encryption (SQLCipher) | **Declined by architectural decision (Done 2026-05-19)** | `nkz-os/nkz-mobile@079edde`: removed orphan `src/database/encryption.ts` stub (generated a key no adapter consumed — security theatre), stripped misleading TODO in `src/database/index.ts`, added public ADR `docs/DATA_AT_REST_DECISION.md`. Rationale: upstream WatermelonDB has no merged SQLCipher path (PR #907 and #1635 both `mergeable: false`); op-sqlite has SQLCipher but no WatermelonDB integration; stored data is operational (parcels/equipment/operations), not credential material; sensitive tokens already in `expo-secure-store` (Keychain/Keystore); OS-level FBE/Data Protection covers the SQLite file while the device is locked. Re-open triggers documented in the ADR. |
| **MOB-A** — Sprint A: field-photo pipeline + map bugs | **PRs open (2026-05-30)** | nkz #380 (`feat/field-images-sota`) + nkz-mobile #4 (`fix/map-orientation-recenter`). Foto "Image storage unavailable" = api-gateway sin creds MinIO (B4 fix). G1: serving tenant-safe vía proxy auth `GET /api/field-images/<key>` (private key, no `/modules/`, `imageUrl` relativa); Orion calls tenant-scoped + canonical `ld+json` headers (sin esto Orion 400 → observación perdida). Entity ahora `AgriParcelRecord` (SDM canónico) + `refAgriParcel` por geo-query con buffer adaptativo `max(acc*3,50m)`. Mobile B1 `useFocusEffect` (portrait al salir) + B2 recenter FAB + permiso ubicación. Tests: backend 11 pytest, mobile 81 jest + tsc + expo export. **Pendiente:** verificación integración manual post-deploy (checklist en PRs); sub-proyectos B (rediseño mapa), C (F5 entidades/sensores + F4), D (visor fotos web+nativo). |
| **MOB-B** — Sub-project B: map redesign Visor/Guiado | **PR open (2026-05-31)** | nkz-mobile #5 (`feat/map-visor-guiado`, apilado sobre #4). Mapa partido en Visor (tab portrait: capas en bottom-sheet, selección de entidad, FAB centrar, botón Guiado) + Guiado (ruta stack apaisada: HMI+Lightbar+useGuidance+toggle 3D+Salir, reubica el viejo MapScreen que se borra). Hooks `use3DPreference` (re-lee on focus) + `useMapLayers`; `BottomSheet` sin deps; `MapLibreMap` con `visibleLayers` + tap parcela→onEntitySelected. i18n 6 locales. Resuelve B3 y deja B1 contextual. Tests: 94 jest + tsc + expo export. Revisión opus sin Critical/Important (2 Minor corregidos). **Pendiente:** device verify (checklist en PR); sub-proyectos C (F5 sensores + F4, override parcela A→C), D (visor fotos). |
| **MOB-C** — Sub-project C: Entidades/Sensores + F4 | **PR open (2026-05-31)** | nkz-mobile #6 (`feat/entities-sensors-f4`, apilado sobre #5). F5: pantallas nativas EntitiesScreen/EntityDetail/SensorsScreen desde Dashboard; servicio `entities.ts` + utils puros (sensorParse/entityFormat/operationSelect/parcelCentroid); EntitySheet 'Ver sensores' (filtra por URN de parcela). F4: `MapContainer.initialCenter` reactivo, Guidance centra en centroide de parcela, Operations botón 'Guiar'. Revisión opus encontró+arregló 2 defectos (centrado muerto por useState-once/defaultSettings; "Ver sensores" mandaba id local en vez de URN). Tests: 112 jest + tsc + expo export. **Dato modelo:** WatermelonDB parcel id=local, remote_id=URN; op parcel_id=local. **Pendiente:** device verify (checklist en PR); sub-proyecto E (estado agronómico + meteo por parcela — NEXT, backend listo), luego D (visor fotos). |
| **MOB-stack** — Pila móvil A/B/C | **MERGED a master (2026-05-31)** | PRs nkz-mobile #4 (Sprint A móvil), #5 (B mapa Visor/Guiado), #6 (C Entidades/Sensores+F4) mergeados a master; ramas borradas. (Backend Sprint A = nkz #380, estado no re-verificado.) |
| **MOB-E** — Sub-project E: estado agronómico + meteo parcela | **PR open (2026-05-31)** | nkz-mobile #7 (`feat/parcel-agro-weather`, base master). Mobile-only sobre weather-api ya desplegado. `weather.ts` (URN-keyed: agro-status + `?data_type=FORECAST` + alerts) + utils puros `agroSemaphore`/`forecastDaily` + `AgroSemaphores` + `ParcelStatusScreen` (semáforos+tiempo actual+previsión diaria+alertas, secciones independientes, pull-to-refresh) + EntitySheet semáforos inline + EntityDetail entry. Vocab semáforos: spraying optimal/caution/not_suitable; workability +too_wet/too_dry; irrigation satisfied/deficit/alert. Revisión opus sin Critical/Important. Tests: 126 jest + tsc + expo export. **Pendiente:** device verify (checklist en PR); sub-proyecto D (visor fotos — ÚLTIMO). Diferido de E: 6 modelos de riesgo, tap-to-act alertas, gráficas agro-status/history. |
| **MOB-OTA** — EAS Update (OTA) setup | **MERGED — PR #9 (2026-06-01)** | `nkz-mobile` PR #9: `expo-updates@29.0.18` + `updates.url` (proyecto `8c31b69c…`) + `runtimeVersion.policy=appVersion` (=1.0.0) + `channel` por perfil (development/preview/production). ⚠️ **OJO:** los builds `preview` existentes (f6fa1da1=`ca23b7c`, 19970e15=`01cfc5e`) son **anteriores** a este PR → **no tienen OTA embebido**, no pueden recibir updates. Se requiere **1 build nuevo** desde master (`eas build -p android --profile preview`) para activar OTA; ese build crea el canal `preview` en EAS. A partir de ahí, cambios JS van por `eas update --branch preview` sin recompilar. |
| **MOB-MAP-UX** — Map UX fixes (#1/#3/#4/#6/#7) | **MERGED — PR #10 (2026-06-01)** | `nkz-mobile` PR #10 (`feat/mobile-map-ux-fixes`, base master). #3 base demo→**OpenFreeMap** (env `EXPO_PUBLIC_MAPLIBRE_STYLE_URL`); #7 selector **Calle/Satélite** en LayersSheet (`baseMap` en useMapLayers); **satélite compuesto** = Sentinel-2 cloudless (EOX/Copernicus CC-BY) global + PNOA ortho (IGN CC-BY ~0.25m) sobre España vía `bounds` (env `EXPO_PUBLIC_SATELLITE_TILE_URL`/`_PNOA_URL`); #1/#5 cámara FAB Guiado→Visor; #4 recenter por `recenter()` imperativo (forwardRef MapHandle) + UserLocation; #6 FABs sin solape. 9 commits, subagent-driven (impl+spec+quality review/tarea). Gate: tsc 0 · jest 149 · expo export OK. OTA publicado a branch `preview` (group `0c0f8ef5`, runtime 1.0.0). **Pendiente:** device verify (bloqueado por MOB-OTA — falta el build OTA-capaz); **#2 página GIS nativa in-app — NO empezada** (propio brainstorm→spec, WebView vs nativo, criterio F5). **Prod:** decidir proveedor satélite (volumen/licencia EOX/PNOA) antes de release. |

## Module Federation 2.0 — Immutable Deployments (2026-05-24) — DONE

> **Status:** Deployed. `@nekazari/module-builder@2.1.0` published. Migration 077 applied. New endpoints live: `/api/modules/<id>/deploy`, `/rollback`, `/versions`, `/internal/.../resolve-url`. Nginx versioned paths with immutable cache. CI workflows updated in 7 module repos.

## Module Federation 2.0 — Migration Status (2026-05-17)

> **Plataforma 100% en MF 2.0**: 16 módulos en `marketplace_modules` apuntan a `/modules/<id>/mf-manifest.json`. Cero módulos en IIFE legacy. Host PR #271 (rolled out 2026-05-16). Ver memoria `module-federation-deployment-2026-05-16.md` y `module-federation-completion-2026-05-17.md`.
> Published: @nekazari/sdk@1.1.3, @nekazari/module-kit@0.6.2, @nekazari/module-builder@2.1.0, @nekazari/create-module@0.1.0
> Published PyPI: nkz-platform-sdk@0.3.0

### Live en producción (16 módulos)

| Módulo | FE (MF 2.0) | BE | Notas |
|--------|-------------|----|-------|
| agrienergy | ✅ | ⬜ require_auth | |
| bioorchestrator | ✅ | ⬜ Neo4j→OrionClient | |
| carbon | ✅ | ⬜ | Migrado 2026-05-17 |
| catastro-spain | ✅ | ⬜ | Migrado 2026-05-17. Backend Running (verified 2026-05-05) |
| connectivity | ✅ | ⬜ | Migrado 2026-05-17 |
| crop-health | ✅ | ⬜ real JWT→require_auth + OrionClient | |
| cue | ✅ | — | Migrado 2026-05-17 |
| datahub | ✅ | ⬜ BFF proxy→gateway | |
| eu-elevation (`nkz-module-eu-elevation`) | ✅ | — | |
| gis-routing (`nkz-module-gis-routing`) | ✅ | ⬜ require_auth | |
| lidar | ✅ | ⬜ | Migrado 2026-05-17 |
| n8n (`n8n-nkz`) | ✅ | ⬜ K8s+Stripe→lifecycle hooks, JWT→require_auth | ArgoCD PR #313, JWT issuer whitelist fixed 2026-05-20 |
| odoo-erp | ✅ | ⬜ require_auth | Migrado 2026-05-17. Running (fixed 2026-05-29). NetworkPolicy, CSS, slot fixes. |
| robotics | ✅ | ⬜ | Migrado 2026-05-17. Sin backend |
| vegetation-prime | ✅ | ⬜ | Migrado 2026-05-17. 3 pods. Sen2Res SR + geometry buffer 2026-05-17 (PR #11). `SEN2RES_ENABLED=false` |
| zulip | ✅ | ⬜ Flask→FastAPI or keep | |

`backup` migrado pero sin fila en marketplace_modules (necesita INSERT).
`template` baseline placeholder, no se despliega.

### Backend work (separado de la migración FE)
- [x] Publicar nkz-platform-sdk a PyPI (Done 2026-05-19, Trusted Publishing vía OIDC)
- [ ] Migrar real JWT modules (crop-health, odoo, vpn, n8n) a `require_auth()`
- [ ] Añadir test suite completa a Odoo (6 tests de auth ya añadidos 2026-05-20, faltan routers/services)
- [ ] Migrar `inject_fiware_headers` (intelligence) a `OrionClient`
- [ ] Migrar Orion-LD direct queries (crop-health, odoo, robotics, intelligence) a `OrionClient`
- [ ] Lifecycle hooks: n8n (provisioning), bioorchestrator (Neo4j init), zulip (OIDC bot setup)

### Cleanup
- [ ] Validar end-to-end en navegador con tenant auth'd (todos los slots + rutas de los 16)
- [x] Merge `fix/tenant-headers` branches (7 repos) → main (Done 2026-05-18)
- [x] Merge `fix/tenant-normalization` branch en carbon → main (Done 2026-05-18)
- [ ] INSERT `backup` row en `marketplace_modules`
- [x] Bump `@nekazari/module-builder` peer para `@vitejs/plugin-react` a `^4.0.0 (Done 2026-05-18, v2.0.2)`
- [ ] Limpiar `nkz-module.js` viejos de los buckets MinIO de cada módulo (ya no se sirven; mantienen ~2MB cada uno)
- [x] Mover `initI18n()` del SDK al host (P1.2, Done 2026-05-18, PR #293). unwrapI18nPlugin eliminado.
- [ ] Arreglar el build del host: Rollup no resuelve `@remix-run/router` (pre-existing)
- [x] Fix NKZProvider context missing for MF2 remote modules in apps/host (Done 2026-05-20, PR #317)
- [x] nkz-module-soil: Implement UI fixes (routing, search, raster viewer) en frontend (Done 2026-05-20, PR #1)

### Hecho 2026-05-17/18 — MF 2.0 + publish pipeline
- Plataforma 100% en Module Federation 2.0 (16 módulos)
- `@nekazari/sdk@1.1.3`, `module-kit@0.6.1`, `module-builder@2.0.1`, `design-tokens@0.1.0-alpha.3` (con Sigstore attestation) publicados
- Publish CI: Trusted Publishing OIDC + `--provenance` SLSA, sin `NPM_TOKEN`. Ver memoria `mf2-publish-pipeline-2026-05-18.md` para los 7 wedges resueltos y la receta canónica

### Hecho 2026-05-18 (segunda sesión) — ROADMAP P0 completo + P1.3
- **P0.1**: docker-compose alineado con MF 2.0 (PR #288 merged). seed.sql sin módulos IIFE, nginx /modules/ proxy, env.example legacy eliminado, README MF 2.0, Dockerfiles Alpine 3.20 + --no-install-recommends
- **P0.2**: QUICKSTART.md end-to-end (7 pasos: clone → up → login → build → mc cp → SQL → live)
- **P0.3**: Auto-deploy endpoint `POST /api/modules/<id>/dist` en entity-manager (PR #290 merged). Acepta multipart dist/, valida manifest.json, sube a MinIO, upserts marketplace_modules. 8 smoke tests.
- **P0.4**: Tag `v1.0.0-rc.1` + GitHub Release con notas honestas
- **P1.3**: `withModuleProvider()` helper en `@nekazari/module-kit@0.6.1` (PR #291). 3 módulos migrados (vegetation-prime, lidar, odoo-erp). -65 LOC duplicadas.
- **Merge fix/tenant-headers**: 8 repos → main (agrienergy, carbon, crop-health, cue, lidar, odoo-erp, robotics, vegetation-health)
- Landing page i18n fix: dropped redundant `LanguageDetector`, added defensive `unwrapI18nPlugin` walker (`packages/sdk/src/i18n/config.ts`)
- 16/16 `mf-manifest.json` con `publicPath: /modules/<id>/` (hot-patch MinIO + fix permanente en `module-builder@2.0.1`)

### Hecho 2026-05-18/19 — ROADMAP P1 completo + v1.0.0
- **P1.1**: Helm chart (PR #299). 9 subcharts, 26 recursos K8s. CI lint + dry-run.
- **P1.2**: Mover initI18n del SDK al host (PR #293). -130 LOC del SDK. unwrapI18nPlugin eliminado.
- **P1.4**: CLI generator `pnpm create @nekazari/module` (PR #298). Zero-deps, nativo ESM.
- **P1.5**: Playwright E2E suite (PR #305). CI: docker compose → health → smoke tests.
- **P1.6**: Compatibility matrix `COMPATIBILITY.md` (PR #294).
- **P1.7**: Federation runtime health endpoint `GET /api/admin/modules/health` (PR #303).
- **P1.8**: Tag `v1.0.0` + GitHub Release (2026-05-19).
- **Fixes**: module-builder peer dep (PR #292), withModuleProvider type (PR #296), E2E ESM compat (PR #300), Keycloak PKCE (PR #301), playwright config (PR #306).
- **PyPI**: `nkz-platform-sdk@0.3.0` published via Trusted Publishing OIDC.

## Pending Deployments & Server Tasks

| # | Task | Estado | Notas |
|---|------|--------|-------|
| **AUTH-2** | **Deploy Identity-First Provisioning (OTP)** | **Done 2026-05-07** | Desplegado y verificado en prod. OTP flow activo, 2-step verification en 6 idiomas. |
| ~~**EM-OVERHAUL**~~ | ~~**Entity-Manager Overhaul (3 fases)**~~ | **Done 2026-05-06** | 7 PRs mergeados (#189, #191, #192, #193, #198, #201, #202). Fase A: 4 bugs críticos (403 silencioso, 500 auditoría, requested_by NULL, tier mapping). Fase B: NDVI removal (850 líneas), sensor Orion-first, 4 security fixes, NGSI-LD compliance. Fase C: 7 Blueprints extraídos (8633→272 líneas), 164 smoke tests, helpers/ compartidos. Hotfix: import inject_fiware_headers corregido (CrashLoopBackOff). Desplegado en prod. |
| ~~**VEG-AUDIT-1**~~ | ~~External audit blockers: /viewer-url 404, manifest/slot mismatch, window.alert, hardcoded es-ES, commit-per-iter, Python-side scene_id filter, hard-vs-soft season delete, hardcoded slot title, dead legacy code~~ | **Done 2026-05-07** | `nkz-module-vegetation-health@3f33b19`. -1058 LOC net (legacy purge: VegetationAnalytics, useJobPolling, SetupWizard, CalculationCard). All 10 audit blockers resolved + soft-delete season + JSONB SQL filter for scene_id + try/except orion-cleanup + setTimeout cleanup-on-unmount. Verified via TestClient: soft-delete idempotent, EXCLUDE constraint ignores tombstones, re-create on same parcel after soft-delete succeeds. Deferred by user agreement: #5 ca/eu/fr/pt translations (kept EN fallback), #11 migration-008 guard (prod already clean). |
| ~~**VEG-FIX-1**~~ | ~~Vegetation Prime: 500 on duplicate crop-season + silent skip on cloudy scenes + orphan jobs~~ | **Done 2026-05-06** | Backend `nkz-module-vegetation-health@ae7bd53`: crop-seasons IntegrityError → 409 with friendly message; defensive `.delay()` records `celery_task_id` and fails fast if queue down; new periodic `reap_stuck_jobs` (every 15 min) for pending without `celery_task_id` >10min and `running` zombies >1h; `task_acks_late` + `task_reject_on_worker_lost`. Cloud threshold: per-job `local_cloud_threshold` (0-100) on `/analyze`, env default raised 10→30. UI slider 5-80% (default 30) + `/data-status` exposes `recent_cloud_skips` so UI can explain why no layer rendered. Cleanup: 78 orphan + 1 zombie job marked failed in DB. Verified via FastAPI TestClient: 409 + friendly msg, 422 on end<start, recent_cloud_skips populated. |

### Entity-Manager Overhaul — Deferred Items

| # | Task | Notas |
|---|------|-------|
| **EM-DEF-1** | Migrate `commands` to NGSI-LD | Touches IoT pipeline (MQTT, IoT Agent, downlink). Out of scope for overhaul. |
| **EM-DEF-2** | `DROP TABLE ndvi_jobs/ndvi_results` | Data retention decision pending. Tables frozen (historical data). |
| **EM-DEF-3** | Pin entity-manager image to SHA (instead of `:latest`) | **Done 2026-05-29** | Deployment usa SHA digest `sha256:4ff4a93e`. Pinned via `kubectl set image`. |
| **EM-DEF-4** | Real integration tests (Orion + Postgres in docker-compose) | High cost, smoke is sufficient for this intervention. |
| **EM-DEF-5** | Performance fixes (`_gather_usage_for_tenant` cache, etc.) | Low-priority debt. |
| ~~**AUTH-1**~~ | ~~Signup/Activate password validation & UI hint~~ | **Done 2026-04-23** | Frontend host deployment triggered. |

| # | Task | Estado | Notas |
|---|------|--------|-------|
| ~~**TUM-3**~~ | ~~keycloak-setup-mappers.sh deploy~~ | **Done 2026-03-26** | Attributes `tenant_id`, `tenant` already registered in KC26 User Profile. Mapper exists on `nekazari-frontend` client. |
| ~~**FW-1g**~~ | ~~Run migration 060 on server~~ | **Done 2026-03-26** | Connectivity module deactivated in marketplace DB. |
| ~~**IOT-1**~~ | ~~Run migration 061 on server~~ | **Done 2026-03-26** | Compression active: segmentby=tenant_id,device_id. RLS disabled (TimescaleDB limitation, tenant isolation at app layer). |
| ~~**IOT-2**~~ | ~~Deploy telemetry-worker v2 (asyncpg)~~ | **Done 2026-03-26** | PR#87 merged. asyncpg pool (5-20), EventSink, batch inserts. Pod running. |
| **IOT-3** | **Verify frontend sensors UI** | Pendiente | Confirm sensor `120786a0cf364796` shows real data. User must re-login with `tenant_id=asociacinallotarra`. |
| ~~**TS-RDR-1**~~ | ~~Apply Timescale migration `062_telemetry_events_tenant_device_time_index.sql`~~ | **Done 2026-03-28** | Applied on prod via `kubectl exec` + `psql`; `postgresql-migrations` ConfigMap refreshed with **server-side apply** (annotation size limit). Index `ix_telemetry_tenant_device_time` live. |
| **KC-PAT-1** | **Keycloak + K8s secrets for ADR 003 (PAT)** | Pendiente (ops) | Create realm client **`nkz-api-gateway`** (service account), assign realm role **`urn:nkz:role:system-gateway`**, mapper → `realm_access.roles`. K8s: Secret **`api-gateway-keycloak-secret`** (`client-id`, `client-secret`), **`pat-internal-secret`** (`secret` — shared with tenant-webhook). Apply migration **`063_pat_personal_access_tokens.sql`**. See `nkz/internal-docs/adr/003-pat-delegated-auth.md`. |
| **DH-DEP-1** | **DataHub — despliegue y verificación en producción** | **Importante (pendiente ops)** | Repo **`nkz-os/nkz-module-data-hub`**: CI **`.github/workflows/build-push.yml`** → **`ghcr.io/nkz-os/nkz-module-data-hub/datahub-backend:latest`** (público). ArgoCD: apps **`datahub`** (`k8s/`) + **`datahub-config`** (overlay `nkz/gitops/overlays/datahub`). Tras imagen nueva: sync + **`kubectl rollout restart deployment/datahub-api -n nekazari`**. **2026-04-10:** aplicado fix de robustez en `/api/datahub/entities` (header `Link` @context + filtrado/canonización de atributos consultables en `timeseries-reader`; evita opciones no ploteables como `dateObserved` que provocaban 400). Verificar `/api/datahub/*`, marketplace/slot, JupyterLite si aplica. |
| **EM-EDIT-ENT** | **Entity editing UI — editar atributos de entidades existentes** | **Done 2026-05-11** | EntityEditor implementado en core (`nkz/apps/host/src/components/EntityEditor/`). 13 tasks, 12 commits. Single-page form con secciones colapsables, ~30 schemas conocidos + data-driven fallback, Relationships, kinematic attributes (ManufacturingMachine), Geometry con inputs de coordenadas. Entrada vía lista de entidades (botón lápiz), popup del mapa, y SDK (`window.__NKZ__OPEN_ENTITY_EDITOR__`). Spec: `internal-docs/specs/2026-05-11-entity-editor-design.md`. Plan: `internal-docs/plans/2026-05-11-entity-editor-plan.md`. |
| **WTH-WIDGET** | **Weather Widget — cambiar buscador de municipios por dropdown de parcelas** | **Done 2026-05-11** | `WeatherWidget.tsx` refactorizado: dropdown de parcelas con filtro client-side, `loadWeatherByParcel()`, fallback a municipio preservado. `Weather.tsx` simplificado (selector duplicado eliminado). |
| **WTH-SPATIAL** | **Eliminar dependencia de `catalog_municipalities` — resolución puramente espacial** | **Done 2026-05-11** | Migración 074: columna `location GEOMETRY(Point, 4326)` en `weather_observations` + índice GIST + backfill. Queries KNN migradas en `urn_resolution.py`, `parcels.py`, `locations.py`. Ingesta del weather-worker actualizada. |
| **DH-UX-2** | **DataHub — saneamiento de series para render y diagnóstico UI** | **In progress 2026-04-19** | Investigación en prod: `/api/datahub/timeseries/entities/.../data` devuelve 200 con puntos para `asociacinallotarra`, pero el lienzo puede quedar en blanco en frontend. Implementado en `nkz-module-datahub`: coerción y filtrado de timestamps/values no finitos antes de uPlot + contador visible de puntos ploteables vs recibidos para evitar falsos “sin datos”. Pendiente: desplegar bundle y validar en Firefox/Chromium. |
| **DH-WORKER-V2-1** | **DataHub — formal Worker Contract V2 spec (zero-copy + MinMaxLTTB)** | **In progress 2026-04-20** | Spec drafted in `internal-docs/specs/2026-04-20-datahub-worker-contract-v2.md` (private workspace doc) with strict request/response types, invariants, gap policy (`NaN` in typed Y), transfer protocol (`postMessage` with transfer list), and acceptance criteria. **2026-04-20 audit addendum applied:** delta-first UI/Worker model from day one, Timescale toolkit requirement for valid rollups, execution order fix (`PR-2.5` before alignment algorithms), and mandatory MB-budget LRU + explicit worker GC semantics. **Implemented now in module code:** active-panel multi-series composition, worker-first fetch/parse/process, local per-series cache + delta fetch, local outer-join, segmented MinMaxLTTB downsampling, budget-aware LRU eviction, explicit UI→worker release messages (`RELEASE_SERIES`) on panel lifecycle changes, per-series Y-axis assignment (`left`/`right`) persisted in workspace state, correlation plot mode (X-series vs Y-series scatter with optional trendline), and on-panel Pearson correlation coefficient (`r`, with pair count `n`) for selected X/Y series. Pending: intelligence rollup wiring/validation against production datasets and hard verification on real tenant dashboards. |
| **TS-SOTA-1** | **Core Timescale statistics SOTA rollout (Toolkit + hierarchical CAGGs)** | **In progress 2026-04-20** | Launch package drafted at `nkz/internal-docs/specs/2026-04-20-timescale-toolkit-sota-rollout-plan-v2.md`. PR-0 implementation artifacts created: migration `066_telemetry_measurements_long_format.sql` (canonical long-format table + incremental trigger sync + backfill checkpoint table) and async WAL-safe backfill/validation job `scripts/backfill_telemetry_measurements.py` (chunked commit, throttle, resume, parity checks). **Server execution status (PR-0):** migration applied successfully in `nekazari` DB (Timescale 2.10.2), partial table in wrong `postgres` DB cleaned, staged backfill completed (`source=3468`, `target=3468`, ratio 1.0 across full validation window), checkpoints updated (`telemetry_measurements_pr0`, `telemetry_measurements_pr0_history`). **Server execution status (PR-1):** `timescaledb_toolkit` enabled (`1.16.0`), migration `067_telemetry_10m_toolkit_cagg.sql` applied, CAGG `telemetry_10m` created with `stats_agg` + `percentile_agg`, refresh/compression jobs registered, and accessor query (`average`, `stddev`, `approx_percentile`) validated with real rows. **Server execution status (PR-2):** part A (`068`) and part B (`069`) applied. Hierarchical `telemetry_1h` and `telemetry_1d` are active (`finalized=true`), policy jobs registered (`1006/1007` and `1008/1009`), and timezone-aware tenant-local daily view `telemetry_1d_tenant_localized` is live using `admin_platform.tenant_timezones`. **PR-3 status:** code refactor done in `services/timeseries-reader/app.py`; production deployment updated with flags (`TIMESERIES_STATS_ENGINE=v2`, `TIMESERIES_USE_TENANT_LOCAL_DAILY=true`) and service recovered after scheduler CPU saturation (request lowered to `10m`). Pending: publish/deploy image containing PR-3 code changes and run in-cluster endpoint validation (`10m`/`1h`/`1d`). |
| **GIS-SOTA-1** | **GIS Routing — Hito 0+4 (stabilization + UX + VRA + execution + mobile hardware)** | **Done 2026-05-06** | Hito 0 done+verified in local isolated backend env (`backend/.venv`): fixed slot geometry usage (no hardcoded polygon), fixed centroid extraction, and unified NGSI-LD relationship handling (`object` + legacy `value` fallback). Validation command: `PYTHONPATH=/home/g/Documents/nekazari/nkz-module-gis-routing/backend .venv/bin/pytest tests/test_api.py tests/test_sync_api.py tests/test_orion_client.py -q` → 31 passed. Hito 1 done+verified: `RoutingDesigner` follows Plan/Validate/Execute with operation type + DEM toggle and preflight checklist. Hito 2 done+verified: VRA zone selection (`zone_ids`) wired in routing payload, zonification polling states in `ZoningTab`, and prescription-map summary (segments/zones/length/rate) in routing results, with i18n `es/en`. Hito 3 done+verified (web scope): operations history in routing UI, close-session action (`/operations/session/close`), and actual coverage fetch (`/operations/coverage/{operation_id}`) with summarized geometry diagnostics, keeping i18n and typed API contracts. Hito 4 done+verified (mobile full scope): `nkz-mobile` now includes GIS-routing API facade, upgraded `OperationsScreen` with UDP hardware state + backend merge + close-session and coverage actions, persisted offline close-operation queue with auto flush on reconnect, and map-level plan-vs-actual overlay in `MapLibreMap` via `ab_line_geojson` (plan) and `coverage_geojson` (actual). Verification: `cd nkz-mobile && pnpm lint` (passes with pre-existing non-blocking inline-style warnings only). |
| **GIS-SOTA-3** | **GIS Routing — Plan Maestro SOTA (web + backend + mobile)** | **Done 2026-05-06 (workspace)** | **Backend:** `active_operation_service` (Orion-LD `AgriParcelOperation` with `status=in_progress`), `POST /operations/session/start` returns **409** `ACTIVE_OPERATION_CONFLICT` when another active session exists, `GET /operations/active` for web/mobile. **Generate:** trajectory **alternatives** (`alt-0..`, headings +0°/+45°/+90°), `selected_alternative_id` + persisted `routingVariantId` Property on operation entity. **Web:** SDM pre-flight gaps by operation type, required tractor+implement for execute, active-session banner, handoff copy-ID panel, trajectory comparator + apply, start blocked when another active (client + server). **ZoningTab:** parcel dropdown + optional manual URN override. **Mobile:** active-session card, start **Alert** confirmation, queue flush feedback i18n, `getActiveRoutingOperation`, 409 messaging. **Verification:** `pytest` GIS backend `tests/test_operations_api.py` + critical subset → **39 passed**; `pnpm lint` / `tsc` in `nkz-module-gis-routing`; mobile `pnpm exec eslint` on touched files (pre-existing inline-style warning on UDP dot). |
| **GIS-SOTA-4** | **GIS Routing Machinery + VRA source + UX traceability (PR-1 + PR-2 + PR-4 + PR-5 workspace)** | **In progress 2026-05-07 (workspace)** | `nkz-module-gis-routing`: `GET /equipment` now returns canonical `machine_role` (`tractor|implement|unknown`) and routing generation accepts/persists `coupling_model` (`rigid` only; articulated guarded with explicit 400). Routing UI separates tractor/apero by role, lists unclassified machines, and exposes coupling selector with explicit “articulated not supported yet”. `nkz/apps/host` Entity Wizard (`ManufacturingMachine`) captures role + SDM kinematics/dimensions (`trackWidth`, `wheelbase`, `gpsOffsetX/Y/Z`, `hitchType`, `hitchOffsetX`, `implementLength`, `implementOffsetX`, `implementWidth`, `steeringType`, `steeringAxles`) and writes them as NGSI-LD Properties; `category` derived from role. VRA source selector (`orion` vs `external`) added in routing UI, plus external file ingest endpoint (`POST /zones/external/ingest`) for GeoJSON/CSV and generation payload support (`vra_source`, `external_zone_features`). PR-5 UX hardening adds operation traceability panel (operation id, trajectory variant, coupling model, VRA source, tractor/apero ids) and richer operations list context (parcel + width). Verification: `pnpm typecheck` (`nkz-module-gis-routing` + `nkz/apps/host`), backend syntax check (`python3 -m py_compile .../routing.py`), backend tests (`PYTHONPATH=... .venv/bin/pytest tests/test_api.py -q` → 5 passed), and no lints in touched files. |
| **LIDAR-REF-1** | **LiDAR refactor Orion-first (phase 1)** | **Done 2026-04-08** | `nkz-module-lidar`: jobs/layers moved to Orion-LD (`DataProcessingJob` + `DigitalAsset`), runtime SQL writes removed from API/worker flow, CORS wildcard removed, coverage index switched to read-only GeoJSON catalog, migration script added (`backend/scripts/migrate_legacy_to_orion.py`). |
| **LIDAR-SOTA-2** | **LiDAR SOTA EU+UK implementation (phase 2)** | **Done 2026-04-08** | Implemented worker-based zero-trust ingestion, backend geodesy validator + ECEF reprojection fail-fast, EU+UK bbox anti-outlier check, PROJ persistent cache env/PVC, and direct MinIO tiles delivery path. Pending ops: validate public MinIO endpoint DNS and bucket CORS policy in production. |
| ~~**DOC-AGENTS-1**~~ | ~~Reorganize workspace agent docs (context vs operational router)~~ | **Done 2026-04-08** | `AGENTS.md` rewritten as execution router by task type/tool; `CLAUDE.md` retained as full context; `.ai/CURRENT_STATE.md` updated with role split. |
| ~~**VEG-UX-1**~~ | ~~**Vegetation Health — tabla unificada + fórmulas custom**~~ | **Deployed 2026-04-27** | Commit `592677c` deployed: K8s pods restarted with new `vegetation-prime-api:latest` (digest `sha256:efd9a760`, pulled via `docker save` + `k3s ctr images import` due to CPU saturation on GHCR direct pull). IIFE bundle uploaded from local `dist/nkz-module.js` (built 2026-04-26, 548KB) to MinIO `modules/vegetation-prime/nkz-module.js` (ETag `dc3feffb`). API health: `{"status":"healthy","module":"vegetation-prime"}`. All 3 pods Running 1/1. |
| ~~**MOB-SYNC-1**~~ | ~~nkz-mobile: sync unificado WatermelonDB~~ | **Done 2026-04-10** | `/api/mobile/sync` removed from entity-manager. Single contract: `GET`+`POST` `/api/core/sync/vectorial` (pull with `id` per row, `collections=`, ms timestamps; push with Orion PATCH validation + `experimentalRejectedIds` by table). API gateway proxies POST. Client: `sync.ts`, schema v3 parcels-only + migration, `.env.example`. |

### Frontend host — i18n (SDK + locales) — **Done 2026-03-29**

> **Merged:** PR **#105** a `main` (init estable `NekazariI18nProvider` + `hostI18nConfig`, restauración de namespaces `weather` / `wizard` / `machines` / `livestock` / `layout` / gaps `sensors`, claves `navigation.entities` & `navigation.control_center`). **Ops:** tras cada release del host, `kubectl rollout restart deployment/frontend-host -n nekazari` y comprobar `https://nekazari.robotika.cloud` (hard refresh).

---

## AgriEnergy (nekazari-module-agrienergy)

| # | Task | Estado | Notas |
|---|------|--------|-------|
| AGE-2 | **POST /simulate-period** | Pendiente | Implementar endpoint según `nekazari-module-agrienergy/docs/SIMULATE_PERIOD_SPEC.md`: límite 96 pasos si include_biology, pre-index series O(1), state carry-over. |
| AGE-3 | **Phase 8.2 (Odoo/N8N)** | Bloqueada | Job de agregación diaria bloqueado hasta documentar método de cálculo de `generation_wh` (hardware o integración numérica sobre measured_w). FinBridgeEmitter y webhook listos. |
| AGE-4 | **Tests backend (recomendado SOTA)** | Pendiente | Añadir tests: AlgorithmEngine (evaluate_rule con un preset + fail-safe), GET /algorithms devuelve lista, PATCH algorithm con id resuelve preset. Opcional: /notify con Intelligence mockeado. |
| AGE-5 | **Frontend Fase 2: visualización avanzada** | Pendiente | Shadow visualization en Cesium, panel estrés/biología, gráficas temporales (uPlot/Chart.js). |
| AGE-6 | **Frontend Fase 3: editor de reglas** | Pendiente | Editor visual JSON Logic drag-and-drop, audit trail de cambios de algoritmo. |
| AGE-7 | **Frontend Fase 4: FinBridge/Intelligence** | Bloqueada (AGE-3) | Dashboard Odoo integrado, predicciones Intelligence para producción energética. |

---

## Zulip Communications Module (nekazari-module-zulip) — Replaces Mattermost

> **Repo:** `nkz-module-zulip/` (local), target `nkz-os/nkz-module-zulip` (GitHub)
> **Architecture:** Zulip (Apache 2.0) + custom provisioner (Flask). One realm per tenant. OIDC via Keycloak.
> **RAM budget:** ~1.5GB request / ~3GB limit (Zulip+RabbitMQ+Memcached). 4.4GB available on server (2026-04-18).
> **Internal docs:** `nkz-module-zulip/.ai/` (gitignored)

| # | Task | Estado | Notas |
|---|------|--------|-------|
| ZULIP-1 | **Fase 1 — PoC técnico** | **In progress 2026-04-18** | Server deployed and running. RAM ~2.15GB total. DNS live. Frontend IIFE deployed to MinIO. Marketplace registration in migration 064 (PR#130 merged). Pending: run migration on server, OIDC, webhook test. See `.ai/FASE1_CHECKLIST.md`. |
| ZULIP-2 | **Create GitHub repo + CI** | **Done 2026-04-18** | `nkz-os/nkz-module-zulip` created, 6 commits pushed. CI pending. |
| ZULIP-3 | **Deploy PoC on server** | **Done 2026-04-18** | All pods running. DNS live. Frontend IIFE in MinIO. ArgoCD gitops merged (PR#130). `VITE_ZULIP_URL` in host entrypoint. tsearch permanent fix merged (PR#131 — PG init container). **OPS PENDING:** run migration 064, restart frontend-host, verify marketplace. |
| ZULIP-4 | **OIDC integration** | **Prepared** | Keycloak client script: `scripts/keycloak-create-zulip-client.sh`. OIDC env vars commented in deployment YAML (uncomment after secret exists). **OPS:** run script → patch secret → uncomment OIDC → redeploy. |
| ZULIP-5 | **Webhook validation** | Pendiente | Send test webhook from n8n → Zulip topic, measure latency. Target <2s. |
| ZULIP-6 | **Mobile WebView test** | Pendiente | Test Zulip web in mobile WebView, verify long-polling >10min. |
| ZULIP-7 | **Fase 1 decision gate** | Pendiente (blocked: ZULIP-3..6) | RAM ok? Latency ok? Mobile UX ok? → proceed to Fase 2 or evaluate alternatives. |
| ZULIP-8 | **Fase 2 — Module development** | Pendiente (blocked: ZULIP-7) | Connectors (FIWARE, n8n, CEP), zulip-producer-lib, tenant auto-provisioning, integration tests. 4-6 weeks. |
| ZULIP-9 | **Fase 2.5a — NKZ embedding** | Pendiente (blocked: ZULIP-8) | `/communications` IIFE iframe + mobile WebView tab + SSO injection. 1-2 weeks. |
| ZULIP-10 | **Fase 2.5b — CesiumJS contextual** | Pendiente (blocked: ZULIP-9) | Side panel in 3D viewer, unread indicators, reply from geo context. 4-6 weeks. Differentiator. |
| ZULIP-11 | **Fase 3 — Mattermost deprecation** | Pendiente (blocked: ZULIP-9) | Announce, export tool, remove from catalog, cleanup DB/MinIO. |

---

## Script Cleanup & Job Migration (Audit 2026-03-06)

> Los siguientes scripts manuales han sido eliminados por violar el protocolo GitOps. Deben ser migrados a **Kubernetes Jobs** o **Helm Hooks** en la carpeta `gitops/`.

| Script Eliminado | Proceso Requerido | Prioridad |
|------------------|-------------------|-----------|
| `backup-database.sh` | CronJob (Velasero/Native) | Alta |
| `keycloak-*.sh` | Keycloak Config CLI / JSON Import | Media |
| `deploy-platform.sh` | ArgoCD Application Set | Crítica |
| `create-secrets-*.sh` | Mozilla SOPS / K8s Secrets | Crítica |
| `manage-tenants.sh` | Admin API (Tenant Webhook) | Media |
| `restore-database.sh` | Disaster Recovery Job | Alta |

---

## MAP — Minimum Adoptable Product

> **Objetivo**: que una entidad relevante (cooperativa, empresa energética, centro de investigación) pueda evaluar Nekazari en <1 hora, entender qué hace, levantarlo, y ver un caso de uso completo funcionando.
>
> **Modelo de licencia**: AGPL-3.0 para el core (innegociable). SDK bajo Apache-2.0 para facilitar creación de módulos propietarios por terceros. Los módulos se comunican vía API → no son obra derivada → cualquier licencia.
>
> ~~**TODO licencia**: Cambiar licencia de `@nekazari/sdk` y `@nekazari/ui-kit` de AGPL-3.0 a Apache-2.0 en `packages/sdk/package.json` y `packages/ui-kit/package.json`. Añadir fichero `LICENSE-APACHE` en cada paquete. Actualizar README del SDK indicando explícitamente que módulos de terceros pueden usar cualquier licencia. Publicar nueva versión en npm con licencia corregida.~~ — **DONE 2026-03-24** (Commit cf20d67, a la espera de `publish-sdk.yml` CI).
>
> **Fase 1 — COMPLETADA** (MAP-1 a MAP-5). Ver archivo.
>
> **Ninguna tarea fuera de MAP es prioritaria hasta completar la fase 2.**

### Fase 2 — Módulos priorizados por madurez e impacto

> Clasificación basada en auditoría real de código (LOC, Dockerfile, frontend, k8s, estado de despliegue).
> **TIER 1 = producto demostrable hoy. TIER 2 = estratégico a corto plazo. TIER 3 = prometedor pero no demo-ready. TIER 4 = aparcar o descartar.**

#### TIER 1 — Producto (funcional end-to-end, demostrable)

| # | Módulo | LOC | Estado | Notas |
|---|--------|-----|--------|-------|
| ~~MAP-10~~ | ~~**Vegetation Health**~~ | ~18.5K | **Completado 2026-05-01** | v2.0.0 deployed. 43 files, +4K/-1.1K lines. Backend: cropSeason API, VRA zoning wired (enriched GeoJSON + Orion-LD), export API (GeoJSON/Shapefile/CSV), custom formulas PATCH. Frontend: standalone page simplificado (9→4 secciones), unified viewer slots (context-panel + timeline + layer), 6-language i18n, mobile auth (NKZ_AUTH_INJECTION), cross-module links with GIS-routing. Docs: VRA API contract. Deferred: TiTiler migration (Phase 4 ACTION_PLAN), integration tests, service worker offline.
| MAP-5b | **DataHub** | ~3.4K | **Funcional** | Timeseries canvas, uPlot, CSV/Parquet export, stats, AI predictions. **Desplegado y verificado en prod** (ver **DH-DEP-1**). |
| — | **Weather + Risks** (core) | core | **Funcional** | 6 modelos de riesgo, OpenMeteo+AEMET, GDD, soil moisture. No son módulos externos pero son el vertical agrícola. |

#### TIER 2 — Estratégico (código real, falta pulir o desplegar)

| # | Módulo | LOC | Estado | Notas |
|---|--------|-----|--------|-------|
| MAP-6 | **Odoo ERP** | ~4.7K | **Running (fixed 2026-05-29)** | Backend + Odoo 16 + postgres-odoo pods all 1/1 Running. Bad Gateway fixed (NetworkPolicy port 8069/8072). Frontend rewritten with @nekazari/ui-kit. Slot moved to context-panel. SSO auto-config via XML-RPC. ArgoCD (PR#11). Falta: verificar SSO e2e, tenant provisioning flow test, comunidades energéticas (N2). |
| MAP-6b | **N8N Integration Hub** | ~9K | **Phase 1+2 completado 2026-05-10** | Per-tenant n8n config (external + auto-provisioning). Stripe addon 4.99€/mes. K8s provisioner por tenant. Grace period 30d. Backend Fernet crypto, frontend 6-state provisioning panel. Tenant-webhook forwarding endpoints (PR#227 nkz). Docs: `docs/connect-n8n.md`. Repo: `nkz-os/n8n-module-nkz`. |
| MAP-7 | **EU Elevation / Terrain** | ~4K | **Desplegado 2026-04-30** | SOTA overhaul completo: 20 fuentes DEM EU+UK, ETL pipeline con fallback Copernicus S3, CLC overlay, multi-tier (Cesium World/MapTiler/Custom/IDENA/IGN). Fernet encryption para tokens. Ingesta regional funcional. Integrado con host NKZ (respeta IGN/IDENA). |
| MAP-7b | **LiDAR Point Cloud** | ~6K | **Parcial** | Frontend IIFE desplegado, backend en cluster. Falta: e2e testing, tree entity extraction (M3). **Diferenciador técnico fuerte.** |
| MAP-7c | **Catastro España** | ~6.2K | **Parcial** | Backend a 0 réplicas (CPU). Regional (España), pero demuestra integración con datos oficiales. ArgoCD (PR#11). |

#### TIER 3 — Prometedor (scaffold o early-stage, no demo-ready)

| # | Módulo | LOC | Estado | Notas |
|---|--------|-----|--------|-------|
| MAP-8 | **AgriEnergy Orchestrator** | ~3.5K | **Desplegado** | Frontend profesional (14 ficheros, 2257 LOC). Backend con JSON Logic, algoritmos, parques, MQTT, simulación. Pendiente: AGE-2 simulate-period. |
| MAP-8b | **Crop Health Engine** | ~3.5K | **Desplegado 2026-05-04** | v1.0.0 en `nkz-os/nekazari-module-crop-health`. 9 motores (CWSI, MDS, Water Balance, Thermal, Vigor, Composite FAO-33, Yield Gap, WUE, Phenology Progress). 5 modelos epidemiológicos (LWD NHRH/CART/DPD, Magarey, Mills, TomCast, Gubler-PM). Schemas con provenance (PhenologyProvenance, CI, alternatives). Context client con circuit breaker hacia BioOrchestrator. Redis sliding window para sensores. WUE con detección de fuente (MQTT→operational, declared→advisory, none→suppressed). CropHealthWidget con Composite Stress + Yield Gap. DiseaseRiskWidget + DiseaseRiskContextPanel (slot context-panel). i18n completa en 6 idiomas (0 strings hardcoded). Auto-refresh 5min. Estados vacíos accionables. Bundle IIFE en MinIO. Backend en K8s. |
| MAP-8c | **BioOrchestrator** | ~4.2K | **Desplegado 2026-06-17** | v0.1.0 + data normalisation pipeline en `nkz-os/nkz-module-bioorchestrator`. Backend FastAPI + Neo4j + IkerKeta. 25 conectores IkerKeta. **Normalización de datos**: `normalization_registry.py` con 9 traits BSL→AGROVOC, 55 ubicaciones normalizadas, 27 EPPO codes, escalas BSL 1-9→0-1. `base_ingester.normalize_nodes()` ejecutado automáticamente en toda ingesta. **Backfill completado**: 33.378 VarietyTrial, 181 ManagementTrial unificados (mergeKeyNormalized, varietyNormalized, agronomicTraitsUnified, diseaseScoresUnified, locationNormalized, validationPassed). **56 tests** (45 unit + 11 integración). Bundle IIFE en MinIO (62KB). Backend en K8s (8420). |
| MAP-9 | **Robotics** | ~3.5K | **No operativo** | Frontend IIFE listo. Sin backend desplegado. Necesita basabot real (→ M1). |
| MAP-9b | **PVLib Solar** | — | **No existe** | Módulo nuevo: paneles como entidades, simulación producción. Atractivo para energético pero hay que crearlo desde cero. |

#### TIER 4 — Aparcar (no invertir tiempo hasta que TIER 1-2 estén sólidos)

| Módulo | Razón |
|--------|-------|
| ~~**Mattermost**~~ | **Reemplazado por Zulip** (2026-04-18). Ver ZULIP-1. |
| ~~**Carbon**~~ | ~~Skeleton sin código funcional. Reevaluar tras adopción~~ **v0.1.0 SOTA completado 2026-05-03** |
| ~~**CUE/SIEX**~~ | **Fases 1-4 completadas (2026-05-04).** Backend NGSI-LD SOTA + PostGIS + IUWS + state machine + Anti-Corruption Layer (XML/XSD). Frontend IIFE (7 tabs, 70KB). 53 rutas, 44 commits. Desplegado en prod (pod `cue-backend`). Repo: `nkz-os/nkz-module-cue`. Pendiente solo trámites externos (XSD oficiales, certificados). Ver `memory/cue-module-state.md`. |
| **Backup module** | Código overhauled pero no desplegado. Prioridad ops, no producto |
| **VPN/Device Mgmt** | Verificado en producción (2026-05-02). ZTP, rate limiting, RLS, audit log funcionales. |

### Fase 3 — Hacer adoptable (después de Fase 1+2)

| # | Task | Estado | Notas |
|---|------|--------|-------|
| MAP-11 | **Helm chart** para K8s — el público real son equipos con clusters. Helm > docker-compose para producción. | Pendiente | Después de MAP-1 |
| MAP-12 | **Demo online** — instancia de solo lectura donde un evaluador vea datos reales sin instalar nada. Podría ser la instancia actual de producción con un tenant `demo` read-only. | Pendiente | Reduce fricción de evaluación a 0 |
| MAP-13 | **Caso de éxito documentado** — aunque sea propio: "Finca X con Y parcelas, Z sensores, monitoreada durante N meses". Con datos reales, screenshots, métricas. | Pendiente | Credibilidad |
| MAP-14 | **SDK docs + tutorial "crea tu primer módulo en 15 min"** — incluido en docs site pero como sección dedicada con video/gif. Es la propuesta de valor del ecosistema. | Pendiente | Parte de MAP-2 pero más profundo |

---

## Platform Integration Gaps — Auditoría Core vs Crop-Health/BioOrchestrator/Vegetation (2026-04-30)

> Descubierto durante la auditoría completa de los módulos crop-health, bioorchestrator y vegetation-prime. Estos gaps no son bugs de módulo — son carencias de la plataforma core que limitan el valor de los módulos existentes.

### Duplicaciones (misma lógica en múltiples sitios)

| # | Gap | Ubicaciones | Impacto |
|---|-----|------------|---------|
| DUP-1 | **Estrés hídrico — 3 implementaciones** | `risk_models/water_stress_model.py` (risk-worker batch), `engines/water_balance.py` (crop-health real-time), `engines/water_stress.py` (crop-health CWSI) | Sin severidad unificada. Si risk-worker dice "moderado" y crop-health dice "critical", ¿a quién cree el usuario? |
| DUP-2 | **Weather — 4 consumidores, cada uno por su lado** | weather-worker (escribe DB), risk-worker (lee DB directo), crop-health (lee DB directo), vegetation-prime (llama a Open-Meteo directo, bypassea la plataforma) | Sin weather API. Si cambia el schema de `weather_observations`, se rompen 3 servicios. Vegetation ignora datos ya ingeridos. |
| DUP-3 | **Spray suitability — 3 implementaciones** | `risk_models/spray_suitability_model.py`, `risk_models/wind_spray_model.py`, `services/agro_status_service.py` (telemetry-worker) | Semáforos independientes sin consenso. |
| DUP-4 | **GDD — 2 implementaciones** | weather-worker `metrics_calculator.py`, vegetation-prime `weather_service.py` | Cálculo duplicado, umbrales inconsistentes. |

### Fricciones (puntos donde los módulos no encajan)

| # | Gap | Detalle | Prioridad |
|---|-----|---------|-----------|
| FRIC-1 | **CropHealthAssessment es huérfano** | crop-health publica en Orion-LD pero NADIE se suscribe. El telemetry-worker no persiste estas entidades a TimescaleDB. Si Orion-LD se reinicia, el histórico de evaluaciones desaparece. | **Alta** |
| FRIC-2 | **No hay weather REST API** | weather-worker solo escribe en TimescaleDB. No expone endpoint de consulta. Cada consumidor se acopla al schema SQL directamente. Viola la regla de "API, no BBDD directa". | **Alta** |
| FRIC-3 | **Sistema de alertas roto** | `weather_alerts` (AEMET) se ingieren pero NADIE las lee. `notification_channels` (email/push) se configuran pero NADIE las envía. `email-service` existe pero no está conectado a ningún pipeline. Solo funcionan los webhooks. | **Alta** |
| FRIC-4 | **Sin bus de eventos entre módulos** | Vegetation detecta NDVI bajo → risk-worker no se entera. Risk-worker detecta riesgo → crop-health no ajusta. Crop-health evalúa severidad → vegetation no re-analiza. Módulos en silos. | **Media** |
| FRIC-5 | **Tres mecanismos de suscripción paralelos** | Telemetry-worker (NGSI-LD subscriptions), risk-api (tenant_risk_subscriptions), vegetation-prime (scheduling propio). Sin modelo unificado. | **Media** |

### Sync Gaps (inconsistencias de datos)

| # | Gap | Detalle | Prioridad |
|---|-----|---------|-----------|
| SYNC-1 | **Catálogo de riesgos en dos fuentes** | Frontend: `riskCatalog.ts` (22 entradas TypeScript). Backend: `admin_platform.risk_catalog` (PostgreSQL). Sin sincronización automática. | **Media** |
| SYNC-2 | ~~**FIWARE_NATIVE_MODE**~~ | → **Done 2026-05-03**. Vegetation DataHub adapter ahora lee `telemetry_events` en native mode. Commit `a8aaffa`, PR #6 mergeado, FIWARE_NATIVE_MODE=true en prod. |
| SYNC-3 | ~~**Sin convención de entity ID entre módulos**~~ | → **Done 2026-05-03**. `refAgriParcel` como estándar documentado en PLATFORM_CONVENTIONS.md §9. Todos los módulos alineados. Commit `2cd5f95`, PR #166 mergeado. |

### Acciones recomendadas — TODAS RESUELTAS

1. ~~**FRIC-1**~~ → **Done 2026-04-30**. CropHealthAssessment subscription in telemetry-worker.
2. ~~**FRIC-2**~~ → **Done 2026-05-01**. Weather REST API (3 endpoints) in timeseries-reader. All 4 consumers migrated.
3. ~~**FRIC-3**~~ → **Done 2026-04-30**. Email alerts via risk-orchestrator → email-service.
4. ~~**FRIC-4**~~ → **Done 2026-05-01**. Redis Streams `crop:events` + `risk:events`. Cross-module event bus active.
5. ~~**DUP-1**~~ → **Done 2026-05-02**. Water stress unified: CWSI → MDS → weather cascade in risk-worker.
6. ~~**SYNC-1**~~ → **Done 2026-05-03**. Risk catalog: API canonical source, TypeScript fallback. Commit `9876786`.
7. ~~**DUP-2**~~ → **Done 2026-04-30**. Vegetation WEATHER_MODULE_URL → timeseries-reader. Commit `68f88a2`.
8. ~~**DUP-3**~~ → **Done 2026-05-03**. agro_status_service refactored to use Weather API. Commit `d4d1482`, PR #159.
9. ~~**FRIC-5**~~ → **Done 2026-05-03**. Subscription architecture documented. Dynamic extension via env var. Commit `8f2941b`.
10. ~~**SYNC-2**~~ → **Done 2026-05-03**. DataHub adapter reads telemetry_events in native mode.
11. ~~**SYNC-3**~~ → **Done 2026-05-03**. Entity ID conventions documented.

### Crop Health Ecosystem — Built & Deployed (2026-04-30 to 2026-05-04)

| Component | Details |
|-----------|---------|
| **Crop-Health engines** | 9 motors: CWSI, MDS, Water Balance, Thermal, Vigor, Composite (Ky FAO-33), Yield Gap, WUE, Phenology Progress |
| **Epidemiological models** | 5 models: LWD estimator (NHRH + CART/DPD), Magarey (mildew), Mills (scab), TomCast (alternaria), Gubler (PM) |
| **BioOrchestrator** | 15 REST endpoints, Neo4j (16 entity types, 33.378 VarietyTrial, 5.854 variedades, 206 TrialSites, 27 Species, 181 ManagementTrial), Pipeline de normalización (traits AGROVOC, escalas 1-9→0-1, ubicaciones canónicas) |
| **IkerKeta** | 25 connectors (7 new), GitHub public, pip installable, licenses verified (CC0/CC-BY/Copernicus/EEA/EC) |
| **Weather API** | 3 endpoints (current, historical, GDD), 4 consumers migrated (crop-health, risk-worker, vegetation, agro_status) |
| **Event Bus** | Redis Streams `crop:events` + `risk:events`, email + push + webhook dispatch |
| **UI** | CropHealthWidget (dashboard, 7 indicators), CropHealthContextPanel (3D viewer, 6-lang i18n, WUE real, auto-refresh), CropHealthLayer (CesiumJS heatmap), DiseaseRiskWidget (crop+parcel visible), DiseaseRiskContextPanel (slot context-panel per parcel), RecommendationsPanel (11 sections), Scenario Simulator, PipelineRunner (progress bar + history table), PhenologyBrowser (dynamic species dropdown) |
| **DataHub** | CropHealthAssessment whitelisted for auto-visualization in timeseries charts |
| **i18n** | 6 locales (es, en, ca, eu, fr, pt) in both BioOrchestrator and Crop-Health |
| **Mobile** | Push notifications (token registration + Android channels + tap handler), Expo build ready |
| **N8N + Zulip** | Workflow JSON (Webhook → LLM → Zulip message), setup documented |

### IkerKeta — 25 conectores

| # | Conector | Dominio | Licencia | Estado |
|---|----------|---------|----------|--------|
| 1 | AGROVOC (FAO) | Taxonomía | CC-BY 4.0 | Active |
| 2 | EPPO | Fitosanitario | Consulta previa | Active |
| 3 | EcoCrop (FAO) | Edafoclimático | Open | Active |
| 4 | USDA Plants | Taxonomía | Public Domain | Active |
| 5 | USPEST | Fenología | Open | Active |
| 6 | Companion Planting | Asociaciones | Open | Active |
| 7 | DG SANTE | Regulatory | Open | Active |
| 8 | CABI | Biocontrol | License required | Stub |
| 9 | AgroPortal | Management | Open | Active |
| 10 | FiBL | Organic Inputs | Open | Active |
| 11 | DAD-IS (FAO) | Livestock | FAO | Active |
| 12 | WAHIS | Livestock | Open | Active |
| 13 | Feedipedia | Livestock | Open | Active |
| 14 | GlobalTreeSearch | Forestry | BGCI Open | Active |
| 15 | EUFORGEN | Forestry | Open | Active |
| 16 | Agroforestree | Agroforestry | Open | Active |
| 17 | Forages | Agroforestry | Open | Active |
| 18 | GlobAllomeTree | Forestry | Open | Active |
| 19 | **SoilGrids 2.0** | Suelos | CC-BY 4.0 | **New** |
| 20 | **Copernicus DEM** | Topografía | Copernicus | **New** |
| 21 | **Natura 2000** | Ambiente | EEA Reuse | **New** |
| 22 | **EU Pesticides DB** | Regulatory | EC Public | **New** |
| 23 | **CPVO Varieties** | Variedades | CPVO Public | **New** |
| 24 | **GBIF (CC0/CC-BY)** | Biodiversidad | CC0/CC-BY | **New** |
| 25 | **ERA5 (Copernicus)** | Clima | Copernicus | **New** |
| ~~IUCN~~ | — | — | License incompatible | **Removed** |

---

## Crop Health — Catálogo de Sensores Recomendados

> Para maximizar los indicadores activos por parcela. Cada sensor añade motores específicos. Un tenant sin sensores solo recibe indicadores basados en weather + satélite (confidence baja).

### Sensores para crop-health (tiempo real)

| Sensor | Atributo MQTT | Motores activados | Fidelidad | Proveedores |
|--------|--------------|-------------------|-----------|-------------|
| **Termómetro IR** (canopy) | `leafTemperature` | CWSI, Thermal Stress | `onsite_uncalibrated` | Apogee SI-411, Campbell IR120, DIY MLX90614 |
| **Dendrómetro** (tronco) | `trunkDiameter` | MDS | `onsite_uncalibrated` | Ecomatik DRL26, PhytoSense SD-5 |
| **Sonda TDR** (suelo) | `soilMoisture` | Water Balance | `onsite_uncalibrated` | Campbell CS655, Meter TEROS-12, Truebner SMT100 |
| **Clorofilómetro SPAD** | `spadValue` | Nutrient Sufficiency (futuro) | `onsite_uncalibrated` | Konica Minolta SPAD-502, atLeaf CHL Plus |
| **Estación meteorológica** | `airTemperature`, `relativeHumidity`, `precipitation`, `windSpeed` | Todos (mejora fidelidad de `regional_proxy` a `local_proxy`) | `local_proxy` | Davis Vantage Pro2, Campbell MetSens, Onset HOBO |
| **Contador de riego** | `irrigationVolume` | WUE (operational) | `onsite_calibrated` | Hunter Hydrawise, Rain Bird, Galcon, Any MQTT/Modbus |
| **Sensor de hoja** (wetness) | `leafWetness` | Modelos epidemiológicos (confidence→high) | `onsite_calibrated` | Meter PHYTOS 31, Decagon LWS |

### Sensores para risk-worker (batch)

| Sensor | Atributo MQTT | Modelos activados | Fidelidad |
|--------|--------------|-------------------|-----------|
| **Trampa de plagas** (automática) | `pestCount` | GDD Pest Models (confidence→high) | `onsite_uncalibrated` |
| **Sensor de pH suelo** | `soilPH` | Match suelo-cultivo (confidence→high) | `onsite_uncalibrated` |
| **Sensor NPK suelo** | `soilNitrogen`, `soilPhosphorus`, `soilPotassium` | Fertilizer planner (confidence→high) | `onsite_uncalibrated` |

### Cómo conectar un sensor

1. Dashboard → "+ New Sensor" → Seleccionar `AgriSensor`
2. Importar Device Profile JSON desde `nkz-module-crop-health/templates/` (IR Canopy, Dendrometer, o TDR Probe)
3. El wizard genera credenciales MQTT
4. Configurar el datalogger para publicar en `/<tenant_apikey>/<device_id>/attrs`
5. Asociar el sensor a la parcela vía `refAgriParcel`

### Sin sensores — ¿qué obtengo?

| Indicador | Fuente | Confianza |
|-----------|--------|-----------|
| Water Balance | Weather API (ETo, precip) + FAO-56 Kc | 0.70 |
| Thermal Stress | Weather API (T min/max) | 0.70 |
| Vigor | Sentinel-2 NDVI (vegetation-prime) | 0.65 |
| Composite Stress | Combinación de los anteriores | 0.70 |
| Yield Gap | CWSI estimado de water balance | 0.60 (low) |
| WUE | Suprimido (sin datos de riego) | — |
| CWSI | Suprimido (sin sensor IR) | — |
| MDS | Suprimido (sin dendrómetro) | — |

---

## Priority Roadmap (tareas fuera de MAP)

> Estas tareas se abordan **después** de completar MAP Fase 1+2, o en paralelo si no compiten por recursos.

| Prio | Ref | Area | Task | Effort |
|------|-----|------|------|--------|
| P1 | M4 | Feat | Mobile HMI Retrofit (Dual Theme en UI-Kit, Vector Sync Celery, PMTiles API) | Alto |
| P2 | O7 | Ops | **Vegetation Prime storage optimization** — ~~25GB COGs~~ Band cleanup implementado 2026-04-27: Redis counter deletea bandas crudas del bucket tenant tras último índice calculado (caché global las conserva). ILM MinIO reducido 60d→30d para tenant COGs. Disco servidor 96GB al 85%. | Alto |
| P3 | O8 | Ops | **Ampliar disco servidor o mover MinIO a S3 externo** — 96GB insuficiente a medio plazo. K3s disk-pressure threshold bajado a 5% como medida temporal (2026-03-26). | Medio |
| P8 | O4 | Ops | Prometheus + Grafana — aplicar manifests | Medio |
| P9 | O5 | Sec | Network policies — testing + aplicar | Medio |
| P10 | O6 | Ops | MinIO ILM TTL en IaC | Bajo |
| P10 | S1b | Sec | LiDAR Cesium tile auth cross-origin (no crítico hoy) | Bajo |
| P11 | S2 | Sec | K8s security contexts uniformes | Medio |
| P12 | S3 | Debt | 454 TypeScript `any` types | Alto (progresivo) |
| ~~P13~~ | P1 | Feat | ~~Carbon module~~ **v0.1.0 SOTA completado 2026-05-03** — 145 tests, deploy listo | — |
| P14 | P4 | Feat | Intelligence + N8N — testing integración | Medio |
| P16 | P6 | Feat | Meteorológico — verificar datos + UI | Medio |
| ~~P17~~ | P7 | Feat | ~~EU Elevation map — verificar funcionamiento~~ DONE 2026-03-25 (overhaul completo) | — |
| — | E2 | Data | `refParent` → `isPartOf` (diferido) | Bajo |
| — | E4 | Data | ~~Ingesta datalogger Datak/Campbell~~ **IoT pipeline funcional** (2026-03-26): DaTaK→MQTT→IoT Agent 3.13.0 (NGSI-LD native)→Orion-LD→suscripción→telemetry-worker→TimescaleDB. PR#86 merged; **PR#87 merged** (asyncpg pool — alineado con IOT-2). Pendiente: IOT-3 (verificar UI), Campbell integration. | Medio |
| — | E6 | Data | **DaTaK→NKZ flow simplification** — simplificar provisioning de sensores DaTaK en NKZ (pocos clics, nativo). Mejorar `cloud_sync.py` mapping, push DaTaK repo. | Medio |
| — | E7 | Feat | **DataHub sensor visualization** — conectar datos live DaTaK al módulo DataHub para visualización interactiva de series temporales. Módulo existe (MAP-5b, T1 funcional), falta wiring con telemetry pipeline. **Depende de despliegue estable del módulo (→ DH-DEP-1).** | Alto |
| — | E5 | Arch | Subdivisión de parcelas (decisión diseño) | Decisión |
| — | M3 | Mod | LiDAR — entidades desde nube de puntos | Medio |
| — | V2 | Test | ~~Mattermost~~ → **Zulip PoC** (ZULIP-1) | Medio |
| ✅ | V3 | Feat | SIEX — cuaderno de campo (Fases 1-5 completas) | Alto — Completado 2026-05-04 |
| — | Q1 | Debt | ~690 `console.log` + ESLint `no-console` | Medio |
| — | Q6 | Debt | CesiumPolygonDrawer NDVI flat-color | Bajo |
| — | D2 | Docs | External module installation docs | Bajo |
| — | O2 | Ops | Version pinning para imágenes GHCR propias (semver/sha) | Medio |
| — | O10 | Ops | Migrate Flask dev servers to WSGI (Gunicorn) — **CUE done (S2), catastro-spain done (S3)** | Medio |
| — | O11 | Ops | DNS fallback en netplan (8.8.8.8 + 1.1.1.1) | Bajo |

---

## Module Inventory

### Core Internal Services (host-bundled, `localAddons.ts`)

Páginas internas de la plataforma, bundleadas con el host. Se activan/desactivan por tenant en DB.

| ID | Nombre | Ruta | Estado | Notas |
|----|--------|------|--------|-------|
| `weather` | Meteorología | `/weather` | **Funcional** | OpenMeteo + AEMET. Backend: `weather-worker` (FastAPI). Falta: verificar UI completa, alertas, integración riesgos (→ P6) |
| `risks` | Riesgos Agrícolas | `/risks` | **Funcional** | 6 modelos (Spray, Frost, Wind, WaterStress, GDD×2). Monitor + config + webhooks. Overlay CesiumMap |
| `sensors` | Sensores IoT | `/sensors` | **Funcional** | Health summary bar (online/warning/offline), dynamic readings column, proper NGSI-LD pagination. Queries Orion-LD `AgriSensor` entities directly |
| `predictions` | Predicciones IA | `/predictions` | **Parcial** | Página existe. Depende de `intelligence-service` backend. Verificar estado real (→ P4) |
| `analytics` | Analytics (Grafana) | `/analytics` | **No desplegado** | Embed de Grafana. Grafana no está aplicado en cluster (→ O4) |

### External Modules — Desplegados (IIFE + backend)

| ID | Nombre | Repo local | Ruta API | Frontend | Backend | Tier | Estado | Notas |
|----|--------|------------|----------|----------|---------|------|--------|-------|
| `datahub` | DataHub | `nekazari-module-datahub/` | `/api/datahub` | IIFE ✅ | FastAPI ✅ | **T1** | **Funcional** | Timeseries canvas, uPlot, CSV/Parquet export, stats, AI predictions |
| `vegetation-prime` | Vegetation Health | `nekazari-module-vegetation-health/` | `/api/vegetation` | IIFE ✅ | FastAPI ✅ | **T1** | **Desplegado** | 3 pods running (ArgoCD). Pendiente: TiTiler viewer, SmartTimeline |
| `odoo-erp` | Odoo ERP | `nekazari-module-odoo/` | `/api/odoo` | IIFE ✅ | Python ✅ | **T2** | **Scaled to 0** | Escalado a 0 para liberar RAM (2026-04-30). Falta: SSO e2e |
| `lidar` | LiDAR Point Cloud | `nekazari-module-lidar/` | `/api/lidar` | IIFE ✅ | Python ✅ | **T2** | **Parcial** | Backend desplegado, no probado e2e. Falta: M3 (tree entities) |
| `intelligence` | Intelligence | `nekazari-module-intelligence/` | `/api/intelligence` | Sin frontend | FastAPI ✅ | **T3** | **Parcial** | 1 réplica. Sin frontend IIFE propio. Falta: testing N8N (→ P4) |
| `catastro-spain` | Catastro España | `nekazari-module-cadastral-spain/` | `/api/cadastral-api` | IIFE ✅ | Flask→gunicorn ✅ | **T2** | **Funcional** | 1/1 Running, gunicorn 26.0.0, startupProbe + 256/512Mi, KEYCLOAK_URL interno |
| `agrienergy` | AgriEnergy Orchestrator | `nekazari-module-agrienergy/` | `/api/agrienergy` | IIFE ✅ | FastAPI ✅ | **T2** | **Desplegado** | Motor agrivoltaico, closed-loop, shadow mode. Cookie auth. Pendiente: simulate-period |

### External Modules — Solo frontend (sin backend propio)

| ID | Nombre | Repo local | Frontend | Tier | Estado | Notas |
|----|--------|------------|----------|------|--------|-------|
| `robotics` | Robótica & Telemetría | `nekazari-module-robotics/` | IIFE ✅ | **T3** | **No operativo** | Frontend listo. Sin backend desplegado. Necesita basabot real (→ M1) |
| `n8n-nkz` | N8N Integration Hub | `nkz-module-n8n/` | IIFE ✅ | **T1** | **Phase 1+2 deployed** | Per-tenant n8n (external config + auto-provision). Stripe 4.99€/mes. Backend FastAPI + K8s provisioner. Frontend 6-state provisioning panel. Docs: connect-n8n.md |

### External Modules — No desplegados / Skeleton

| ID | Nombre | Repo local | Tier | Estado | Notas |
|----|--------|------------|------|--------|-------|
| `nekazari-module-eu-elevation` | EU Elevation | `nkz-module-eu-elevation/` | **T2** | **Desplegado 2026-04-30** | Multi-tier terrain (Cesium World/MapTiler/Custom), 20 DEM sources EU+UK, ETL pipeline Copernicus S3, CLC overlay, Fernet token encryption. No interfiere con IGN/IDENA del host. |
| `bioorchestrator` | BioOrchestrator | `nekazari-module-bioorchestrator/` | **T2** | **Desplegado 2026-06-17** | v0.1.0 + data normalisation pipeline. Backend FastAPI + Neo4j + IkerKeta. Frontend IIFE (Sources, Pipeline, DAD-IS). Endpoints: phenology-params, species, pipeline, heat-tolerance, nutrient-profile, soil, rotation, recommendations. 25 conectores IkerKeta. Grafo: 33.378 VarietyTrial, 5.854 variedades, 206 TrialSites, 181 ManagementTrial, 27 Species. Pipeline normalización: traits AGROVOC, escalas 1-9→0-1, ubicaciones canónicas, mergeKey unificado. 56 tests. |
| `nkz-module-soil` | Soil Intelligence | `nkz-module-soil/` | **T2** | **Hito 0-4 completados (workspace)** | Backend FastAPI + ARQ worker: SoilGrids, AWC, kriging/IDW interpolación, raster generation (MinIO), CSV batch upload. Frontend MF 2.0 (ModulePage con Dashboard/Manual/CSV/History tabs + slots). 109 tests unitarios. CI workflow `.github/workflows/build-push.yml` (test + build frontend IIFE + Docker API/Worker → GHCR). ArgoCD manifest `nkz/gitops/modules/soil-module.yaml`. Estructura migrada a patrón estándar (frontend en raíz). Typecheck + build verdes. **Pendiente**: push a GitHub + GHCR publish + deploy cluster. |
| `nkz-billing-module` | Billing Control | `nkz-billing-module/` | — | **Desplegado 2026-05-24** | Secret `nkz-billing-module-secrets` creado. Imagen builteada y pusheada a `ghcr.io/nkz-os/`. Pod 1/1 Running. Stripe + Keycloak IAM pendiente de configurar (API keys). |
| `carbon` | Carbon Intelligence | `nekazari-module-carbon/` | **T1** | **v0.1.0 SOTA** | 3-tier engine, RothC, GHG, MRV, IIFE frontend, 145 tests, deployed 2026-05-03 |
| `vpn` | Device Management | `nekazari-module-vpn/` | **T3** | **Verificado 2026-05-02** | ZTP funcional, rate limiting Redis, RLS PostgreSQL, audit log, cuotas, UX PlatformAdmin cross-tenant |
| `backup` | Backup & DR | `nekazari-module-backup/` | **T4** | **No desplegado** | Código overhauled |
| ~~`mattermost`~~ | ~~Communications~~ | ~~`nekazari-module-mattermost/`~~ | ~~T4~~ | ~~No desplegado~~ | **Replaced by Zulip (2026-04-18)**. See ZULIP-1. |
| `zulip` | Communications | `nkz-module-zulip/` | **T2** | **Funcional** | 1/1 Ready (2026-05-24). startupProbe 20min, necesita varios intentos de arranque por timeout intermitente de PostgreSQL durante bootstrap Django. OIDC SSO configurado. |

### Templates

| Nombre | Repo local | Estado |
|--------|------------|--------|
| Module Template | `nekazari-module-template/` | **Funcional** — IIFE, `@nekazari/module-builder`, `init-module.sh` |
| Module Template (interno) | `nkz/module-template/` | **Funcional** — Sincronizado con el público |

### Core Backend Services

| Servicio | Estado | Notas |
|----------|--------|-------|
| api-gateway | **Funcional** | Flask, JWT, CORS, rate limiting, httpOnly cookie |
| entity-manager | **Funcional** | NGSI-LD CRUD, module health, marketplace. Fixed 2026-05-24: 1 worker + startupProbe + 512Mi (#336). |
| weather-worker | **Funcional** | OpenMeteo + AEMET, GDD, soil moisture, delta_t |
| risk-api | **Funcional** | Risk states query API |
| risk-worker | **Funcional** | 6 modelos horarios (batch) |
| risk-orchestrator | **Funcional** | Coordinación y scheduling de risk-worker |
| timeseries-reader | **Funcional** | Historical data from TimescaleDB |
| sdm-integration | **Funcional** | IoT provisioning, entity CRUD, batch creation. Fixed 2026-05-24: 1 worker + startupProbe + 512Mi (#331). |
| tenant-user-api | **Funcional** | Multi-tenant user management |
| tenant-webhook | **Funcional** | Tenant lifecycle, activation codes. Fixed 2026-05-24: 1 worker + startupProbe (#330). |
| email-service | **Funcional** | SMTP notifications |
| mqtt-credentials-manager | **Funcional** | Dynamic MQTT provisioning |
| isobus-bridge | **Retirado 2026-07** | Code complete, never deployed to prod (0 pods, no ArgoCD app). Superseded by Connectivity Device Profiles (ISOBUS) + planned OEM-cloud module. |
| intelligence-service | **Parcial** | 1 réplica. Monitorear CPU |
| keycloak | **Funcional** | OIDC/OAuth2, RS256, custom image 26.4.7 |
| orion-ld | **Funcional** | FIWARE Context Broker |
| postgresql | **Funcional** | TimescaleDB, RLS per tenant. Fixed 2026-05-24: 512Mi→4Gi + 1000m CPU (#333). Ya no OOMKilled. |
| mongodb | **Funcional** | Orion-LD entity registry |
| redis | **Funcional** | Cache, job queue |
| minio | **Funcional** | Object storage (frontend, uploads) |
| mosquitto | **Funcional** | MQTT broker |
| titiler | **Funcional** | COG tile server (vegetation) |
| iot-agent-json | **Funcional** | FIWARE IoT Agent HTTP |
| frontend-static | **Funcional** | nginx → MinIO (MF 2.0 module artifacts at `/modules/`) |
| frontend-host | **Funcional** | Docker pod (GHCR image, main frontend) |
| n8n | **Funcional** | Automation workflows |

---

## Billing Module — Hardening Follow-ups

| # | Priority | Task | Notes |
|---|----------|------|-------|
| B1 | High | **Stripe Test Mode integration suite** — run end-to-end tests against Stripe test environment to verify metadata roundtrip | `nkz-billing-module/backend/` |
| B2 | High | **Webhook extreme concurrency validation (PostgreSQL)** — stress-test duplicate and near-simultaneous webhook deliveries | `nkz-billing-module/backend/`, staging |
| B3 | High | **Tenant Reaper progressive rollout** — deploy reaper in dry-run/log-only mode first | `nkz-billing-module/k8s/reaper/` |
| B4 | High | **Deploy unified expiration source-of-truth (2026-04-25)** | Code merged. Deploy required: tenant-webhook (new `/internal/billing/tenants/<id>/license` + `/internal/tenants/managed`), billing module (new `_notify_tenant_webhook` + `fetch_subscription_period_end`), reaper (A∧B gate). Required env: `INTERNAL_BILLING_SECRET` shared between billing and tenant-webhook (K8s secret), `BILLING_API_URL` in tenant-webhook deployment, `ENABLE_LEGACY_EXPIRATION_NOTIFIER=true` (kill switch). Validate: place a test checkout → confirm `tenants.expires_at` populated from Stripe `current_period_end`, dashboard banner uses i18n via `t()`, PlatformAdmin no longer sees tenant banner. |

---

## Vegetation Prime — Remaining Work

### Pendiente para visualización completa en mapa

1. **Verificar TiTiler integration** — presigned URL flow (backend `viewer-url` → TiTiler → Cesium)
2. **Habilitar VegetationLayer.tsx** — reconectar con `getViewerUrl()` una vez COG pipeline verificado
3. ~~**Wiring SmartTimeline**~~ — **Done 2026-04-27** — `TimelineWidget.tsx` ya consume `getScenesAvailable()` (sparse availability endpoint) con auto-selección de fecha, sync con Cesium viewer y soporte year-over-year comparison.

### Frontend minor

| Item | Estado |
|------|--------|
| VegetationLayer TiTiler | PENDIENTE — espera pipeline COG completo |
| SmartTimeline sparse wiring | **Done 2026-04-27** — `TimelineWidget` → `getScenesAvailable()` (§12.8.1) ya cableado |
| EST-2d: Pestaña Configuración confusa | **Resuelto 2026-04-26** — consolidado a vista única analytics |
| EST-2e: Lanzar análisis Sentinel-2 | PENDIENTE — hacer botón más prominente |
| i18n comprehensive | **Done 2026-04-26** — `indexInfo` (NDVI/EVI/SAVI/GNDVI/NDRE/NDMI), `legend`, `calculationCard`, `dateRange`, `timelineWidget` en es+en+eu |
| Hardcoded strings | **Done 2026-04-26** — `VegetationLayerToggle` ahora usa `t()` |
| Custom formulas support | **Done 2026-04-26** — `custom-formulas` API + `/analyze` con `custom_formulas` |

---

## Incidencias Activas

| # | Prio | Task | Context |
|---|------|------|---------|
| INC-2 | Media | **lidar-frontend ImagePullBackOff** — imagen `lidar-frontend:latest` no existe en GHCR. Buildear y pushear o escalar a 0. | `nekazari-module-lidar/` |
| INC-4 | Baja | **sensor-ingestor endpoint 404** — legacy reference en frontend apunta a servicio inexistente. Limpiar referencia. | `apps/host/src/` |
| INC-5 | Info | **Ramas huérfanas en GitHub** — 10+ ramas antiguas. Limpiar. | GitHub `nkz-os/nkz` |
| INC-6 | Info | **`docs/risk-module-401-fix-deployment.md`** — documento de 228 líneas creado por un agente. Revisar si aporta valor. | `nkz/docs/` |
| ~~INC-7~~ | ~~Alta~~ | ~~**n8n — 500 /health/integrations**~~ — Resuelto 2026-05-20. Causa: excepciones no capturadas + issuer estricto en `get_current_user()`. Fix: catch-all `except Exception` + issuer whitelist (internal+public). Commit `nkz-os/n8n-module-nkz@fb024b8`. | `nkz-module-n8n/` |
| ~~INC-8~~ | ~~Alta~~ | ~~**Odoo — 401 "Invalid token" por issuer mismatch**~~ — Resuelto 2026-05-20. Causa: `validate_token()` solo aceptaba issuer interno (`KEYCLOAK_URL`), rechazaba el público. Fix: whitelist con `KEYCLOAK_URL` + `KEYCLOAK_PUBLIC_URL`. Commit `nkz-os/nkz-module-odoo@18dbf8c`. | `nkz-module-odoo/` |

---

## Modules — Robótica & Conectividad

| # | Task | Notes |
|---|------|-------|
| M1 | **ROS2/robots — terminar y probar con basabot** — completar módulo: integración ROS2 + Zenoh, control desde plataforma, telemetría, misiones. Core robotics eliminado del host (2026-02-26) — módulo externo es la única fuente. | `nekazari-module-robotics/` |
| M3 | **LiDAR — creación automática de entidades desde nube de puntos** | `nekazari-module-lidar/` |

## Modules — Energía & Solar

| # | Task | Notes |
|---|------|-------|
| N1 | **PVLib — módulo paneles solares y orientaciones** — nuevo módulo para modelado fotovoltaico con PVLib | módulo nuevo |
| N2 | **Odoo — comunidades energéticas** — terminar y probar gestión de comunidades energéticas | `nekazari-module-odoo/` |

## Modules — Verificación y Testing

| # | Task | Notes |
|---|------|-------|
| V1 | **Odoo módulo — comprobar y probar** — backend a 0 réplicas (CPU). Escalar, verificar integración, identificar bugs. | `nekazari-module-odoo/` |
| V2 | **Mattermost — probar integración** | módulo Mattermost |
| V3 | **CUE / SIEX — Cuaderno de Campo** — RD 1054/2022. ✅ Fases 1-5 completas (2026-05-04): backend NGSI-LD SOTA (66 rutas), frontend IIFE (8 tabs, 80KB), IUWS + state machine + Anti-Corruption Layer (XML/XSD) + AutoFirma + Gestoría multi-farm. Pendiente solo trámites externos. | `nkz-module-cue/` |

## Pending Admin Panel Work

| # | Task | Notes |
|---|------|-------|
| EST-1c | **Arreglar weather → risks pipeline** | `/api/weather/municipalities/search` devuelve 502 (INC-3). Probablemente resuelto con restauración entity-manager — verificar. |
| EST-3c | **Integración Billing Admin** | Configurar `nkz-billing-module` para slot `admin-tab`. |
| ~~EST-3d~~ | ~~**Integración VPN Admin**~~ | **Hecho (2026-05-02).** Módulo completo: factory panel, cross-tenant view, quota widget, rate limiting UI. Verificado E2E. |
| Q7 | **Ruff errors in entity-manager** | Pre-existing (E402, F841, E722, E701). Fix optionally to unblock strict lint. |

## DataHub Module — Technical Debt

> **Despliegue:** tratado como tarea **importante** en la tabla superior (**DH-DEP-1**). La deuda siguiente es posterior al pipeline estable en cluster.

| # | Prio | Task | Notes |
|---|------|------|-------|
| ~~DH-1~~ | ~~Alta~~ | ~~**UI: Unificar DataHubPanel → DataHubDashboard**~~ | **Done 2026-03-26** |
| DH-2 | Media | **Migrar DataHubWorkspace de Orion-LD a PostgreSQL** — `DataHubWorkspace` no es un digital twin de una entidad real, es configuración de UI (layouts, rangos temporales). CLAUDE.md establece que "TenantConfig is NOT a FIWARE entity — belongs in PostgreSQL". Crear tabla `datahub_workspaces` (tenant_id, name, config JSONB, timestamps). Actualizar backend `workspaces.py` para usar psycopg2/asyncpg en vez de Orion. | `backend/app/api/workspaces.py`, nuevo migration SQL |
| DH-3 | Baja | **Backend: Evaluar migración de Polars align a timeseries-reader** — El BFF ejecuta lógica de interpolación/alineación CPU-bound con Polars (`_align_multi_source_to_df_sync`). Idealmente esta lógica reside en el servicio de plataforma `timeseries-reader` para reutilización inter-módulo. Evaluar tras estabilizar el módulo. | `backend/app/api/timeseries.py` |

**Integración plataforma `timeseries-reader` (2026-03-28):** telemetría en Timescale = objeto JSON plano bajo `payload.measurements`; agregación v2 usa `->>` y whitelist. Índice **`ix_telemetry_tenant_device_time`** (migración **062**) aplicado en prod. Documentación del módulo: `nkz-module-datahub/docs/PLATFORM_TIMESERIES_INTEGRATION.md`; mandato: `MANDATE_TIMESERIES_READER_STRANGLER.md`. Estado plataforma: `.ai/CURRENT_STATE.md` (raíz workspace), `nkz/DEPLOYMENT.md`.

---

## Platform Scaling Plan — Nekazari Production Hardening

> **Audit date:** 2026-04-30
> **Current:** K3s single-node (4 vCPU, 8GB RAM usable, 96GB disk). 44 pods running. All modules except Zulip/Odoo/n8n active.

### Resource Estimates

| | 20 users | 200 users | 2000 users |
|---|---|---|---|
| **RAM** | 8 GB | 16 GB | 32-48 GB |
| **CPU** | 2-4 cores | 4-8 cores | 8-16 cores |
| **Disco** | 80 GB | 200 GB | 500 GB - 1 TB |
| **Nodos** | 1 | 1-2 | 3+ |
| **Coste/mes (Hetzner)** | ~15€ | ~30€ | ~60-100€ |

### Phase 1 — Stabilisation (0-20 users, 0€)

| # | Task | Effort | Notes |
|---|------|--------|-------|
| **SCALE-1** | Resource `requests=limits` on all pods (Guaranteed QoS) | Medium | Prevents OOM kills under memory pressure. 44 pods to audit. |
| **SCALE-2** | Prometheus + Grafana monitoring (cluster metrics) | Medium | Grafana already in cluster (shared). Need Prometheus stack + node-exporter. Alerts: RAM>80%, disk>85%, pod restarts. |
| **SCALE-3** | Disk log rotation + retention | Low | K3s logs fill disk fast. Configure `kubelet` log rotation (10MB/3 files). Docker `--log-opt max-size=10m`. |
| **SCALE-4** | MinIO bucket lifecycle policies | Low | Auto-expire temporary data (COG bands, old terrain tiles). Currently 77GB used with no ILM. |
| **SCALE-5** | PostgreSQL pgBouncer sidecar | Medium | Connection pooling. Currently direct connections from all 44 pods. |
| **SCALE-6** | Liveness/readiness probes on all deployments | Medium | K8s auto-healing. Currently missing on several services. |
| **SCALE-7** | Node taints/tolerations — PostgreSQL to dedicated CPU share | Low | DB doesn't compete with workers for CPU under load. |
| **SCALE-8** | Velero backup to S3-compatible storage | Medium | Automated etcd + volume backups. Currently no automated backup. |
| **SCALE-9** | ArgoCD stale RS pruning (`revisionHistoryLimit`) | Low | Set to 3 on all deployments. Currently accumulating hundreds. |
| **SCALE-10** | Pod anti-affinity for critical services | Low | Spread API replicas across failure domains (when second node added). |

### Phase 2 — Horizontal Scaling (20-200 users, ~30€/month)

| # | Task | Effort | Notes |
|---|------|--------|-------|
| **SCALE-11** | Second K3s node (8-16GB, worker-only) | Low | Add node via `k3s agent`. Move workers + MinIO distributed. |
| **SCALE-12** | HPA on API services (`cpu>70%`, min 2 replicas) | Medium | api-gateway, entity-manager, tenant-webhook first. |
| **SCALE-13** | MinIO distributed mode (2 nodes, erasure coding) | High | Protects against single-node data loss. Requires reformat. |
| **SCALE-14** | Redis AOF persistence | Low | Prevents job queue loss on restart. |
| **SCALE-15** | External Secrets Operator (ESO) | Medium | Credential rotation without redeploy. Integrate with K8s secrets. |
| **SCALE-16** | CDN for static assets (Cloudflare free tier) | Low | Offload `/modules/`, `/assets/`, `/cesium/` traffic. Reduces node bandwidth. |
| **SCALE-17** | K3s embedded etcd → external etcd | Medium | Required for HA control plane. Do before adding node 3. |
| **SCALE-18** | NetworkPolicy (Calico/Cilium) for tenant isolation | High | Segment network traffic by namespace/label. Required for multi-tenant security. |

### Phase 3 — Production Grade (200-2000 users, ~60-100€/month)

| # | Task | Effort | Notes |
|---|------|--------|-------|
| **SCALE-19** | PostgreSQL HA (Patroni + etcd, 2+ replicas) | High | Automatic failover <30s. Dedicated storage. |
| **SCALE-20** | MongoDB replica set (3 nodes) | Medium | Distributed reads, automatic failover. |
| **SCALE-21** | Redis Sentinel (3 nodes) | Medium | Automatic failover for job queue + cache. |
| **SCALE-22** | K3s multi-master (3 control-plane nodes) | Medium | HA API server. Requires external etcd (SCALE-17). |
| **SCALE-23** | HPA with custom metrics (Prometheus adapter) | High | Scale by request latency, not just CPU. |
| **SCALE-24** | Service mesh (Istio/Linkerd) | High | mTLS, circuit breaking, rate limiting, distributed tracing. |
| **SCALE-25** | Topology spread constraints | Medium | Ensure pods span nodes and availability zones. |
| **SCALE-26** | Dedicated TimescaleDB node | Medium | Time-series data is the main storage growth vector. |
| **SCALE-27** | Multi-region MinIO (site replication) | High | Disaster recovery for object storage. Requires >2 nodes. |

### Immediate Priority (do this week)

| # | Task | Impact |
|---|------|--------|
| **SCALE-1** | Resource limits | Prevents cascade failure |
| **SCALE-3** | Log rotation | Prevents disk exhaustion |
| **SCALE-4** | MinIO lifecycle | Auto-cleanup saves GB/week |
| **SCALE-9** | RS pruning | Prevents pod slot exhaustion |

---

_Last updated: 2026-05-24 (S3) — KEYCLOAK_URL audit: 8 modules fixed (internal URL pattern). 3 modules hardened (startupProbe + 256/512Mi): crop-health, agrienergy, catastro-spain. catastro-spain Flask→gunicorn. eu-elevation, n8n, odoo, robotics, core-intelligence, core-timeseries-reader KEYCLOAK/JWKS fixes. Public vs internal URL pattern documented in CLAUDE.md._

---
---


---

## Systemic Finding — Gunicorn Worker Deadlock Pattern (2026-05-24)

Multiple services exhibited the same failure mode:

- **Symptom**: Pod Running but 0/1 Ready, health check times out, workers never log init completion
- **Root cause**: 2+ gunicorn workers importing Flask/FastAPI app simultaneously → both try Redis connection via `flask-limiter` → deadlock during module-level init. `socket_connect_timeout` not respected.
- **Fix recipe**: `--workers 1 --threads 1 --timeout 300` + startupProbe (150s grace) + liveness `timeoutSeconds: 5` + readiness probes + memory bump if needed
- **Fixed**: tenant-webhook (#330), sdm-integration (#331), entity-manager (#336), weather-api (previous #328), CUE (S2), catastro-spain (S3 Flask→gunicorn)
- **StartupProbe + memory added (S3)**: crop-health, agrienergy, catastro-spain (all 256/512Mi)
- **Still at risk**: Any Flask/FastAPI service with >1 gunicorn worker and Redis-based rate limiter. Module backends not yet audited.

---

## NetworkPolicies — Status (2026-05-24)

| Fase | Politica | Estado | PR |
|------|----------|--------|-----|
| 1 | `essential-services-access` — DNS + Traefik ingress | Applied | #334 |
| 2 | `allow-same-namespace` — baseline intra-ns communication | Applied | #334 |
| 3 | `mongodb-restricted`, `mosquitto-restricted`, `minio-restricted` | Applied | #335 |
| 4 | `default-deny-all` | NOT applied — requires per-service verification first | — |

**Blockers for Phase 4**: ~30 module backends lack `layer` labels. Per-service policies need to use `app` labels instead. `database-policies.yaml` created as audit reference but NOT applied.

---

## Issues Detected — Next Session (2026-05-24)

### From platform testing

| # | Issue | Detail |
|---|-------|--------|
| ~~**MOD-401**~~ | ~~Module backends return 401~~ | **Done 2026-05-24 (S2+S3).** JWKS/KEYCLOAK_URL→internal for 8 modules: lidar, vegetation-prime, bioorchestrator, CUE, gis-routing (S2), crop-health, agrienergy, catastro-spain (S3). `require_auth()` migration still pending for most. |
| ~~**CUE-502**~~ | ~~`/api/modules/cue/explotaciones` 502 after 15s~~ | **Done 2026-05-24 (S2).** gunicorn 1 worker + startupProbe + 256/512Mi. |
| ~~**ROUTING-500**~~ | ~~`/api/routing/patterns` 500 after 5-7s~~ | **Done 2026-05-24 (S2).** Service name, IngressRoute, KEYCLOAK_URL fixed. |
| ~~**LIDAR-CONN**~~ | ~~Lidar: "Connection refused" to Keycloak~~ | **Done 2026-05-24 (S2).** lidar-config → internal JWKS_URL + KEYCLOAK_URL. |
| **MIME-CACHE** | Browser: MIME "text/plain" for .js modules | Server returns correct `text/javascript`. Browser cache issue. Hard refresh fixes. |

### Pre-existing

| # | Issue | Detail |
|---|-------|--------|
| **soil-migrate** | ImagePullBackOff | Image `nkz-module-soil/migrate:latest` doesn't exist. Another session. |
| **soil-module-worker** | Error state | Another session. |
| **core-auth** | ArgoCD OutOfSync | Jobs recreated. Should self-heal. |
| **MOD-AUDIT** | Module backends without startupProbe | **Partially done 2026-05-24 (S3).** crop-health, agrienergy, catastro-spain have startupProbe + 256/512Mi. Additional KEYCLOAK/JWKS fixes for eu-elevation, n8n, odoo, robotics, core-intelligence, core-timeseries-reader. Pending: carbon, connectivity (not deployed), lidar, vegetation-prime, bioorchestrator, intelligence, soil, zulip. |

---

## PRs merged this session (2026-05-24)

| PR | Repo | Description |
|----|------|-------------|
| #330 | nkz-os/nkz | fix(tenant-webhook): pin image + startupProbe + 1 worker |
| #331 | nkz-os/nkz | fix(sdm-integration): startupProbe + 1 worker + 512Mi |
| #332 | nkz-os/nkz | fix(tenant-webhook): unpin image, keep :latest + IfNotPresent |
| #333 | nkz-os/nkz | fix(postgresql): bump memory 512Mi->4Gi |
| #334 | nkz-os/nkz | feat(network-policies): allow-same-namespace baseline |
| #335 | nkz-os/nkz | feat(network-policies): Phase 3 per-service policies |
| #336 | nkz-os/nkz | fix(entity-manager): 1 worker + probes + 512Mi |
| #338 | nkz-os/nkz | fix(agrienergy): startupProbe + 256/512Mi |
| — | gitops-config (6 commits) | KEYCLOAK_URL→internal: crop-health, catastro-sp, agrienergy, eu-elevation, odoo, core-intelligence, core-timeseries-reader, platform-config |
| — | nkz-module-crop-health | startupProbe + 256/512Mi |
| — | nkz-module-cadastral-spain | startupProbe + 256/512Mi + Flask→gunicorn migration |
| — | nkz-module-n8n | KEYCLOAK_URL + KEYCLOAK_INTERNAL_URL fix |
| 3b10ede | nkz-os/nkz-module-billing | fix: update image org k8-benetis -> nkz-os |
| e033fce | nkz-os/nkz-module-zulip | fix(zulip): startupProbe 20min grace |

---

## V1.3 Platform Closure — SHA-pin remaining services (2026-06-04)

> **Context:** Core services api-gateway, tenant-webhook, timeseries-reader, entity-manager are SHA-pinned and deployed. 8 core services + ~20 modules + infra still use `:latest`. See audit: `internal-docs-local/2026-06-04-platform-audit-pre-production.md`.
> **PRs:** nkz#428 (merged — 4 core services), nkz#429 (open — docs)

### P0 — SHA-pin remaining core services (cluster has 31 `:latest` deployments) — DONE (commit `0164f57`, 2026-06-11)

- [x] **email-service** — `@sha256:47b7d0fe`
- [x] **keycloak** — `@sha256:ca03b696`
- [x] **pmtiles-packager** — `@sha256:5a1ceb49`
- [x] **push-notification-service** — `@sha256:6ee4b902`
- [x] **risk-api** — `@sha256:2fa076c2`
- [x] **risk-orchestrator** — `@sha256:c5fe7940`
- [x] **sdm-integration** — `@sha256:10b305cf`
- [x] **telemetry-worker** — `@sha256:c5c531f0`
- [x] **minio** — `@sha256:14cea493`

**Aún usan `:latest`** (para próxima iteración):

- [ ] **risk-worker**, **weather-api**, **weather-worker**, **mqtt-credentials-manager** (NKZ images → rebuild + SHA-pin)
- [ ] **mosquitto-exporter** (`sapcc/mosquitto-exporter:latest` → `1.0.6`)
- [ ] **prometheus**, **alertmanager**, **grafana**, **node-exporter** (monitoring → version pins)

### P0 — Recuperar commits perdidos del merge (template hardening) — DONE (2026-06-11)

> Estos cambios estaban en `docs/quickstart-oidc-v2` (commit `a9cb8ce`) pero se perdieron al hacer cherry-pick por conflicto. Recuperar del reflog y crear PR.

- [x] **`:latest` → `:stable` en templates internos**: Ya aplicado vía SHA-pinning (P0 arriba) — los servicios internos tienen `@sha256:` (superior a `:stable`)
- [x] **`bitnami/kubectl:latest`** → `bitnami/kubectl:1.32.3` — Ya en main
- [x] **minio-init-job** → `minio/mc:RELEASE.2024-11-21T17-21-54Z` — Ya en main
- [x] **busybox postgresql init** → `busybox:1.37.0-glibc` — Ya en main
- [x] **minio** → `@sha256:` pin — Ya en main
- [ ] **`:latest` → versiones pinned en addons**: weather-api, weather-worker, mqtt-credentials-manager — pendiente (ver sección P0 arriba)
- [ ] **`:latest` → versiones pinned en monitoring**: prometheus, alertmanager, grafana, node-exporter — pendiente

### P1 — Module images (each module has independent repo + OIDC publish)

> **Nota:** Los módulos tienen pipelines independientes. Cada uno requiere:
> 1. Actualizar `:latest` → `@sha256:` en su propio `gitops/modules/<id>/deployment.yaml`
> 2. O bien configurar OIDC publish para que actualice el SHA automáticamente

- [ ] carbon-api, catastro-spain (backend + frontend), cue-backend, datahub-api, elevation (api + worker), lidar (api + worker), billing-module, vpn/network-controller, odoo (2), robotics-api, vegetation-prime (3), zulip-provisioner
- [ ] nkz-os-website: `:latest` → SHA-pin
- [ ] n8n tenant instances (asociacionallotarra, montiko): `n8nio/n8n:latest` → version pin

### P2 — FIWARE data debt (weather/telemetry Orion-first migration) — DONE (2026-06-11)

> **Plan:** `internal-docs-local/plans/2026-06-04-weather-orion-first-migration.md`

- [x] **Phase 1+2+3**: El `ParcelWeatherEngine` implementa Orion-first completo: Open-Meteo → Orion-LD (WeatherObserved) → subscription → telemetry-worker → TimescaleDB.
  - `timescaledb_writer.py`: `write_observations()` y `write_alerts()` son DEPRECATED no-ops.
  - `orion_writer.py`: `sync_weather_to_orion()` activo con spatial downscaling por parcela.
  - `parcel_engine.py`: Motor principal — descubre parcelas desde Orion-LD, baja datos Open-Meteo, aplica downscaling, persiste WeatherObserved 1:1 por parcela.
  - El path legacy municipality-based (`MUNICIPALITY_WORKER_ENABLED=false`) está desactivado por defecto.

### P3 — Frontend polish

- [ ] ~119 `console.*` calls remaining → migrate to `logger.*` (down from 214; ESLint `no-console: warn` active)
- [ ] 473 native `<button>` → `@nekazari/ui-kit` Button (focused migration by screen: admin, registration, parcels)
- [ ] `nkz-module-odoo/scripts/patch-odoo-admin-secret.sh`: verify `jq` is available (script now uses `jq` for JSON manipulation)
- [ ] `AlertTriangle` → toast notification system (useNotification hook created, ui-kit Toast component needed for V1.4)

### ⚠️ ArgoCD sync status

- [ ] `core-services` app shows `OutOfSync Healthy` — caused by ConfigMap/template diffs between `nkz/k8s/` (main) and cluster. Resolves automatically when P0 template hardening is merged.

---

# ═══════════════════════════════════════════════════════════════════

## Session: 2026-06-17 BioOrchestrator Data Normalisation Pipeline

| # | Prio | Task | Status |
|---|------|------|--------|
| NORM-1 | Alta | **normalization_registry.py** — 9 traits BSL→AGROVOC, 55 ubicaciones, 27 EPPO codes, escalas 1-9→0-1, mergeKey canónico | ✅ |
| NORM-2 | Alta | **base_ingester.py** — normalize_nodes() auto-llamado desde transform(), persiste campos unificados en Neo4j | ✅ |
| NORM-3 | Alta | **Backfill 33.378 VT + 181 MT** — mergeKeyNormalized, varietyNormalized, locationNormalized, agronomicTraitsUnified, validationPassed | ✅ |
| NORM-4 | Alta | **471 nodos inválidos reparados** — CREA (EPPO inferido), INTIA-EXP (year=0), NULL source | ✅ |
| NORM-5 | Alta | **56 tests** (45 unit + 11 integration) + ruff clean + deploy ArgoCD | ✅ |
| NORM-6 | Media | **validate_source.py** — script para validar nuevas fuentes contra el registry | ✅ |
| NORM-7 | Media | **n8n singleton conflict** — i18next/react-i18next eliminados de devDependencies, CI verde, OIDC publish | ✅ |
| NORM-8 | Baja | **ESPECIFICACION_SCRAPER_INGESTA.md** — documentación para agentes futuros | ✅ |
# ═══ ARCHIVE — COMPLETED TASKS (reference only, do NOT work on) ═══
# ═══════════════════════════════════════════════════════════════════

> Everything below this line has been completed. It is preserved for
> historical reference only. **Agents: do NOT interpret any of this as
> pending work.**

---

## Completed — Production Config Separation (2026-05-20/21)

**Zero `robotika.cloud` refs in all public repos.** 4-phase security hardening to remove production domains, emails, and infrastructure topology from ~20 public GitHub repos.

| Phase | Scope | Ref count | Method |
|-------|-------|-----------|--------|
| Fase 0-2 | Core infrastucture | ~13 files | Private gitops-config repo + ArgoCD overlays for networking, configmaps, frontend |
| Fase 3 Tier 2 | odoo, zulip, datahub overlays | ~15 refs | Moved to gitops-config, ArgoCD apps updated |
| Fase 3 Tier 3 | 15 module repos | ~45 refs | `valueFrom.configMapKeyRef` patterns, ingress files moved to private repo |
| Fase 3 Tier 4 | nkz core k8s (49 files) | ~165 refs | `YOUR_DOMAIN` placeholders + ConfigMap refs for ArgoCD-managed services |
| Fase 4 | CI hardening | Prevention | Pre-commit hook + CI job block new `robotika.cloud` refs; diff-based, with bypass |

**Repos:** `nkz` (PRs #316, #318, #319 all merged), `gitops-config` (16 production overlays, 10 commits), 15 module repos (1 commit each).

**Deployed in production** — all ArgoCD config apps Synced/Healthy, core-services ConfigMaps active, VPN ConfigMap corrected.

## Completed — GitOps Starter + Docs Website (2026-05-21)

**nkz-gitops-starter:** Public template repo for zero-to-production deployment.
58 files: 20 ArgoCD Application CRDs, 16 overlay manifests, interactive `setup.sh` wizard. All domains templated with `{{PLACEHOLDER}}` tokens. Repo: `nkz-os/nkz-gitops-starter`.

**nkz-os/web docs overhaul:** Filled 4 empty pages (`quickstart`, `architecture`, `ngsi-ld-models`, `deploy`), Spanish translation for deploy. Separated website (`nkz-os/web`) as standalone source of truth; `nkz/docs/` now developer-only (PR #322). Removed fragile `trigger-docs-update` dispatch workflow.

| # | Date | Task |
|---|------|------|
| C5 | 2026-03-07 | Bot Protection & Rate Limiting — 3-layer defense for `/register` |
| G1 | 2026-03-07 | GitOps Enforcement — manual deploy prohibited |
| G2 | 2026-03-07 | GHCR Public Access & Secret Cleanup — removed `imagePullSecrets` |
| G3 | 2026-03-23 | GHCR image copy (org migration) — 11 images copied via docker pull/tag/push + crane installed |

## Completed — SOTA Standards & Uniformity (NEK-SOTA-01)

All valid changes committed and merged to main (commit `b7e5684`):

| # | Task | Date |
|---|------|------|
| UNI-0 | unitCode A97→HPA | In main + PR#70 (cherry-pick to fix/frontend-routing) |
| UNI-0b | telemetry-worker: AgriSensor subscription | In main |
| UNI-0c | tenant-user-api: normalize_tenant_id | In main |
| UNI-0d | SDM: @context local + cleanup IOT_ENTITY_TYPES | In main |
| UNI-1 | UI licensing attribution (AGPL) | DONE 2026-03-19 |

Discarded changes (reverted): rename `nkz-module.js` → `nekazari-module.js`, rename repos in gitops, rename GHCR images, api-gateway SOTA type mapping, `NEKAZARI_SOTA_STANDARDS.md`.

## Completed — TUM-1: Tenant & User Management Overhaul (2026-03-22)

Fully deployed to production. PRs #59, #60, #61, #64, #65 merged.

| # | Task |
|---|------|
| TUM-1a | Push changes via PR |
| TUM-1b | Delete `nekazari-webhook` namespace |
| TUM-1c | Purge all tenants |
| TUM-1d | Run keycloak-setup-mappers.sh |
| TUM-1e | Apply K8s manifests |
| TUM-1f | Rebuild & push Docker images |
| TUM-1g | E2E verification |
| TUM-2 | Audit Logs instrumentation (entity-manager 3 ops, tenant-webhook 7 ops) |

## Completed — FW-1: FIWARE Compliance & SDM Cleanup (2026-03-23)

Merged via PR#69 (`feat/premium-modules`). Deployed 2026-03-23. K8s manifests applied via kubectl.

| # | Task |
|---|------|
| FW-1a | CONTEXT_URL internal cluster URL (5 deployments) |
| FW-1b | TenantConfig → PostgreSQL |
| FW-1c | Connectivity module removed (ArgoCD app deleted, marketplace deactivated) |
| FW-1d | SDM dead endpoints removed (~100 lines) |
| FW-1e | SDMManagement.tsx deleted |
| FW-1f | Sensors.tsx enhanced (health summary, pagination, i18n) |

## Completed — GitHub Org Migration (2026-03-22/23)

All repos migrated from `k8-benetis/*` to `nkz-os/*`. All code refs updated. GHCR images migrated. All packages public and linked to repos.

## Completed — Frontend Routing Fix (2026-03-24)

PR#70 (`fix/frontend-routing`). Main `nekazari-ingress` now routes `/` to `frontend-host-service` (Docker). Stale ingresses deleted (frontend-host-ingress, agrienergy-api-frontend-host, connectivity-api-frontend-host).

## Completed — Previous Deployments

| # | Task |
|---|------|
| DEP-1 | tenant-user-api: JWT fallback — fixed in TUM-1 |
| DEP-2 | api-gateway: tenant-limits in ADMIN_ROUTE_MAP |
| DEP-3 | entity-manager: terms 200 + audit logs + public assets |
| DEP-4 | Session expired redirect |
| DEP-5 | Admin NEK codes: Estado column |

## Completed — MAP Phase 1

| # | Task | Date |
|---|------|------|
| MAP-1 | `docker-compose.yml` completo | 2026-03-02 |
| MAP-2 | Docs site (VitePress) | 2026-03-03 |
| MAP-3 | CI/CD robusto | 2026-03-07 |
| MAP-4 | README.md orientado a decisores | 2026-03-03 |
| MAP-5 | Vertical agrícola pulido | 2026-03-06 |
| MAP-15 | Landing page (Astro/nkz-os.org) | 2026-03-21 |
| MAP-15b | Docs-as-Code Ingestion Engine | 2026-03-22 |

## Completed — Stabilization Sprint (2026-03-13)

| # | Task |
|---|------|
| EST-1a | Eliminar `/alerts` (AlertCenter) |
| EST-1b | Limpiar navegación |
| EST-2a | Área de parcelas (hectáreas, no m²) |
| EST-2b | Historial NDVI vacío |
| EST-2c | Visor unificado parcela — verificado OK |
| EST-3a | Restaurar componentes maestros admin |
| EST-3b | Slots administrativos dinámicos |
| EST-3e | Estabilización Control Center |

## Completed — Risk Engine

R1 through R8 all DONE (2026-02-24 to 2026-03-07). 6 models + visual rule builder.

## Completed — Entity Creation & Data

| # | Task | Date |
|---|------|------|
| E1 | EntityWizard 3 flows | 2026-02-24 |
| E3 | Bulk GPS import | 2026-02-26 |

## Completed — Module Work

| # | Task | Date |
|---|------|------|
| M2 | LiDAR IIFE migration | 2026-03-01 |
| N3 | AgriEnergy Orchestrator scaffold + deploy | 2026-03-05 / 2026-03-18 |
| AGE-1 | AgriEnergy production deploy | 2026-03-18 |

## Completed — Billing Module (2026-03-02/06)

Zero-Trust checkout, webhook hardening, Tenant Reaper CronJob (dry-run), integration tests, self-service onboarding.

## Completed — Infra / Ops / Security / Quality

| # | Task | Date |
|---|------|------|
| C1 | Module template rewrite | 2026-02-23 |
| C2 | Automated database backups (code) | 2026-02-25 |
| C3 | Parcel drawing double-click | 2026-02-25 |
| C4 | Performance audit | 2026-02-26 |
| O1 | imagePullPolicy: Always | 2026-03-01 |
| O3 | Disk cleanup (89%→58%) | 2026-02-26 |
| O8 | weather-worker imagePullPolicy | 2026-02-26 |
| O9 | Admin panel cleanup | 2026-02-26 |
| S1 | JWT in localStorage → httpOnly cookie | 2026-03-02 |
| P2 | Vegetation health bugs | 2026-02-26 |
| P3 | DataHub visibility + charts | 2026-03-01 |
| P8 | Dashboard cleanup | 2026-02-25 |
| Q2 | ESLint config | 2026-03-03 |
| Q4 | debug_parcels.py removed | 2026-02-23 |
| Q5 | frontend-dist.tar.gz cleaned | 2026-02-23 |
| I1 | Traducciones incompletas | 2026-02-26 |
| D1 | SETUP.md templates | 2026-02-23 |
| D3 | ADAPTER_SPEC.md | 2026-02-23 |
| A1 | NEK + Billing integration | 2026-03-06 |

## Completed — Incidents

| # | Task | Date |
|---|------|------|
| INC-1 | entity-manager CrashLoopBackOff | 2026-03-12 |
| INC-3 | `/api/weather/municipalities/search` 502 | Probablemente resuelto 2026-03-14 |
| INC-7 | Risks missing weather data | 2026-03-17 |

## Completed — Hotfixes

- **Auth Login Loop (2026-03-14)**: `verify_aud: False` + manual azp validation. PR#33.
- **Entity Manager Missing (2026-03-14)**: Pod recreated from git manifests.
- **Timeseries reader entity fallback + DataHub worker rollout (2026-04-21)**: merged `nkz` PR #138 and `nkz-module-data-hub` PR #2, restarted `timeseries-reader` deployment successfully, and published updated DataHub bundle to MinIO (`/modules/datahub/nkz-module.js`) after CI skipped upload due to missing MinIO secrets.
- **DataHub cookie auth propagation hotfix (2026-04-21)**: merged `nkz-module-data-hub` PR #3 to fix middleware header injection from `nkz_token` cookie (avoid stale header cache), then redeployed `datahub-api` with forced recreate (`scale 0 -> 1`) to avoid rollout surge CPU deadlock.
- **DataHub SOTA frontend execution in progress (2026-04-21)**: started plan-aligned implementation with rigid panel removal and phase-1 decomposition (`ChartSurface`, `ChartHeaderControls`, `ChartStatusLayer`) while keeping worker-first + uPlot plotting pipeline active.
- **DataHub phase-1 render consolidation (2026-04-21)**: introduced `ChartRenderHost` and switched active panel consumers (`DataHubDashboard`, `DataHubQuickChart`, slots export) to `DataCanvasPanelLite`, enforcing a single worker-first + uPlot runtime path in active UX.
- **DataHub legacy panel cleanup + dual UX controls (2026-04-21)**: removed `DataCanvasPanel.tsx` legacy implementation and added lightweight basic/advanced visual controls in active panel (`DataCanvasPanelLite`) without re-fetching timeseries payloads.
- **DataHub quality gate progress (2026-04-21)**: added runtime viewport telemetry (`ResizeObserver` width/height feed) in active chart runtime (`ChartRenderHost` → `DataCanvasPanelLite`) to validate container-driven resizing behavior and detect hidden layout regressions while keeping single worker-first + uPlot render path.
- **DataHub push+deploy executed (2026-04-22)**: `nkz-module-datahub` changes pushed to `main` (`17f321c`) and production bundle published from server to MinIO path `modules/datahub/nkz-module.js`; public module URL returns updated object (`ETag 6be6e68f2724a4f29513eaebe7564bf1`).
- **DataHub transient empty fetch guard (2026-04-22)**: hotfix pushed to `main` (`fbbaa16`) adding worker one-shot retry for empty single-series responses to prevent false "no data" states from intermittent upstream empties; bundle redeployed on server MinIO (`ETag 94587025a2014d363ffb31f1d836fda1`).
- **DataHub panel shell redesign deployment (2026-04-22)**: visual container pass pushed to `main` (`40c110c`) replacing rigid panel shell with fluid/glass layout and larger chart viewport (`ChartSurface` min-height uplift, updated header/status/legend chrome); bundle redeployed on server MinIO (`ETag 7e2e52675e429e157229930f8271c053`).
- **DataHub resize/contrast emergency pass (2026-04-22)**: pushed to `main` (`2aa71b1`) with larger grid defaults, explicit panel resize handles (`se/s/e`), minimum panel dimensions, stronger chart contrast, and force-refresh retry before empty fallback in panel worker flow; bundle redeployed on server (`ETag 27bb41342528bd9db3f1e567aa9d7f7a`).
- **DataHub plotting emergency fallback (2026-04-22)**: pushed to `main` (`a72ed7d`) adding direct UI fetch fallback (`/api/datahub/timeseries/entities/.../data`) plus local outer-join when worker path returns empty/null, to prevent false “no data” states; bundle redeployed on server (`ETag 7fdd58420532b1d9e6979f8d7d036297`).
- **DataHub frameless full-bleed chart deployment (2026-04-22)**: pushed to `main` (`6cbec4c`) removing chart panel frame and moving controls to floating overlays so the plotting surface remains fully visible; bundle redeployed on server (`ETag 4816601bf8db703adb47ac7383c1e59e`).
- **DataHub viewport stabilization pass (2026-04-25)**: pushed to `main` (`57bfa6b`) with overflow containment and deferred/stable uPlot init to prevent tactical line rendering outside visible viewport, plus minimum panel height guardrail restore (`PANEL_MIN_H=4`); bundle redeployed on server (`ETag 58aeba6d78723e52951dc237e79ccb63`).
- **DataHub first-graph readability pass (2026-04-25)**: pushed to `main` (`95d66d1`) with cleaner chart footer (runtime debug hidden by default) and Y-scale toggle (`focus`/`full`) to avoid flat-bottom perception on skewed series; bundle redeployed on server (`ETag f102c363b947fc267ed32eaaee0f4d06`).
- **DataHub plotting reliability hotfix (2026-04-25)**: pushed to `main` (`35bed87`) with two root-cause fixes: (1) stale closure bug in `DataCanvasPanelLite` fallback path (missing `processDirectFallback` dependency) that could return obsolete/no series data under worker failure, and (2) JWT payload base64 padding fix in backend middleware tenant extraction (`backend/app/main.py`) to reduce auth/tenant propagation flakiness. Frontend bundle redeployed to MinIO (`ETag 00d56ea8607f1846f932f89197bbcb26`).

## Stabilization Report — 2026-03-07

Database constraint fix, webhook recovery, frontend GitOps, GHCR 403 fix, cluster recovery, ArgoCD expansion.

## NEK Codes vs Billing Module — Relationship

NEK codes = onboarding (who can register). Billing = monetization (who has paid access). Complementary, not duplicate. Flow: NEK code → register → tenant → (optional) Stripe checkout → pro roles.

## Vegetation Prime — Backend Status (COMPLETE)

All phases implemented: routes, Celery workers, models, 6 migrations, Phase 5 (Arrow IPC), Phase 6 §12.8.1. `rio-cogeo` in requirements. Dockerfile production-ready. Docker image built and pushed 2026-03-14.

---

## GIS Routing Module — Known Gaps

| # | Priority | Task | Notes |
|---|----------|------|-------|
| GR-1 | High | **Reconstruir frontend con datos reales** — eliminar mocks, poblar desplegables desde Orion-LD (AgriParcel, ManufacturingMachine), añadir mapa 2D Leaflet, slot map-layer CesiumJS | `nkz-module-gis-routing/src/`. Plan detallado: Fases 1-6 |
| GR-2 | High | **Sync real para mobile** — WatermelonDB pull/push con collections `parcels`, `equipment`, `operations` via `/api/core/sync/vectorial`. Actualmente mockeado. | `nkz-module-gis-routing/backend/`, `nkz/services/entity-manager/` |
| GR-3 | Medium | **PMTiles real (no mock)** — tippecanoe + pmtiles-convert → MinIO cache → streaming. | `nkz-module-gis-routing/backend/app/services/pmtiles_generator.py` |
| GR-4 | High | **ISOBUS file push automatizado** — Cerrar el gap entre generacion ISOXML y carga en terminal del tractor. El ISOXML se genera correctamente (`GET /api/routing/export/{id}?format=isoxml`) y se descarga manual (USB / upload a John Deere Operations Center). Opciones a evaluar: (a) MQTT file transfer via mosquitto → ESP32 → CAN bus, (b) API John Deere Operations Center / CNH AFS para push remoto. Documentado en `internal-docs/specs/2026-04-30-gis-routing-sota-design.md`. | `nkz-module-gis-routing/backend/` |
| GR-5 | Low | **XTE/Lightbar en nkz-mobile** — Componente nativo React Native de guiado visual para cabina (ISO 11783-6). Depende de GR-2 (sync real con A-B lines). UDP listener ya existe en `nkz-mobile/src/services/telemetryUdp.ts`. | `nkz-mobile/`, ACTION_PLAN.md |

---

## Session: 2026-06-12 Weather-Map + BioOrch FIWARE Integration

| # | Prio | Task | File |
|----|------|------|------|
| WM-1 | Alta | **Nuevo módulo: weather-map** — Repo creado, CI, SHA pin, deploy K8s. Verificar CI en próximos pushes. | `nkz-module-weather-map` |
| WM-2 | Media | **Frontend publish** — Reconstruir dist/ con fixes C1/C2 y publicar a MinIO (CI publish-module o manual). | `nkz-module-weather-map` |
| WM-3 | Media | **Añadir weather-map al Module Inventory** en tabla External Modules de este mismo archivo. | `PENDING.md` |
| BO-1 | Alta | **Push bioorchestrator weather penalties** — `dao.py` + `recommendation.py` pusheado a main ✅ | `nkz-module-bioorchestrator` |
| BO-2 | Media | **Verificar SHA pin bioorchestrator** — k8s local `f3a72d8…` vs deployed `b83f4c4…` (gestionado por ArgoCD/gitops-config). | `nkz-module-bioorchestrator/k8s/` |
| WM-4 | Alta | **Verificar CI weather-map** — Actions backlogged. El próximo push debe funcionar (paquete GHCR existe). | CI Actions |
| WM-5 | Alta | **Publicar frontend dist/ a MinIO** — CI publish-module o manual via SSH `mc cp`. Sin esto el host no sirve el overlay. | `nkz-module-weather-map/dist/` |

---

## Completed 2026-06-19 — Smart Region Base Layer (Sub-feature B)

### Host (nkz) — PR #633 merged
| File | Action |
|------|--------|
| `utils/regions.ts` | `resolveRegion()` with bbox hierarchy + hysteresis (0.15°)
| `context/MapRegionContext.tsx` | Context `{ currentRegion, layerAutoMode, setManual, enableAuto }`
| `hooks/useRegionResolver.ts` | Debounced camera.moveEnd → region change callback
| `hooks/cesium/useTerrainProvider.ts` | Region-based auto (replaces parcel-based); `eu` delegates to module
| `utils/terrain.ts` | `terrainProviderForRegion()`, `imageryProviderForRegion()`
| `components/CesiumMap.tsx` | Auto-switch imagery by region, "🌍 Auto" toggle, manual override

**Region table:** `navarra` (PNOA + IDENA), `spain` (PNOA + IGN), `eu` (ESRI + eu-elevation), `world` (ESRI + eu-elevation)

### Module (nkz-module-eu-elevation)
| File | Action |
|------|--------|
| `ElevationLayer.tsx` | `shouldInjectEuTerrain()`, subscribe to `viewer.__nkzRegion`, remove internal bbox-match auto

### Email-service SMTP fix
| File | Action |
|------|--------|
| `gitops-config/overlays/core/network-policies/email-service-smtp-egress.yaml` | NetworkPolicy allowing egress to smtp.ionos.es:465/587

**Root cause:** email-service pod blocked by NetworkPolicies — no internet egress for SMTP. Applied via gitops-config (ArgoCD-managed).

