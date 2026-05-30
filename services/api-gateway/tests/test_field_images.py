from unittest.mock import MagicMock, patch
import fiware_api_gateway as gw


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else []
    return r


def test_resolve_parcel_intersects_hit():
    inside = [{"id": "urn:ngsi-ld:AgriParcel:P1"}]
    with patch.object(gw.requests, "get", return_value=_resp(200, inside)) as m:
        pid = gw.resolve_parcel_for_point("acme", lng=-1.98, lat=43.31, accuracy=4.0)
    assert pid == "urn:ngsi-ld:AgriParcel:P1"
    # First call must be the intersects query, tenant-scoped.
    called_params = m.call_args.kwargs["params"]
    assert called_params["georel"] == "intersects"
    assert m.call_args.kwargs["headers"]["NGSILD-Tenant"] == "acme"


def test_resolve_parcel_near_fallback_uses_margin():
    # intersects empty -> near returns a parcel
    seq = [_resp(200, []), _resp(200, [{"id": "urn:ngsi-ld:AgriParcel:P2"}])]
    with patch.object(gw.requests, "get", side_effect=seq) as m:
        pid = gw.resolve_parcel_for_point("acme", lng=-1.98, lat=43.31, accuracy=4.0)
    assert pid == "urn:ngsi-ld:AgriParcel:P2"
    near_params = m.call_args_list[1].kwargs["params"]
    # accuracy 4.0 * K(3.0) = 12 < FLOOR 50 -> margin must be 50
    assert near_params["georel"] == "near;maxDistance==50"


def test_resolve_parcel_margin_scales_with_accuracy():
    seq = [_resp(200, []), _resp(200, [])]
    with patch.object(gw.requests, "get", side_effect=seq) as m:
        gw.resolve_parcel_for_point("acme", lng=-1.0, lat=43.0, accuracy=40.0)
    # 40 * 3 = 120 > 50 -> margin 120
    assert m.call_args_list[1].kwargs["params"]["georel"] == "near;maxDistance==120"


def test_resolve_parcel_none_when_no_match():
    with patch.object(gw.requests, "get", return_value=_resp(200, [])):
        assert gw.resolve_parcel_for_point("acme", -1.0, 43.0, None) is None


def test_resolve_parcel_swallows_errors():
    with patch.object(gw.requests, "get", side_effect=Exception("orion down")):
        assert gw.resolve_parcel_for_point("acme", -1.0, 43.0, 5.0) is None
