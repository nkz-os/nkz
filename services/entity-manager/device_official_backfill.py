"""Backfill of the official attributes of the Devices registered before they existed.

    python -m device_official_backfill --tenant T [--device URN] [--apply]

Gives each Device with a profile what a new registration writes: `refDeviceModel` (and the
DeviceModel it points to), `deviceCategory`, `controlledProperty`, `dateInstalled` and
`deviceState`. The old attributes stay. Dry run unless `--apply`; safe to run again.

Run it in a one-off pod with the entity-manager image and environment, never inside the serving
pod, and only once the broker's cached context has the terms: a term it does not know is stored
under the default vocabulary, where a normal read cannot tell. The run checks that and stops.

Any write on a Device fires the Device -> DeviceMeasurement bridge, so a Device whose profile
declares a reading without `observedAt` is refused (the bridge would store a measurement nobody
took), and written Devices are 5 seconds apart.

stdout carries one JSON summary, logs go to stderr. Exit codes: 0 ok; 1 the run could not be done
or, with `--apply`, something was refused, failed or not attempted; 2 bad arguments.
"""
import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Optional

import dataloggers
import device_models
from common.device_model import (DEFAULT_CONTEXT_IRI, DEVICE_IRI, build_device_model,
                                 device_model_id, profile_codes)
from common.tenant_utils import validate_tenant_id
from device_urn import external_id_of, is_device_type

logger = logging.getLogger('device_official_backfill')

# Each written Device fires the measurement bridge.
WAIT_S = 5
OUTCOMES = ('planned', 'written', 'unchanged', 'refused', 'ignored', 'failed', 'not_attempted')
# What a registration writes on a Device, all of it under DEVICE_IRI.
_DEVICE_ATTRS = ('refDeviceModel', 'deviceCategory', 'controlledProperty', 'dateInstalled', 'deviceState')
# What a DeviceModel has under DEVICE_IRI; `name` and `category` have other IRIs on this platform.
_MODEL_ATTRS = ('manufacturerName', 'brandName', 'modelName', 'controlledProperty')


class BackfillAborted(Exception):
    """The run cannot go on; `error` goes to the summary."""

    def __init__(self, code: str, **context):
        super().__init__(code)
        self.error = {'code': code, **context}


def _abort(code: str, exc: Exception, **context) -> BackfillAborted:
    logger.error("backfill stopped: reason=%s error=%s %s", code, device_models.why(exc), context)
    return BackfillAborted(code, **context)


# ── what one Device needs ──────────────────────────────────────────────

@dataclass(frozen=True)
class Assessment:
    """`planned`, `unchanged`, `refused` or `ignored`, and why; what to write when planned."""

    outcome: str
    code: Optional[str] = None
    profile: Optional[Mapping] = None
    changed: dict = field(default_factory=dict)
    stale: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)  # goes to the summary as it is

    def report(self, urn: str, outcome: Optional[str] = None, **extra) -> dict:
        out = {'urn': urn, 'outcome': outcome or self.outcome}
        if self.code is not None:
            out['profile'] = self.code
        if self.changed:
            out['attributes'] = sorted(self.changed)
        if self.stale:
            out['deleted'] = sorted(self.stale)
        return {**out, **self.detail, **extra}


def _refused(reason: str, code: Optional[str] = None, **detail) -> Assessment:
    return Assessment('refused', code=code, detail={'reason': reason, **detail})


def named_profile(tenant: str, entity: Mapping) -> Assessment | str:
    """The profile code of a Device, or why it has none that can be used."""
    if entity.get('type') != 'Device':
        return _refused('type_not_device')
    codes = profile_codes(tenant, entity)
    if not codes.present:
        return Assessment('ignored', detail={'reason': 'no_profile'})
    if external_id_of(tenant, entity['id']) is None or not is_device_type(entity['id']):
        return _refused('id_not_parseable')
    if codes.model_error:
        return _refused('ref_device_model_unusable')
    if codes.model and codes.legacy and codes.model != codes.legacy:
        return _refused('profile_codes_disagree',
                        codes={'refDeviceModel': codes.model, 'profileCode': codes.legacy})
    code = codes.model or codes.legacy
    try:
        device_model_id(tenant, code)
    except ValueError:
        return _refused('profile_code_unusable')
    return code


