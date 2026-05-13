-- Migration 075: Add wind_gusts and precipitation_probability to weather_observations
-- Required for agro-status spraying semaphore (wind gusts) and rain risk forecasting

ALTER TABLE weather_observations
    ADD COLUMN IF NOT EXISTS wind_gusts_ms DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS precip_probability DOUBLE PRECISION;
