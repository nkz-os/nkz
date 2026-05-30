import io
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


def _img_bytes():
    return (io.BytesIO(b"\xff\xd8\xff\xe0fakejpeg"), "shot.jpg")


def test_upload_creates_agriparcelrecord_with_parcel(client, app):
    captured = {}

    def fake_put(**kwargs):
        captured["key"] = kwargs.get("Key")

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        captured["entity"] = json
        captured["post_headers"] = headers
        r = MagicMock()
        r.status_code = 201
        return r

    fake_s3 = MagicMock()
    fake_s3.put_object.side_effect = fake_put
    with (
        patch.object(app, "validate_jwt_token", return_value={"sub": "u1"}),
        patch.object(app, "extract_tenant_id", return_value="acme"),
        patch.object(app, "get_request_token", return_value="tok"),
        patch.object(
            app, "resolve_parcel_for_point", return_value="urn:ngsi-ld:AgriParcel:P9"
        ),
        patch.object(app.boto3, "client", return_value=fake_s3),
        patch.object(app.requests, "post", side_effect=fake_post),
    ):
        buf, name = _img_bytes()
        resp = client.post(
            "/api/field-images/upload",
            data={
                "image": (buf, name),
                "lat": "43.31",
                "lng": "-1.98",
                "accuracy": "4.0",
                "note": "leaf spot",
            },
            content_type="multipart/form-data",
        )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["image_url"].endswith(captured["key"])
    assert "/modules/" not in body["image_url"]
    assert body["image_url"].startswith("/api/field-images/")
    ent = captured["entity"]
    assert ent["type"] == "AgriParcelRecord"
    assert ent["refAgriParcel"]["object"] == "urn:ngsi-ld:AgriParcel:P9"
    assert ent["note"]["value"] == "leaf spot"
    # Orion POST must carry tenant header
    assert captured["post_headers"]["NGSILD-Tenant"] == "acme"
    # NGSI-LD mutual exclusivity: body has @context -> ld+json and NO Link header,
    # otherwise Orion-LD rejects the entity (400) and the observation is lost.
    assert ent["@context"]
    assert captured["post_headers"]["Content-Type"] == "application/ld+json"
    assert "Link" not in captured["post_headers"]
    # MinIO key must NOT be under the public modules/ prefix
    assert captured["key"].startswith("field-images/acme/")


def test_upload_omits_parcel_when_none(client, app):
    fake_s3 = MagicMock()
    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        sent["entity"] = json
        r = MagicMock()
        r.status_code = 201
        return r

    with (
        patch.object(app, "validate_jwt_token", return_value={"sub": "u1"}),
        patch.object(app, "extract_tenant_id", return_value="acme"),
        patch.object(app, "get_request_token", return_value="tok"),
        patch.object(app, "resolve_parcel_for_point", return_value=None),
        patch.object(app.boto3, "client", return_value=fake_s3),
        patch.object(app.requests, "post", side_effect=fake_post),
    ):
        buf, name = _img_bytes()
        resp = client.post(
            "/api/field-images/upload",
            data={"image": (buf, name), "lat": "43.31", "lng": "-1.98"},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 200
    assert "refAgriParcel" not in sent["entity"]


def test_upload_502_when_minio_fails(client, app):
    fake_s3 = MagicMock()
    fake_s3.put_object.side_effect = Exception("denied")
    with (
        patch.object(app, "validate_jwt_token", return_value={"sub": "u1"}),
        patch.object(app, "extract_tenant_id", return_value="acme"),
        patch.object(app, "get_request_token", return_value="tok"),
        patch.object(app.boto3, "client", return_value=fake_s3),
    ):
        buf, name = _img_bytes()
        resp = client.post(
            "/api/field-images/upload",
            data={"image": (buf, name), "lat": "43.31", "lng": "-1.98"},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 502


def test_field_image_read_streams_for_owner(client, app):
    body = b"\xff\xd8jpegdata"
    obj = {"Body": io.BytesIO(body), "ContentType": "image/jpeg"}
    fake_s3 = MagicMock(); fake_s3.get_object.return_value = obj
    with patch.object(app, "validate_jwt_token", return_value={"sub": "u1"}), \
         patch.object(app, "extract_tenant_id", return_value="acme"), \
         patch.object(app, "get_request_token", return_value="tok"), \
         patch.object(app.boto3, "client", return_value=fake_s3):
        resp = client.get("/api/field-images/field-images/acme/20260530T100000_abcd1234.jpg")
    assert resp.status_code == 200
    assert resp.data == body
    assert resp.headers["Content-Type"] == "image/jpeg"


def test_field_image_read_rejects_cross_tenant(client, app):
    with patch.object(app, "validate_jwt_token", return_value={"sub": "u1"}), \
         patch.object(app, "extract_tenant_id", return_value="acme"), \
         patch.object(app, "get_request_token", return_value="tok"):
        resp = client.get("/api/field-images/field-images/other/x.jpg")
    assert resp.status_code == 403


def test_field_image_read_requires_auth(client, app):
    with patch.object(app, "get_request_token", return_value=None):
        resp = client.get("/api/field-images/field-images/acme/x.jpg")
    assert resp.status_code == 401
