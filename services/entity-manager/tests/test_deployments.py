"""Device deployment repository: one transaction per write, a change row per write."""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import psycopg2.errors
import pytest

import dataloggers
import deployments
from deployments import DeploymentSpec

TENANT = "tenant-a"
DEVICE = "urn:ngsi-ld:Device:tenant-a:probe-1"
T0 = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=30)


def _row(dep_id, valid_from, valid_to=None, **kw):
    row = {"id": dep_id, "device_urn": DEVICE, "valid_from": valid_from, "valid_to": valid_to,
           "observes_feature": None, "observation_context": {}, "channel_overrides": {},
           "mobility": "fixed", "lat": 42.0, "lon": -1.5}
    row.update(kw)
    return row


class FakeDb:
    """Records every statement; answers fetchone/fetchall by statement shape."""

    def __init__(self, rows=None):
        self.rows = {r["id"]: dict(r) for r in (rows or [])}
        self.log = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on = None          # substring -> exception raised by execute
        self.next_id = 0
        self.last_end = None
        self.updated = None

    @contextmanager
    def connect(self, tenant_id):
        assert tenant_id == TENANT
        conn = _Conn(self)
        try:
            yield conn
        except Exception:
            self.rollbacks += 1
            raise

    def statements(self, prefix):
        return [(s, p) for s, p in self.log if s.startswith(prefix)]


class _Conn:
    def __init__(self, db):
        self.db = db

    def cursor(self, **kw):
        return _Cur(self.db)

    def commit(self):
        self.db.commits += 1

    def rollback(self):
        self.db.rollbacks += 1


class _Cur:
    def __init__(self, db):
        self.db = db
        self._rows = []

    def close(self):
        pass

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.db.log.append((sql, params))
        if self.db.fail_on and self.db.fail_on in sql:
            raise self.db.fail_on_exc
        self._rows = []
        if sql.startswith("SELECT max(upper(valid))"):
            self._rows = [{"last_end": self.db.last_end}]
        elif sql.startswith("SELECT pg_advisory_xact_lock"):
            pass
        elif sql.startswith("SELECT") and "FROM device_deployments" in sql:
            if "upper_inf(valid)" in sql and "FOR UPDATE" in sql:
                self._rows = [r for r in self.db.rows.values() if r["valid_to"] is None]
            elif "FOR UPDATE" in sql and "upper(valid) =" in sql:
                self._rows = [r for r in self.db.rows.values() if r["valid_to"] == params[-1]]
            elif "WHERE id = %s" in sql:
                r = self.db.rows.get(params[0])
                self._rows = [r] if r else []
            elif "@> now()" in sql:
                self._rows = [r for r in self.db.rows.values() if r["valid_to"] is None]
            else:
                self._rows = sorted(self.db.rows.values(), key=lambda r: r["valid_from"], reverse=True)
        elif sql.startswith("INSERT INTO device_deployments"):
            self.db.next_id += 1
            new_id = f"dep-{self.db.next_id}"
            self.db.rows[new_id] = _row(new_id, params[2], None)
            self._rows = [{"id": new_id}]
        elif sql.startswith("UPDATE device_deployments"):
            self.db.updated = (sql, params)
        elif sql.startswith("DELETE FROM device_deployments"):
            self.db.deleted = params

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


@pytest.fixture
def db():
    d = FakeDb()
    with patch.object(deployments, "get_db_connection_with_tenant", d.connect):
        yield d


def _changes(db):
    out = []
    for _sql, params in db.statements("INSERT INTO device_deployment_changes"):
        out.append(params)
    return out


def test_open_first_deployment_inserts_only(db):
    spec = DeploymentSpec(None, {}, {}, "fixed", (-1.5, 42.0))
    res = deployments.open_deployment(TENANT, DEVICE, spec, T0, "alice", "installed")
    assert res["id"] == "dep-1"
    assert db.commits == 1
    assert len(db.statements("INSERT INTO device_deployments")) == 1
    assert len(_changes(db)) == 1


def test_open_closes_previous_and_inserts_new(db):
    db.rows["old"] = _row("old", T0)
    spec = DeploymentSpec("urn:ngsi-ld:Feature:tenant-a:f1", {"heightAboveGround": 2},
                          {}, "fixed", (-1.4, 42.1))
    res = deployments.open_deployment(TENANT, DEVICE, spec, T1, "alice", "moved")
    log = [s for s, _ in db.log]
    sel = next(i for i, s in enumerate(log) if "FOR UPDATE" in s)
    upd = next(i for i, s in enumerate(log) if s.startswith("UPDATE device_deployments"))
    ins = next(i for i, s in enumerate(log) if s.startswith("INSERT INTO device_deployments"))
    assert sel < upd < ins                      # lock, then close, then insert
    assert db.updated[1][0] == T1               # upper bound of the old row = valid_from
    assert res["id"].startswith("dep-")
    assert db.commits == 1


