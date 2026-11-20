import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest

import entity_type_guard as g

REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / "config"


@pytest.fixture(scope="module")
def accepted():
    return g.load_accepted(CONFIG)


def test_parse_legacy_name_before_header():
    with pytest.raises(ValueError):
        g.parse_legacy("FooAsset\n## broker\n")


def test_parse_legacy_unknown_header():
    with pytest.raises(ValueError, match="unknown block header"):
        g.parse_legacy("## broker\nA\n## other\nB\n")


def test_parse_legacy_comments_and_blanks():
    assert g.parse_legacy("# c\n\n## broker\n# x\nA\n\n## code\nB\n# # two\n") == (["A"], ["B"])


def test_load_real_files(accepted):
    assert "AgriParcel" in accepted.names and "AgriPlant" in accepted.names
    assert "AgriEnergyTracker" not in accepted.names
    assert "AgriculturalRobot" not in accepted.names
    assert "https://saref.etsi.org/saref4agri/AgriParcel" in accepted.iris
    assert "https://nkz-os.org/ns/AgriPlant" in accepted.iris
    # The zone has no term: a second IRI for it must not be admitted, and
    # names with an SDM IRI must not gain a platform-namespace twin.
    assert "https://nkz-os.org/ns/AgriManagementZone" not in accepted.iris
    assert "https://nkz-os.org/ns/Device" not in accepted.iris
    assert accepted.prefixes["nkz"] == "https://nkz-os.org/ns/"
    zone = "https://uri.etsi.org/ngsi-ld/default-context/AgriManagementZone"
    assert zone in accepted.iris
    others = [i for i in accepted.iris if i.startswith("https://uri.etsi.org/ngsi-ld/default-context/") and i != zone]
    assert others == []


def test_load_missing_files(tmp_path):
    with pytest.raises(g.GuardUnavailable):
        g.load_accepted(tmp_path)


def test_find_config_dir_override_is_only_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("ENTITY_TYPE_CONFIG_DIR", str(tmp_path))
    assert g.find_config_dir() is None
    (tmp_path / "entity-types").mkdir()
    (tmp_path / "entity-types" / "allowed.txt").write_text("")
    assert g.find_config_dir() == tmp_path


@pytest.mark.parametrize("body,expected", [
    ({"type": "A"}, ["A"]),
    ({"@type": "A"}, ["A"]),
    ({"type": ["A", "B"]}, ["A", "B"]),
    ([], None), ("x", None), (None, None), ({}, None),
    ({"type": []}, None), ({"type": 5}, None), ({"type": ["A", 5]}, None),
    ({"type": "A", "@type": "B"}, ["A", "B"]),
    ({"type": None, "@type": "B"}, ["B"]),
    ({"type": [], "@type": "B"}, ["B"]),
    ({"type": "A", "@type": 5}, ["A"]),
])
def test_declared_types(body, expected):
    assert g.declared_types(body) == expected


@pytest.mark.parametrize("types,expected", [
    (["AgriParcel"], []),
    (["nkz:AgriPlant"], []),
    (["https://saref.etsi.org/saref4agri/AgriParcel"], []),
    (["AgriParcel", "FooAsset"], ["FooAsset"]),
    (["https://nkz-os.org/ns/AgriEnergyTracker"], ["https://nkz-os.org/ns/AgriEnergyTracker"]),
    (["agriparcel"], ["agriparcel"]),
    (["nkz:Device"], ["nkz:Device"]),
    (["nkz:AgriParcel"], ["nkz:AgriParcel"]),
    (["nkz:AgriManagementZone"], ["nkz:AgriManagementZone"]),
    (["foo:Device"], ["foo:Device"]),
    (["urn:ngsi-ld:Device"], ["urn:ngsi-ld:Device"]),
    (["nkz:OpenChannelFlow"], []),
    (["Device"], []),
    (["AgriManagementZone"], []),
    (["saref4agri:AgriParcel"], []),
    (["schema:AgriParcel"], ["schema:AgriParcel"]),
    (["https://uri.etsi.org/ngsi-ld/default-context/FooAsset"], ["https://uri.etsi.org/ngsi-ld/default-context/FooAsset"]),
])
def test_rejected_types(accepted, types, expected):
    assert g.rejected_types(types, accepted) == expected


