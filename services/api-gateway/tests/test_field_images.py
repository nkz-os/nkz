import io

import requests as app_requests
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
    assert ent["hasAgriParcel"]["object"] == "urn:ngsi-ld:AgriParcel:P9"
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
    assert "hasAgriParcel" not in sent["entity"]


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
    fake_s3 = MagicMock()
    fake_s3.get_object.return_value = obj
    with (
        patch.object(app, "validate_jwt_token", return_value={"sub": "u1"}),
        patch.object(app, "extract_tenant_id", return_value="acme"),
        patch.object(app, "get_request_token", return_value="tok"),
        patch.object(app.boto3, "client", return_value=fake_s3),
    ):
        resp = client.get(
            "/api/field-images/field-images/acme/20260530T100000_abcd1234.jpg"
        )
    assert resp.status_code == 200
    assert resp.data == body
    assert resp.headers["Content-Type"] == "image/jpeg"


def test_field_image_read_rejects_cross_tenant(client, app):
    with (
        patch.object(app, "validate_jwt_token", return_value={"sub": "u1"}),
        patch.object(app, "extract_tenant_id", return_value="acme"),
        patch.object(app, "get_request_token", return_value="tok"),
    ):
        resp = client.get("/api/field-images/field-images/other/x.jpg")
    assert resp.status_code == 403


def test_field_image_read_requires_auth(client, app):
    with patch.object(app, "get_request_token", return_value=None):
        resp = client.get("/api/field-images/field-images/acme/x.jpg")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Field notebook v2: tag, idempotency, explicit parcel, visible failure
# ---------------------------------------------------------------------------
import re
from botocore.exceptions import ClientError

CID = "3F2504E0-4F89-41D3-9A0C-0305E82C3301"
CID_L = CID.lower()
ENT_ID = f"urn:ngsi-ld:AgriParcelRecord:acme:{CID_L}"
PARCEL = "urn:ngsi-ld:AgriParcel:p1"


def _not_found():
    return ClientError({"Error": {"Code": "404", "Message": "nf"}}, "HeadObject")


def _upload(client, app, data, *, get=None, post=None, s3=None, resolve=None):
    """POST the form with auth/MinIO/Orion mocked; returns (resp, s3, get_m, post_m, resolve_m)."""
    fake_s3 = s3 or MagicMock()
    if s3 is None:
        fake_s3.head_object.side_effect = _not_found()
    get_m = MagicMock(side_effect=get or (lambda url, **kw: _resp(404)))
    post_m = MagicMock(side_effect=post or (lambda url, **kw: _resp(201)))
    resolve_m = MagicMock(return_value=resolve)
    with (
        patch.object(app, "validate_jwt_token", return_value={"sub": "u1"}),
        patch.object(app, "extract_tenant_id", return_value="acme"),
        patch.object(app, "get_request_token", return_value="tok"),
        patch.object(app, "resolve_parcel_for_point", resolve_m),
        patch.object(app.boto3, "client", return_value=fake_s3),
        patch.object(app.requests, "get", get_m),
        patch.object(app.requests, "post", post_m),
    ):
        form = {"image": _img_bytes(), "lat": "43.31", "lng": "-1.98"}
        form.update(data)
        resp = client.post(
            "/api/field-images/upload", data=form, content_type="multipart/form-data"
        )
    return resp, fake_s3, get_m, post_m, resolve_m


def test_upload_with_tag_writes_category(client, app):
    resp, _, _, post_m, _ = _upload(client, app, {"tag": "pest"})
    assert resp.status_code == 200
    assert post_m.call_args.kwargs["json"]["category"] == {
        "type": "Property",
        "value": "pest",
    }


def test_upload_invalid_tag_400(client, app):
    resp, s3, *_ = _upload(client, app, {"tag": "foo"})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "invalid_tag"}
    s3.put_object.assert_not_called()


