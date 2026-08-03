"""Unit tests for the canonical HMAC signature primitive (crypto.py).

Signature format is FROZEN platform-wide (do not change):
    payload = "{data}|{tenant_id}|{timestamp}"      (pipe-separated)
    output  = "{HMAC-SHA256 hexdigest}:{timestamp}"  (colon-separated, sig first,
              full 64-char hex, no truncation)
Verification is constant-time (hmac.compare_digest), 5-minute (300s) default window.
Reference: services/common/keycloak_auth.py:generate_hmac_signature
"""

import time

from nkz_platform_sdk.crypto import generate_hmac_signature, verify_hmac_signature

SECRET = "test-secret"


def test_round_trip_generate_then_verify_passes() -> None:
    sig = generate_hmac_signature(SECRET, "some-token", "acme")
    assert verify_hmac_signature(SECRET, sig, "some-token", "acme") is True


def test_output_format_is_hex_colon_timestamp() -> None:
    ts = 1_800_000_000
    sig = generate_hmac_signature(SECRET, "tok", "acme", timestamp=ts)
    hex_part, _, ts_part = sig.partition(":")
    assert len(hex_part) == 64
    assert all(c in "0123456789abcdef" for c in hex_part)
    assert ts_part == str(ts)


def test_tampered_signature_fails() -> None:
    sig = generate_hmac_signature(SECRET, "tok", "acme")
    hex_part, _, ts_part = sig.partition(":")
    tampered_char = "0" if hex_part[0] != "0" else "1"
    tampered = tampered_char + hex_part[1:] + ":" + ts_part
    assert verify_hmac_signature(SECRET, tampered, "tok", "acme") is False


def test_wrong_secret_fails() -> None:
    sig = generate_hmac_signature(SECRET, "tok", "acme")
    assert verify_hmac_signature("other-secret", sig, "tok", "acme") is False


def test_wrong_tenant_fails() -> None:
    sig = generate_hmac_signature(SECRET, "tok", "acme")
    assert verify_hmac_signature(SECRET, sig, "tok", "other-tenant") is False


def test_wrong_data_fails() -> None:
    sig = generate_hmac_signature(SECRET, "tok", "acme")
    assert verify_hmac_signature(SECRET, sig, "different-tok", "acme") is False


def test_expired_timestamp_fails() -> None:
    old_ts = int(time.time()) - 600
    sig = generate_hmac_signature(SECRET, "tok", "acme", timestamp=old_ts)
    assert verify_hmac_signature(SECRET, sig, "tok", "acme") is False


def test_malformed_header_no_colon_fails() -> None:
    assert verify_hmac_signature(SECRET, "not-even-a-hmac", "tok", "acme") is False


def test_malformed_header_non_integer_timestamp_fails() -> None:
    assert verify_hmac_signature(SECRET, "abc123:not-a-number", "tok", "acme") is False


def test_malformed_header_too_many_parts_fails() -> None:
    assert verify_hmac_signature(SECRET, "a:b:c", "tok", "acme") is False


def test_empty_secret_returns_fail_open_true() -> None:
    sig = generate_hmac_signature("some-secret", "tok", "acme")
    assert verify_hmac_signature("", sig, "tok", "acme", fail_open=True) is True


def test_empty_secret_returns_fail_open_false() -> None:
    sig = generate_hmac_signature("some-secret", "tok", "acme")
    assert verify_hmac_signature("", sig, "tok", "acme", fail_open=False) is False


def test_empty_signature_header_returns_fail_open_true() -> None:
    assert verify_hmac_signature(SECRET, "", "tok", "acme", fail_open=True) is True


def test_empty_signature_header_returns_fail_open_false() -> None:
    assert verify_hmac_signature(SECRET, "", "tok", "acme", fail_open=False) is False


def test_boundary_exactly_300_seconds_passes() -> None:
    ts = int(time.time()) - 300
    sig = generate_hmac_signature(SECRET, "tok", "acme", timestamp=ts)
    assert verify_hmac_signature(SECRET, sig, "tok", "acme") is True


def test_boundary_301_seconds_fails() -> None:
    ts = int(time.time()) - 301
    sig = generate_hmac_signature(SECRET, "tok", "acme", timestamp=ts)
    assert verify_hmac_signature(SECRET, sig, "tok", "acme") is False


def test_custom_window_seconds_respected() -> None:
    ts = int(time.time()) - 30
    sig = generate_hmac_signature(SECRET, "tok", "acme", timestamp=ts)
    assert verify_hmac_signature(SECRET, sig, "tok", "acme", window_seconds=10) is False
    assert verify_hmac_signature(SECRET, sig, "tok", "acme", window_seconds=60) is True
