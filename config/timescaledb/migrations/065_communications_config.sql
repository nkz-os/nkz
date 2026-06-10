-- 065: Communications module configuration
-- Stores bot config, notification templates, and stream templates
-- for the Zulip communications hub.

CREATE TABLE IF NOT EXISTS admin_platform.communications_config (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Default stream templates
INSERT INTO admin_platform.communications_config (key, value)
VALUES (
    'stream_templates',
    '[
        {"suffix": "general", "description": "Open team communication"},
        {"suffix": "alerts", "description": "Automated IoT and risk alerts"}
    ]'::jsonb
)
ON CONFLICT (key) DO NOTHING;

-- Default notification templates
INSERT INTO admin_platform.communications_config (key, value)
VALUES (
    'notification_templates',
    '[
        {
            "id": "iot_alert",
            "name": "IoT Alert",
            "topic": "iot-alerts",
            "template": "**{severity} Alert** — {sensor_name}\n\nValue: `{value}` (threshold: `{threshold}`)\nTime: {timestamp}\n\n[View entity]({entity_link})"
        },
        {
            "id": "risk_warning",
            "name": "Risk Warning",
            "topic": "risk-warnings",
            "template": "**Risk: {risk_type}** — {parcel_name}\n\nLevel: {level}\nDetails: {details}\nTime: {timestamp}"
        },
        {
            "id": "maintenance",
            "name": "Maintenance Notice",
            "topic": "maintenance",
            "template": "**Scheduled Maintenance**\n\nDate: {date}\nDuration: {duration}\nAffected services: {services}\n\n{details}"
        }
    ]'::jsonb
)
ON CONFLICT (key) DO NOTHING;

-- Bot configuration placeholder
INSERT INTO admin_platform.communications_config (key, value)
VALUES (
    'bot_config',
    '{"announcements_stream": "platform-announcements"}'::jsonb
)
ON CONFLICT (key) DO NOTHING;
