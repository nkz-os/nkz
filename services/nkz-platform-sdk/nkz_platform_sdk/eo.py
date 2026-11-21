"""Satellite observations of a parcel or a zone, read from the context broker.

Two stored shapes exist and both are understood here, so that no module parses
these entities by hand:

- ``EOSatelliteImagery`` (Smart Data Models): one entity per target, product
  family and UTC day; every value lives in the ``analytics`` list as a string.
- ``EOProduct`` with loose attributes (the earlier shape): one entity per target
  and day that may carry the results of several products at once. Such an
  entity is split into one observation per family.

Values come back as numbers under the same names whatever the stored shape.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Collection, Iterable, Mapping

logger = logging.getLogger(__name__)

SDM_TYPE = "EOSatelliteImagery"
LEGACY_TYPE = "EOProduct"

EO_FAMILIES = (
    "s2-indices",
    "s2-composite",
    "s1-backscatter",
    "s1-soil-moisture",
    "s1-change",
    "landsat-lst",
    "clms-lst",
)

INDEX_NAMES = ("ndvi", "evi", "savi", "osavi", "gndvi", "ndre", "ndmi")

# Values that are the zonal summary of a stored raster.
_RASTER_NAMES = INDEX_NAMES + ("lst", "backscatterVV", "backscatterVH", "soilMoistureIndex")

# Every value name a caller can ask for; statistics follow their value with a dot.
EO_VALUE_NAMES = _RASTER_NAMES + (
    "pixelCount",
    "validPixelFraction",
    "parcelCloudFraction",
    "cloudCoverage",
    "incidenceAngle",
    "changeFlag",
    "changeConfidence",
    "deltaVV",
)

_STATS = ("min", "max", "std")
_INTERIOR = (
    "interiorMean",
    "interiorMedian",
    "interiorP10",
    "interiorP90",
    "interiorPixelCount",
    "interiorValidFraction",
    "interiorMask",
)
_TEXT_NAMES = frozenset({"changeFlag"})
_TEXT_SUFFIXES = (".interiorMask",)
_INTEGER_NAMES = frozenset({"pixelCount"})
_INTEGER_SUFFIXES = (".interiorPixelCount",)

# An attribute can arrive under its short name or under any IRI it has been
# stored with. The Smart Data Models IRI goes first: while a term is being
# moved to it an entity carries both copies, and that one is the reference.
_PREFIXES = (
    "https://smartdatamodels.org/dataModel.SatelliteImagery/",
    "https://smartdatamodels.org/dataModel.Agrifood/",
    "",
    "nkz:",
    "https://nkz-os.org/ns/",
    "https://uri.etsi.org/ngsi-ld/default-context/",
)

_PAGE = 500
_MAX_PAGES = 200

_SDM_ID = re.compile(
    r"^urn:ngsi-ld:EOSatelliteImagery:[^:]+:(?P<target>parcel|zone):.+:"
    r"(?P<family>[a-z0-9-]+):(?P<day>\d{4}-\d{2}-\d{2})$"
)
_DAY_AT_END = re.compile(r":(\d{4}-\d{2}-\d{2})$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DECIMAL = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_URN = re.compile(r"^urn:[A-Za-z0-9_.:~%+@/-]+$")


class EOReadIncomplete(RuntimeError):
    """The listing did not reach its end, so no result can be trusted."""


@dataclass(frozen=True)
class EOObservation:
    """One product family observed over one target on one UTC day.

    ``sensing_time`` is the real acquisition time and is only known in the
    ``EOSatelliteImagery`` shape. ``parcel_id`` is None for zone entities of the
    earlier shape, which never stored it. ``values`` and ``legacy_raster_urls``
    are plain dicts so that they serialise; treat them as read-only.
    """

    entity_id: str
    shape: str
    family: str
    target: str
    parcel_id: str | None
    zone_id: str | None
    sensing_day: date
    sensing_time: datetime | None
    values: Mapping[str, float | int | str] = field(hash=False)
    product_id: str | None = None
    product_type: str | None = None
    raster_path: str | None = None
    legacy_raster_urls: Mapping[str, str] = field(default_factory=dict, hash=False)

    def raster_url(self, name: str) -> str | None:
        """Where the raster of one value is stored, or None when there is none on record."""
        if self.shape == "legacy":
            return self.legacy_raster_urls.get(name)
        if self.raster_path and name in _RASTER_NAMES and name in self.values:
            return f"{self.raster_path.rstrip('/')}/{name}.tif"
        return None

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "shape": self.shape,
            "family": self.family,
            "target": self.target,
            "parcel_id": self.parcel_id,
            "zone_id": self.zone_id,
            "sensing_day": self.sensing_day.isoformat(),
            "sensing_time": self.sensing_time.strftime("%Y-%m-%dT%H:%M:%SZ") if self.sensing_time else None,
            "values": dict(self.values),
            "product_id": self.product_id,
            "product_type": self.product_type,
            "raster_path": self.raster_path,
            "legacy_raster_urls": dict(self.legacy_raster_urls),
        }


# ── reading one attribute ───────────────────────────────────────────────────


def _raw(holder: Mapping[str, Any], name: str) -> Any:
    for prefix in _PREFIXES:
        if prefix + name in holder:
            return holder[prefix + name]
    return None


def _unwrap(attr: Any) -> Any:
    if isinstance(attr, dict):
        if attr.get("type") == "Relationship" and "object" in attr:
            return attr["object"]
        if attr.get("type") in ("Property", "GeoProperty") and "value" in attr:
            attr = attr["value"]
    if isinstance(attr, dict) and "@value" in attr:
        return attr["@value"]
    return attr


def _get(holder: Mapping[str, Any], name: str) -> Any:
    return _unwrap(_raw(holder, name))


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not _DECIMAL.fullmatch(value):
            return None
    elif not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _convert(name: str, value: Any, entity_id: str) -> float | int | str | None:
    """A stored value under its contract name, or None (with a warning) when it cannot be read."""
    if name in _TEXT_NAMES or name.endswith(_TEXT_SUFFIXES):
        text = _text(value)
        if text is None:
            logger.warning("eo: %s has no readable text for %s", entity_id, name)
        return text
    number = _number(value)
    if number is None:
        logger.warning("eo: %s has no readable number for %s", entity_id, name)
        return None
    if name in _INTEGER_NAMES or name.endswith(_INTEGER_SUFFIXES):
        if not number.is_integer() or number < 0:
            logger.warning("eo: %s has a count that is not a whole number for %s", entity_id, name)
            return None
        return int(number)
    return number


def _as_list(value: Any) -> list:
    """The broker returns a one-element list as the bare element."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _relations(entity: Mapping[str, Any]) -> tuple[str | None, str | None]:
    parcel = _text(_get(entity, "hasAgriParcel")) or _text(_get(entity, "refAgriParcel"))
    zone = _text(_get(entity, "hasAgriManagementZone")) or _text(_get(entity, "hasAgriParcelZone"))
    return parcel, zone


