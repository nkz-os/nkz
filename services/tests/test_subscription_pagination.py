"""A reconciler must read the whole listing before deciding a subscription is missing.

Orion-LD returns 20 subscriptions when `limit` is omitted. A service that reads one
page, does not find its own subscriptions and re-creates them adds duplicates on every
cycle; the duplicates push the real ones further out of the window, so the loop never
recovers on its own. Asking for one oversized page is not a fix either — Orion rejects
limit > 1000 with 400.
"""

from unittest.mock import MagicMock, patch

import pytest

from ._subscription_managers import KNOWN, MANAGERS, load, service_id


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = [] if body is None else body
    r.text = ""
    r.raise_for_status.return_value = None
    r.headers = {}
    return r


def test_the_discovery_actually_found_the_known_managers():
    assert KNOWN <= {service_id(p) for p in MANAGERS}


@pytest.mark.parametrize("path", MANAGERS, ids=service_id)
def test_existing_subscription_past_the_first_page_is_not_recreated(path):
    module = load(path)
    page_size = module.ORION_PAGE_SIZE
    filler = [
        {"id": f"urn:ngsi-ld:Subscription:{i}", "description": f"unrelated-{i}"}
        for i in range(page_size)
    ]
    mine = [{"id": "urn:ngsi-ld:Subscription:mine", "description": s["description"]}
            for s in module.SUBSCRIPTIONS]

    with patch.object(
        module.requests, "get", side_effect=[_resp(200, filler), _resp(200, mine)]
    ) as get, patch.object(module.requests, "post", return_value=_resp(201)) as post:
        module._ensure_tenant_subscriptions("montiko")

    assert get.call_count == 2, "stopped reading before the end of the listing"
    assert get.call_args_list[1].kwargs["params"]["offset"] == page_size
    assert post.call_count == 0, "re-created subscriptions that already existed"


@pytest.mark.parametrize("path", MANAGERS, ids=service_id)
def test_a_short_page_ends_the_listing(path):
    """No speculative extra round-trip once a page comes back short."""
    module = load(path)
    with patch.object(module.requests, "get", return_value=_resp(200, [])) as get, patch.object(
        module.requests, "post", return_value=_resp(201)
    ) as post:
        module._ensure_tenant_subscriptions("montiko")

    assert get.call_count == 1
    assert post.call_count == len(module.SUBSCRIPTIONS), "did not create the missing ones"
