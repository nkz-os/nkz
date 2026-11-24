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


def two_subjects(tmp_path):
    """Two official subjects that give the term `status` two different IRIs."""
    other = "https://smartdatamodels.org/dataModel.Other/"
    pins, served = make_pins()
    other_context = {"@context": {"OtherThing": other + "OtherThing", "status": other + "status"}}
    other_schema = {"type": "object", "properties": {"id": {}, "type": {}, "status": {"type": "string"}}}
    pins["subjects"]["Other"] = {"repo": "org/dataModel.Other", "commit": "c" * 40,
                                 "context": {"path": "context.jsonld", "sha256": digest(other_context)}}
    pins["types"]["OtherThing"] = {"subject": "Other", "schema": {"path": "OtherThing/schema.json",
                                                                  "sha256": digest(other_schema)}}
    base = "https://raw.githubusercontent.com/org/dataModel.Other/" + "c" * 40
    served[base + "/context.jsonld"] = other_context
    served[base + "/OtherThing/schema.json"] = other_schema
    return pins, checker.fetch(pins, tmp_path / "cache", downloader(served)), other


def test_a_deviation_forced_by_a_name_defined_twice_says_which_types_it_is_official_for(tmp_path):
    pins, files, other = two_subjects(tmp_path)
    context = {"@context": {"DemoThing": OFFICIAL + "DemoThing", "OtherThing": other + "OtherThing",
                            "status": OFFICIAL + "status"}}
    assert checker.compute_deviations(pins, files, context, ["DemoThing", "OtherThing"])["attributes"] == [
        {"term": "status", "platform": OFFICIAL + "status", "official": other + "status", "types": ["OtherThing"],
         "officialFor": ["DemoThing"]}]


def test_a_deviation_to_an_iri_no_model_uses_is_official_for_nothing(tmp_path):
    pins, files, other = two_subjects(tmp_path)
    context = {"@context": {"DemoThing": OFFICIAL + "DemoThing", "OtherThing": other + "OtherThing",
                            "status": "nkz:status"}}
    found = checker.compute_deviations(pins, files, context, ["DemoThing", "OtherThing"])["attributes"]
    assert len(found) == 2 and all("officialFor" not in entry for entry in found)


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


def test_ratchet_accepts_a_deviation_that_is_official_for_another_type(repo):
    grown = json.loads(json.dumps(RECORDED))
    grown["attributes"].append({"term": "brand", "platform": OFFICIAL + "brand", "official": "https://other/brand",
                                "types": ["Alert"], "officialFor": ["DemoThing"]})
    (repo / "deviations.json").write_text(json.dumps(grown))
    assert ratchet(repo) == []
    # the same entry without a type it is official for is an ordinary new deviation
    del grown["attributes"][1]["officialFor"]
    (repo / "deviations.json").write_text(json.dumps(grown))
    assert ratchet(repo) == ["Alert: attribute brand added to the recorded deviations, which may only shrink"]


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
    (tmp_path / "context.json").write_text(json.dumps({"@context": [PLATFORM_CONTEXT["@context"], CORE_URL]}))
    argv = ["deviations"] + argv[2:] + ["--deviations", str(tmp_path / "deviations.json")]
    assert checker.main(argv + ["--write"]) == 0
    assert (tmp_path / "deviations.json").read_text() == '{\n  "types": {},\n  "attributes": []\n}\n'
    assert checker.main(argv) == 0
    (tmp_path / "deviations.json").write_text(json.dumps(RECORDED))
    assert checker.main(argv) == 1
    assert "remove it" in capsys.readouterr().out
    (tmp_path / "deviations.json").unlink()
    assert checker.main(argv) == 3


# ── upstream watch ───────────────────────────────────────────────────────────

def upstream_served(served):
    """What the default branch serves when nothing changed since the pinned commits."""
    return {
        url.replace("/" + "a" * 40 + "/", "/HEAD/").replace("/" + "b" * 40 + "/", "/HEAD/"): document
        for url, document in served.items()
        if url != CORE_URL
    }