# ── the two shapes ──────────────────────────────────────────────────────────


def _parse_sdm(entity: Mapping[str, Any]) -> list[EOObservation]:
    entity_id = str(entity.get("id", ""))
    match = _SDM_ID.match(entity_id)
    if not match or match["family"] not in EO_FAMILIES:
        logger.warning("eo: %s does not follow the identifier format; skipped", entity_id)
        return []
    # The day is read where the broker filters it, so that asking for a window
    # and not asking for one see the same entities.
    stamp = _raw(entity, "sensingDate")
    if isinstance(stamp, dict) and stamp.get("type") == "Property":
        stamp = stamp.get("value")
    try:
        day = date.fromisoformat(stamp[:10]) if isinstance(stamp, str) and _DAY.match(stamp) else None
    except ValueError:
        day = None
    if day is None:
        logger.warning("eo: %s has no sensingDate written as a plain ISO 8601 string; skipped", entity_id)
        return []
    moment = None
    if len(stamp) > 10:
        if stamp.endswith("Z"):
            try:
                parsed = datetime.fromisoformat(stamp[:-1])
            except ValueError:
                parsed = None
            if parsed is not None and parsed.tzinfo is None:
                moment = parsed.replace(tzinfo=timezone.utc)
        if moment is None:
            logger.warning("eo: %s has a sensingDate time that is not UTC ISO 8601; time ignored", entity_id)
    parcel, zone = _relations(entity)
    if (match["target"] == "zone") != (zone is not None):
        logger.warning("eo: %s has relations that contradict its identifier; skipped", entity_id)
        return []
    values: dict[str, float | int | str] = {}
    for item in _as_list(_get(entity, "analytics")):
        if not isinstance(item, dict) or not _text(item.get("name")):
            continue
        converted = _convert(item["name"], item.get("value"), entity_id)
        if converted is not None:
            values[item["name"]] = converted
    return [
        EOObservation(
            entity_id=entity_id,
            shape="sdm",
            family=match["family"],
            target=match["target"],
            parcel_id=parcel,
            zone_id=zone,
            sensing_day=day,
            sensing_time=moment,
            values=values,
            product_id=_text(_get(entity, "productID")),
            product_type=_text(_get(entity, "productType")),
            raster_path=_text(_get(entity, "localServerPath")),
        )
    ]


