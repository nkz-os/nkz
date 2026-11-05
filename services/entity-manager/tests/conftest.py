"""Environment read while entity_management_api is imported, whichever test file imports it first."""
import os

os.environ.setdefault("CORS_ORIGINS", "https://app.invalid")