def test_open_rejects_backdating_before_current_start(db):
    db.rows["old"] = _row("old", T1)
    spec = DeploymentSpec(None, {}, {}, "fixed", (0.0, 0.0))
    for bad in (T0, T1):                        # before, and equal (strictly after is required)
        with pytest.raises(dataloggers.ApiError) as e:
            deployments.open_deployment(TENANT, DEVICE, spec, bad, "alice", "moved")
        assert e.value.status == 409
    assert db.statements("INSERT INTO device_deployments") == []
    assert db.commits == 0


def test_open_rejects_naive_datetime(db):
    spec = DeploymentSpec(None, {}, {}, "fixed", (0.0, 0.0))
    with pytest.raises(dataloggers.ApiError) as e:
        deployments.open_deployment(TENANT, DEVICE, spec, datetime(2026, 3, 1), "alice", "x")
    assert e.value.status == 400
    assert db.log == []


def test_open_exclusion_violation_is_409(db):
    db.fail_on = "INSERT INTO device_deployments"
    db.fail_on_exc = psycopg2.errors.ExclusionViolation("overlap")
    spec = DeploymentSpec(None, {}, {}, "fixed", (0.0, 0.0))
    with pytest.raises(dataloggers.ApiError) as e:
        deployments.open_deployment(TENANT, DEVICE, spec, T0, "alice", "installed")
    assert e.value.status == 409
    assert db.commits == 0


def test_open_rejects_bad_mobility(db):
    spec = DeploymentSpec(None, {}, {}, "floating", None)
    with pytest.raises(dataloggers.ApiError) as e:
        deployments.open_deployment(TENANT, DEVICE, spec, T0, "alice", "x")
    assert e.value.status == 400


def test_correct_overlap_maps_to_409(db):
    db.rows["a"] = _row("a", T0, T1)
    db.fail_on = "UPDATE device_deployments"
    db.fail_on_exc = psycopg2.errors.ExclusionViolation("overlap")
    with pytest.raises(dataloggers.ApiError) as e:
        deployments.correct_deployment(TENANT, "a", {"valid_to": T1 + timedelta(days=5)},
                                       "alice", "typo")
    assert e.value.status == 409
    assert "overlap" in e.value.message
    assert db.commits == 0
    assert _changes(db) == []


def test_correct_requires_reason(db):
    db.rows["a"] = _row("a", T0)
    for reason in ("", "   ", None):
        with pytest.raises(dataloggers.ApiError) as e:
            deployments.correct_deployment(TENANT, "a", {"observesFeature": "urn:x"}, "alice", reason)
        assert e.value.status == 400
    assert db.log == []


def test_correct_rejects_unknown_field_and_empty_changes(db):
    db.rows["a"] = _row("a", T0)
    for changes in ({}, {"mobility": "mobile"}):
        with pytest.raises(dataloggers.ApiError) as e:
            deployments.correct_deployment(TENANT, "a", changes, "alice", "why")
        assert e.value.status == 400


def test_correct_unknown_deployment_is_404(db):
    with pytest.raises(dataloggers.ApiError) as e:
        deployments.correct_deployment(TENANT, "nope", {"observesFeature": None}, "alice", "why")
    assert e.value.status == 404


def test_correct_writes_before_and_after(db):
    db.rows["a"] = _row("a", T0)
    deployments.correct_deployment(TENANT, "a", {"observesFeature": "urn:f"}, "alice", "fix feature")
    sql, params = db.updated
    assert "observes_feature = %s" in sql
    assert "urn:f" in params
    (ch,) = _changes(db)
    # (tenant, deployment_id, changed_by, reason, before, after)
    assert ch[0] == TENANT and ch[1] == "a" and ch[2] == "alice" and ch[3] == "fix feature"
    assert ch[4].adapted["id"] == "a"
    assert ch[5].adapted["id"] == "a"
    assert db.commits == 1


def test_every_write_records_a_change_row(db):
    db.rows["old"] = _row("old", T0)
    spec = DeploymentSpec(None, {}, {}, "fixed", (0.0, 0.0))
    deployments.open_deployment(TENANT, DEVICE, spec, T1, "alice", "moved")
    assert len(_changes(db)) == 2               # closed previous + opened new
    deployments.correct_deployment(TENANT, "old", {"observesFeature": "urn:f"}, "bob", "fix")
    assert len(_changes(db)) == 3
    assert all(c[3] for c in _changes(db))      # reason never empty


def test_current_deployment_contains_now(db):
    db.rows["a"] = _row("a", T0)
    cur = deployments.current_deployment(TENANT, DEVICE)
    assert cur["id"] == "a"
    assert "valid @> now()" in db.log[-1][0]
    assert cur["valid_to"] is None
    assert cur["location"] == {"lat": 42.0, "lon": -1.5}
    assert cur["observesFeature"] is None