def _legacy_day(entity: Mapping[str, Any], entity_id: str) -> date | None:
    candidates = [_get(entity, "sensingDate"), _get(entity, "acquisitionDate")]
    at_end = _DAY_AT_END.search(entity_id)
    if at_end:
        candidates.append(at_end.group(1))
    for candidate in candidates:
        if isinstance(candidate, str) and _DAY.match(candidate):
            try:
                return date.fromisoformat(candidate[:10])
            except ValueError:
                continue
    return None


def _with_stats(
    values: dict, name: str, attr: Any, entity_id: str, subs: tuple[str, ...] = _STATS
) -> None:
    converted = _convert(name, _unwrap(attr), entity_id)
    if converted is None:
        return
    values[name] = converted
    if not isinstance(attr, dict):
        return
    for sub in subs:
        raw = _raw(attr, sub)
        if raw is None:
            continue
        sub_value = _convert(f"{name}.{sub}", _unwrap(raw), entity_id)
        if sub_value is not None:
            values[f"{name}.{sub}"] = sub_value


def _parse_legacy(entity: Mapping[str, Any]) -> list[EOObservation]:
    entity_id = str(entity.get("id", ""))
    day = _legacy_day(entity, entity_id)
    if day is None:
        logger.warning("eo: %s has no day; skipped", entity_id)
        return []
    parcel, zone = _relations(entity)
    target = "zone" if zone or ":zone:" in entity_id else "parcel"

    # One entity holds every index of a parcel and day, so a single scene and a
    # temporal composite can share it. A composite keeps its raster under its own
    # folder: the family is decided index by index. Without a raster an index
    # cannot be told apart from a single scene.
    indices: dict[str, float | int | str] = {}
    composite: dict[str, float | int | str] = {}
    raster_urls: dict[str, str] = {}
    composite_urls: dict[str, str] = {}
    carried: set[str] = set()  # raster products the entity carries, readable or not
    for name in INDEX_NAMES:
        attr = _raw(entity, name)
        if attr is None:
            continue
        url = _text(_get(attr, "rasterUrl")) if isinstance(attr, dict) else None
        in_composite = bool(url) and "/composites/" in url
        carried.add("composite" if in_composite else "indices")
        group = composite if in_composite else indices
        _with_stats(group, name, attr, entity_id, _STATS + _INTERIOR)
        if url and name in group:
            (composite_urls if in_composite else raster_urls)[name] = url

    surface: dict[str, float | int | str] = {}
    if _raw(entity, "lst") is not None:
        carried.add("surface")
        _with_stats(surface, "lst", _raw(entity, "lst"), entity_id)

    backscatter: dict[str, float | int | str] = {}
    for name in ("backscatterVV", "backscatterVH"):
        if _raw(entity, name) is not None:
            carried.add("backscatter")
            _with_stats(backscatter, name, _raw(entity, name), entity_id, ())

    moisture: dict[str, float | int | str] = {}
    if _raw(entity, "sarMoisture") is not None:
        _with_stats(moisture, "soilMoistureIndex", _raw(entity, "sarMoisture"), entity_id)
        if moisture and _raw(entity, "sarIncidenceDeg") is not None:
            angle = _convert("incidenceAngle", _get(entity, "sarIncidenceDeg"), entity_id)
            if angle is not None:
                moisture["incidenceAngle"] = angle

    change: dict[str, float | int | str] = {}
    flag_attr = _raw(entity, "sarChangeFlag")
    if flag_attr is not None:
        flag = _convert("changeFlag", _unwrap(flag_attr), entity_id)
        if flag is not None:
            change["changeFlag"] = flag
            if isinstance(flag_attr, dict):
                for stored, name in (("confidence", "changeConfidence"), ("deltaVV", "deltaVV")):
                    if _raw(flag_attr, stored) is not None:
                        extra = _convert(name, _get(flag_attr, stored), entity_id)
                        if extra is not None:
                            change[name] = extra

    # Cloud cover describes a scene: it goes with the single-scene indices when
    # there are any, otherwise with the composite.
    optical = indices or composite
    if optical:
        for stored, name in (("cloudCoverPercentage", "cloudCoverage"), ("parcelCloudFraction", "parcelCloudFraction")):
            if _raw(entity, stored) is not None:
                quality = _convert(name, _get(entity, stored), entity_id)
                if quality is not None:
                    optical[name] = quality

    # The pixel counters are written by whichever product came last. They are
    # only attributed when a single raster product is present in the entity.
    counted = [group for group in (indices, composite, surface, backscatter) if group]
    if len(carried) == 1 and len(counted) == 1:
        for name in ("pixelCount", "validPixelFraction"):
            if _raw(entity, name) is not None:
                counter = _convert(name, _get(entity, name), entity_id)
                if counter is not None:
                    counted[0][name] = counter

    radar_scene = _text(_get(entity, "sarSceneId"))
    surface_scene = _text(_get(entity, "lstSourceScene"))
    product_type = _text(_get(entity, "productType"))

    def observation(family: str, values: dict, **extra: Any) -> EOObservation:
        return EOObservation(
            entity_id=entity_id,
            shape="legacy",
            family=family,
            target=target,
            parcel_id=parcel,
            zone_id=zone,
            sensing_day=day,
            sensing_time=None,
            values=values,
            **extra,
        )

    found: list[EOObservation] = []
    if indices:
        found.append(observation("s2-indices", indices, legacy_raster_urls=raster_urls))
    if composite:
        found.append(observation("s2-composite", composite, legacy_raster_urls=composite_urls))
    if backscatter:
        found.append(observation("s1-backscatter", backscatter, product_type=product_type))
    if moisture:
        found.append(observation("s1-soil-moisture", moisture, product_id=radar_scene))
    if change:
        found.append(observation("s1-change", change, product_id=radar_scene))
    if surface:
        family = "clms-lst" if (surface_scene or "").startswith("CLMS:") else "landsat-lst"
        found.append(observation(family, surface, product_id=surface_scene))
    return found


