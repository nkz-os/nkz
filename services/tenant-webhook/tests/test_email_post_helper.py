"""All email-service calls go through _email_post so the internal secret
header can never be forgotten at a call site."""
import re
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_no_direct_email_service_posts(webhook_module):
    src = Path(webhook_module.__file__).read_text()
    assert re.search(r'requests\.post\(\s*f"\{(EMAIL_SERVICE_URL|email_service_url)\}', src) is None
    assert "def _email_post(" in src


def test_email_post_sends_internal_secret(webhook_module, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "s")
    with patch.object(webhook_module.requests, "post", return_value=MagicMock(status_code=200)) as m:
        webhook_module._email_post("/send/welcome", {"email": "a@b.c"}, timeout=7)
    assert m.call_args.args[0].endswith("/send/welcome")
    assert m.call_args.kwargs["headers"]["X-Internal-Service-Secret"] == "s"
    assert m.call_args.kwargs["json"] == {"email": "a@b.c"}
    assert m.call_args.kwargs["timeout"] == 7
