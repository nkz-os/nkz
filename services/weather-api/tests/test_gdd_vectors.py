"""daily_gdd is pinned to the vectors shared with the other GDD implementations."""

import json
import pathlib
import sys

import pytest

_SVC_DIR = str(pathlib.Path(__file__).resolve().parents[1])
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from app.services.daily_totals import daily_gdd

_DATA = json.loads(
    (pathlib.Path(__file__).parent / "data" / "gdd_vectors.json").read_text()
)
_CASES = _DATA["cases"]


@pytest.mark.parametrize("method", ["legacy", "m2", "aquacrop_m3"])
@pytest.mark.parametrize("case", _CASES)
def test_daily_gdd_matches_shared_vectors(case, method):
    got = daily_gdd(case["tmin"], case["tmax"], case["base"], case["upper"], method)
    assert got == pytest.approx(case[method], abs=1e-9), case