# ── selection ───────────────────────────────────────────────────────────────


def _type_name(entity: Mapping[str, Any]) -> str:
    kind = entity.get("type")
    if isinstance(kind, list):
        kind = kind[0] if kind else ""
    return str(kind or "").rsplit("/", 1)[-1]


def _target_key(obs: EOObservation) -> tuple[str, str]:
    identity = obs.zone_id if obs.target == "zone" else obs.parcel_id
    return obs.target, identity or obs.entity_id


def _order(obs: EOObservation) -> tuple:
    moment = obs.sensing_time or datetime.min.replace(tzinfo=timezone.utc)
    return obs.sensing_day, moment, obs.entity_id


def _name_set(given: Collection[str] | None, allowed: tuple[str, ...], what: str) -> frozenset[str] | None:
    """The names asked for, read once (an iterator is not consumed twice) and checked."""
    if given is None:
        return None
    if isinstance(given, str):
        raise TypeError(f"{what} must be a collection of names, not a string")
    names = frozenset(given)
    if not names:
        raise ValueError(f"{what} is empty: leave it out to ask for everything")
    unknown = sorted(str(name) for name in names - set(allowed))
    if unknown:
        raise ValueError(f"unknown name in {what}: {', '.join(unknown)}")
    return names


def _check_window(since: date | None, until: date | None) -> None:
    for value, what in ((since, "since"), (until, "until")):
        if value is not None and (isinstance(value, datetime) or not isinstance(value, date)):
            raise TypeError(f"{what} must be a date")
    if since is not None and until is not None and since > until:
        raise ValueError("since is after until")


def _narrow(obs: EOObservation, names: frozenset[str]) -> EOObservation | None:
    kept = {name: value for name, value in obs.values.items() if name.split(".", 1)[0] in names}
    if not kept:
        return None
    urls = {name: url for name, url in obs.legacy_raster_urls.items() if name in names}
    return replace(obs, values=kept, legacy_raster_urls=urls)