def test_upload_client_id_derives_ids(client, app):
    resp, s3, _, post_m, _ = _upload(client, app, {"client_id": CID})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["entity_id"] == ENT_ID
    assert body["duplicate"] is False
    key = f"field-images/acme/{CID_L}.jpg"
    assert s3.put_object.call_args.kwargs["Key"] == key
    assert body["image_url"] == f"/api/field-images/{key}"
    url = post_m.call_args.args[0]
    assert url.endswith("/ngsi-ld/v1/entityOperations/upsert?options=update")
    sent = post_m.call_args.kwargs["json"]
    assert isinstance(sent, list) and len(sent) == 1 and sent[0]["id"] == ENT_ID


def test_upload_duplicate_returns_existing(client, app):
    existing = {
        "id": ENT_ID,
        "imageUrl": {"type": "Property", "value": f"/api/field-images/field-images/acme/{CID_L}.jpg"},
        "hasAgriParcel": {"type": "Relationship", "object": PARCEL},
    }
    resp, s3, get_m, post_m, _ = _upload(
        client,
        app,
        {"client_id": CID},
        s3=MagicMock(),  # head_object ok: object present
        get=lambda url, **kw: _resp(200, existing),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["duplicate"] is True
    assert body["entity_id"] == ENT_ID
    assert body["parcel_id"] == PARCEL
    assert body["image_url"] == existing["imageUrl"]["value"]
    assert get_m.call_args.args[0].endswith(f"/ngsi-ld/v1/entities/{ENT_ID}")
    s3.put_object.assert_not_called()
    post_m.assert_not_called()


def test_upload_partial_retry_rewrites(client, app):
    s3 = MagicMock()  # head_object succeeds: file left by a failed attempt
    resp, s3, _, post_m, _ = _upload(client, app, {"client_id": CID}, s3=s3)
    assert resp.status_code == 200
    assert resp.get_json()["duplicate"] is False
    s3.put_object.assert_called_once()
    post_m.assert_called_once()


def test_upload_invalid_client_id_400(client, app):
    resp, s3, *_ = _upload(client, app, {"client_id": "not-a-uuid"})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "invalid_client_id"}
    s3.put_object.assert_not_called()


def test_upload_parcel_id_wins(client, app):
    def get(url, **kw):
        return _resp(200, {"id": PARCEL}) if url.endswith(PARCEL) else _resp(404)

    resp, _, get_m, post_m, resolve_m = _upload(
        client, app, {"parcel_id": PARCEL}, get=get
    )
    assert resp.status_code == 200
    resolve_m.assert_not_called()
    assert get_m.call_args.kwargs["headers"]["NGSILD-Tenant"] == "acme"
    assert "Link" in get_m.call_args.kwargs["headers"]
    ent = post_m.call_args.kwargs["json"]
    assert ent["hasAgriParcel"]["object"] == PARCEL
    assert resp.get_json()["parcel_id"] == PARCEL


def test_upload_parcel_not_found_404(client, app):
    resp, s3, *_ = _upload(client, app, {"parcel_id": PARCEL})
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "parcel_not_found"}
    s3.put_object.assert_not_called()


def test_upload_parcel_id_bad_shape_400(client, app):
    resp, s3, get_m, *_ = _upload(client, app, {"parcel_id": "urn:ngsi-ld:Other:x"})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "invalid_parcel_id"}
    get_m.assert_not_called()
    s3.put_object.assert_not_called()


def test_upload_orion_failure_502_deletes_new_object(client, app):
    resp, s3, *_ = _upload(
        client, app, {"client_id": CID}, post=lambda url, **kw: _resp(500)
    )
    assert resp.status_code == 502
    assert resp.get_json() == {"error": "record_not_saved"}
    s3.delete_object.assert_called_once_with(
        Bucket="nekazari-frontend", Key=f"field-images/acme/{CID_L}.jpg"
    )


def test_upload_orion_failure_keeps_preexisting_object(client, app):
    s3 = MagicMock()  # head_object ok -> object pre-existed
    resp, s3, *_ = _upload(
        client, app, {"client_id": CID}, s3=s3, post=lambda url, **kw: _resp(500)
    )
    assert resp.status_code == 502
    s3.delete_object.assert_not_called()


def test_upload_orion_207_is_failure(client, app):
    resp, *_ = _upload(
        client, app, {"client_id": CID}, post=lambda url, **kw: _resp(207)
    )
    assert resp.status_code == 502


