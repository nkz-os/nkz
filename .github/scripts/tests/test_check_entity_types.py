"""Entity type lists: parsing and loading."""

import ast
import importlib.util
import json
import re
import subprocess
import sys
import time
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
check_ratchet, context_iri = cet.check_ratchet, cet.context_iri


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
    assert len(lists.allowed) == 22 and len(lists.legacy_broker) == 12 and len(lists.legacy_code) == 82
    assert not (lists.allowed & (lists.legacy_broker | lists.legacy_code))
    assert not (lists.legacy_broker & lists.legacy_code)
    assert "AgriParcel" in lists.allowed and "AgriPlant" in lists.legacy_broker and "AgriEnergyTracker" in lists.legacy_code


@pytest.mark.parametrize("line,expected", [
    ('id = f"urn:ngsi-ld:FooAsset:{tenant}:{x}"', ["FooAsset"]),
    ('{"type": "FooAsset", "id": x}', ["FooAsset"]),
    ("entity_type = 'FooAsset'", ["FooAsset"]),
    ('if entity["@type"] == "FooAsset":', ["FooAsset"]),
    ("target_sdm_type='FooAsset'", ["FooAsset"]),
    ('if (e.type === "FooAsset") {', ["FooAsset"]),
    ("if entity_type != 'FooAsset':", ["FooAsset"]),
    ('x.type !== "FooAsset"', ["FooAsset"]),
    ('ENTITY_TYPE = "FooAsset"', ["FooAsset"]),
    ('PV_TYPE = "FooAsset"', ["FooAsset"]),
    ("PROFILE_ENTITY_TYPE = 'FooAsset'", ["FooAsset"]),
    ('type: str = "FooAsset"', ["FooAsset"]),
    ('type: Optional[str] = "FooAsset"', ["FooAsset"]),
    ('entity_type: Literal["FooAsset"] = "FooAsset"', ["FooAsset"]),
    ('sdm_entity_type = "FooAsset"', ["FooAsset"]),
    ('{"soilType": "Cambisol"}', []),
    ('{"sampleType": "Leaf"}', []),
    ('service_type = "Internal"', []),
    ('data_type: "Number"', []),
    ('mimeType: "Image"', []),
    ('CONTENT_TYPE = "application/json"', []),
    ('url = base + "?type=FooAsset,nkz:BarRecord&limit=10"', ["FooAsset", "BarRecord"]),
    ('"FooAsset": "nkz:FooAsset",', ["FooAsset"]),
    ('"FooAsset": "https://nkz-os.org/ns/FooAsset"', ["FooAsset"]),
    ("entities: ['AgriParcel', 'nkz:FooAsset'],", ["AgriParcel", "FooAsset"]),
    ('"zoneId": "nkz:zoneId"', []),
    ("x = compute(a, b)", []),
])
def test_extract_type_names(line, expected):
    assert extract_type_names(line) == expected


def _elapsed(line):
    start = time.perf_counter()
    result = extract_type_names(line)
    return result, time.perf_counter() - start


