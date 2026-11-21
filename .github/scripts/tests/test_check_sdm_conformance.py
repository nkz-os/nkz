"""Smart Data Models conformance checker. No network: pinned files are served from memory."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_sdm_conformance as checker  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
OFFICIAL = "https://smartdatamodels.org/dataModel.Demo/"
SHARED_URL = "https://smart-data-models.github.io/data-models/common-schema.json"
CORE_URL = "https://example.org/core-context.jsonld"

SCHEMA = {
    "$schema": "http://json-schema.org/schema#",
    "type": "object",
    "allOf": [
        {"$ref": SHARED_URL + "#/definitions/Common"},
        {
            "properties": {
                "type": {"type": "string", "enum": ["DemoThing"]},
                "status": {"type": "string", "enum": ["ok", "broken"]},
                "dateIssued": {"type": "string", "format": "date-time"},
                "level": {"type": "number"},
                "refOther": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "object", "required": ["name"]}},
            }
        },
    ],
    "required": ["id", "type", "status"],
}
SHARED = {"definitions": {"Common": {"type": "object", "properties": {"id": {"type": "string"}}}}}
SUBJECT_CONTEXT = {"@context": {"DemoThing": OFFICIAL + "DemoThing", "status": OFFICIAL + "status"}}
CORE_CONTEXT = {"@context": {"location": "https://uri.etsi.org/ngsi-ld/location", "observedAt": "x:observedAt"}}
PLATFORM_CONTEXT = {
    "@context": {
        "nkz": "https://nkz-os.org/ns/",
        "DemoThing": {"@id": OFFICIAL + "DemoThing", "@type": "@id"},
        "OwnThing": "nkz:OwnThing",
        "status": OFFICIAL + "status",
        "dateIssued": OFFICIAL + "dateIssued",
        "level": "nkz:level",
        "refOther": "nkz:refOther",
        "tags": "nkz:tags",
        "min": "nkz:min",
    }
}


def digest(document):
    return hashlib.sha256(json.dumps(document).encode()).hexdigest()


def make_pins(schema=SCHEMA, shared=SHARED):
    pins = {
        "subjects": {"Demo": {"repo": "org/dataModel.Demo", "commit": "a" * 40,
                               "context": {"path": "context.jsonld", "sha256": digest(SUBJECT_CONTEXT)}}},
        "types": {"DemoThing": {"subject": "Demo", "schema": {"path": "DemoThing/schema.json", "sha256": digest(schema)}}},
        "shared": {SHARED_URL: {"repo": "org/data-models", "commit": "b" * 40, "path": "common-schema.json",
                                "sha256": digest(shared)}},
        "core_context": {"url": CORE_URL, "sha256": digest(CORE_CONTEXT)},
    }
    served = {
        "https://raw.githubusercontent.com/org/dataModel.Demo/" + "a" * 40 + "/context.jsonld": SUBJECT_CONTEXT,
        "https://raw.githubusercontent.com/org/dataModel.Demo/" + "a" * 40 + "/DemoThing/schema.json": schema,
        "https://raw.githubusercontent.com/org/data-models/" + "b" * 40 + "/common-schema.json": shared,
        CORE_URL: CORE_CONTEXT,
    }
    return pins, served


def downloader(served, calls=None):
    def download(url):
        if calls is not None:
            calls.append(url)
        return json.dumps(served[url]).encode()

    return download


@pytest.fixture
def validator(tmp_path):
    pins, served = make_pins()
    return checker.Validator(pins, checker.fetch(pins, tmp_path, downloader(served)), PLATFORM_CONTEXT)


def prop(value, **members):
    return {"type": "Property", "value": value, **members}


def thing(**attrs):
    base = {"id": "urn:ngsi-ld:DemoThing:a", "type": "DemoThing", "status": prop("ok")}
    base.update(attrs)
    return {key: value for key, value in base.items() if value is not None}


def kinds(findings):
    return sorted({finding.kind for finding in findings})


# ── pinned files ─────────────────────────────────────────────────────────────


def test_fetch_downloads_once_and_then_reads_the_cache(tmp_path):
    pins, served = make_pins()
    calls = []
    first = checker.fetch(pins, tmp_path, downloader(served, calls))
    assert len(calls) == 4 and first["schema:DemoThing"] == SCHEMA and first[SHARED_URL] == SHARED
    again = checker.fetch(pins, tmp_path, downloader(served, calls))
    assert len(calls) == 4 and again == first


def test_fetch_refuses_a_download_that_does_not_match_its_checksum(tmp_path):
    pins, served = make_pins()
    served = {url: ({"changed": True} if url.endswith("schema.json") and "DemoThing" in url else doc)
              for url, doc in served.items()}
    with pytest.raises(checker.PinError, match="checksum"):
        checker.fetch(pins, tmp_path, downloader(served))
    assert not any(path.read_bytes() == json.dumps({"changed": True}).encode() for path in tmp_path.iterdir())


def test_fetch_downloads_again_when_the_cached_file_was_altered(tmp_path):
    pins, served = make_pins()
    checker.fetch(pins, tmp_path, downloader(served))
    (tmp_path / pins["types"]["DemoThing"]["schema"]["sha256"]).write_text("{}")
    calls = []
    files = checker.fetch(pins, tmp_path, downloader(served, calls))
    assert len(calls) == 1 and files["schema:DemoThing"] == SCHEMA


def test_fetch_reports_a_failed_download(tmp_path):
    pins, _ = make_pins()

    def broken(url):
        raise OSError("no network")

    with pytest.raises(checker.PinError, match="could not fetch"):
        checker.fetch(pins, tmp_path, broken)


def test_download_urls_carry_the_pinned_commit():
    pins, _ = make_pins()
    files = checker.pinned_files(pins)
    assert files["schema:DemoThing"][0].endswith("/" + "a" * 40 + "/DemoThing/schema.json")
    assert files[SHARED_URL][0].endswith("/" + "b" * 40 + "/common-schema.json")


# ── entities ─────────────────────────────────────────────────────────────────


def test_a_conforming_entity_has_no_findings(validator):
    assert validator.check_entity(thing(dateIssued=prop("2026-05-01T10:00:00Z"), level=prop(3))) == []


def test_missing_required_attribute(validator):
    findings = validator.check_entity(thing(status=None))
    assert kinds(findings) == ["schema"] and "required" in findings[0].message


def test_value_outside_the_enumeration(validator):
    findings = validator.check_entity(thing(status=prop("fine")))
    assert kinds(findings) == ["schema"] and findings[0].where == "status" and "enum" in findings[0].message


@pytest.mark.parametrize("bad", [
    "2026-05-01", "yesterday", "2026-13-01T00:00:00Z", "2026-10-07T00:00:00", "2026-10-07T00:00:00+0100",
    "2026-10-07T00:00Z", "20261007T000000Z", "2026-10-07 00:00:00Z", "2026-10-07T00:00:00 +01:00",
    "2026-10-07T24:00:00Z", "2026-02-30T10:00:00Z", "2026-10-07T23:60:00Z", "2026-10-07T23:59:61Z",
    "2026-10-07T10:00:00+24:00", "2026-10-07T10:00:00+01:60", "2026-00-10T10:00:00Z",
])
def test_date_time_format_is_enforced(validator, bad):
    findings = validator.check_entity(thing(dateIssued=prop(bad)))
    assert kinds(findings) == ["schema"] and findings[0].where == "dateIssued" and "format" in findings[0].message


@pytest.mark.parametrize("good", [
    "2026-10-07T00:00:00Z", "2026-10-07t00:00:00z", "2026-10-07T00:00:00.123Z", "2026-10-07T00:00:00+01:00",
    "2026-12-31T23:59:60Z",
])
def test_rfc3339_date_times_are_accepted(validator, good):
    assert validator.check_entity(thing(dateIssued=prop(good))) == []


@pytest.mark.parametrize("value,ok", [("2026-10-07", True), ("20261007", False), ("2026-W41-3", False),
                                       ("2026-02-30", False)])
def test_date_format_is_enforced(tmp_path, value, ok):
    schema = json.loads(json.dumps(SCHEMA))
    schema["allOf"][1]["properties"]["dateIssued"]["format"] = "date"
    pins, served = make_pins(schema=schema)
    validator = checker.Validator(pins, checker.fetch(pins, tmp_path, downloader(served)), PLATFORM_CONTEXT)
    assert (validator.check_entity(thing(dateIssued=prop(value))) == []) is ok


@pytest.mark.parametrize("bad", [
    5, "text", {"type": "Point", "coordinates": [0, 0]}, {"type": "Relationship", "value": "urn:x"},
    {"type": "Property", "object": "urn:x"}, {"type": ["Property"], "value": 1}, {"value": 1}, None,
])
def test_an_attribute_that_is_not_normalized_is_reported(validator, bad):
    entity = thing()
    entity["level"] = bad
    findings = validator.check_entity(entity)
    assert ("not-normalized", "level") in [(f.kind, f.where) for f in findings]


def test_one_bad_instance_among_several_is_reported(validator):
    findings = validator.check_entity(thing(level=[prop(1), 2]))
    assert [(f.kind, f.where) for f in findings if f.kind == "not-normalized"] == [("not-normalized", "level")]


def test_typed_literal_is_read_as_its_value(validator):
    stamp = {"@type": "DateTime", "@value": "2026-05-01T10:00:00Z"}
    assert validator.check_entity(thing(dateIssued=prop(stamp))) == []


def test_relationship_is_validated_by_its_object(validator):
    entity = thing(refOther={"type": "Relationship", "object": "urn:ngsi-ld:Other:1"})
    assert validator.check_entity(entity) == []
    assert checker.key_values(entity)["refOther"] == "urn:ngsi-ld:Other:1"


def test_a_one_element_list_stored_as_an_object_breaks_an_array_schema(validator):
    findings = validator.check_entity(thing(tags=prop({"name": "a"})))
    assert kinds(findings) == ["schema"] and findings[0].where == "tags"
    assert validator.check_entity(thing(tags=prop([{"name": "a"}, {"name": "b"}]))) == []


def test_attribute_outside_the_context(validator):
    findings = validator.check_entity(thing(colour=prop("red")))
    assert [(f.kind, f.where) for f in findings] == [("attribute-outside-context", "colour")]


def test_core_terms_and_declared_terms_are_accepted(validator):
    entity = thing(location={"type": "GeoProperty", "value": {"type": "Point", "coordinates": [0, 0]}},
                   level=prop(3, observedAt="2026-05-01T10:00:00Z", unitCode="CEL", min=prop(1)))
    assert validator.check_entity(entity) == []


def test_property_of_a_property_outside_the_context(validator):
    findings = validator.check_entity(thing(level=prop(3, max=prop(9))))
    assert [(f.kind, f.where) for f in findings] == [("subattribute-outside-context", "level.max")]


def test_a_prefixed_name_is_not_a_declared_term(validator):
    findings = validator.check_entity(thing(**{"nkz:other": prop(1)}))
    assert [(f.kind, f.where) for f in findings] == [("attribute-outside-context", "nkz:other")]


def test_type_mapped_to_another_iri(tmp_path):
    pins, served = make_pins()
    context = json.loads(json.dumps(PLATFORM_CONTEXT))
    context["@context"]["DemoThing"] = "https://example.org/other/DemoThing"
    validator = checker.Validator(pins, checker.fetch(pins, tmp_path, downloader(served)), context)
    findings = validator.check_entity(thing())
    assert kinds(findings) == ["type-not-official-iri"] and "official is " + OFFICIAL + "DemoThing" in findings[0].message


def test_own_type_gets_the_context_checks_only(validator):
    entity = {"id": "urn:ngsi-ld:OwnThing:a", "type": "OwnThing", "level": prop(1), "colour": prop("red")}
    assert [(f.kind, f.where) for f in validator.check_entity(entity)] == [("attribute-outside-context", "colour")]


def test_type_missing_from_the_context(validator):
    findings = validator.check_entity({"id": "urn:ngsi-ld:Nope:a", "type": "Nope"})
    assert [(f.kind, f.where) for f in findings] == [("attribute-outside-context", "type")]


@pytest.mark.parametrize("bad_type", [OFFICIAL + "DemoThing", ["DemoThing"], None])
def test_type_must_be_one_short_name(validator, bad_type):
    findings = validator.check_entity({"id": "urn:ngsi-ld:DemoThing:a", "type": bad_type, "status": prop("ok")})
    assert kinds(findings) == ["schema"] and findings[0].where == "type"


def test_a_reference_outside_the_pinned_set_is_never_resolved(tmp_path, monkeypatch):
    import urllib.request

    from referencing.exceptions import Unresolvable

    schema = json.loads(json.dumps(SCHEMA))
    schema["allOf"].append({"$ref": "https://example.org/unpinned.json#/definitions/X"})
    pins, served = make_pins(schema=schema)
    validator = checker.Validator(pins, checker.fetch(pins, tmp_path, downloader(served)), PLATFORM_CONTEXT)
    reached = []

    def no_network(*args, **kwargs):
        reached.append(args)
        raise AssertionError("the validator reached the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    monkeypatch.setattr(checker, "_download", no_network)
    with pytest.raises(Unresolvable):
        validator.check_entity(thing())
    assert reached == []


def test_validation_needs_no_network_once_the_files_are_fetched(validator, monkeypatch):
    import urllib.request

    def no_network(*args, **kwargs):
        raise AssertionError("the validator reached the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    assert validator.check_entity(thing(status=prop("fine"))) != []
    assert validator.check_entity(thing()) == []


def test_context_terms_expand_prefixes():
    terms = checker.context_terms(PLATFORM_CONTEXT)
    assert terms["level"] == "https://nkz-os.org/ns/level" and terms["DemoThing"] == OFFICIAL + "DemoThing"


# ── examples ─────────────────────────────────────────────────────────────────


def run_examples(validator, entities, allowed=("DemoThing",), pending=()):
    return checker.check_examples(validator, [(f"{i}.json", e) for i, e in enumerate(entities)], allowed, pending)


def test_examples_clean(validator):
    assert run_examples(validator, [thing()]) == ([], [])


def test_examples_report_findings_with_the_file_name(validator):
    errors, notes = run_examples(validator, [thing(status=prop("fine"))])
    assert len(errors) == 1 and errors[0].startswith("0.json: DemoThing urn:ngsi-ld:DemoThing:a: schema at status")
    assert notes == []


def test_pending_type_with_failing_example_is_a_note(validator):
    errors, notes = run_examples(validator, [thing(status=prop("fine"))], pending=("DemoThing",))
    assert errors == [] and notes == ["DemoThing: pending, 1 findings in 1 example(s)"]


def test_pending_type_whose_examples_validate_must_leave_the_list(validator):
    errors, _ = run_examples(validator, [thing()], pending=("DemoThing",))
    assert errors == ["DemoThing: every example validates; remove it from the pending list"]


def test_pending_type_without_example_is_accepted(validator):
    assert run_examples(validator, [], pending=("DemoThing",)) == ([], [])


def test_official_type_without_example_must_be_pending(validator):
    errors, _ = run_examples(validator, [])
    assert errors == ["DemoThing: official type with no example and not on the pending list"]


def test_allowed_type_without_a_pinned_schema(validator):
    errors, _ = run_examples(validator, [thing()], allowed=("DemoThing", "Unpinned"))
    assert errors == ["Unpinned: allowed entity type without a pinned official schema"]


def test_pending_name_that_is_not_an_official_type(validator):
    errors, _ = run_examples(validator, [thing()], pending=("OwnThing",))
    assert errors == ["OwnThing: on the pending list but not an official type with a pinned schema"]


def test_one_failing_example_among_several_fails_the_type(validator):
    errors, _ = run_examples(validator, [thing(), thing(status=prop("fine"))])
    assert len(errors) == 1 and errors[0].startswith("1.json")


def test_read_examples_accepts_an_entity_or_a_list(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(thing()))
    (tmp_path / "b.json").write_text(json.dumps([thing(), thing()]))
    (tmp_path / "notes.txt").write_text("ignored")
    assert [name for name, _ in checker.read_examples(tmp_path)] == ["a.json", "b.json", "b.json"]
    (tmp_path / "c.json").write_text("[1]")
    with pytest.raises(ValueError, match="entity object"):
        checker.read_examples(tmp_path)


def test_read_names_skips_comments_and_blank_lines(tmp_path):
    path = tmp_path / "list.txt"
    path.write_text("# header\n\nAlert\n  # indented comment\nDevice  \n")
    assert checker.read_names(path) == ["Alert", "Device"]


# ── command line ─────────────────────────────────────────────────────────────


def cli_files(tmp_path, entities, pending="", pins_served=None):
    pins, served = pins_served or make_pins()
    cache = tmp_path / "cache"
    cache.mkdir()
    for url, sha in checker.pinned_files(pins).values():
        (cache / sha).write_bytes(json.dumps(served[url]).encode())
    examples = tmp_path / "examples"
    examples.mkdir()
    for index, entity in enumerate(entities):
        (examples / f"{index}.json").write_text(json.dumps(entity))
    (tmp_path / "pins.json").write_text(json.dumps(pins))
    (tmp_path / "context.json").write_text(json.dumps(PLATFORM_CONTEXT))
    (tmp_path / "allowed.txt").write_text("DemoThing\n")
    (tmp_path / "pending.txt").write_text(pending)
    return ["examples", str(examples), "--pins", str(tmp_path / "pins.json"), "--context", str(tmp_path / "context.json"),
            "--allowed", str(tmp_path / "allowed.txt"), "--pending", str(tmp_path / "pending.txt"),
            "--cache", str(cache)]


def test_cli_clean(tmp_path, capsys):
    assert checker.main(cli_files(tmp_path, [thing()])) == 0
    assert "OK" in capsys.readouterr().out


def test_cli_findings(tmp_path, capsys):
    assert checker.main(cli_files(tmp_path, [thing(status=prop("fine"))])) == 1
    out = capsys.readouterr().out
    assert "::error title=Smart Data Models conformance::0.json" in out and "1 problem(s)." in out


def test_cli_internal_error_is_never_a_pass(tmp_path, capsys):
    argv = cli_files(tmp_path, [thing()])
    (tmp_path / "pins.json").write_text("{}")
    assert checker.main(argv) == 3
    assert "internal error" in capsys.readouterr().err


def test_cli_reference_outside_the_pinned_set_is_an_internal_error(tmp_path, capsys):
    schema = json.loads(json.dumps(SCHEMA))
    schema["allOf"].append({"$ref": "https://example.org/unpinned.json#/definitions/X"})
    assert checker.main(cli_files(tmp_path, [thing()], pins_served=make_pins(schema=schema))) == 3
    assert "outside the pinned set" in capsys.readouterr().err


def test_cli_bad_arguments(capsys):
    assert checker.main(["nope"]) == 2


def test_cli_without_examples_is_an_internal_error(tmp_path, capsys):
    argv = cli_files(tmp_path, [])
    assert checker.main(argv) == 3
    assert "no example entities" in capsys.readouterr().err
    argv[1] = str(tmp_path / "missing")
    assert checker.main(argv) == 3


# ── the repository's own lists ───────────────────────────────────────────────


def test_every_allowed_type_has_a_pinned_schema():
    pins = checker.load_pins(REPO_ROOT / "config" / "sdm" / "pins.json")
    allowed = checker.read_names(REPO_ROOT / "config" / "entity-types" / "allowed.txt")
    assert sorted(set(allowed) - set(pins["types"])) == []


def test_pins_are_full_commits_and_checksums():
    pins = checker.load_pins(REPO_ROOT / "config" / "sdm" / "pins.json")
    for entry in list(pins["subjects"].values()) + list(pins["shared"].values()):
        assert len(entry["commit"]) == 40 and set(entry["commit"]) <= set("0123456789abcdef")
    for _, sha in checker.pinned_files(pins).values():
        assert len(sha) == 64
    for url, _ in checker.pinned_files(pins).values():
        assert url.startswith("https://")


def test_pending_list_names_only_official_types():
    pins = checker.load_pins(REPO_ROOT / "config" / "sdm" / "pins.json")
    pending = checker.read_names(REPO_ROOT / "config" / "sdm" / "pending.txt")
    assert sorted(set(pending) - set(pins["types"])) == [] and len(pending) == len(set(pending))


# ── a module's own examples ───────────────────────────────────────────────────


def test_module_mode_judges_only_the_examples_given(validator):
    examples = [("a.json", thing())]
    assert checker.check_examples(validator, examples, ["DemoThing", "Unpinned"], [], complete=False) == ([], [])
    assert checker.check_examples(validator, [], ["DemoThing"], [], complete=False) == ([], [])


def test_module_mode_reports_findings_and_honours_its_own_pending_list(validator):
    bad = [("a.json", thing(status=prop("fine")))]
    errors, _ = checker.check_examples(validator, bad, [], [], complete=False)
    assert len(errors) == 1 and errors[0].startswith("a.json: DemoThing")
    errors, notes = checker.check_examples(validator, bad, [], ["DemoThing"], complete=False)
    assert errors == [] and notes == ["DemoThing: pending, 1 findings in 1 example(s)"]


def test_module_mode_lets_an_own_type_be_pending_and_refuses_idle_entries(validator):
    own = [("o.json", {"id": "urn:ngsi-ld:OwnThing:a", "type": "OwnThing", "colour": prop("red")})]
    assert checker.check_examples(validator, own, [], ["OwnThing"], complete=False)[0] == []
    errors, _ = checker.check_examples(validator, own, [], ["OwnThing", "Ghost"], complete=False)
    assert errors == ["Ghost: on the pending list but there is no example of it"]
    errors, _ = checker.check_examples(validator, [("a.json", thing())], [], ["DemoThing"], complete=False)
    assert errors == ["DemoThing: every example validates; remove it from the pending list"]


def test_cli_validate(tmp_path, capsys):
    argv = cli_files(tmp_path, [thing(status=prop("fine"))])
    argv[0] = "validate"
    (tmp_path / "allowed.txt").write_text("DemoThing\nUnpinned\n")
    assert checker.main(argv) == 1
    (tmp_path / "examples" / "pending.txt").write_text("DemoThing\n")
    assert checker.main(argv) == 0
    assert "note: DemoThing: pending" in capsys.readouterr().out
    for path in (tmp_path / "examples").glob("*.json"):
        path.unlink()
    assert checker.main(argv) == 3


def test_cli_validate_can_annotate_findings_as_warnings(tmp_path, capsys):
    argv = cli_files(tmp_path, [thing(status=prop("fine"))])
    argv[0] = "validate"
    assert checker.main(argv) == 1
    assert "::error title=Smart Data Models conformance::" in capsys.readouterr().out
    assert checker.main(argv + ["--annotate", "warning"]) == 1
    out = capsys.readouterr().out
    assert "::warning title=Smart Data Models conformance::" in out and "::error" not in out


# ── deviations from the official IRIs ────────────────────────────────────────

import subprocess  # noqa: E402


def deviation_setup(tmp_path, platform_terms):
    pins, served = make_pins()
    files = checker.fetch(pins, tmp_path / "cache", downloader(served))
    context = {"@context": {"nkz": "https://nkz-os.org/ns/", **platform_terms}}
    return pins, files, context


def test_schema_properties_follow_all_of_and_pinned_refs(tmp_path):
    shared = {"definitions": {"Common": {"properties": {"id": {}, "address": {}},
                                          "allOf": [{"$ref": "#/definitions/More"}]},
                              "More": {"properties": {"seeAlso": {}}, "allOf": [{"$ref": "#/definitions/Common"}]}}}
    names = checker.schema_properties(SCHEMA, {SHARED_URL: shared})
    assert names == {"status", "dateIssued", "level", "refOther", "tags", "address", "seeAlso"}


@pytest.mark.parametrize("keyword", ["allOf", "anyOf", "oneOf"])
def test_schema_properties_follow_every_combining_keyword(keyword):
    schema = {keyword: [{"properties": {"one": {}}}, {"properties": {"two": {}, "type": {}}}]}
    assert checker.schema_properties(schema, {}) == {"one", "two"}


def test_schema_properties_refuse_a_reference_outside_the_pinned_set():
    with pytest.raises(checker.PinError, match="outside the pinned set"):
        checker.schema_properties(SCHEMA, {})


def test_no_deviation_when_the_context_uses_the_official_iris(tmp_path):
    pins, files, context = deviation_setup(tmp_path, {"DemoThing": OFFICIAL + "DemoThing", "status": OFFICIAL + "status"})
    assert checker.compute_deviations(pins, files, context, ["DemoThing"]) == {"types": {}, "attributes": []}


def test_type_and_attribute_mapped_elsewhere_are_deviations(tmp_path):
    pins, files, context = deviation_setup(tmp_path, {"DemoThing": "nkz:DemoThing", "status": "nkz:status",
                                                      "level": "nkz:level"})
    assert checker.compute_deviations(pins, files, context, ["DemoThing"]) == {
        "types": {"DemoThing": {"platform": "https://nkz-os.org/ns/DemoThing", "official": OFFICIAL + "DemoThing"}},
        "attributes": [{"term": "status", "platform": "https://nkz-os.org/ns/status",
                        "official": OFFICIAL + "status", "types": ["DemoThing"]}],
    }


def test_terms_the_context_does_not_declare_are_not_deviations(tmp_path):
    pins, files, context = deviation_setup(tmp_path, {"DemoThing": OFFICIAL + "DemoThing"})
    assert checker.compute_deviations(pins, files, context, ["DemoThing"])["attributes"] == []


def test_a_core_term_means_the_core_iri_whatever_the_platform_says(tmp_path):
    pins, served = make_pins()
    core = {"@context": {"status": OFFICIAL + "status"}}
    served[CORE_URL] = core
    pins["core_context"]["sha256"] = digest(core)
    files = checker.fetch(pins, tmp_path / "cache", downloader(served))
    context = {"@context": {"DemoThing": OFFICIAL + "DemoThing", "status": "https://example.org/other/status"}}
    assert checker.compute_deviations(pins, files, context, ["DemoThing"])["attributes"] == []


def test_types_outside_the_allowed_list_are_not_inspected(tmp_path):
    pins, files, context = deviation_setup(tmp_path, {"DemoThing": "nkz:DemoThing"})
    assert checker.compute_deviations(pins, files, context, ["Other"]) == {"types": {}, "attributes": []}


RECORDED = {
    "types": {"DemoThing": {"platform": "p", "official": OFFICIAL + "DemoThing"}},
    "attributes": [{"term": "status", "platform": "p", "official": OFFICIAL + "status", "types": ["DemoThing"]}],
}


def test_recorded_deviations_matching_the_computed_ones_are_clean():
    assert checker.check_deviations(RECORDED, json.loads(json.dumps(RECORDED))) == []


def test_a_deviation_that_is_not_recorded_is_an_error():
    recorded = {"types": {}, "attributes": []}
    errors = checker.check_deviations(RECORDED, recorded)
    assert len(errors) == 2 and all("is not recorded" in error for error in errors)


def test_a_recorded_deviation_that_no_longer_exists_must_be_removed():
    errors = checker.check_deviations({"types": {}, "attributes": []}, RECORDED)
    assert len(errors) == 2 and all("remove it" in error for error in errors)


def test_same_deviations_written_differently_must_be_regenerated():
    other = json.loads(json.dumps(RECORDED))
    other["attributes"][0]["platform"] = "q"
    assert checker.check_deviations(RECORDED, other) == [
        "the deviations file does not match the computed one; regenerate it with `deviations --write`"
    ]


# ── ratchet ──────────────────────────────────────────────────────────────────


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.org", "PATH": __import__("os").environ["PATH"],
                        "HOME": str(repo)})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "pending.txt").write_text("# may only shrink\nAlert\nDevice\n")
    (tmp_path / "deviations.json").write_text(json.dumps(RECORDED))
    (tmp_path / "allowed.txt").write_text("Alert\nDevice\nDemoThing\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def ratchet(repo):
    return checker.check_ratchet("main", Path("pending.txt"), Path("deviations.json"))


def test_ratchet_accepts_shrinking_lists(repo):
    (repo / "pending.txt").write_text("# may only shrink\nAlert\n")
    (repo / "deviations.json").write_text(json.dumps({"types": {}, "attributes": []}))
    assert ratchet(repo) == []


def test_ratchet_refuses_a_new_pending_name(repo):
    (repo / "pending.txt").write_text("Alert\nDevice\nWeatherForecast\n")
    assert ratchet(repo) == ["WeatherForecast: added to the pending list, which may only shrink"]


def test_ratchet_refuses_a_new_deviation(repo):
    grown = json.loads(json.dumps(RECORDED))
    grown["attributes"][0]["types"].append("OtherThing")
    grown["types"]["Extra"] = {"platform": "p", "official": OFFICIAL + "Extra"}
    (repo / "deviations.json").write_text(json.dumps(grown))
    errors = ratchet(repo)
    assert len(errors) == 2 and all("may only shrink" in error for error in errors)


def test_a_newly_allowed_type_may_bring_its_deviations_and_be_pending(repo):
    (repo / "allowed.txt").write_text("Alert\nDevice\nDemoThing\nNewThing\n")
    grown = json.loads(json.dumps(RECORDED))
    grown["attributes"][0]["types"].append("NewThing")
    grown["types"]["NewThing"] = {"platform": "p", "official": OFFICIAL + "NewThing"}
    (repo / "deviations.json").write_text(json.dumps(grown))
    (repo / "pending.txt").write_text("Alert\nDevice\nNewThing\n")
    assert checker.check_ratchet("main", Path("pending.txt"), Path("deviations.json"), Path("allowed.txt")) == []
    # the same growth for a type that was already allowed is refused
    grown["attributes"][0]["types"].append("Alert")
    (repo / "deviations.json").write_text(json.dumps(grown))
    errors = checker.check_ratchet("main", Path("pending.txt"), Path("deviations.json"), Path("allowed.txt"))
    assert errors == ["Alert: attribute status added to the recorded deviations, which may only shrink"]
    # and without the allowed list nothing is forgiven
    assert len(checker.check_ratchet("main", Path("pending.txt"), Path("deviations.json"))) == 4


def test_ratchet_reads_the_base_list_whatever_its_line_endings(repo):
    (repo / "pending.txt").write_bytes(b"# header\r\nAlert  \r\n\r\nDevice\r\n")
    git(repo, "commit", "-q", "-am", "crlf")
    (repo / "pending.txt").write_text("Alert\nDevice\n")
    assert ratchet(repo) == []


def test_ratchet_refuses_a_file_outside_the_repository(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "pending.txt"
    outside.write_text("Alert\nTank\n")
    with pytest.raises(ValueError):
        checker.check_ratchet("main", outside, Path("deviations.json"))


def test_ratchet_has_nothing_to_compare_when_the_files_are_new(repo, tmp_path):
    (repo / "fresh.txt").write_text("Alert\n")
    (repo / "fresh.json").write_text(json.dumps(RECORDED))
    assert checker.check_ratchet("main", Path("fresh.txt"), Path("fresh.json")) == []


def test_cli_ratchet(repo, capsys):
    argv = ["ratchet", "--base", "main", "--pending", "pending.txt", "--deviations", "deviations.json",
            "--allowed", "allowed.txt"]
    assert checker.main(argv) == 0
    (repo / "pending.txt").write_text("Alert\nDevice\nTank\n")
    assert checker.main(argv) == 1
    assert "Tank: added to the pending list" in capsys.readouterr().out
    argv[2] = "no-such-ref"
    assert checker.main(argv) == 2


def test_cli_deviations(tmp_path, capsys):
    argv = cli_files(tmp_path, [thing()])
    argv = ["deviations"] + argv[2:] + ["--deviations", str(tmp_path / "deviations.json")]
    assert checker.main(argv + ["--write"]) == 0
    assert (tmp_path / "deviations.json").read_text() == '{\n  "types": {},\n  "attributes": []\n}\n'
    assert checker.main(argv) == 0
    (tmp_path / "deviations.json").write_text(json.dumps(RECORDED))
    assert checker.main(argv) == 1
    assert "remove it" in capsys.readouterr().out
    (tmp_path / "deviations.json").unlink()
    assert checker.main(argv) == 3