def assess(tenant: str, entity: Mapping, code: str, profile: Optional[Mapping],
           stored: Optional[Mapping]) -> Assessment:
    """What a Device on profile `code` needs; `stored` is the Device read without Link."""
    if profile is None:
        return _refused('profile_unknown', code)
    if (profile.get('sdm_entity_type') or 'Device') != 'Device':
        return _refused('profile_not_of_a_device', code)
    try:
        changed, stale = device_models.profile_attr_changes(tenant, profile, entity)
        channels = device_models.reading_channels(profile)
        undated = device_models.undated_readings(profile, entity)
    except (KeyError, TypeError, AttributeError) as exc:
        logger.error("profile_malformed: tenant=%s profile=%s device=%s cause=%r",
                     tenant, code, entity['id'], exc)
        return _refused('profile_malformed', code)
    if undated:
        return _refused('undated_readings', code, readings=undated)
    copied, notes = _copied_attrs(entity, channels)
    changed.update(copied)
    clash = sorted(channels.intersection([*changed, *stale]))
    if clash:
        return _refused('official_attribute_is_a_reading', code, readings=clash)
    if stored is None:
        return _refused('gone')
    # ASSUMPTION: refused, not corrected. A write would add the official attribute next to this one,
    # and without one the Device would pass for complete, because a normal read shows either.
    outside = _under_default_vocabulary(stored, _DEVICE_ATTRS)
    if outside:
        return _refused('attributes_outside_the_official_context', code, attributes_outside=outside)
    return Assessment('planned' if changed or stale else 'unchanged', code, profile, changed, stale,
                      {'notes': notes} if notes else {})


def _copied_attrs(entity: Mapping, channels: set) -> tuple[dict, list[str]]:
    """`dateInstalled` and `deviceState` from the attributes they replace, only when missing."""
    out, notes = {}, []
    for new, old in (('dateInstalled', 'installedAt'), ('deviceState', 'status')):
        if new in entity:
            continue
        if new == 'deviceState' and old in channels:
            notes.append('status_is_a_reading')
            continue
        attr = entity.get(old)
        value = attr.get('value') if isinstance(attr, Mapping) and attr.get('type') == 'Property' else None
        if isinstance(value, str) and value.strip():
            out[new] = {'type': 'Property', 'value': value}
    return out, notes


# ── the context guard ──────────────────────────────────────────────────

def _under_default_vocabulary(stored: Mapping, names: Iterable[str]) -> list[str]:
    """Which of `names` an entity read without Link has under the default vocabulary.

    The broker shortens that vocabulary in every read, so there the bare name is the sign of it.
    """
    return [name for name in names if name in stored or DEFAULT_CONTEXT_IRI + name in stored]


def _outside_context(stored: Mapping, type_name: str, official: Iterable[str],
                     names: Iterable[str]) -> list[str]:
    """What of an entity read without Link is not stored where the platform context puts it."""
    outside = {name for name in official if DEVICE_IRI + name not in stored}
    outside.update(_under_default_vocabulary(stored, names))
    if stored.get('type') != DEVICE_IRI + type_name:
        outside.add('type')
    return sorted(outside)


def _check_context(tenant: str, urn: str, type_name: str, official: Iterable[str],
                   names: Iterable[str]) -> None:
    stored = device_models.read_entity(tenant, urn, link=False) or {}
    outside = _outside_context(stored, type_name, official, names)
    if outside:
        logger.error("stored outside the official context: tenant=%s entity=%s attributes=%s",
                     tenant, urn, outside)
        raise BackfillAborted(
            'outside_the_official_context', entity=urn, attributes=outside,
            detail='the broker stored these terms under another vocabulary: sync its context, then '
                   'delete them (a DeviceModel: the whole entity, its type cannot be altered) and '
                   'run again')


# ── the run ────────────────────────────────────────────────────────────

