-- =============================================================================
-- Migration 110: Global water_meter sensor profile
-- =============================================================================
-- Cumulative irrigation volume reported by a flowmeter, in litres. Device type
-- per 099 (canonical device types). A meter that reports another unit is scaled
-- at registration with a measurement `expression`.
--
-- Idempotent: re-applying leaves an existing row untouched.
-- =============================================================================

INSERT INTO sensor_profiles (
    tenant_id,
    code,
    name,
    description,
    sdm_entity_type,
    sdm_category,
    default_units,
    mapping,
    metadata
) VALUES (
    NULL, 'water_meter', 'Water meter', 'Cumulative water volume (irrigation flowmeter)', 'Device', 'irrigation',
    jsonb_build_array('L'),
    jsonb_build_object(
        'version', 1,
        'measurements', jsonb_build_array(
            jsonb_build_object(
                'type', 'waterConsumption',
                'sdmAttribute', 'waterConsumption',
                'unit', 'L'
            )
        )
    ),
    jsonb_build_object('measurementKind', 'irrigation')
)
ON CONFLICT (COALESCE(tenant_id, '__global__'), code) DO NOTHING;