def test_upstream_is_quiet_when_the_default_branch_still_serves_the_pinned_files():
    pins, served = make_pins()
    calls = []
    assert checker.check_upstream(pins, downloader(upstream_served(served), calls)) == []
    assert len(calls) == 3
    assert all("/HEAD/" in url for url in calls)


def test_upstream_reports_a_file_that_changed_with_where_to_look():
    pins, served = make_pins()
    current = upstream_served(served)
    current["https://raw.githubusercontent.com/org/dataModel.Demo/HEAD/DemoThing/schema.json"] = {"changed": True}
    findings = checker.check_upstream(pins, downloader(current))
    assert len(findings) == 1
    assert findings[0].startswith("schema:DemoThing: ")
    assert "https://github.com/org/dataModel.Demo/compare/" + "a" * 40 + "...HEAD" in findings[0]


def test_upstream_reports_a_file_that_is_gone():
    import urllib.error

    pins, served = make_pins()
    current = upstream_served(served)

    def download(url):
        if url.endswith("/common-schema.json"):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return json.dumps(current[url]).encode()

    findings = checker.check_upstream(pins, download)
    assert len(findings) == 1
    assert findings[0].startswith(SHARED_URL + ": ")
    assert "no longer" in findings[0]


def test_upstream_does_not_take_a_failed_download_for_a_change():
    pins, _ = make_pins()

    def broken(url):
        raise OSError("no network")

    with pytest.raises(checker.PinError, match="could not fetch"):
        checker.check_upstream(pins, broken)


def test_cli_upstream(tmp_path, monkeypatch, capsys):
    pins, served = make_pins()
    (tmp_path / "pins.json").write_text(json.dumps(pins))
    argv = ["upstream", "--pins", str(tmp_path / "pins.json")]
    current = upstream_served(served)
    monkeypatch.setattr(checker, "_download", downloader(current))
    assert checker.main(argv) == 0
    assert "3 pinned files" in capsys.readouterr().out
    current["https://raw.githubusercontent.com/org/dataModel.Demo/HEAD/context.jsonld"] = {"@context": {}}
    assert checker.main(argv) == 1
    assert "::warning title=Smart Data Models upstream::context:Demo: " in capsys.readouterr().out

    def broken(url):
        raise OSError("no network")

    monkeypatch.setattr(checker, "_download", broken)
    assert checker.main(argv) == 3


# ── a tenant read from the broker ────────────────────────────────────────────

BROKER = "http://broker.test:1026"
CONTEXT_LINK = "http://gateway.test/context.json"
THING_IRI = OFFICIAL + "DemoThing"


class FakeBroker:
    """Stands in for `urllib.request.urlopen`: answers from `routes`, records every request."""

    def __init__(self, types, entities, failures=()):
        self.types = types
        self.entities = entities
        self.failures = list(failures)
        self.requests = []

    def __call__(self, request, timeout=None):
        import io
        import urllib.parse

        self.requests.append(request)
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        parts = urllib.parse.urlsplit(request.full_url)
        query = dict(urllib.parse.parse_qsl(parts.query))
        if parts.path == "/ngsi-ld/v1/types":
            body = self.types
        else:
            stored = self.entities.get(query["type"], [])
            offset = int(query.get("offset", 0))
            body = stored[offset:offset + int(query["limit"])]
        if isinstance(body, bytes):
            return io.BytesIO(body)
        return io.BytesIO(json.dumps(body).encode())


def type_row(iri):
    return {"id": iri, "type": "EntityType", "typeName": iri.rsplit("/", 1)[-1], "attributeNames": []}


def no_sleep(seconds):
    return None


def test_signature_has_no_entity_id_value_or_list_index(validator):
    first = validator.check_entity(thing(id="urn:ngsi-ld:DemoThing:a", status=prop("fine")))
    second = validator.check_entity(thing(id="urn:ngsi-ld:DemoThing:b", status=prop("worse")))
    assert {checker.signature(f) for f in first} == {checker.signature(f) for f in second}
    assert {checker.signature(f) for f in first} == {"DemoThing | schema | status | enum"}
    indexed = checker.Finding("urn:x", "DemoThing", "schema", "analytics/12/value", "type: 3 is not of type 'string'",
                              "type string")
    assert checker.signature(indexed) == "DemoThing | schema | analytics/*/value | type string"


