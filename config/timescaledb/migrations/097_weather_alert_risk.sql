-- 097_weather_alert_risk.sql
-- Seed the weather_alert risk: notify a tenant when an active MeteoAlarm
-- WeatherAlert geographically covers one of its parcels. Idempotent (Expand).
--
-- The risk-worker weather_alert model (model_type='weather_alert') fetches the
-- covering alerts per AgriParcel via weather-api and maps the most severe one to
-- a probability score; scores >= 50 dispatch a tenant notification through the
-- existing risk pipeline.

INSERT INTO admin_platform.risk_catalog (
    risk_code, risk_name, risk_description,
    target_sdm_type, data_sources, risk_domain,
    evaluation_mode, model_type, model_config,
    severity_levels, is_active
) VALUES (
    'weather_alert',
    'Weather Alert Coverage',
    'Active meteorological alert whose awareness zone covers the parcel.',
    'AgriParcel',
    '["weather_alerts"]'::jsonb,
    'agronomic',
    'batch',
    'weather_alert',
    '{}'::jsonb,
    '{"low": 30, "medium": 60, "high": 80, "critical": 95}'::jsonb,
    TRUE
)
ON CONFLICT (risk_code) DO UPDATE SET
    model_type   = EXCLUDED.model_type,
    data_sources = EXCLUDED.data_sources,
    target_sdm_type = EXCLUDED.target_sdm_type,
    evaluation_mode = EXCLUDED.evaluation_mode,
    is_active    = EXCLUDED.is_active,
    updated_at   = CURRENT_TIMESTAMP;
