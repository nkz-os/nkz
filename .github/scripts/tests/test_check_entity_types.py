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
