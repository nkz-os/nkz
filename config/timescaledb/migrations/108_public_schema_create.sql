-- =============================================================================
-- 108 — Object creation in the public schema is granted, not open to PUBLIC
-- Idempotent. Privileges only.
--
-- PostgreSQL 15 and later no longer let every role create objects in public; this
-- applies the same default to older servers. Since 106 the core role holds CREATE
-- explicitly. Three modules still run CREATE ... IF NOT EXISTS on start (which
-- needs CREATE on the schema even when the object exists), so they keep it
-- explicitly until that DDL moves out of the start path.
-- =============================================================================

DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['nkz_module_gis_routing', 'nkz_module_odoo', 'nkz_module_vegetation_prime'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT CREATE ON SCHEMA public TO %I', r);
    END IF;
  END LOOP;
END $$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