def test_current_deployment_none(db):
    assert deployments.current_deployment(TENANT, DEVICE) is None


def test_list_deployments_newest_first(db):
    db.rows["a"] = _row("a", T0, T1)
    db.rows["b"] = _row("b", T1)
    out = deployments.list_deployments(TENANT, DEVICE)
    assert [d["id"] for d in out] == ["b", "a"]
    assert "ORDER BY lower(valid) DESC" in db.log[-1][0]


def test_location_none_when_absent(db):
    db.rows["a"] = _row("a", T0, lat=None, lon=None)
    assert deployments.current_deployment(TENANT, DEVICE)["location"] is None


def test_revert_open_restores_previous_open_interval(db):
    db.rows["old"] = _row("old", T0, T1)
    db.rows["new"] = _row("new", T1)
    deployments.revert_open(TENANT, "new", "alice")
    sql, params = db.updated
    assert "tstzrange(lower(valid), NULL" in sql    # previous row is open again
    assert params[0] == "old" or "old" in params
    assert db.deleted[0] == "new"
    chs = _changes(db)
    assert len(chs) == 2
    assert all(c[3] == "rollback: orion mirror failed" for c in chs)
    assert db.commits == 1


def test_revert_open_rejects_non_open_deployment(db):
    db.rows["old"] = _row("old", T0, T1)
    db.rows["new"] = _row("new", T1)
    with pytest.raises(dataloggers.ApiError) as e:
        deployments.revert_open(TENANT, "old", "alice")
    assert e.value.status == 409
    with pytest.raises(dataloggers.ApiError) as e:
        deployments.revert_open(TENANT, "missing", "alice")
    assert e.value.status == 409
    assert db.commits == 0


def test_db_error_is_502(db):
    db.fail_on = "SELECT"
    db.fail_on_exc = RuntimeError("down")
    with pytest.raises(dataloggers.ApiError) as e:
        deployments.list_deployments(TENANT, DEVICE)
    assert e.value.status == 502


def test_delete_deployment_removes_only_that_id(db):
    db.rows["a"] = _row("a", T0)
    deployments.delete_deployment(TENANT, "a")
    assert db.deleted == (TENANT, "a")
    assert db.commits == 1
    sql, params = db.statements("DELETE FROM device_deployment_changes")[0]
    assert params == (TENANT, "a")


def test_open_takes_the_device_advisory_lock_before_anything_else(db):
    deployments.open_deployment(TENANT, DEVICE, DeploymentSpec(None, {}, {}, "fixed", (0.0, 0.0)),
                                T0, "alice", "x")
    assert "pg_advisory_xact_lock" in db.log[0][0]
    assert db.log[0][1] == (f"{TENANT}|{DEVICE}",)


def test_first_open_rejects_an_existing_open_deployment(db):
    db.rows["old"] = _row("old", T0)
    spec = DeploymentSpec(None, {}, {}, "fixed", (0.0, 0.0))
    with pytest.raises(dataloggers.ApiError) as e:
        deployments.open_deployment(TENANT, DEVICE, spec, T1, "alice", "registration", first=True)
    assert e.value.status == 409 and "open deployment" in e.value.message
    assert db.statements("INSERT INTO device_deployments") == [] and db.commits == 0


def test_first_open_after_closed_history_requires_a_later_valid_from(db):
    db.rows["old"] = _row("old", T0, T1)
    db.last_end = T1
    spec = DeploymentSpec(None, {}, {}, "fixed", (0.0, 0.0))
    with pytest.raises(dataloggers.ApiError) as e:
        deployments.open_deployment(TENANT, DEVICE, spec, T1, "alice", "registration", first=True)
    assert e.value.status == 409 and "after the end" in e.value.message
    res = deployments.open_deployment(TENANT, DEVICE, spec, T1 + timedelta(days=1),
                                      "alice", "registration", first=True)
    assert res["id"].startswith("dep-")


def test_close_open_deployment_ends_it_and_logs_the_reason(db):
    db.rows["a"] = _row("a", T0)
    res = deployments.close_open_deployment(TENANT, DEVICE, "alice", "device deleted")
    assert res is not None
    sql, params = db.updated
    assert params[0] > T0 and params[1] == "a"
    assert _changes(db)[0][3] == "device deleted"
    assert db.commits == 1


def test_close_open_deployment_without_open_row_is_a_noop(db):
    db.rows["a"] = _row("a", T0, T1)
    assert deployments.close_open_deployment(TENANT, DEVICE, "alice", "device deleted") is None
    assert db.updated is None
    assert _changes(db) == []


def test_get_deployment_by_id(db):
    db.rows["d1"] = _row("d1", T0)
    assert deployments.get_deployment(TENANT, "d1")["device_urn"] == DEVICE
    assert deployments.get_deployment(TENANT, "missing") is None