def test_signature_of_a_wrong_json_type_names_the_expected_one(validator):
    found = validator.check_entity(thing(status=prop(5)))
    assert "DemoThing | schema | status | type string" in {checker.signature(f) for f in found}


def test_signature_of_a_missing_attribute_names_it(validator):
    found = validator.check_entity(thing(status=None))
    assert [checker.signature(f) for f in found] == ["DemoThing | schema | (entity) | required status"]


def test_signature_of_the_context_kinds(validator):
    found = validator.check_entity(thing(extra=prop(1), status=prop("ok", note=prop("x"))))
    assert sorted(checker.signature(f) for f in found) == [
        "DemoThing | attribute-outside-context | extra",
        "DemoThing | subattribute-outside-context | status.note",
    ]


def test_broker_types_reads_the_stored_iris_without_a_link():
    broker = FakeBroker([type_row(THING_IRI), type_row("https://example.org/Other")], {})
    assert checker.broker_types(BROKER, "tenant-a", opener=broker, sleep=no_sleep) == [
        THING_IRI, "https://example.org/Other"]
    request = broker.requests[0]
    assert "limit=1000" in request.full_url and "details=true" in request.full_url
    assert request.get_header("Ngsild-tenant") == "tenant-a"
    assert request.get_header("Fiware-service") == "tenant-a"
    assert request.get_header("Link") is None


def test_broker_entities_pages_until_a_short_page_and_sends_the_platform_link():
    stored = [thing(id=f"urn:ngsi-ld:DemoThing:{n}") for n in range(5)]
    broker = FakeBroker([], {THING_IRI: stored})
    got = checker.broker_entities(BROKER, "tenant-a", THING_IRI, CONTEXT_LINK, page=2, opener=broker, sleep=no_sleep)
    assert [e["id"] for e in got] == [e["id"] for e in stored]
    assert len(broker.requests) == 3
    assert f"<{CONTEXT_LINK}>" in broker.requests[0].get_header("Link")
    assert broker.requests[0].get_header("Accept") == "application/json"


def test_broker_read_retries_a_connection_failure_and_a_5xx():
    import urllib.error

    failures = [ConnectionRefusedError(), urllib.error.HTTPError(BROKER, 503, "busy", {}, None),
                urllib.error.HTTPError(BROKER, 429, "slow down", {}, None)]
    broker = FakeBroker([type_row(THING_IRI)], {}, failures)
    waits = []
    assert checker.broker_types(BROKER, "tenant-a", opener=broker, sleep=waits.append) == [THING_IRI]
    assert len(broker.requests) == 4 and len(waits) == 3 and waits[1] > waits[0]


@pytest.mark.parametrize("failures,types", [
    ([ConnectionRefusedError()] * 5, [type_row(THING_IRI)]),          # never answers
    (["http-404"], [type_row(THING_IRI)]),                             # a client error is not retried
    ([], b"<html>"),                                                   # not JSON
    ([], {"typeList": []}),                                            # not the expected listing
    ([], [type_row(THING_IRI)] * 1000),                                # a full page may hide more
])
def test_broker_types_never_takes_a_failed_read_for_an_answer(failures, types):
    import urllib.error

    failures = [urllib.error.HTTPError(BROKER, 404, "no", {}, None) if f == "http-404" else f for f in failures]
    broker = FakeBroker(types, {}, failures)
    with pytest.raises(checker.BrokerError):
        checker.broker_types(BROKER, "tenant-a", opener=broker, sleep=no_sleep)


def test_broker_address_needs_a_scheme():
    with pytest.raises(checker.BrokerError):
        checker.broker_types("broker.test:1026", "tenant-a", opener=FakeBroker([], {}), sleep=no_sleep)