def test_upload_captured_at_normalised(client, app):
    resp, _, _, post_m, _ = _upload(
        client, app, {"captured_at": "2026-11-04T10:00:00+02:00"}
    )
    assert resp.status_code == 200
    assert post_m.call_args.kwargs["json"]["dateObserved"]["value"] == "2026-11-04T08:00:00Z"
    resp, _, _, post_m, _ = _upload(client, app, {"captured_at": "2026-11-04T10:00:00Z"})
    assert post_m.call_args.kwargs["json"]["dateObserved"]["value"] == "2026-11-04T10:00:00Z"
    resp, _, _, post_m, _ = _upload(client, app, {"captured_at": "2026-11-04T10:00:00"})
    assert post_m.call_args.kwargs["json"]["dateObserved"]["value"] == "2026-11-04T10:00:00Z"


def test_upload_invalid_captured_at_400(client, app):
    resp, s3, *_ = _upload(client, app, {"captured_at": "ayer"})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "invalid_captured_at"}
    s3.put_object.assert_not_called()


def test_upload_legacy_without_client_id_unchanged(client, app):
    resp, s3, get_m, post_m, resolve_m = _upload(client, app, {})
    assert resp.status_code == 200
    body = resp.get_json()
    assert re.fullmatch(r"urn:ngsi-ld:AgriParcelRecord:photo-\d{8}T\d{6}-[0-9a-f]{8}", body["entity_id"])
    assert re.fullmatch(r"field-images/acme/\d{8}T\d{6}_[0-9a-f]{8}\.jpg", s3.put_object.call_args.kwargs["Key"])
    assert post_m.call_args.args[0].endswith("/ngsi-ld/v1/entities")
    assert body["duplicate"] is False
    get_m.assert_not_called()
    resolve_m.assert_called_once()


def test_upload_legacy_orion_failure_502(client, app):
    resp, s3, *_ = _upload(client, app, {}, post=lambda url, **kw: _resp(500))
    assert resp.status_code == 502
    assert resp.get_json() == {"error": "record_not_saved"}
    s3.delete_object.assert_called_once()


def test_upload_non_v4_uuid_rejected(client, app):
    resp, s3, *_ = _upload(client, app, {"client_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301"})
    assert resp.status_code == 400
    s3.put_object.assert_not_called()


def test_upload_duplicate_with_missing_object_reuploads(client, app):
    existing = {
        "id": ENT_ID,
        "imageUrl": {"type": "Property", "value": f"/api/field-images/field-images/acme/{CID_L}.jpg"},
        "hasAgriParcel": {"type": "Relationship", "object": PARCEL},
    }
    s3 = MagicMock()
    s3.head_object.side_effect = _not_found()
    resp, s3, _, post_m, _ = _upload(
        client,
        app,
        {"client_id": CID},
        s3=s3,
        get=lambda url, **kw: _resp(200, existing),
    )
    assert resp.status_code == 200
    assert resp.get_json()["duplicate"] is False
    s3.put_object.assert_called_once()
    assert s3.put_object.call_args.kwargs["Key"] == f"field-images/acme/{CID_L}.jpg"
    post_m.assert_called_once()
    assert post_m.call_args.args[0].endswith(
        "/ngsi-ld/v1/entityOperations/upsert?options=update"
    )


def _raise_timeout(url, **kw):
    raise app_requests.Timeout("orion timeout")



def test_upload_broker_timeout_keeps_object_client_id(client, app):
    resp, s3, *_ = _upload(client, app, {"client_id": CID}, post=_raise_timeout)
    assert resp.status_code == 502
    assert resp.get_json() == {"error": "record_not_saved"}
    s3.put_object.assert_called_once()
    s3.delete_object.assert_not_called()


def test_upload_broker_timeout_keeps_object_legacy(client, app):
    resp, s3, *_ = _upload(client, app, {}, post=_raise_timeout)
    assert resp.status_code == 502
    assert resp.get_json() == {"error": "record_not_saved"}
    s3.put_object.assert_called_once()
    s3.delete_object.assert_not_called()