class _Run:
    def __init__(self, tenant: str, apply: bool, sleep: Callable[[float], None]):
        self.tenant = tenant
        self.apply = apply
        self.sleep = sleep
        self.reports: dict[str, dict] = {}
        self.models: list[dict] = []
        self.verified: set[str] = set()  # attributes read back under their official IRI
        self.error: Optional[dict] = None

    def execute(self, device: Optional[str]) -> None:
        try:
            if not os.getenv('CONTEXT_URL'):
                # Without it no request carries the platform context.
                raise BackfillAborted('context_url_not_set')
            assessed = self._assess_all(self._select(device))
            self._bring_models_up_to_date(assessed)
            if self.apply:
                self._write({urn: a for urn, a in assessed.items() if a.outcome == 'planned'})
        except BackfillAborted as exc:
            self.error = exc.error
        except Exception as exc:
            logger.exception("backfill stopped: tenant=%s", self.tenant)
            self.error = {'code': 'unexpected_error', 'detail': type(exc).__name__}

    def _select(self, device: Optional[str]) -> list[dict]:
        if device is None:
            try:
                entities = device_models.list_entities(self.tenant, 'Device')
            except Exception as exc:
                raise _abort('listing_failed', exc) from None
            if not entities:
                raise BackfillAborted('no_devices')
            return entities
        if external_id_of(self.tenant, device) is None or not is_device_type(device):
            raise BackfillAborted('not_a_device_of_the_tenant', entity=device)
        try:
            entity = device_models.read_entity(self.tenant, device)
        except Exception as exc:
            raise _abort('device_read_failed', exc, entity=device) from None
        if entity is None:
            raise BackfillAborted('device_not_found', entity=device)
        if entity.get('type') != 'Device':
            raise BackfillAborted('not_a_device_of_the_tenant', entity=device)
        return [entity]

    def _assess_all(self, entities: list[dict]) -> dict[str, Assessment]:
        profiles: dict = {}
        assessed = {}
        for entity in sorted(entities, key=lambda e: e['id']):
            urn = entity['id']
            named = named_profile(self.tenant, entity)
            if isinstance(named, Assessment):
                assessed[urn] = named
            else:
                if named not in profiles:
                    try:
                        profiles[named] = dataloggers.read_profile(self.tenant, named)
                    except Exception as exc:
                        raise _abort('profile_read_failed', exc, profile=named) from None
                try:
                    stored = device_models.read_entity(self.tenant, urn, link=False)
                except Exception as exc:
                    raise _abort('device_read_failed', exc, entity=urn) from None
                assessed[urn] = assess(self.tenant, entity, named, profiles[named], stored)
            self.reports[urn] = assessed[urn].report(urn)
        return assessed

    def _bring_models_up_to_date(self, assessed: dict[str, Assessment]) -> None:
        """Every DeviceModel in use, before any Device. The first that is not under the official
        context stops the run: the broker's context lacks the terms.

        ASSUMPTION: a DeviceModel that already says what it should is not upserted, because a second
        run writes nothing. Its context is checked all the same: one that an earlier run stored
        under the default vocabulary reads like a right one.
        """
        in_use = {a.code: a.profile for a in assessed.values() if a.outcome in ('planned', 'unchanged')}
        for code, profile in sorted(in_use.items()):
            model = build_device_model(self.tenant, profile)
            names = [name for name in model if name not in ('id', 'type')]
            try:
                outcome = self._write_model(code, profile, model['id'])
                if outcome != 'planned':
                    _check_context(self.tenant, model['id'], 'DeviceModel',
                                   [name for name in _MODEL_ATTRS if name in model], names)
            except BackfillAborted:
                raise
            except Exception as exc:
                raise _abort('device_model_failed', exc, entity=model['id']) from None

    def _write_model(self, code: str, profile: Mapping, model_id: str) -> str:
        """Write the DeviceModel if it needs it. Its outcome goes to the summary before its guard,
        and before the write as `failed`: one that raises may have landed."""
        current = device_models.is_model_current(self.tenant, profile)
        report = {'id': model_id,
                  'outcome': 'unchanged' if current else 'failed' if self.apply else 'planned'}
        self.models.append(report)
        if self.apply and not current:
            device_models.ensure_device_model(self.tenant, profile)
            report['outcome'] = 'written'
            logger.info("device model written: tenant=%s device_model=%s profile=%s",
                        self.tenant, model_id, code)
        return report['outcome']

    def _write(self, planned: dict[str, Assessment]) -> None:
        wait = False
        for urn, before in planned.items():
            if wait:
                self.sleep(WAIT_S)
            try:
                fresh = self._reassess(urn, before)
            except Exception as exc:
                self._fail(urn, before.code, 'read_failed', exc)
                return  # after any failure: do not hammer a sick broker
            wait = fresh.outcome == 'planned'
            if not wait:
                self.reports[urn] = fresh.report(urn)
                continue
            if not self._post(urn, fresh):
                return
            complete = self._delete_stale(urn, fresh)
            self._verify_terms(urn, fresh.changed)
            if not complete:
                return

    def _reassess(self, urn: str, before: Assessment) -> Assessment:
        """The same assessment on fresh reads; the entity is the last one, just before the write."""
        profile = dataloggers.read_profile(self.tenant, before.code)
        stored = device_models.read_entity(self.tenant, urn, link=False)
        entity = device_models.read_entity(self.tenant, urn)
        if entity is None:
            return _refused('gone')
        named = named_profile(self.tenant, entity)
        if isinstance(named, Assessment):
            return named if named.outcome == 'refused' else _refused('left_the_profile')
        if named != before.code:
            return _refused('left_the_profile', named)
        fresh = assess(self.tenant, entity, named, profile, stored)
        if fresh.outcome != 'refused' and profile != before.profile:
            # Its DeviceModel was written from the profile of before.
            return _refused('profile_changed', named)
        return fresh

    def _fail(self, urn: str, code: str, reason: str, exc: Exception, **extra) -> None:
        logger.error("device not backfilled: tenant=%s device=%s profile=%s reason=%s error=%s %s",
                     self.tenant, urn, code, reason, device_models.why(exc), extra)
        self.reports[urn] = {'urn': urn, 'outcome': 'failed', 'profile': code, 'reason': reason,
                             'error': device_models.why(exc), **extra}

    def _post(self, urn: str, fresh: Assessment) -> bool:
        """The one POST /attrs; False when it failed."""
        if not fresh.changed:
            return True
        names = sorted(fresh.changed)
        try:
            device_models.append_attrs(self.tenant, urn, fresh.changed)
        except Exception as exc:
            # A partial answer or a lost one may have stored some of them.
            self._fail(urn, fresh.code, 'write_failed', exc, attributes_unconfirmed=names)
            return False
        logger.info("device attributes written: tenant=%s device=%s profile=%s attributes=%s",
                    self.tenant, urn, fresh.code, names)
        return True

    def _delete_stale(self, urn: str, fresh: Assessment) -> bool:
        """Delete what the profile no longer has and report the Device; False when one failed."""
        for attr in fresh.stale:
            try:
                device_models.delete_attr(self.tenant, urn, attr)
            except Exception as exc:
                landed = {'attributes': sorted(fresh.changed)} if fresh.changed else {}
                self._fail(urn, fresh.code, 'delete_failed', exc, **landed)
                return False
            logger.info("device attribute deleted: tenant=%s device=%s profile=%s attribute=%s",
                        self.tenant, urn, fresh.code, attr)
        self.reports[urn] = fresh.report(urn, 'written')
        return True

    def _verify_terms(self, urn: str, written: Iterable[str]) -> None:
        """Read the Device back when it wrote a term that no Device of this run has proven yet."""
        if self.verified.issuperset(written):
            return
        try:
            _check_context(self.tenant, urn, 'Device', written, written)
        except BackfillAborted:
            raise
        except Exception as exc:
            raise _abort('device_read_failed', exc, entity=urn) from None
        self.verified.update(written)

    def summary(self) -> dict:
        devices = list(self.reports.values())
        if self.apply:
            devices = [{**d, 'outcome': 'not_attempted'} if d['outcome'] == 'planned' else d
                       for d in devices]
        out = {'tenant': self.tenant, 'mode': 'apply' if self.apply else 'dry-run',
               'counts': {'devices': len(devices),
                          **{name: sum(d['outcome'] == name for d in devices) for name in OUTCOMES}},
               'device_models': self.models, 'devices': devices}
        if self.error:
            out['error'] = self.error
        return out


