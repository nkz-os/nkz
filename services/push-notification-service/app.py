#!/usr/bin/env python3
# =============================================================================
# Push Notification Service — Expo Push API
# =============================================================================
# Handles device token registration and push notification dispatch.
# Uses Expo Push API (https://docs.expo.dev/push-notifications/sending-notifications/)
#
# Endpoints:
#   POST /register      — mobile app registers device token
#   POST /send/push     — internal services dispatch push notifications
#   GET  /health        — health check
# =============================================================================

import json
import logging
import os
import sys

import psycopg2
import psycopg2.extras
import requests
from flask import Flask, g, jsonify, request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "")
POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    f"postgresql://{os.getenv('POSTGRES_USER', 'nekazari')}:"
    f"{os.getenv('POSTGRES_PASSWORD', '')}@"
    f"{os.getenv('POSTGRES_HOST', 'postgresql-service')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/"
    f"{os.getenv('POSTGRES_DB', 'nekazari')}",
)


def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(POSTGRES_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        g.db.autocommit = True
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    g.pop("db", None)


# ── Health ────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT 1")
        cur.close()
        return jsonify({"status": "healthy", "service": "push-notification"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 503


# ── Token Registration ────────────────────────────────────────────────────────

@app.route("/register", methods=["POST"])
def register_device():
    """Register a device push token. Called by nkz-mobile on startup."""
    data = request.get_json(silent=True) or {}
    token = data.get("token", "").strip()
    platform = data.get("platform", "unknown")
    user_id = data.get("user_id", "")
    tenant_id = data.get("tenant_id", "")

    if not token or not user_id or not tenant_id:
        return jsonify({"error": "token, user_id, and tenant_id are required"}), 400

    try:
        db = get_db()
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO user_push_tokens (user_id, tenant_id, expo_token, platform, device_info)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (expo_token) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                tenant_id = EXCLUDED.tenant_id,
                platform = EXCLUDED.platform,
                updated_at = NOW()
            """,
            (user_id, tenant_id, token, platform, json.dumps(data.get("device_info", {}))),
        )
        cur.close()
        logger.info("Push token registered: user=%s tenant=%s platform=%s", user_id, tenant_id, platform)
        return jsonify({"status": "registered"}), 200
    except Exception as e:
        logger.error("Token registration failed: %s", e)
        return jsonify({"error": str(e)}), 500


# ── Send Push ─────────────────────────────────────────────────────────────────

@app.route("/send/push", methods=["POST"])
def send_push():
    """Dispatch push notification via Expo Push API.

    Called by risk-orchestrator and other internal services.
    Requires X-Internal-Secret header for auth.

    Body: {
        "tenant_id": "...",
        "user_ids": ["..."],   // optional, if omitted sends to all tenant devices
        "title": "CWSI Critical",
        "body": "Olivar Sur-03: CWSI 0.72",
        "data": {"screen": "module/crop-health", "parcelId": "..."}
    }
    """
    if request.headers.get("X-Internal-Secret") != INTERNAL_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    tenant_id = data.get("tenant_id", "")
    user_ids = data.get("user_ids")
    title = data.get("title", "Nekazari Alert")
    body = data.get("body", "")
    notification_data = data.get("data", {})

    if not tenant_id or not body:
        return jsonify({"error": "tenant_id and body are required"}), 400

    try:
        db = get_db()
        cur = db.cursor()

        if user_ids:
            cur.execute(
                "SELECT expo_token FROM user_push_tokens "
                "WHERE tenant_id = %s AND user_id = ANY(%s) AND is_active = true",
                (tenant_id, user_ids),
            )
        else:
            cur.execute(
                "SELECT expo_token FROM user_push_tokens "
                "WHERE tenant_id = %s AND is_active = true",
                (tenant_id,),
            )
        tokens = [row["expo_token"] for row in cur.fetchall()]
        cur.close()

        if not tokens:
            return jsonify({"status": "no_tokens", "message": "No registered devices for this tenant"}), 200

        # Expo Push API: send in batches
        messages = [
            {
                "to": t,
                "title": title,
                "body": body,
                "data": notification_data,
                "sound": "default",
                "priority": "high",
            }
            for t in tokens
        ]

        # Expo accepts up to 100 messages per request
        results = []
        for i in range(0, len(messages), 100):
            batch = messages[i : i + 100]
            resp = requests.post(EXPO_PUSH_URL, json=batch, timeout=10)
            results.append(resp.json() if resp.ok else {"error": resp.text})

        logger.info("Push sent: %d tokens, tenant=%s", len(tokens), tenant_id)
        return jsonify({"status": "sent", "tokens": len(tokens), "results": results}), 200

    except Exception as e:
        logger.error("Push dispatch failed: %s", e)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("DEBUG", "").lower() == "true")