def parse_eo_entities(
    entities: Iterable[Mapping[str, Any]],
    *,
    families: Collection[str] | None = None,
    names: Collection[str] | None = None,
    require: Collection[str] | None = None,
) -> list[EOObservation]:
    """Observations held by broker entities of either shape, oldest first.

    Pass entities in the normalized representation: ``keyValues`` drops the
    statistics that the earlier shape keeps as sub-properties. An entity listed
    twice is read once.

    ``names`` selects the values returned. They are value names without a
    suffix, from ``EO_VALUE_NAMES`` (``ndvi``, ``lst``, ``pixelCount``); a name
    brings its dotted statistics with it (``ndvi.min``, ``ndvi.interiorMean``).
    An observation holding none of them is left out. ``require`` names the
    values an observation must hold, all of them, to be returned at all: ask
    for ``names={"ndvi", "cloudCoverage"}, require={"ndvi"}`` when a row without
    ``ndvi`` is of no use.

    When the same target, family and day exists in both shapes, the
    ``EOSatelliteImagery`` one is kept, whatever values each holds.

    About the earlier shape: an entity carrying more than one raster product
    does not say which one ``pixelCount`` and ``validPixelFraction`` belong to,
    so they are not returned; its acquisition times are not real and are never
    returned; an index of a temporal composite is recognised by its raster
    folder (without a raster it reads as a single scene) and is dated by its
    first scene, not by the last day of its window.

    An ``EOSatelliteImagery`` entity whose ``sensingDate`` is not a plain
    string starting with ``YYYY-MM-DD``, or whose relations contradict its
    identifier, is skipped with a warning.
    """
    wanted_families = _name_set(families, EO_FAMILIES, "families")
    wanted_names = _name_set(names, EO_VALUE_NAMES, "names")
    required = _name_set(require, EO_VALUE_NAMES, "require") or frozenset()
    found: list[EOObservation] = []
    seen: set[str] = set()
    for entity in entities:
        entity_id = entity.get("id") if isinstance(entity, Mapping) else None
        if not isinstance(entity_id, str) or not entity_id:
            logger.warning("eo: an item without an identifier was skipped")
            continue
        if entity_id in seen:
            continue
        seen.add(entity_id)
        kind = _type_name(entity)
        if kind == SDM_TYPE:
            found.extend(_parse_sdm(entity))
        elif kind == LEGACY_TYPE:
            found.extend(_parse_legacy(entity))
    current = {(*_target_key(o), o.family, o.sensing_day) for o in found if o.shape == "sdm"}
    selected: list[EOObservation] = []
    for obs in found:
        if obs.shape == "legacy" and (*_target_key(obs), obs.family, obs.sensing_day) in current:
            continue
        if wanted_families is not None and obs.family not in wanted_families:
            continue
        if not required <= obs.values.keys():
            continue
        if wanted_names is not None:
            narrowed = _narrow(obs, wanted_names)
            if narrowed is None:
                continue
            obs = narrowed
        elif not obs.values:
            continue
        selected.append(obs)
    return sorted(selected, key=_order)


def _select(
    observations: list[EOObservation],
    *,
    parcel_id: str | None,
    zone_id: str | None,
    since: date | None,
    until: date | None,
    latest: bool,
) -> list[EOObservation]:
    kept = []
    for obs in observations:
        if zone_id is not None:
            if obs.target != "zone" or obs.zone_id != zone_id:
                continue
        else:
            if obs.target != "parcel":
                continue
            if parcel_id is not None and obs.parcel_id != parcel_id:
                continue
        if since is not None and obs.sensing_day < since:
            continue
        if until is not None and obs.sensing_day > until:
            continue
        kept.append(obs)
    if not latest:
        return kept
    newest: dict[tuple, EOObservation] = {}
    for obs in kept:  # already oldest first
        newest[(*_target_key(obs), obs.family)] = obs
    return sorted(newest.values(), key=_order)