def run(tenant: str, device: Optional[str] = None, apply: bool = False,
        sleep: Callable[[float], None] = time.sleep) -> tuple[dict, int]:
    """(summary, exit code) of one run over the Devices of a tenant, or over one of them."""
    job = _Run(tenant, apply, sleep)
    logger.info("backfill started: tenant=%s apply=%s device=%s", tenant, apply, device)
    job.execute(device)
    summary = job.summary()
    counts = summary['counts']
    logger.info("backfill done: tenant=%s apply=%s counts=%s error=%s", tenant, apply, counts, job.error)
    incomplete = apply and any(counts[name] for name in ('refused', 'failed', 'not_attempted'))
    return summary, 1 if job.error or incomplete else 0


def _tenant_argument(value: str) -> str:
    # The id is part of URNs and of the RLS value: another spelling of it is another tenant.
    valid, reason = validate_tenant_id(value)
    if not valid:
        raise argparse.ArgumentTypeError(reason)
    return value


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog='device_official_backfill',
                                     description='Backfill the official attributes of existing Devices.')
    parser.add_argument('--tenant', required=True, type=_tenant_argument)
    parser.add_argument('--device', metavar='URN', help='only this Device')
    parser.add_argument('--apply', action='store_true', help='write; without it, a dry run')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    summary, code = run(args.tenant, device=args.device, apply=args.apply)
    print(json.dumps(summary, indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
