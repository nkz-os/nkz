-- 103_agent_module_role.sql
-- Least-privilege DB role for the conversational agent module (nkz-module-agent).
--
-- WHY: agent_turn_audit is born with ENABLE + FORCE ROW LEVEL SECURITY
-- (migration 102), but RLS is inert for superusers (rolbypassrls skips it
-- unconditionally, FORCE included), and until now the module connected as
-- `postgres`. This role is what makes the migration 102 policy actually
-- protect something: connected as agent_module, RLS applies.
--
-- The password is NOT set here (public repo): it is assigned out-of-band once
-- per deployment (ALTER ROLE ... PASSWORD) and lives only in the module's
-- SealedSecret. Everything below is idempotent, so re-running is safe.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_module') THEN
        CREATE ROLE agent_module LOGIN;
    END IF;
END $$;

-- current_database() keeps the migration portable across deployment names.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO agent_module', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO agent_module;

-- Identity / dedup tables (migration 101): the module owns these rows'
-- lifecycle (link, unlink, expire tokens, prune processed updates).
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE agent_channel_links,
    agent_link_tokens, agent_processed_updates TO agent_module;

-- Audit table (migration 102): append + read (quota counts read it). Never
-- UPDATE/DELETE: an audit trail that can be rewritten is not an audit trail.
GRANT INSERT, SELECT ON TABLE agent_turn_audit TO agent_module;

-- Both agent tables with surrogate keys carry a BIGSERIAL: INSERT fails
-- without sequence usage. Distinguish by error message when testing: a
-- sequence-permission failure is NOT an RLS rejection, and mixing them up
-- hides the real guarantee. (agent_link_tokens and agent_processed_updates
-- use natural keys — no sequences there.)
GRANT USAGE, SELECT ON SEQUENCE agent_channel_links_id_seq,
    agent_turn_audit_id_seq TO agent_module;