def test_broker_entities_must_be_a_list_of_objects():
    broker = FakeBroker([], {THING_IRI: ["nope"]})
    with pytest.raises(checker.BrokerError):
        checker.broker_entities(BROKER, "tenant-a", THING_IRI, CONTEXT_LINK, opener=broker, sleep=no_sleep)


def test_a_broker_read_takes_a_single_value_for_the_one_element_array_it_was(validator):
    """A broker may store a one-element array as its element; in JSON-LD the two are the same value."""
    stored = thing(tags=prop({"name": "a"}))
    assert kinds(validator.check_entity(stored)) == ["schema"]
    lenient = validator.check_entity(stored, single_for_array=True)
    assert [(f.kind, f.where) for f in lenient] == [("single-value-for-array", "tags")]
    assert checker.signature(lenient[0]) == "DemoThing | single-value-for-array | tags"


def test_a_single_value_that_would_not_be_a_valid_element_is_still_a_finding(validator):
    found = validator.check_entity(thing(tags=prop({"other": 1})), single_for_array=True)
    assert sorted((f.kind, f.where, f.rule) for f in found) == [
        ("schema", "tags/0", "required name"), ("single-value-for-array", "tags", "")]


def test_only_an_array_schema_gets_the_single_value_reading(validator):
    found = validator.check_entity(thing(level=prop("high")), single_for_array=True)
    assert [(f.kind, f.where, f.rule) for f in found] == [("schema", "level", "type number")]


def test_check_broker_keeps_single_values_apart_from_the_baseline(validator):
    entities = [thing(id="urn:ngsi-ld:DemoThing:a", tags=prop({"name": "a"})),
                thing(id="urn:ngsi-ld:DemoThing:b", status=prop("fine"))]
    new, stale, current, tolerated = checker.check_broker(validator, entities, [])
    assert current == ["DemoThing | schema | status | enum"]
    assert len(new) == 1 and new[0].startswith("DemoThing | schema | status | enum")
    assert tolerated == ["DemoThing | single-value-for-array | tags: 1 entities, e.g. urn:ngsi-ld:DemoThing:a"]


def test_check_broker_reports_what_is_new_and_what_the_baseline_can_lose(validator):
    entities = [thing(id="urn:ngsi-ld:DemoThing:a", status=prop("fine")),
                thing(id="urn:ngsi-ld:DemoThing:b", status=prop("worse")),
                thing(id="urn:ngsi-ld:DemoThing:c", extra=prop(1))]
    baseline = ["DemoThing | attribute-outside-context | extra", "DemoThing | schema | (entity) | required status"]
    new, stale, current, _ = checker.check_broker(validator, entities, baseline)
    assert len(new) == 1
    assert new[0].startswith("DemoThing | schema | status | enum: 2 entities, e.g. urn:ngsi-ld:DemoThing:a")
    assert stale == ["DemoThing | schema | (entity) | required status"]
    assert current == ["DemoThing | attribute-outside-context | extra", "DemoThing | schema | status | enum"]


def test_check_broker_reports_a_type_stored_outside_the_context(validator):
    stray = {"id": "urn:ngsi-ld:Stray:1", "type": "https://example.org/Stray"}
    new, _, current, _ = checker.check_broker(validator, [stray], [])
    assert current == ["https://example.org/Stray | schema | type"]
    assert len(new) == 1


def broker_cli(tmp_path, monkeypatch, entities, baseline, types=None, extra=()):
    argv = cli_files(tmp_path, [thing()])
    (tmp_path / "baseline.txt").write_text(baseline)
    broker = FakeBroker([type_row(THING_IRI)] if types is None else types, {THING_IRI: entities})
    monkeypatch.setattr(checker.urllib.request, "urlopen", broker)
    monkeypatch.setattr(checker.time, "sleep", no_sleep)
    return ["broker", "--orion-url", BROKER, "--tenant", "tenant-a", "--link-context", CONTEXT_LINK,
            "--baseline", str(tmp_path / "baseline.txt"), *argv[2:], *extra]


