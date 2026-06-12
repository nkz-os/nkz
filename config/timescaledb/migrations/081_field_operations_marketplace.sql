-- =============================================================================
-- Migration 081: Backfill field-operations marketplace entry
-- =============================================================================
-- Populates required_roles, description_i18n, setup_parcel_url, and slots
-- in marketplace_modules for the field-operations module.
--
-- Uses jsonb || operator to merge without overwriting existing metadata keys.
-- Uses dollar-quoting ($$...$$) to avoid escaping issues.
--
-- Dependencies: 024_module_federation_registry.sql, 080_tenant_parcel_modules.sql
-- =============================================================================

BEGIN;

-- 1. Update required_roles to match manifest.json
UPDATE marketplace_modules
SET required_roles = ARRAY['Farmer', 'TenantAdmin', 'PlatformAdmin']
WHERE id = 'field-operations';

-- 2. Merge metadata: description_i18n + setup_parcel_url + slots
UPDATE marketplace_modules
SET metadata = metadata || $${"description_i18n": {"es": "Registro y gestión de operaciones agronómicas de campo: siembra, riego, abonado, fitosanitario, laboreo y cosecha con integración ISOBUS, SIEX y BioOrchestrator", "en": "Field agronomic operations registry and management: sowing, irrigation, fertilization, spraying, tillage and harvesting with ISOBUS, SIEX and BioOrchestrator integration", "eu": "Landa eragiketa agronomikoen erregistroa eta kudeaketa: ereitea, ureztatzea, ongarritzea, fitosanitarioa, lur-lantzea eta uzta-bilketa ISOBUS, SIEX eta BioOrchestrator integrazioarekin", "fr": "Registre et gestion des opérations agronomiques de terrain : semis, irrigation, fertilisation, phytosanitaire, travail du sol et récolte avec intégration ISOBUS, SIEX et BioOrchestrator", "pt": "Registro e gestão de operações agronómicas de campo: sementeira, rega, fertilização, fitossanitário, lavoura e colheita com integração ISOBUS, SIEX e BioOrchestrator", "ca": "Registre i gestió d'operacions agronòmiques de camp: sembra, reg, adobat, fitosanitari, llaurada i collita amb integració ISOBUS, SIEX i BioOrchestrator"}, "setup_parcel_url": "http://field-operations-api-service:8420/internal/setup-parcel", "slots": {}}$$::jsonb
WHERE id = 'field-operations';

COMMIT;
