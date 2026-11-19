"""Validation of observation context and per-channel overrides (pure functions)."""
import pytest

from common.observation_context import (
    MEDIA,
    ORGANS,
    validate_channel_overrides,
    validate_observation_context,
)

STRUCT = "urn:ngsi-ld:Building:acme:greenhouse-1"
ENCL = "urn:ngsi-ld:Building:acme:tunnel-2"


def test_vocabulary():
    assert MEDIA == {"ambientAir", "ambientAirUnderStructure", "enclosedAir",
                     "soil", "plant", "water", "equipment"}
    assert ORGANS == {"stem", "leaf", "fruit", "canopy", "root"}


def test_ambient_air_height_ok():
    out = validate_observation_context({"medium": "ambientAir", "heightAboveGround": 2})
    assert out == {"medium": "ambientAir", "heightAboveGround": 2.0}
    assert isinstance(out["heightAboveGround"], float)


def test_ambient_air_without_height_ok():
    assert validate_observation_context({"medium": "ambientAir"}) == {"medium": "ambientAir"}


def test_height_negative_rejected():
    with pytest.raises(ValueError, match="heightAboveGround"):
        validate_observation_context({"medium": "ambientAir", "heightAboveGround": -1})


@pytest.mark.parametrize("bad", [True, "2", float("nan"), float("inf"), None])
def test_numbers_reject_bool_string_nonfinite(bad):
    with pytest.raises(ValueError, match="heightAboveGround"):
        validate_observation_context({"medium": "ambientAir", "heightAboveGround": bad})


def test_soil_requires_depth_range():
    with pytest.raises(ValueError, match="depthFrom"):
        validate_observation_context({"medium": "soil", "depthTo": 20})
    with pytest.raises(ValueError, match="depthTo"):
        validate_observation_context({"medium": "soil", "depthFrom": 0})
    out = validate_observation_context({"medium": "soil", "depthFrom": 0, "depthTo": 20})
    assert out == {"medium": "soil", "depthFrom": 0.0, "depthTo": 20.0}


def test_soil_depth_from_must_be_below_depth_to():
    with pytest.raises(ValueError, match="depthFrom"):
        validate_observation_context({"medium": "soil", "depthFrom": 20, "depthTo": 20})
    with pytest.raises(ValueError, match="depthFrom"):
        validate_observation_context({"medium": "soil", "depthFrom": -1, "depthTo": 5})


def test_under_structure_requires_structure_urn():
    with pytest.raises(ValueError, match="structure"):
        validate_observation_context({"medium": "ambientAirUnderStructure"})
    with pytest.raises(ValueError, match="structure"):
        validate_observation_context({"medium": "ambientAirUnderStructure", "structure": "x"})
    out = validate_observation_context(
        {"medium": "ambientAirUnderStructure", "structure": STRUCT, "heightAboveGround": 1})
    assert out["structure"] == STRUCT and out["heightAboveGround"] == 1.0


def test_enclosed_air_requires_enclosure():
    with pytest.raises(ValueError, match="enclosure"):
        validate_observation_context({"medium": "enclosedAir"})
    with pytest.raises(ValueError, match="enclosure"):
        validate_observation_context({"medium": "enclosedAir", "enclosure": 5})
    out = validate_observation_context({"medium": "enclosedAir", "enclosure": ENCL})
    assert out == {"medium": "enclosedAir", "enclosure": ENCL}


def test_plant_organ_must_be_known():
    with pytest.raises(ValueError, match="organ"):
        validate_observation_context({"medium": "plant"})
    with pytest.raises(ValueError, match="organ"):
        validate_observation_context({"medium": "plant", "organ": "petal"})
    assert validate_observation_context({"medium": "plant", "organ": "leaf"}) == {
        "medium": "plant", "organ": "leaf"}


@pytest.mark.parametrize("medium", ["water", "equipment"])
def test_water_equipment_no_fields(medium):
    assert validate_observation_context({"medium": medium}) == {"medium": medium}
    with pytest.raises(ValueError, match="heightAboveGround"):
        validate_observation_context({"medium": medium, "heightAboveGround": 1})


def test_field_not_allowed_for_medium_is_rejected():
    with pytest.raises(ValueError, match="depthFrom"):
        validate_observation_context({"medium": "ambientAir", "depthFrom": 0})


def test_unknown_medium_is_rejected():
    with pytest.raises(ValueError, match="medium"):
        validate_observation_context({"medium": "lava"})
    with pytest.raises(ValueError, match="medium"):
        validate_observation_context({})
    with pytest.raises(ValueError, match="observationContext"):
        validate_observation_context("ambientAir")


def test_input_not_mutated():
    ctx = {"medium": "soil", "depthFrom": 0, "depthTo": 10}
    validate_observation_context(ctx)
    assert ctx == {"medium": "soil", "depthFrom": 0, "depthTo": 10}


def test_overrides_unknown_attribute_rejected():
    with pytest.raises(ValueError, match="humidity"):
        validate_channel_overrides({"humidity": {"observesFeature": STRUCT}}, {"temperature"})


def test_override_needs_observes_feature_or_context():
    with pytest.raises(ValueError, match="temperature"):
        validate_channel_overrides({"temperature": {}}, {"temperature"})
    with pytest.raises(ValueError, match="temperature"):
        validate_channel_overrides({"temperature": "x"}, {"temperature"})
    with pytest.raises(ValueError, match="temperature"):
        validate_channel_overrides({"temperature": {"foo": 1}}, {"temperature"})


def test_override_observes_feature_must_be_urn():
    with pytest.raises(ValueError, match="observesFeature"):
        validate_channel_overrides({"temperature": {"observesFeature": "plant-1"}}, {"temperature"})


def test_override_context_is_validated():
    with pytest.raises(ValueError, match="depthTo"):
        validate_channel_overrides(
            {"temperature": {"observationContext": {"medium": "soil", "depthFrom": 0}}},
            {"temperature"})
    out = validate_channel_overrides(
        {"temperature": {"observesFeature": STRUCT,
                         "observationContext": {"medium": "soil", "depthFrom": 0, "depthTo": 10}}},
        {"temperature"})
    assert out == {"temperature": {
        "observesFeature": STRUCT,
        "observationContext": {"medium": "soil", "depthFrom": 0.0, "depthTo": 10.0}}}


def test_overrides_must_be_dict():
    with pytest.raises(ValueError, match="channelOverrides"):
        validate_channel_overrides([], {"temperature"})


def test_unhashable_values_give_value_error():
    with pytest.raises(ValueError, match="medium"):
        validate_observation_context({"medium": []})
    with pytest.raises(ValueError, match="organ"):
        validate_observation_context({"medium": "plant", "organ": []})