def test_extract_type_names_is_linear_on_hostile_lines():
    result, took = _elapsed("type:" + " " * 5000)
    assert result == [] and took < 0.5
    _, took = _elapsed("type: " + "a " * 5000)
    assert took < 0.5
    _, took = _elapsed("x" * 1_000_000)
    assert took < 2
    _, took = _elapsed("entities:[ " * 90000)
    assert took < 2
    line = '{"soilType": "Cambisol", "type": "Point"}, ' * (1_000_000 // 44)
    result, took = _elapsed(line)
    assert took < 5
    assert [n for n in result if not is_vocabulary(n)] == []


@pytest.mark.parametrize("name", ["Property", "Relationship", "GeoProperty", "Polygon", "FeatureCollection",
                                  "Deployment", "SELECT", "CabeceraType", "ParcelasType", "Tipo", "Mota"])
def test_vocabulary_is_not_a_type(name):
    assert is_vocabulary(name)


def test_entity_names_are_not_vocabulary():
    assert not is_vocabulary("FooAsset")
    assert not is_vocabulary("AgriParcel")


def test_a_name_ending_in_type_is_not_vocabulary_by_itself():
    assert is_vocabulary("ZoneType") is False
    assert is_vocabulary("AgriProductType") is False
    assert is_vocabulary("CabeceraType") is True


@pytest.mark.parametrize("path,scanned", [
    ("services/foo/app.py", True), ("apps/host/src/x.tsx", True), ("config/ngsi-ld-context.json", True),
    ("services/foo/tests/test_app.py", False), ("README.md", False), ("k8s/deploy.yaml", False),
    ("src/locales/es.json", False), ("config/entity-types/legacy.txt", False),
    ("apps/host/src/latest.tsx", True), ("apps/host/src/x.test.tsx", False), ("apps/host/e2e/visual.spec.ts", False),
    ("backend/app/contest.py", True), ("backend/conftest.py", False),
    ("backend/docs/x.py", False), ("deploy/k8s/x.json", False), (".hygiene-tooling/x.py", False),
    (".github/scripts/check_entity_types.py", False),
    ("src/i18n.ts", False), ("src/i18nHelper.ts", True), ("package-lock.json", False),
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


def test_scan_diff_ignores_a_deleted_file():
    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-entity = {\"type\": \"FooAsset\"}\n"
    assert scan_diff(diff, Lists(frozenset(), frozenset(), frozenset())) == []


def test_scan_diff_parses_a_hunk_header_without_counts():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old = 1\n+entity = {\"type\": \"FooAsset\"}\n"
    findings = scan_diff(diff, Lists(frozenset(), frozenset(), frozenset()))
    assert [(f.path, f.line_no, f.name) for f in findings] == [("x.py", 1, "FooAsset")]


def _run_diff_cli(stdin):
    data = stdin if isinstance(stdin, bytes) else stdin.encode("utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "diff", "--lists", str(REPO_ROOT / "config" / "entity-types")],
        input=data, capture_output=True, check=False,
    )


def test_diff_cli_fails_on_an_unknown_type():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old = 1\n+entity = {\"type\": \"FooAsset\"}\n"
    result = _run_diff_cli(diff)
    assert result.returncode == 1
    out = result.stdout.decode("utf-8")
    assert "x.py:1: entity type 'FooAsset' is not in the lists" in out
    assert cet.CLOSING_PARAGRAPH in out


def test_diff_cli_passes_on_empty_input():
    result = _run_diff_cli("")
    assert result.returncode == 0 and result.stdout == b""


def test_diff_cli_reports_an_internal_error_with_status_3():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old = 1\n+entity = {\"type\": \"FooAsset\"}\n"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "diff", "--lists", "/nonexistent-entity-types-dir"],
        input=diff, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 3
    assert result.stderr.startswith("internal error:")


def test_diff_cli_survives_a_non_utf8_byte():
    diff = b'--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n-old = 1\n+caf\xe9 = 1\n+entity = {"type": "FooAsset"}\n'
    result = _run_diff_cli(diff)
    assert result.returncode == 1
    assert "x.py:2: entity type 'FooAsset' is not in the lists" in result.stdout.decode("utf-8")


def test_scan_diff_line_breaks_inside_a_line_do_not_shift_the_hunk():
    diff = '--- a/x.py\n+++ b/x.py\n@@ -0,0 +1,2 @@\n+a = "x\x0cy\u2028z"\n+entity = {"type": "FooAsset"}\n'
    findings = scan_diff(diff, Lists(frozenset(), frozenset(), frozenset()))
    assert [(f.path, f.line_no, f.name) for f in findings] == [("x.py", 2, "FooAsset")]


def test_scan_diff_tolerates_carriage_returns():
    diff = '--- a/x.py\r\n+++ b/x.py\r\n@@ -0,0 +1,1 @@\r\n+entity = {"type": "FooAsset"}\r\n'
    findings = scan_diff(diff, Lists(frozenset(), frozenset(), frozenset()))
    assert [(f.path, f.line_no, f.name) for f in findings] == [("x.py", 1, "FooAsset")]


def test_scan_diff_reads_a_quoted_non_ascii_path():
    diff = '--- "a/src/a\\303\\261o.py"\n+++ "b/src/a\\303\\261o.py"\n@@ -0,0 +1,1 @@\n+entity = {"type": "FooAsset"}\n'
    findings = scan_diff(diff, Lists(frozenset(), frozenset(), frozenset()))
    assert [(f.line_no, f.name) for f in findings] == [(1, "FooAsset")]
    assert findings[0].path.startswith("src/a")


def test_inventory_line_numbers_ignore_form_feeds(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / "a.py").write_text('x = "a\x0cb"\n{"type": "FooAsset"}\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    assert cet.inventory(tmp_path, Lists(frozenset(), frozenset(), frozenset())) == {"FooAsset": ["a.py:2"]}


def test_inventory_lists_unknown_names_with_locations(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "tests").mkdir()
    (tmp_path / "a.py").write_text('{"type": "FooAsset"}\n{"type": "AgriParcel"}\n')
    (tmp_path / "tests" / "test_a.py").write_text('{"type": "BarRecord"}\n')
    git("add", "-A")
    lists = Lists(frozenset({"AgriParcel"}), frozenset(), frozenset())
    assert cet.inventory(tmp_path, lists) == {"FooAsset": ["a.py:1"]}


def _never():
    raise AssertionError("official list must not be fetched")


A = "AgriCrop\nAlert\n"
L = "## broker\nFooAsset\n\n## code\nBarRecord\nBazThing\n"


def test_ratchet_clean_without_download():
    assert check_ratchet(A, L, A, L, _never) == []


def test_ratchet_legacy_may_shrink_and_move_between_blocks():
    head = "## broker\nBarRecord\nFooAsset\n\n## code\n"
    assert check_ratchet(A, L, A, head, _never) == []


def test_ratchet_legacy_growth_is_an_offence():
    head = L + "NewThing\n"
    assert check_ratchet(A, L, A, head, _never) == ["legacy.txt may only lose names: 'NewThing' was added"]


def test_ratchet_allowed_growth_needs_the_official_list():
    head = "AgriCrop\nAlert\nTank\n"
    assert check_ratchet(A, L, head, L, lambda: {"Tank"}) == []
    assert check_ratchet(A, L, head, L, lambda: set()) == ["'Tank' is not in the official list of Smart Data Models"]


def test_ratchet_download_failure_is_reported():
    def boom():
        raise RuntimeError("could not download the official list: timeout")
    assert check_ratchet(A, L, "AgriCrop\nAlert\nTank\n", L, boom) == ["could not download the official list: timeout"]


def test_ratchet_overlap_duplicate_and_order():
    msgs = check_ratchet(A, L, "Alert\nAgriCrop\nAlert\n", "## broker\nAlert\n\n## code\n", _never)
    assert any("both" in m for m in msgs) and any("duplicate" in m for m in msgs) and any("sorted" in m for m in msgs)


def test_ratchet_first_introduction():
    assert check_ratchet(None, None, A, L, lambda: {"AgriCrop", "Alert"}) == []


def string_constants(path, variable):
    """String elements of the set/list/tuple literal assigned to a module-level name."""
    for node in ast.parse(Path(path).read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == variable for t in node.targets):
            assert isinstance(node.value, (ast.Set, ast.List, ast.Tuple)), variable
            return {e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    raise AssertionError(f"{variable} not found in {path}")


def returned_dict_keys(path, function):
    """String keys of the dict literal a function returns (directly or through a local variable)."""
    for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            assigned = {}
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Dict):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            assigned[t.id] = sub.value
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return):
                    value = sub.value
                    if isinstance(value, ast.Name):
                        value = assigned.get(value.id)
                    if isinstance(value, ast.Dict):
                        return {k.value for k in value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            raise AssertionError(f"{function} returns no dict literal that can be read statically")
    raise AssertionError(f"{function} not found in {path}")


# Static checks on the real files. No network.
CONTEXT = json.loads((REPO_ROOT / "config" / "ngsi-ld-context.json").read_text())
ALLOWED_IRI_EXCEPTIONS = {"AgriParcel": "https://saref.etsi.org/saref4agri/AgriParcel"}
NO_CONTEXT_TERM = {"AgriManagementZone"}


def test_allowed_names_expand_to_a_smart_data_models_iri():
    lists = load_lists(REPO_ROOT / "config" / "entity-types")
    for name in sorted(lists.allowed):
        iri = context_iri(CONTEXT, name)
        if name in ALLOWED_IRI_EXCEPTIONS:
            assert iri == ALLOWED_IRI_EXCEPTIONS[name]
        else:
            assert iri and iri.startswith("https://smartdatamodels.org/dataModel.") and iri.endswith("/" + name), name


def test_broker_block_names_expand_to_the_platform_namespace():
    lists = load_lists(REPO_ROOT / "config" / "entity-types")
    for name in sorted(lists.legacy_broker - NO_CONTEXT_TERM):
        assert context_iri(CONTEXT, name) == "https://nkz-os.org/ns/" + name, name
    for name in NO_CONTEXT_TERM:
        assert context_iri(CONTEXT, name) is None


def test_in_code_type_catalogues_are_inside_the_lists():
    known = load_lists(REPO_ROOT / "config" / "entity-types").known
    names = (
        string_constants(REPO_ROOT / "services/entity-manager/blueprints/entities.py", "SDM_ALLOWED_TYPES")
        | string_constants(REPO_ROOT / "services/sdm-integration/sdm_api.py", "IOT_ENTITY_TYPES")
        | returned_dict_keys(REPO_ROOT / "services/sdm-integration/sdm_api.py", "get_sdm_entities")
    )
    assert names and not (names - known), sorted(names - known)


def _object_literal(text, name):
    start = text.index(f"export const {name}")
    return text[text.index("{", text.index("=", start)):text.index("\n};", start)]


def test_host_wizard_catalogue_is_inside_the_lists():
    known = load_lists(REPO_ROOT / "config" / "entity-types").known
    text = (REPO_ROOT / "apps/host/src/components/EntityWizard/entityTypes.ts").read_text(encoding="utf-8")
    metadata = set(re.findall(r"^  ([A-Z][A-Za-z0-9]*)\s*:\s*\{", _object_literal(text, "ENTITY_TYPE_METADATA"), re.M))
    categories = set()
    for array in re.findall(r"\[([^\]]*)\]", _object_literal(text, "ENTITY_CATEGORIES")):
        categories |= set(re.findall(r"[\"']([A-Z][A-Za-z0-9]*)[\"']", array))
    assert metadata and categories
    assert not (metadata - known), sorted(metadata - known)
    assert not (categories - known), sorted(categories - known)


def _run_ratchet_cli(base):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "ratchet", "--base", base, "--lists", "config/entity-types"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )


def test_ratchet_cli_rejects_an_unknown_base_ref():
    result = _run_ratchet_cli("no-such-ref-zzz")
    assert result.returncode == 2
    assert result.stderr.strip() == "base ref 'no-such-ref-zzz' not found"


def test_ratchet_cli_clean_against_head():
    result = _run_ratchet_cli("HEAD")
    assert result.returncode == 0, result.stdout + result.stderr


def test_parse_legacy_rejects_unknown_block_header():
    with pytest.raises(ValueError, match="unknown block header"):
        parse_legacy("## broker\nA\n## other\nB\n")


def test_parse_legacy_keeps_plain_comments():
    assert parse_legacy("## broker\n# note\n# # two\nA\n## code\nB\n") == (["A"], ["B"])
