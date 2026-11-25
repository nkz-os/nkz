"""Environment read while entity_management_api is imported, whichever test file imports it first."""
import os

os.environ.setdefault("CORS_ORIGINS", "https://app.invalid")

# Several files replace the `common` package in sys.modules. Loaded first, this
# constants module stays importable under the stub.
import common.active_tenants  # noqa: E402,F401