@pytest.mark.parametrize("raw,expected", [
    (None, False), ("", False), ("false", False), ("1", False), ("yes", False),
    ("true", True), (" TRUE ", True),
])
def test_enforce_enabled(raw, expected):
    assert g.enforce_enabled(raw) is expected


def test_guard_unavailable_passes(caplog):
    guard = g.EntityTypeGuard("svc", None, True)
    assert guard.check(["FooAsset"], tenant="tenant-a", path="/x") == []


def test_guard_not_enforcing_logs(accepted, caplog):
    guard = g.EntityTypeGuard("svc", accepted, False)
    with caplog.at_level(logging.INFO, logger=g.logger.name):
        assert guard.check(["FooAsset"], tenant="tenant-a", path="/x") == []
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage().split(" ", 1)[1])
    assert payload["enforced"] is False and payload["types"] == ["FooAsset"]
    assert payload["tenant"] == "tenant-a" and payload["service"] == "svc"


def test_guard_enforcing_rejects(accepted):
    guard = g.EntityTypeGuard("svc", accepted, True)
    assert guard.check(["AgriParcel", "FooAsset"], tenant="tenant-a", path="/x") == ["FooAsset"]


def test_echo_is_bounded_by_count(accepted, caplog):
    guard = g.EntityTypeGuard("svc", accepted, True)
    types = [f"FooAsset{n}" for n in range(100)]
    with caplog.at_level(logging.INFO, logger=g.logger.name):
        returned = guard.check(types, tenant="tenant-a", path="/x")
    assert returned == types[:20]
    payload = json.loads(caplog.records[0].getMessage().split(" ", 1)[1])
    assert payload["types"] == types[:20]


def test_echo_is_bounded_by_length(accepted, caplog):
    guard = g.EntityTypeGuard("svc", accepted, True)
    with caplog.at_level(logging.INFO, logger=g.logger.name):
        returned = guard.check(["F" * 5000], tenant="tenant-a", path="/x")
    assert returned == ["F" * 200 + "\u2026"]
    payload = json.loads(caplog.records[0].getMessage().split(" ", 1)[1])
    assert payload["types"] == returned
    assert len(caplog.records[0].getMessage()) < 1000


def test_guard_foreign_context_info(accepted, caplog):
    guard = g.EntityTypeGuard("svc", accepted, True)
    with caplog.at_level(logging.INFO, logger=g.logger.name):
        assert guard.check(["AgriParcel"], tenant="tenant-a", path="/x", foreign_context=True) == []
    records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(records) == 1
    assert json.loads(records[0].getMessage().split(" ", 1)[1])["types"] == []


def test_from_env_unavailable(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("ENTITY_TYPE_CONFIG_DIR", str(tmp_path))
    with caplog.at_level(logging.INFO, logger=g.logger.name):
        guard = g.EntityTypeGuard.from_env("svc")
    assert [r.levelno for r in caplog.records].count(logging.CRITICAL) == 1
    assert guard.check(["FooAsset"], tenant="tenant-a", path="/x") == []


def test_parity_with_ci_checker(accepted):
    path = REPO / ".github" / "scripts" / "check_entity_types.py"
    spec = importlib.util.spec_from_file_location("ci_check_entity_types", path)
    ci = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ci  # dataclasses looks the module up by name
    spec.loader.exec_module(ci)
    lists = ci.load_lists(CONFIG / "entity-types")
    assert accepted.names == lists.allowed | lists.legacy_broker
    context = json.loads((CONFIG / "ngsi-ld-context.json").read_text(encoding="utf-8"))
    for name in accepted.names:
        iri = ci.context_iri(context, name)
        if iri is not None:
            assert iri in accepted.iris, name
