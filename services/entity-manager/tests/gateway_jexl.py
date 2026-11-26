"""Evaluation of the gateway's IoT Agent mappings with the agent's own JEXL code.

`evaluate` runs `gateway_jexl_eval.js` in the pinned IoT Agent image (docker, no network), which
repeats the agent's per-attribute context handling (see the script). The results are recorded in
`fixtures/gateway_jexl_golden.json` together with the digest of the mappings they were produced
from, so the suite can check them without docker and fails when the mappings change without a
new run. To refresh after a deliberate change (needs docker and the agent image):

    python3 gateway_jexl.py --write
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
GOLDEN = HERE / "fixtures" / "gateway_jexl_golden.json"
IMAGE = "fiware/iotagent-json:3.13.0"
TENANT = "acme"
EXTERNAL_ID = "gw-aaaaaaaaaaaa"
SHAPES = ("legacy", "sdm")

VALUES = {
    "uptime": [("zero", 0), ("typical", 86400), ("max", 999999999999), ("limit", 1e12),
               ("fraction", 3.7), ("integral float", 5.0), ("negative", -5), ("huge", 1e20),
               ("numeric string", "86400"), ("fraction string", "3.5"), ("text", "abc"),
               ("mixed string", "12abc"), ("null", None), ("true", True), ("false", False),
               ("list", [1]), ("object", {"a": 1}), ("empty string", "")],
    "batteryLevel": [("zero", 0), ("half", 0.5), ("one", 1), ("unknown", -1), ("five", 5),
                     ("just above one", 1.0001), ("negative fraction", -0.5), ("huge", 1e20),
                     ("numeric string", "0.5"), ("text", "abc"), ("null", None), ("true", True),
                     ("list", [0.5]), ("object", {}), ("empty string", "")],
    "rssi": [("typical", -71), ("fraction", -71.5), ("zero", 0), ("floor", -200),
             ("below floor", -201), ("positive", 1), ("huge", 1e20), ("numeric string", "-71"),
             ("text", "abc"), ("null", None), ("true", True), ("list", [-71]), ("object", {}),
             ("empty string", "")],
    "softwareVersion": [("typical", "1.2.3"), ("64 chars", "a" * 64), ("65 chars", "a" * 65),
                        ("empty string", ""), ("number", 123), ("numeric string", "123"), ("null", None),
                        ("true", True), ("list", ["1"]), ("object", {}), ("blank", " ")],
}

FULL = {"uptime": 86400, "softwareVersion": "1.2.3", "batteryLevel": 0.82, "rssi": -71}


def messages():
    """(id, message) of every case: each attribute x each value class, then whole messages."""
    out = [(f"{attr}: {label}", {attr: value}) for attr, cases in VALUES.items()
           for label, value in cases]
    out += [
        ("whole message", dict(FULL)),
        ("empty message", {}),
        ("uptime only", {"uptime": 7}),
        ("no uptime", {"batteryLevel": 0.5, "rssi": -60, "softwareVersion": "2"}),
        ("past TimeInstant", {**FULL, "TimeInstant": "2020-01-01T00:00:00Z"}),
        ("future TimeInstant", {**FULL, "TimeInstant": "2099-01-01T00:00:00Z"}),
        # The live agent drops this whole message (an unparseable TimeInstant fails the agent before
        # any mapping runs); the harness evaluates attribute by attribute, so its record here shows
        # per-attribute writes. The mappings never read TimeInstant either way.
        ("garbage TimeInstant", {**FULL, "TimeInstant": "garbage"}),
        ("bad uptime, good rest", {**FULL, "uptime": "abc"}),
        ("extra keys", {**FULL, "foo": 1, "location": [1, 2], "deviceState": "x"}),
        ("keys named like DM attributes", {
            "uptime": 5, "numValue": 9, "name": "x", "type": "y", "id": "z", "refDevice": "urn:x",
            "controlledProperty": "p", "measurementType": "m", "dateObserved": "2001-01-01T00:00:00Z",
            "timeSource": "device", "dateLastValueReported": "2001-01-01T00:00:00Z"}),
    ]
    return out


def mappings_digest(mappings):
    return hashlib.sha256(json.dumps(mappings).encode()).hexdigest()


def evaluate(mappings, msgs):
    """Per message, {entity: {attribute: {value, type, metadata?}}} as the agent would write it."""
    run = subprocess.run(
        ["docker", "run", "--rm", "-i", "--network", "none", "--entrypoint", "node",
         "-v", f"{HERE}:/h:ro", IMAGE, "/h/gateway_jexl_eval.js"],
        input=json.dumps({"mappings": mappings, "measures": msgs}),
        capture_output=True, text=True, check=True)
    lines = [ln for ln in run.stdout.splitlines() if ln.startswith("RESULT:")]
    return json.loads(lines[-1][len("RESULT:"):])


def build_golden(build_iota_attributes):
    msgs = messages()
    golden = {"agent_image": IMAGE, "tenant": TENANT, "external_id": EXTERNAL_ID, "shapes": {}}
    for shape in SHAPES:
        mappings = build_iota_attributes(TENANT, EXTERNAL_ID, shape)
        results = evaluate(mappings, [m for _, m in msgs])
        golden["shapes"][shape] = {
            "mappings_sha256": mappings_digest(mappings),
            "cases": [{"id": i, "message": m, "written": r}
                      for (i, m), r in zip(msgs, results)],
        }
    return golden


def main():
    sys.path.insert(0, str(HERE.parents[1]))
    sys.path.insert(0, str(HERE.parent))
    import gateways  # pure: needs only common.device_measurement_shape and common.unit_codes
    golden = build_golden(gateways.build_iota_attributes)
    if "--write" in sys.argv:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(golden, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {GOLDEN}")
    elif os.path.exists(GOLDEN):
        same = json.loads(GOLDEN.read_text(encoding="utf-8")) == json.loads(json.dumps(golden))
        print("golden matches a live run" if same else "golden DIFFERS from a live run")
        sys.exit(0 if same else 1)


if __name__ == "__main__":
    main()
