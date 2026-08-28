"""SyncOrionClient must offer the same NGSI-LD surface as the async client.

The gap was real debt: sync-only callers (Flask services, CLI scripts, cron jobs)
had no batch, no attribute-append and no subscription-create, so they hand-rolled
raw requests calls against Orion — the pattern the SDK exists to eliminate.

SyncOrionClient wraps requests.Session (not httpx), so respx cannot intercept it;
these tests patch the session verbs and assert the wire contract, mirroring
test_orion_options_sync.py.
"""

from unittest.mock import MagicMock, patch

import pytest

from nkz_platform_sdk.orion import OrionClient, SyncOrionClient

ORION = "http://orion-ld-service:1026"
CTX = "http://api-gateway-service:5000/ngsi-ld-context.json"

ASYNC_ONLY_BEFORE = [
    "create_entities_batch",
    "upsert_entities_batch",
    "update_entity_attrs",
    "append_entity_attrs",
    "create_subscription",
    "get_subscription",
    "query_all_subscriptions",
]


def _resp(status=201, json_body=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.content = b"{}"
    r.text = ""
    r.headers = headers or {}
    r.json.return_value = json_body if json_body is not None else {}
    r.raise_for_status.return_value = None
    return r


@pytest.mark.parametrize("name", ASYNC_ONLY_BEFORE)
def test_sync_client_has_parity_method(name):
    assert hasattr(SyncOrionClient, name), f"SyncOrionClient is missing {name}()"


@pytest.mark.parametrize("name", ASYNC_ONLY_BEFORE)
def test_async_client_has_the_same_method(name):
    assert hasattr(OrionClient, name), f"OrionClient is missing {name}()"


class TestBatch:
    def test_create_batch_posts_to_entity_operations(self):
        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        ents = [{"id": "urn:ngsi-ld:X:1", "type": "X"}]
        with patch.object(c._session, "post", return_value=_resp(201)) as m:
            out = c.create_entities_batch(ents)
        assert m.call_args[0][0].endswith("/ngsi-ld/v1/entityOperations/create")
        assert out == {"created": 1, "errors": [], "entity_ids": ["urn:ngsi-ld:X:1"]}

    def test_create_batch_injects_context(self):
        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        with patch.object(c._session, "post", return_value=_resp(201)) as m:
            c.create_entities_batch([{"id": "urn:ngsi-ld:X:1", "type": "X"}])
        sent = m.call_args.kwargs["json"][0]
        assert sent["@context"] == [CTX]
        assert m.call_args.kwargs["headers"]["Content-Type"] == "application/ld+json"

    def test_upsert_batch_uses_options_update(self):
        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        with patch.object(c._session, "post", return_value=_resp(204)) as m:
            out = c.upsert_entities_batch([{"id": "urn:ngsi-ld:X:1", "type": "X"}])
        assert m.call_args[0][0].endswith("/ngsi-ld/v1/entityOperations/upsert")
        assert m.call_args.kwargs["params"] == {"options": "update"}
        assert out["upserted"] == 1

    def test_batch_207_reports_partial_success(self):
        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        body = {"success": ["urn:ngsi-ld:X:1"], "errors": [{"entityId": "urn:ngsi-ld:X:2"}]}
        with patch.object(c._session, "post", return_value=_resp(207, body)):
            out = c.create_entities_batch(
                [{"id": "urn:ngsi-ld:X:1", "type": "X"}, {"id": "urn:ngsi-ld:X:2", "type": "X"}]
            )
        assert out["created"] == 1
        assert len(out["errors"]) == 1

    def test_empty_batch_is_a_noop(self):
        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        with patch.object(c._session, "post") as m:
            assert c.create_entities_batch([])["created"] == 0
        m.assert_not_called()


class TestAttributeFragments:
    def test_update_attrs_uses_json_plus_link(self):
        """Fragments carry no @context: ld+json without one is an Orion 400."""
        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        with patch.object(c._session, "patch", return_value=_resp(204)) as m:
            c.update_entity_attrs("urn:ngsi-ld:X:1", {"a": {"type": "Property", "value": 1}})
        h = m.call_args.kwargs["headers"]
        assert h["Content-Type"] == "application/json"
        assert CTX in h["Link"]

    def test_append_attrs_posts_to_attrs(self):
        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        with patch.object(c._session, "post", return_value=_resp(204)) as m:
            c.append_entity_attrs("urn:ngsi-ld:X:1", {"a": {"type": "Property", "value": 1}})
        assert m.call_args[0][0].endswith("/ngsi-ld/v1/entities/urn:ngsi-ld:X:1/attrs")
        assert m.call_args.kwargs["params"] == {}

    def test_append_attrs_no_overwrite(self):
        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        with patch.object(c._session, "post", return_value=_resp(204)) as m:
            c.append_entity_attrs("urn:ngsi-ld:X:1", {"a": 1}, overwrite=False)
        assert m.call_args.kwargs["params"] == {"options": "noOverwrite"}

    def test_append_attrs_raises_on_partial_207(self):
        """207 is below 400: raise_for_status would pass and silently drop attrs."""
        import requests

        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        with patch.object(c._session, "post", return_value=_resp(207)):
            with pytest.raises(requests.HTTPError):
                c.append_entity_attrs("urn:ngsi-ld:X:1", {"a": 1})


class TestSubscriptions:
    def test_create_subscription_hits_subscriptions_endpoint(self):
        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        loc = "/ngsi-ld/v1/subscriptions/urn:ngsi-ld:Subscription:s1"
        with patch.object(c._session, "post", return_value=_resp(201, headers={"Location": loc})) as m:
            out = c.create_subscription({"id": "urn:ngsi-ld:Subscription:s1", "type": "Subscription"})
        assert m.call_args[0][0].endswith("/ngsi-ld/v1/subscriptions")
        assert out == loc

    def test_get_subscription_by_id(self):
        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        sid = "urn:ngsi-ld:Subscription:s1"
        with patch.object(c._session, "get", return_value=_resp(200, {"id": sid})) as m:
            out = c.get_subscription(sid)
        assert m.call_args[0][0].endswith(f"/ngsi-ld/v1/subscriptions/{sid}")
        assert out["id"] == sid


class TestPaginatedSubscriptionListing:
    """The sync client must follow the listing too — Flask services and cron
    jobs are exactly the callers that reconcile subscriptions on a timer."""

    def test_query_all_follows_pagination(self):
        from nkz_platform_sdk.orion import ORION_PAGE_SIZE

        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        page1 = [{"id": f"urn:ngsi-ld:Subscription:{i}"} for i in range(ORION_PAGE_SIZE)]
        page2 = [{"id": "urn:ngsi-ld:Subscription:last"}]
        with patch.object(
            c._session,
            "get",
            side_effect=[_resp(200, page1), _resp(200, page2)],
        ) as m:
            subs = c.query_all_subscriptions()
        assert len(subs) == ORION_PAGE_SIZE + 1
        assert m.call_count == 2
        assert m.call_args_list[0].kwargs["params"]["offset"] == 0
        assert m.call_args_list[1].kwargs["params"]["offset"] == ORION_PAGE_SIZE

    def test_query_all_stops_on_a_short_page(self):
        c = SyncOrionClient("montiko", base_url=ORION, context_url=CTX)
        with patch.object(
            c._session, "get", return_value=_resp(200, [{"id": "urn:x"}])
        ) as m:
            subs = c.query_all_subscriptions()
        assert len(subs) == 1
        assert m.call_count == 1
