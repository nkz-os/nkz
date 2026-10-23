-- =============================================================================
-- 107 — public.schema_migrations belongs to vegetation-prime
-- Idempotent. Ownership only.
--
-- The table is the migration ledger of the vegetation-prime backend, whose
-- runner reads and writes it on every start; core keeps no ledger of its own.
-- 106 assigned it to nkz_core, so under its own role the vegetation backend
-- could not start. Runs after 106, so a re-run of 106 is corrected here.
-- =============================================================================

DO $$ BEGIN
  IF to_regclass('public.schema_migrations') IS NOT NULL
     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nkz_module_vegetation_prime') THEN
    ALTER TABLE public.schema_migrations OWNER TO nkz_module_vegetation_prime;
  END IF;
END $$;
