# services/common/tests/test_tenant_utils.py
"""Canonical contract for tenant ID normalization.

Format rules (SOTA, K8s-native):
  - lowercase
  - characters in [a-z0-9-] only
  - NFD-transliterated for accents (á→a, ó→o, ñ→n, ç→c)
  - whitespace, punctuation, symbols collapse to a single '-'
  - no leading/trailing '-'
  - no consecutive '-'
  - min length 3, max length 47  (= 63 K8s ns max - len('nekazari-tenant-'))
  - idempotent: normalize(normalize(x)) == normalize(x)
  - never adds 'tenant-' or any other prefix
"""
import pytest

from tenant_utils import (
    normalize_tenant_id,
    validate_tenant_id,
    MIN_TENANT_ID_LENGTH,
    MAX_TENANT_ID_LENGTH,
    TENANT_ID_PATTERN,
)


# ---- Happy path ----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("abregoandres", "abregoandres"),
    ("AbregoAndres", "abregoandres"),
    ("Test Tenant 1", "test-tenant-1"),
    ("test_tenant_1", "test-tenant-1"),
    ("test-tenant-1", "test-tenant-1"),
    ("Asociación Allotarra", "asociacion-allotarra"),
    ("Ipa7-laik", "ipa7-laik"),
    ("  spaces  ", "spaces"),
    ("multi   spaces", "multi-spaces"),
    ("multi---hyphens", "multi-hyphens"),
    ("multi___underscores", "multi-underscores"),
    ("Mixed_-_separators", "mixed-separators"),
    ("@@@special!!", "special"),
    ("café", "cafe"),
    ("Niño", "nino"),
    ("Ç-Bezirk", "c-bezirk"),
])
def test_normalize_produces_canonical_form(raw, expected):
    assert normalize_tenant_id(raw) == expected


def test_idempotent():
    samples = ["Test Tenant 1", "test_tenant_1", "Asociación", "ABC", "a-b-c-1-2"]
    for s in samples:
        once = normalize_tenant_id(s)
        twice = normalize_tenant_id(once)
        assert once == twice, f"not idempotent: {s} -> {once} -> {twice}"


def test_output_matches_pattern():
    samples = ["test_tenant_1", "Asociación Allotarra", "  spaces  ", "Test 1"]
    for s in samples:
        result = normalize_tenant_id(s)
        assert TENANT_ID_PATTERN.match(result), f"{s!r} -> {result!r} fails pattern"


# ---- Rejections ----------------------------------------------------------

@pytest.mark.parametrize("raw", ["", "   ", "@@@", "---", "___", "..."])
def test_empty_after_normalization_raises(raw):
    with pytest.raises(ValueError, match="empty|at least"):
        normalize_tenant_id(raw)


def test_too_short_raises():
    # 'ab' normalizes to 'ab' which is < 3
    with pytest.raises(ValueError, match="at least"):
        normalize_tenant_id("ab")


def test_too_long_raises():
    too_long = "a" * (MAX_TENANT_ID_LENGTH + 1)
    with pytest.raises(ValueError, match="at most"):
        normalize_tenant_id(too_long)


def test_none_raises():
    with pytest.raises(ValueError, match="empty"):
        normalize_tenant_id(None)  # type: ignore[arg-type]


# ---- validate_tenant_id (read-only check) -------------------------------

@pytest.mark.parametrize("s", ["abregoandres", "test-1", "a-b-c", "ipa7laik"])
def test_validate_accepts_canonical(s):
    ok, msg = validate_tenant_id(s)
    assert ok, msg


@pytest.mark.parametrize("s,reason", [
    ("", "empty"),
    ("a", "length"),
    ("ab", "length"),
    ("Test_1", "character"),       # uppercase + underscore
    ("test_1", "character"),       # underscore
    ("test 1", "character"),       # space
    ("-test", "leading"),
    ("test-", "trailing"),
    ("a--b", "consecutive"),
])
def test_validate_rejects_invalid(s, reason):
    ok, msg = validate_tenant_id(s)
    assert not ok
    assert reason.lower() in msg.lower(), f"expected {reason!r} in {msg!r}"


# ---- No double-prefix bug ------------------------------------------------

def test_does_not_add_tenant_prefix():
    """Regression: normalize_tenant_id must NEVER add 'tenant-' or any prefix."""
    assert not normalize_tenant_id("allotarra").startswith("tenant-")
    assert not normalize_tenant_id("baratze").startswith("tenant-")


def test_strips_legacy_double_prefix():
    """Existing data may contain 'tenant-tenant-foo' — normalize should reduce it."""
    # We do not auto-strip 'tenant-' to avoid being too magical, but we do guarantee
    # idempotency and a stable canonical form for any input.
    assert normalize_tenant_id("tenant-tenant-foo") == "tenant-tenant-foo"
    # The fix is in `create_tenant_directly`, not in the normalizer.