def _queries(
    parcel_id: str | None,
    zone_id: str | None,
    since: date | None = None,
    until: date | None = None,
) -> list[tuple[str, str | None]]:
    """What to ask the broker, as (type, q) pairs.

    Only filters already in use against this broker are sent: equality on one
    relationship, absence of a relationship, and string comparison of
    ``sensingDate``. The window is asked of ``EOSatelliteImagery`` alone, where
    ``sensingDate`` is always written; part of the earlier shape has none. The
    product family is never asked of the broker. Target, family and window are
    applied again to what comes back.
    """
    if parcel_id is not None and zone_id is not None:
        raise ValueError("pass parcel_id or zone_id, not both")
    for urn in (parcel_id, zone_id):
        if urn is not None and (not isinstance(urn, str) or not _URN.fullmatch(urn)):
            raise ValueError("a target must be an entity URN")
    _check_window(since, until)
    # String comparison on purpose: the day after the last one, never "<=" nor a time.
    window = []
    if since is not None:
        window.append(f'sensingDate>="{since.isoformat()}"')
    if until is not None and until < date.max:
        window.append(f'sensingDate<"{(until + timedelta(days=1)).isoformat()}"')
    if zone_id is not None:
        # One query per relationship name: a zone is linked by the name of its own type.
        targets = [f'{relation}=="{zone_id}"' for relation in ("hasAgriManagementZone", "hasAgriParcelZone")]
        current = targets
    elif parcel_id is not None:
        targets = [f'hasAgriParcel=="{parcel_id}"']
        current = [f"{targets[0]};!hasAgriManagementZone"]
    else:
        targets = current = [""]
    queries = [(SDM_TYPE, ";".join(part for part in (target, *window) if part) or None) for target in current]
    queries += [(LEGACY_TYPE, target or None) for target in targets]
    return queries


def _arguments(
    families: Collection[str] | None, names: Collection[str] | None, require: Collection[str] | None, latest: Any
) -> tuple[frozenset[str] | None, frozenset[str] | None, frozenset[str] | None]:
    if not isinstance(latest, bool):
        raise TypeError("latest must be True or False")
    return (
        _name_set(families, EO_FAMILIES, "families"),
        _name_set(names, EO_VALUE_NAMES, "names"),
        _name_set(require, EO_VALUE_NAMES, "require"),
    )


def _page_offsets() -> Iterable[int]:
    return (page * _PAGE for page in range(_MAX_PAGES))


def _incomplete(kind: str) -> EOReadIncomplete:
    return EOReadIncomplete(f"{kind}: more than {_PAGE * _MAX_PAGES} entities")


async def fetch_eo_observations(
    client: Any,
    *,
    parcel_id: str | None = None,
    zone_id: str | None = None,
    families: Collection[str] | None = None,
    names: Collection[str] | None = None,
    require: Collection[str] | None = None,
    since: date | None = None,
    until: date | None = None,
    latest: bool = False,
) -> list[EOObservation]:
    """Satellite observations of one parcel, one zone or every parcel of the tenant.

    ``client`` is an ``OrionClient``. ``parcel_id`` returns the parcel without its
    zones; ``zone_id`` returns that zone; neither returns every parcel of the
    tenant, zones left out. ``since`` and ``until`` are whole days (``date``),
    both included. ``latest`` keeps, for each target and family, the most recent
    observation that passes ``names`` and ``require``. See ``parse_eo_entities``
    for ``families``, ``names``, ``require`` and the returned values.

    Raises ``EOReadIncomplete`` instead of answering from a partial listing.
    """
    families, names, require = _arguments(families, names, require, latest)
    entities: list[dict] = []
    for kind, query in _queries(parcel_id, zone_id, since, until):
        for offset in _page_offsets():
            batch = await client.query_entities(type=kind, q=query, limit=_PAGE, offset=offset)
            entities.extend(batch)
            if len(batch) < _PAGE:
                break
        else:
            raise _incomplete(kind)
    observations = parse_eo_entities(entities, families=families, names=names, require=require)
    return _select(observations, parcel_id=parcel_id, zone_id=zone_id, since=since, until=until, latest=latest)


def fetch_eo_observations_sync(
    client: Any,
    *,
    parcel_id: str | None = None,
    zone_id: str | None = None,
    families: Collection[str] | None = None,
    names: Collection[str] | None = None,
    require: Collection[str] | None = None,
    since: date | None = None,
    until: date | None = None,
    latest: bool = False,
) -> list[EOObservation]:
    """``fetch_eo_observations`` for a ``SyncOrionClient``."""
    families, names, require = _arguments(families, names, require, latest)
    entities: list[dict] = []
    for kind, query in _queries(parcel_id, zone_id, since, until):
        for offset in _page_offsets():
            batch = client.query_entities(type=kind, q=query, limit=_PAGE, offset=offset)
            entities.extend(batch)
            if len(batch) < _PAGE:
                break
        else:
            raise _incomplete(kind)
    observations = parse_eo_entities(entities, families=families, names=names, require=require)
    return _select(observations, parcel_id=parcel_id, zone_id=zone_id, since=since, until=until, latest=latest)
