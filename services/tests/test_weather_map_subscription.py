import os

SRC = os.path.join(os.path.dirname(__file__), "..", "telemetry-worker",
                   "telemetry_worker", "subscription_manager.py")


def test_agriparcelrecord_subscription_is_filtered():
    src = open(SRC).read()
    assert '"type": "AgriParcelRecord"' in src
    # must be filtered by a weather scalar so it does NOT forward photo records
    assert '"watchedAttributes"' in src
    assert '"eto"' in src or '"solarRadiation"' in src