def test_cli_broker_is_clean_when_nothing_is_new(tmp_path, monkeypatch, capsys):
    argv = broker_cli(tmp_path, monkeypatch, [thing(), thing(status=prop("fine"))],
                      "# known\nDemoThing | schema | status | enum\n")
    assert checker.main(argv) == 0
    out = capsys.readouterr().out
    assert "1 types, 2 entities, 1 signatures, 0 new, 0 stale, 0 single values" in out


def test_cli_broker_fails_on_a_new_signature_and_notes_a_stale_one(tmp_path, monkeypatch, capsys):
    argv = broker_cli(tmp_path, monkeypatch, [thing(extra=prop(1))], "DemoThing | schema | status | enum\n")
    assert checker.main(argv) == 1
    out = capsys.readouterr().out
    assert "NEW DemoThing | attribute-outside-context | extra: 1 entities, e.g. urn:ngsi-ld:DemoThing:a" in out
    assert "STALE DemoThing | schema | status | enum" in out


def test_cli_broker_shows_single_values_without_failing(tmp_path, monkeypatch, capsys):
    argv = broker_cli(tmp_path, monkeypatch, [thing(tags=prop({"name": "a"}))], "")
    assert checker.main(argv) == 0
    out = capsys.readouterr().out
    assert "SINGLE DemoThing | single-value-for-array | tags: 1 entities" in out
    assert "0 signatures, 0 new, 0 stale, 1 single values" in out
    assert checker.main(argv + ["--print-signatures"]) == 0
    assert capsys.readouterr().out == ""


def test_cli_broker_prints_the_signatures_for_a_first_baseline(tmp_path, monkeypatch, capsys):
    argv = broker_cli(tmp_path, monkeypatch, [thing(extra=prop(1))], "", extra=["--print-signatures"])
    assert checker.main(argv) == 0
    assert capsys.readouterr().out.splitlines() == ["DemoThing | attribute-outside-context | extra"]


def test_cli_broker_cannot_check_a_tenant_without_types(tmp_path, monkeypatch, capsys):
    argv = broker_cli(tmp_path, monkeypatch, [], "", types=[])
    assert checker.main(argv) == 3
    assert "holds no entity types" in capsys.readouterr().err


def test_cli_broker_does_not_pass_when_the_listed_types_read_back_empty(tmp_path, monkeypatch, capsys):
    argv = broker_cli(tmp_path, monkeypatch, [], "")
    assert checker.main(argv) == 3
    assert "no entity could be read" in capsys.readouterr().err


def test_cli_broker_cannot_check_without_its_baseline_file(tmp_path, monkeypatch):
    argv = broker_cli(tmp_path, monkeypatch, [thing()], "")
    (tmp_path / "baseline.txt").unlink()
    assert checker.main(argv) == 3


def test_cli_broker_does_not_pass_when_the_broker_stops_answering_midway(tmp_path, monkeypatch):
    import urllib.error

    argv = broker_cli(tmp_path, monkeypatch, [thing()], "")
    broker = FakeBroker([type_row(THING_IRI)], {THING_IRI: [thing()]},
                        [None, urllib.error.HTTPError(BROKER, 400, "bad", {}, None)])
    monkeypatch.setattr(checker.urllib.request, "urlopen", broker)
    assert checker.main(argv) == 3


def test_cli_broker_needs_every_address_spelled_out():
    assert checker.main(["broker", "--tenant", "tenant-a"]) == 2


