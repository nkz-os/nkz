"""Pure aggregation/fusion engine of the `best` observation service (contract 2.2, 8.4)."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

from observations.engine import (  # noqa: E402
    BucketPoint,
    Reading,
    aggregate_tier,
    buckets,
    counter_deltas,
    fall_through,
    group_by_sub_context,
)

UTC = timezone.utc


def d(y, mo, da, h=0, mi=0):
    return datetime(y, mo, da, h, mi, tzinfo=UTC)


def rd(src, t, v, sub=None, kind="iot_sensor"):
    return Reading(source_id=src, deployment_id="dep-" + src, t=t, value=v,
                   sub_context=sub or {}, distance_m=None, kind=kind, calibrated=False)


def day_buckets(day=(2026, 11, 3)):
    return buckets(d(*day), d(*day) + timedelta(days=1), "P1D", "UTC")


def test_p1d_buckets_follow_local_midnight_in_madrid():
    bl = buckets(d(2026, 10, 23, 22), d(2026, 10, 26, 22), "P1D", "Europe/Madrid")
    assert bl[0] == (d(2026, 10, 23, 22), d(2026, 10, 24, 22))
    assert bl[1] == (d(2026, 10, 24, 22), d(2026, 10, 25, 23))
    assert bl[1][1] - bl[1][0] == timedelta(hours=25)
    assert bl[2] == (d(2026, 10, 25, 23), d(2026, 10, 26, 23))
    assert all(b[0].tzinfo is not None and b[0].utcoffset() == timedelta(0) for b in bl)


def test_buckets_pt1h_pt15m_utc_aligned_and_raw_single_window():
    b = buckets(d(2026, 11, 3, 10, 20), d(2026, 11, 3, 12, 0), "PT1H", "Europe/Madrid")
    assert b == [(d(2026, 11, 3, 10), d(2026, 11, 3, 11)), (d(2026, 11, 3, 11), d(2026, 11, 3, 12))]
    q = buckets(d(2026, 11, 3, 10, 20), d(2026, 11, 3, 10, 40), "PT15M", "UTC")
    assert [x[0] for x in q] == [d(2026, 11, 3, 10, 15), d(2026, 11, 3, 10, 30)]
    assert buckets(d(2026, 11, 3), d(2026, 11, 4), "raw", "UTC") == [(d(2026, 11, 3), d(2026, 11, 4))]


def test_instant_mean_per_source_then_median_across_three():
    bl = buckets(d(2026, 11, 3, 10), d(2026, 11, 3, 11), "PT1H", "UTC")
    rs = [rd("a", d(2026, 11, 3, 10, 5), 9), rd("a", d(2026, 11, 3, 10, 35), 11),
          rd("b", d(2026, 11, 3, 10, 10), 11),
          rd("c", d(2026, 11, 3, 10, 10), 30)]
    (p,) = aggregate_tier(rs, bl, "instant", "median", None, None, "PT1H")
    assert p.value == 11 and p.n_sources == 3 and p.coverage == 1.0
    assert {s["source_id"] for s in p.sources} == {"a", "b", "c"}


def test_two_sources_median_is_mean():
    bl = buckets(d(2026, 11, 3, 10), d(2026, 11, 3, 11), "PT1H", "UTC")
    rs = [rd("a", d(2026, 11, 3, 10, 5), 10), rd("b", d(2026, 11, 3, 10, 5), 12)]
    (p,) = aggregate_tier(rs, bl, "instant", "median", None, None, "PT1H")
    assert p.value == 11


def test_unknown_fusion_treated_as_median_with_warning():
    bl = buckets(d(2026, 11, 3, 10), d(2026, 11, 3, 11), "PT1H", "UTC")
    rs = [rd("a", d(2026, 11, 3, 10, 5), 10), rd("b", d(2026, 11, 3, 10, 5), 12),
          rd("c", d(2026, 11, 3, 10, 5), 50)]
    w = []
    (p,) = aggregate_tier(rs, bl, "instant", "weighted_magic", None, None, "PT1H", warnings=w)
    assert p.value == 12
    assert any("unknown_fusion" in x for x in w)


def test_counter_wrap_uses_counter_max():
    rs = [rd("a", d(2026, 11, 3, 10), 6553.0), rd("a", d(2026, 11, 3, 11), 0.5)]
    out, warns = counter_deltas(rs, 6553.5, None)
    assert [r.value for r in out] == [1.0]
    assert out[0].t == d(2026, 11, 3, 11)  # attributed to the later reading


def test_counter_reset_plausible_counts_current():
    rs = [rd("a", d(2026, 11, 3, 10), 400.0), rd("a", d(2026, 11, 3, 11), 2.0)]
    out, warns = counter_deltas(rs, None, 200.0)
    assert [r.value for r in out] == [2.0]
    assert len(warns) == 1 and "counter_reset" in warns[0] and "a" in warns[0]


def test_counter_reset_implausible_discarded():
    rs = [rd("a", d(2026, 11, 3, 10), 1000.0), rd("a", d(2026, 11, 3, 11), 900.0)]
    out, warns = counter_deltas(rs, None, 200.0)
    assert out == []
    assert len(warns) == 1


def test_counter_max_none_negative_delta_is_reset_and_first_has_no_delta():
    rs = [rd("a", d(2026, 11, 3, 10), 50.0), rd("a", d(2026, 11, 3, 11), 5.0),
          rd("a", d(2026, 11, 3, 12), 8.0)]
    out, warns = counter_deltas(rs, None, None)
    assert [r.value for r in out] == [5.0, 3.0]
    assert len(warns) == 1 and "counter_reset" in warns[0]


def test_counter_unsorted_input_and_per_source_independence():
    rs = [rd("a", d(2026, 11, 3, 11), 5.0), rd("b", d(2026, 11, 3, 10), 100.0),
          rd("a", d(2026, 11, 3, 10), 3.0), rd("b", d(2026, 11, 3, 11), 101.5)]
    out, _ = counter_deltas(rs, None, None)
    assert sorted((r.source_id, r.value) for r in out) == [("a", 2.0), ("b", 1.5)]


def test_cumulative_sums_deltas_per_source_and_fuses():
    bl = buckets(d(2026, 11, 3, 10), d(2026, 11, 3, 11), "PT1H", "UTC")
    rs = [rd("a", d(2026, 11, 3, 9, 50), 1.0), rd("a", d(2026, 11, 3, 10, 10), 2.0),
          rd("a", d(2026, 11, 3, 10, 40), 4.0)]
    (p,) = aggregate_tier(rs, bl, "cumulative", "median", 0, 100, "PT1H",
                          counter_max=None, max_rate_per_hour=None)
    assert p.value == 3.0  # deltas 1.0 + 2.0


def test_cumulative_increment_outside_valid_range_excluded_with_warning():
    bl = buckets(d(2026, 11, 3, 10), d(2026, 11, 3, 11), "PT1H", "UTC")
    rs = [rd("a", d(2026, 11, 3, 9, 50), 1.0), rd("a", d(2026, 11, 3, 10, 10), 2.0),
          rd("b", d(2026, 11, 3, 9, 50), 1.0), rd("b", d(2026, 11, 3, 10, 40), 500.0)]
    w = []
    (p,) = aggregate_tier(rs, bl, "cumulative", "median", 0, 100, "PT1H", warnings=w)
    assert p.value == 1.0 and p.n_sources == 1
    assert any("increment_out_of_range" in x for x in w)


def test_r1_cumulative_range_check_is_per_bucket_sum():
    (bk,) = day_buckets()
    rs = [rd("a", bk[0] + timedelta(hours=h), 100.0 * h) for h in range(11)]  # ten deltas of 100
    w = []
    (p,) = aggregate_tier(rs, [bk], "cumulative", "median", 0, 500, "P1D", warnings=w)
    assert p.value is None and p.n_sources == 0
    assert any("increment_out_of_range" in x for x in w)
    (p2,) = aggregate_tier(rs, [bk], "cumulative", "median", 0, 5000, "P1D")
    assert p2.value == 1000.0


def test_r2_any_other_sub_context_key_never_fused():
    a = rd("a", d(2026, 11, 3), 1, {"structureRef": "x"})
    b = rd("b", d(2026, 11, 3), 1, {"structureRef": "y"})
    c = rd("c", d(2026, 11, 3), 1, {"structureRef": "x", "heightAboveGround": 1.0})
    g = group_by_sub_context([a, b, c], {"heightAboveGround": 0.5})
    assert len(g) == 3


def test_r2_mixed_value_types_do_not_crash_sorting():
    a = rd("a", d(2026, 11, 3), 1, {"organ": 5})
    b = rd("b", d(2026, 11, 3), 1, {"organ": "leaf"})
    assert len(group_by_sub_context([a, b], {})) == 2


def test_r3_near_full_value_after_decrease_is_not_a_wrap():
    rs = [rd("a", d(2026, 11, 3, 10), 6553.0), rd("a", d(2026, 11, 3, 11), 6552.9)]
    out, warns = counter_deltas(rs, 6553.5, 200.0)
    assert out == [] and warns and "counter_reset_discarded" in warns[0]


def test_r3_wrap_without_max_rate_requires_small_current():
    rs = [rd("a", d(2026, 11, 3, 10), 6553.0), rd("a", d(2026, 11, 3, 11), 6000.0)]
    out, warns = counter_deltas(rs, 6553.5, None)
    assert [r.value for r in out] == [6000.0] and "counter_reset" in warns[0]


def test_r3_wrap_with_max_rate_accepted_when_wrapped_increment_plausible():
    rs = [rd("a", d(2026, 11, 3, 10), 6553.0), rd("a", d(2026, 11, 3, 11), 0.5)]
    out, warns = counter_deltas(rs, 6553.5, 200.0)
    assert [r.value for r in out] == [1.0] and warns == []


def test_r4_duplicates_keep_max_and_zero_elapsed_skipped():
    t0, t1 = d(2026, 11, 3, 10), d(2026, 11, 3, 11)
    rs = [rd("a", t0, 5.0), rd("a", t0, 7.0), rd("a", t1, 9.0), rd("a", t1, 8.0)]
    out, warns = counter_deltas(rs, None, None)
    assert [r.value for r in out] == [2.0] and warns == []


def test_r5_per_source_counter_max_wins_over_fallback():
    t0, t1 = d(2026, 11, 3, 10), d(2026, 11, 3, 11)
    a = [replace_cm(rd("a", t0, 99.0), 100.0), replace_cm(rd("a", t1, 1.0), 100.0)]
    out, warns = counter_deltas(a, 6553.5, None)
    assert [r.value for r in out] == [2.0] and warns == []
    # fallback used when the reading has none
    out2, _ = counter_deltas([rd("b", t0, 99.0), rd("b", t1, 1.0)], 100.0, None)
    assert [r.value for r in out2] == [2.0]


def replace_cm(r, cm):
    from dataclasses import replace
    return replace(r, counter_max=cm)


def test_r6_cumulative_short_gap_covers_overlapped_hours_and_long_gap_warns():
    (bk,) = day_buckets()
    rs = [rd("a", bk[0] + timedelta(hours=0, minutes=30), 0.0),
          rd("a", bk[0] + timedelta(hours=4, minutes=30), 1.0)]  # 4 h gap -> hours 0..4 = 5
    (p,) = aggregate_tier(rs, [bk], "cumulative", "median", None, None, "P1D")
    assert p.coverage == pytest.approx(5 / 24)
    rs2 = [rd("a", bk[0] + timedelta(hours=1), 0.0), rd("a", bk[0] + timedelta(hours=10), 1.0)]
    w = []
    (p2,) = aggregate_tier(rs2, [bk], "cumulative", "median", None, None, "P1D", warnings=w)
    assert p2.coverage == pytest.approx(1 / 24)
    assert any("long_gap" in x for x in w)


def test_r7_partial_day_expected_hours_use_now():
    (bk,) = day_buckets()
    rs = [rd("a", bk[0] + timedelta(hours=h, minutes=5), 1.0) for h in range(6)]
    now = bk[0] + timedelta(hours=6, minutes=10)  # ceil -> 7 expected hours
    (p,) = aggregate_tier(rs, [bk], "instant", "median", None, None, "P1D", now=now)
    assert p.coverage == pytest.approx(6 / 7)
    (q,) = aggregate_tier(rs, [bk], "instant", "median", None, None, "P1D")
    assert q.coverage == pytest.approx(6 / 24)


def test_r8_raw_fall_through_picks_one_tier_for_whole_window():
    t1, t2 = d(2026, 11, 3, 1), d(2026, 11, 3, 2)
    tier1 = [bp(t1, 1.0, 1.0)]
    tier2 = [bp(t1, 9.0, 1.0), bp(t2, 2.0, 1.0)]
    out = fall_through([(2, tier2), (1, tier1)], None, "raw")
    assert out == [(1, tier1[0])]
    assert fall_through([(1, []), (2, tier2)], None, "raw")[0][0] == 2
    assert fall_through([(1, [])], None, "raw") == []


def test_r9_amount_sums_readings_without_counter_rule_and_checks_bucket_sum():
    (bk,) = day_buckets()
    rs = [rd("a", bk[0] + timedelta(hours=h), 2.0) for h in range(3)]
    (p,) = aggregate_tier(rs, [bk], "amount", "median", 0, 100, "P1D")
    assert p.value == 6.0 and p.sources[0]["n_readings"] == 3
    w = []
    (q,) = aggregate_tier(rs, [bk], "amount", "median", 0, 5, "P1D", warnings=w)
    assert q.value is None and any("increment_out_of_range" in x for x in w)
    # decreasing values are plain amounts, never a counter reset
    rs2 = [rd("a", bk[0], 5.0), rd("a", bk[0] + timedelta(hours=1), 1.0)]
    (r,) = aggregate_tier(rs2, [bk], "amount", "median", None, None, "P1D")
    assert r.value == 6.0


def test_repeated_identical_warnings_are_merged_with_count():
    bl = buckets(d(2026, 11, 3, 10), d(2026, 11, 3, 13), "PT1H", "UTC")
    rs = [rd("a", d(2026, 11, 3, 10, 5), 1.0), rd("a", d(2026, 11, 3, 11, 5), 1.0),
          rd("a", d(2026, 11, 3, 12, 5), 1.0)]
    w = []
    aggregate_tier(rs, bl, "instant", "nope", None, None, "PT1H", warnings=w)
    assert w == ["unknown_fusion:nope x3"]


def test_heights_within_tolerance_form_one_group():
    tol = {"heightAboveGround": 0.5}
    a = rd("a", d(2026, 11, 3), 1, {"heightAboveGround": 1.5})
    b = rd("b", d(2026, 11, 3), 1, {"heightAboveGround": 1.8})
    c = rd("c", d(2026, 11, 3), 1, {"heightAboveGround": 2.5})
    g = group_by_sub_context([a, b], tol)
    assert len(g) == 1 and g[0][0] == {"heightAboveGround": 1.5} and len(g[0][1]) == 2
    assert len(group_by_sub_context([a, c], tol)) == 2


def test_depths_never_fused():
    a = rd("a", d(2026, 11, 3), 1, {"depthFrom": 0, "depthTo": 20})
    b = rd("b", d(2026, 11, 3), 1, {"depthFrom": 20, "depthTo": 40})
    g = group_by_sub_context([b, a], {"heightAboveGround": 0.5})
    assert [x[0] for x in g] == [{"depthFrom": 0, "depthTo": 20}, {"depthFrom": 20, "depthTo": 40}]


def test_organ_and_context_ref_never_fused_and_ordering_deterministic():
    a = rd("a", d(2026, 11, 3), 1, {"organ": "leaf"})
    b = rd("b", d(2026, 11, 3), 1, {"organ": "fruit"})
    c = rd("c", d(2026, 11, 3), 1, {"organ": "leaf", "contextRef": "urn:x"})
    g1 = group_by_sub_context([a, b, c], {})
    g2 = group_by_sub_context([c, b, a], {})
    assert len(g1) == 3 and [x[0] for x in g1] == [x[0] for x in g2]


def test_coverage_p1d_counts_hours():
    (bk,) = day_buckets()
    rs = [rd("a", bk[0] + timedelta(hours=h, minutes=10), 10.0) for h in range(20)]
    (p,) = aggregate_tier(rs, [bk], "instant", "median", None, None, "P1D")
    assert p.coverage == pytest.approx(0.833, abs=1e-3)
    assert p.value == 10.0


def test_coverage_p1d_dst_short_day_has_23_expected_hours():
    bl = buckets(d(2026, 3, 28, 23), d(2026, 3, 29, 22), "P1D", "Europe/Madrid")
    (bk,) = bl
    assert bk[1] - bk[0] == timedelta(hours=23)
    rs = [rd("a", bk[0] + timedelta(hours=h, minutes=1), 1.0) for h in range(23)]
    (p,) = aggregate_tier(rs, bl, "instant", "median", None, None, "P1D")
    assert p.coverage == 1.0


def test_pt1h_covered_with_one_reading():
    bl = buckets(d(2026, 11, 3, 10), d(2026, 11, 3, 11), "PT1H", "UTC")
    (p,) = aggregate_tier([rd("a", d(2026, 11, 3, 10, 7), 5.0)], bl, "instant", "median",
                          None, None, "PT1H")
    assert p.coverage == 1.0
    assert fall_through([(1, [p])], 0.9, "PT1H")[0][0] == 1  # min_coverage not applied


def test_empty_bucket_has_zero_coverage_and_none_value():
    bl = buckets(d(2026, 11, 3, 10), d(2026, 11, 3, 11), "PT1H", "UTC")
    (p,) = aggregate_tier([], bl, "instant", "median", None, None, "PT1H")
    assert p.value is None and p.coverage == 0.0 and p.n_sources == 0 and p.sources == []


def bp(t, v, cov, n=1):
    return BucketPoint(t=t, value=v, n_sources=n, coverage=cov, sources=[{"source_id": "s"}] if v is not None else [])


def test_fall_through_per_bucket():
    t1, t2 = d(2026, 11, 3), d(2026, 11, 4)
    tier2 = [bp(t1, 10.0, 0.9), bp(t2, 12.0, 0.3)]
    tier4 = [bp(t1, 99.0, 1.0), bp(t2, 20.0, 1.0)]
    out = fall_through([(2, tier2), (4, tier4)], 0.5, "P1D")
    assert [o[0] for o in out] == [2, 4]
    assert out[0][1].value == 10.0 and out[1][1].value == 20.0


def test_fall_through_min_coverage_null_defaults_to_half():
    t1 = d(2026, 11, 3)
    assert fall_through([(1, [bp(t1, 1.0, 0.5)])], None, "P1D")[0][0] == 1
    assert fall_through([(1, [bp(t1, 1.0, 0.49)])], None, "P1D")[0][0] is None


def test_empty_bucket_is_unavailable():
    t1 = d(2026, 11, 3)
    out = fall_through([(1, [bp(t1, None, 0.0, 0)])], 0.5, "P1D")
    assert out == [(None, BucketPoint(t=t1, value=None, n_sources=0, coverage=0.0, sources=[]))]


def test_raw_resolution_one_point_per_timestamp():
    win = buckets(d(2026, 11, 3), d(2026, 11, 4), "raw", "UTC")
    rs = [rd("a", d(2026, 11, 3, 1), 1.0), rd("b", d(2026, 11, 3, 1), 3.0), rd("a", d(2026, 11, 3, 2), 5.0)]
    pts = aggregate_tier(rs, win, "instant", "median", None, None, "raw")
    assert [(p.t, p.value, p.coverage) for p in pts] == [
        (d(2026, 11, 3, 1), 2.0, 1.0), (d(2026, 11, 3, 2), 5.0, 1.0)]


def test_c1_mixed_context_shapes_do_not_crash_and_are_deterministic():
    a = rd("a", d(2026, 11, 3), 1, {"organ": "leaf"})
    b = rd("b", d(2026, 11, 3), 1, {"heightAboveGround": 1.5})
    c = rd("c", d(2026, 11, 3), 1, {"depthFrom": 0, "depthTo": 20})
    e = rd("e", d(2026, 11, 3), 1, {})
    tol = {"heightAboveGround": 0.5}
    g1 = group_by_sub_context([a, b], tol)
    assert len(g1) == 2
    assert len(group_by_sub_context([c, e], tol)) == 2
    g2 = group_by_sub_context([a, b, c, e], tol)
    g3 = group_by_sub_context([e, c, b, a], tol)
    assert len(g2) == 4 and [x[0] for x in g2] == [x[0] for x in g3]


def test_i1_wrap_with_previous_above_counter_max_is_not_a_negative_increment():
    rs = [rd("a", d(2026, 11, 3, 10), 7000.0), rd("a", d(2026, 11, 3, 11), 100.0)]
    out, warns = counter_deltas(rs, 6553.5, 200.0)
    assert all(r.value >= 0 for r in out)
    assert [r.value for r in out] == [100.0] and "counter_reset" in warns[0]


def test_i2_amount_duplicate_source_t_not_double_counted():
    (bk,) = day_buckets()
    rs = [rd("a", bk[0] + timedelta(hours=1), 2.0), rd("a", bk[0] + timedelta(hours=1), 2.0)]
    (p,) = aggregate_tier(rs, [bk], "amount", "median", None, None, "P1D")
    assert p.value == 2.0 and p.sources[0]["n_readings"] == 1


def test_warnings_aggregated_per_type_and_source_with_range():
    (bk,) = day_buckets()
    rs = [rd("S1", bk[0] + timedelta(hours=h), 999.0) for h in range(24)]
    w = []
    aggregate_tier(rs, [bk], "instant", "median", 0, 100, "P1D", warnings=w)
    assert w == ["reading_out_of_range src=S1 x24 (2026-11-03T00:00:00Z..2026-11-03T23:00:00Z)"]


def test_m1_cumulative_exact_hour_t_cur_does_not_add_its_own_hour():
    (bk,) = day_buckets()
    rs = [rd("a", bk[0] + timedelta(hours=1), 0.0), rd("a", bk[0] + timedelta(hours=3), 1.0)]
    (p,) = aggregate_tier(rs, [bk], "cumulative", "median", None, None, "P1D")
    assert p.coverage == pytest.approx(2 / 24)  # hours 1 and 2 of [01:00, 03:00)
