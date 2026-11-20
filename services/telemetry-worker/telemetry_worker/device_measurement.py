"""DeviceMeasurement shape and observation-property rule, shared by ingest and recompute.

One decision, two callers: `notification_handler` applies it to a live notification and
`recompute` to the stored raw payload (`telemetry_events.payload->'raw'`). Nothing here
touches a database or logs: callers look up the profile entry and report the reasons.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

# DeviceMeasurement `name`, accepted under the short name or either IRI so a later
# platform @context remap never cuts ingestion (design A.1b). Precedence is the tuple order:
# the short (compacted) name first.
NAME_KEYS = ("name", "https://schema.org/name", "https://smartdatamodels.org/name")
# Statistic gate (design A.2). `measurementType` values (SDM shape) that carry observation
# context: an instant reading, or a window statistic that #122 publishes under the BASE
# channel as the channel's representative value (mean/last/sum). min/max/stddev are spread
# statistics of their own `...Min`/`...Max`/`...StdDev` channels and get none; a missing or
# unknown value is treated the same way (fail-safe: no context, never a guessed one).
# ASSUMPTION: a missing, unknown or differently cased measurementType is gated; the builder
# always writes one from the closed vocabulary.
CONTEXT_MEASUREMENT_TYPES = frozenset({"instant", "mean", "last", "sum"})
# The closed `measurementType` vocabulary of the SDM shape (shape discriminator, see
# `device_measurement_shape`).
SDM_MEASUREMENT_TYPES = frozenset({"instant", "mean", "min", "max", "stddev", "last", "sum"})
# Profile-entry `statistic` values (after window-statistics expansion) that keep context.
CONTEXT_STATISTICS = frozenset({"mean", "last", "sum"})


def prop_value(entity: Dict[str, Any], key: str) -> Optional[Any]:
    attr = entity.get(key)
    if isinstance(attr, dict):
        return attr.get("value")
    return None


def non_empty_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class DeviceMeasurementShape:
    """Which DeviceMeasurement shape a notification carries, and its channel.

    Channel (the storage key): `name` whenever it is present under any NAME_KEYS form,
    otherwise `controlledProperty`.
    Observation-context rule:
    - SDM (`sdm`): `name` present AND `measurementType` in the closed vocabulary
      (SDM_MEASUREMENT_TYPES). Catalogue name = `controlledProperty`, statistic gate on
      `measurementType`.
    - Legacy, anything else: the catalogue name comes from the profile entry of
      (`deviceType`, channel); `measurementType` is the profile `type` and means nothing.
    Rollback safety: the flag-off builder never deletes `name` (DMs are upserted with
    options=update), so after a flag rollback a DM keeps a stale `name` while
    `measurementType` returns to the profile `type`; the DM id is stable, so the stale
    `name` equals `controlledProperty` and both give the same key. A corrupt SDM DM (a
    `name` with a missing or invalid `measurementType`) stays keyed by `name`, so a Min
    channel never merges into the base channel; the legacy rule then finds the channel's
    `min` statistic in the profile and gates it. Recompute mirrors this rule.
    `channel` is None when the reading cannot be keyed: a `name` that is present but not a
    non-empty string is never replaced by `controlledProperty`.
    """

    sdm: bool
    has_name: bool
    channel: Optional[str]
    controlled_property: Optional[str]
    measurement_type: Any

    @property
    def ambiguous(self) -> bool:
        """`name` present and different from `controlledProperty`, with an invalid
        `measurementType`: neither a clean SDM DM nor a rolled-back one."""
        return self.has_name and not self.sdm and self.channel != self.controlled_property


def device_measurement_shape(entity: Dict[str, Any]) -> DeviceMeasurementShape:
    # ASSUMPTION: a legacy reading whose id does not end in `:{controlledProperty}` is still
    # stored (design A.2 says drop it; the plan's global constraints do not). It never gets
    # observation context: `entity_id_conforms` blocks it in `_resolve_observation_payload`.
    # ASSUMPTION: without `name` the legacy rule applies even when `measurementType` is a
    # vocabulary word (a Phase 1 profile `type` may collide with it).
    controlled = non_empty_str(prop_value(entity, "controlledProperty"))
    measurement_type = prop_value(entity, "measurementType")
    name_key = next((key for key in NAME_KEYS if key in entity), None)
    if name_key is None:
        return DeviceMeasurementShape(
            sdm=False, has_name=False, channel=controlled,
            controlled_property=controlled, measurement_type=measurement_type,
        )
    sdm_type = isinstance(measurement_type, str) and measurement_type in SDM_MEASUREMENT_TYPES
    return DeviceMeasurementShape(
        sdm=sdm_type,
        has_name=True,
        channel=non_empty_str(prop_value(entity, name_key)),
        controlled_property=controlled,
        measurement_type=measurement_type,
    )




# Reasons of an ObservationDecision without a property. The first two are deliberate gates
# (the reading is stored, never given context); the others mean the profile cannot say.
SKIP_STATISTIC = "skipped_statistic"
SKIP_PROPERTY_MISMATCH = "property_mismatch"
SKIP_NO_DEVICE_TYPE = "no_device_type"
SKIP_NO_OBSERVED_PROPERTY = "no_observed_property"
SKIP_NAME_UNUSABLE = "name_unusable"
GATE_REASONS = frozenset({SKIP_STATISTIC, SKIP_PROPERTY_MISMATCH, SKIP_NAME_UNUSABLE})


@dataclass(frozen=True)
class ObservationDecision:
    """`observed_property` (the catalogue name) or the `skip` reason, never both."""

    observed_property: Optional[str] = None
    skip: Optional[str] = None
    statistic: Optional[str] = None  # the profile entry's statistic, for log messages
    def_property: Optional[str] = None  # the profile entry's observedProperty, for log messages


def decide_observation_property(
    shape: DeviceMeasurementShape,
    profile_code: Any,
    measurement_def: Optional[Dict[str, Any]],
) -> ObservationDecision:
    """Catalogue name of one channel, or why it gets none.

    `measurement_def` is the profile entry of the PUBLISHED channel (`channel_def`, window
    statistics expanded) for (`profile_code`, the channel), or None.
    SDM shape: the catalogue name is `controlledProperty`, opted in only when the profile
    entry declares the same `observedProperty`. Legacy shape: the profile entry's
    `observedProperty`, which needs a `deviceType`. In both, the statistic gate applies
    first: only `measurementType` instant/mean/last/sum (SDM) and a profile `statistic`
    absent or mean/last/sum keep context.
    """
    if shape.has_name and shape.channel is None:
        # Ingest never stores such a reading; a stored row of it gets no context either.
        return ObservationDecision(skip=SKIP_NAME_UNUSABLE)
    statistic = measurement_def.get("statistic") if measurement_def else None
    def_property = non_empty_str(measurement_def.get("observedProperty")) if measurement_def else None
    gated_type = shape.sdm and not (
        isinstance(shape.measurement_type, str)
        and shape.measurement_type in CONTEXT_MEASUREMENT_TYPES
    )
    if gated_type or (statistic is not None and statistic not in CONTEXT_STATISTICS):
        return ObservationDecision(skip=SKIP_STATISTIC, statistic=statistic, def_property=def_property)
    if shape.sdm:
        # controlledProperty falls back to the base attribute when the profile has no
        # catalogue name, so it opts a channel in only when the profile entry declares the
        # same observedProperty (keeps the contract 8.3 opt-in exact, design A.2).
        if def_property and shape.controlled_property and def_property != shape.controlled_property:
            return ObservationDecision(
                skip=SKIP_PROPERTY_MISMATCH, statistic=statistic, def_property=def_property
            )
        observed_property = shape.controlled_property if def_property else None
    else:
        if not (isinstance(profile_code, str) and profile_code):
            # Expand step 1: a main entity-manager emits no deviceType, so the legacy rule
            # has no profile to read the catalogue name from.
            return ObservationDecision(skip=SKIP_NO_DEVICE_TYPE, statistic=statistic)
        observed_property = def_property
    if not observed_property:
        return ObservationDecision(
            skip=SKIP_NO_OBSERVED_PROPERTY, statistic=statistic, def_property=def_property
        )
    return ObservationDecision(observed_property=observed_property, statistic=statistic, def_property=def_property)