def test_the_image_that_runs_the_broker_check_carries_what_it_reads():
    """The scheduled check runs from an image: it must hold the checker and its default files, same layout."""
    recipe = (REPO_ROOT / "services" / "entity-manager" / "Dockerfile").read_text(encoding="utf-8")
    copies = dict(line.split()[1:3] for line in recipe.splitlines() if line.startswith("COPY ") and len(line.split()) == 3)
    root = "/app/sdm-conformance/"
    for needed in (".github/scripts/check_sdm_conformance.py", "config/sdm/pins.json", "config/ngsi-ld-context.json"):
        assert copies.get(needed) == root + needed
        assert (REPO_ROOT / needed).is_file()
    parser_defaults = (REPO_ROOT / ".github" / "scripts" / "check_sdm_conformance.py").read_text(encoding="utf-8")
    assert 'default=Path("config/sdm/pins.json")' in parser_defaults
    assert 'default=Path("config/ngsi-ld-context.json")' in parser_defaults
    pinned = [line.strip() for line in (REPO_ROOT / "services" / "entity-manager" / "requirements.txt").read_text(
        encoding="utf-8").splitlines() if line.startswith("jsonschema")]
    workflow = (REPO_ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert len(pinned) == 1 and "==" in pinned[0] and pinned[0] in workflow


CORE_TERMS = {"status": "https://uri.etsi.org/ngsi-ld/status", "location": "https://uri.etsi.org/ngsi-ld/location"}


def test_a_context_that_lists_the_core_last_and_repeats_none_of_its_terms_is_clean():
    context = {"@context": [{"DemoThing": OFFICIAL + "DemoThing"}, CORE_URL]}
    assert checker.check_core_context(context, CORE_TERMS, CORE_URL) == []


@pytest.mark.parametrize("entries", [
    [CORE_URL, {"DemoThing": OFFICIAL + "DemoThing"}],
    [{"DemoThing": OFFICIAL + "DemoThing"}],
    [{"DemoThing": OFFICIAL + "DemoThing"}, "https://example.org/ngsi-ld-core-context.jsonld"],
])
def test_the_core_context_must_be_the_pinned_one_and_the_last_entry(entries):
    errors = checker.check_core_context({"@context": entries}, CORE_TERMS, CORE_URL)
    assert len(errors) == 1 and "last entry" in errors[0]


def test_a_context_that_is_not_a_list_has_no_core_entry():
    errors = checker.check_core_context({"@context": {"DemoThing": OFFICIAL + "DemoThing"}}, CORE_TERMS, CORE_URL)
    assert len(errors) == 1 and "last entry" in errors[0]


def test_the_core_context_is_listed_once():
    context = {"@context": [CORE_URL, {"DemoThing": OFFICIAL + "DemoThing"}, CORE_URL]}
    errors = checker.check_core_context(context, CORE_TERMS, CORE_URL)
    assert len(errors) == 1 and "once" in errors[0]


def test_a_platform_term_that_repeats_a_core_term_is_reported_even_with_the_same_iri():
    context = {"@context": [
        {"status": "https://example.org/other/status", "location": CORE_TERMS["location"],
         "DemoThing": OFFICIAL + "DemoThing"},
        CORE_URL,
    ]}
    errors = checker.check_core_context(context, CORE_TERMS, CORE_URL)
    assert len(errors) == 2
    assert "location" in errors[0] and "status" in errors[1]


def test_keywords_of_the_platform_context_are_not_terms():
    context = {"@context": [{"@version": 1.1, "DemoThing": OFFICIAL + "DemoThing"}, CORE_URL]}
    assert checker.check_core_context(context, {"@version": "x", **CORE_TERMS}, CORE_URL) == []


def test_cli_deviations_also_checks_the_core_context(tmp_path, capsys):
    argv = cli_files(tmp_path, [thing()])
    argv = ["deviations"] + argv[2:] + ["--deviations", str(tmp_path / "deviations.json")]
    terms = PLATFORM_CONTEXT["@context"]

    def run(entries):
        (tmp_path / "context.json").write_text(json.dumps({"@context": entries}))
        assert checker.main(argv + ["--write"]) == 0
        return checker.main(argv)

    assert run([terms, CORE_URL]) == 0
    capsys.readouterr()
    assert run([CORE_URL, terms]) == 1
    assert "last entry" in capsys.readouterr().out
    assert run([{**terms, "location": "https://example.org/other/location"}, CORE_URL]) == 1
    assert "defines `location`" in capsys.readouterr().out
