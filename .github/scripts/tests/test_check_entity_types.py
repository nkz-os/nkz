"""Entity type lists: parsing and loading."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check_entity_types.py"
REPO_ROOT = Path(__file__).resolve().parents[3]

_spec = importlib.util.spec_from_file_location("check_entity_types", SCRIPT)
cet = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cet
_spec.loader.exec_module(cet)
parse_allowed, parse_legacy, load_lists = cet.parse_allowed, cet.parse_legacy, cet.load_lists
Lists, extract_type_names, is_vocabulary = cet.Lists, cet.extract_type_names, cet.is_vocabulary
path_is_scanned, scan_diff = cet.path_is_scanned, cet.scan_diff


def test_parse_allowed_ignores_comments_and_blanks():
    assert parse_allowed("# c\n\nAgriCrop\nAlert\n") == ["AgriCrop", "Alert"]


def test_parse_legacy_splits_blocks():
    text = "# c\n## broker\nFooAsset\n\n## code\n# note\nBarRecord\nBazThing\n"
    assert parse_legacy(text) == (["FooAsset"], ["BarRecord", "BazThing"])


def test_parse_legacy_rejects_name_before_block():
    with pytest.raises(ValueError):
        parse_legacy("FooAsset\n## broker\n")


def test_repo_lists_load_and_do_not_overlap():
    lists = load_lists(REPO_ROOT / "config" / "entity-types")
    assert len(lists.allowed) == 20 and len(lists.legacy_broker) == 13 and len(lists.legacy_code) == 78
    assert not (lists.allowed & (lists.legacy_broker | lists.legacy_code))
    assert not (lists.legacy_broker & lists.legacy_code)
    assert "AgriParcel" in lists.allowed and "AgriPlant" in lists.legacy_broker and "AgriEnergyTracker" in lists.legacy_code


@pytest.mark.parametrize("line,expected", [
    ('id = f"urn:ngsi-ld:FooAsset:{tenant}:{x}"', ["FooAsset"]),
    ('{"type": "FooAsset", "id": x}', ["FooAsset"]),
    ("entity_type = 'FooAsset'", ["FooAsset"]),
    ('if entity["@type"] == "FooAsset":', ["FooAsset"]),
    ("target_sdm_type='FooAsset'", ["FooAsset"]),
    ('url = base + "?type=FooAsset,nkz:BarRecord&limit=10"', ["FooAsset", "BarRecord"]),
    ('"FooAsset": "nkz:FooAsset",', ["FooAsset"]),
    ('"FooAsset": "https://nkz-os.org/ns/FooAsset"', ["FooAsset"]),
    ("entities: ['AgriParcel', 'nkz:FooAsset'],", ["AgriParcel", "FooAsset"]),
    ('"zoneId": "nkz:zoneId"', []),
    ("x = compute(a, b)", []),
])
def test_extract_type_names(line, expected):
    assert extract_type_names(line) == expected


@pytest.mark.parametrize("name", ["Property", "Relationship", "GeoProperty", "Polygon", "FeatureCollection",
                                  "Deployment", "SELECT", "CabeceraType", "Tipo", "Mota"])
def test_vocabulary_is_not_a_type(name):
    assert is_vocabulary(name)


def test_entity_names_are_not_vocabulary():
    assert not is_vocabulary("FooAsset")
    assert not is_vocabulary("AgriParcel")


@pytest.mark.parametrize("path,scanned", [
    ("services/foo/app.py", True), ("apps/host/src/x.tsx", True), ("config/ngsi-ld-context.json", True),
    ("services/foo/tests/test_app.py", False), ("README.md", False), ("k8s/deploy.yaml", False),
    ("src/locales/es.json", False), ("config/entity-types/legacy.txt", False),
    ("apps/host/src/latest.tsx", True), ("apps/host/src/x.test.tsx", False), ("apps/host/e2e/visual.spec.ts", False),
    ("backend/app/contest.py", True), ("backend/conftest.py", False),
    ("backend/docs/x.py", False), ("deploy/k8s/x.json", False), (".hygiene-tooling/x.py", False),
    (".github/scripts/check_entity_types.py", False), ("package-lock.json", False),
])
def test_path_is_scanned(path, scanned):
    assert path_is_scanned(path) is scanned


DIFF = """diff --git a/services/foo/app.py b/services/foo/app.py
--- a/services/foo/app.py
+++ b/services/foo/app.py
@@ -10,3 +10,6 @@ def f():
 context = 1
+entity = {"type": "FooAsset"}
+ok = {"type": "AgriParcel"}
+geo = {"type": "Point"}
-old = {"type": "GoneThing"}
+label = {"type": "FooLabel"}  # sdm:not-a-type
diff --git a/services/foo/tests/test_app.py b/services/foo/tests/test_app.py
--- a/services/foo/tests/test_app.py
+++ b/services/foo/tests/test_app.py
@@ -1,1 +1,2 @@
+entity = {"type": "BarRecord"}
"""


def test_scan_diff_reports_only_unknown_added_names():
    lists = Lists(frozenset({"AgriParcel"}), frozenset(), frozenset())
    findings = scan_diff(DIFF, lists)
    assert [(f.path, f.line_no, f.name) for f in findings] == [("services/foo/app.py", 11, "FooAsset")]


def test_scan_diff_accepts_both_legacy_blocks():
    lists = Lists(frozenset({"AgriParcel"}), frozenset({"FooAsset"}), frozenset({"BarRecord"}))
    diff = DIFF.replace("tests/test_app.py", "more.py")
    assert scan_diff(diff, lists) == []
