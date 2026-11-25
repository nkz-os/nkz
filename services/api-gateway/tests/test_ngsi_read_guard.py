"""Grammar and parameters of the entity reads the gateway forwards."""

import time

import pytest
from ngsi_read_guard import (
    InvalidQuery,
    entity_params,
    list_params,
    query_body,
    query_params,
    types_params,
    valid_entity_path,
    valid_filter,
)

VALID_FILTERS = [
    'status==active;refSourceSensor=="urn:ngsi-ld:Device:1"',
    'refAgriParcel=="urn:ngsi-ld:AgriParcel:1"',
    'cadastralReference=="1"',
    "hasAgriCrop",
    '(a=="1"|b=="1")',
    "(a==1;b==2)|(c==3;d==4)",
    "num>=10;num<=20",
    "num>-1.5",
    'd[@value]>="2026-01-01"',
    'validTo>"2026-11-25T00:00:00Z"',
    "d>2026-01-01T00:00:00Z",
    "num==1..5",
    'd=="2026-01-01".."2026-02-01"',
    "a==1,2,3",
    'a=="x","y"',
    "flag==true",
    "flag!=false",
    'name=="Viña Norte"',
    'name=="a;b|c(d)"',
    'name==""',
    "a.b.c==1",
    "name~=Norte",
    "name~=Viña",
    "cad~=^31",
    "rel~=.*:1$",
    "name!~=Parcela Norte",
    "name~=Norte|name~=Sur",
    ";".join(f"n=={i}" for i in range(12)),
    "(" * 4 + "n==1" + ")" * 4,
    "n==" + ",".join(["1"] * 50),
    "a~=" + "x" * 64,
    'a=="' + "x" * 256 + '"',
    "a" * 128 + "==1",
]

INVALID_FILTERS = [
    "", "a==", "a>true", "a>=false", "a==true..1", "a>true..1", "a==1,", "a==1..", "a==,1",
    "[", "a[", "a=[", "a[]", "a[b", "a]", "a==1&b==2", "!a", "a===1", "a=1", "a>active", "a>1,2",
    "a>1..2", 'a=="x', 'a=="x\\"y"', 'a=="x"y', "a==\x00", "a\x00==1", 'a=="\x00"', 'a=="\n"', "a==1\n",
    "(a==1", "a==1)", "()", "a==1;", ";a==1", "a==1;;b==2", "a==1 ;b==2", " a==1", "a==1(b==2)",
    "(a==1)(b==2)", "a==1..2..3", "a==1..2,3",
    "a~=", 'a~="x"', "a~=(x)", "a~=[0-9]", "a~=x+", "a~=x{2}", "a~=x?", "a~=*", "a~=x**", "a~=.*.*.*", "a~=^",
    "a~=$", "a~=^$", "a~=x\\", "a~=..", "a~=x^y", "a~=x$y", "a~= x", "a~=x ", "a~=x,y", "a~=x\n",
    "a==null", "a>null", "a==null,1", "a==1,null",
    ".".join(["a"] * 6) + "==1", "a..b==1", ".a==1", "a.==1", "a" * 129 + "==1", "1a==1",
    'a=="' + "x" * 257 + '"', "a==" + "x" * 129, "a~=" + "x" * 65, "a==1e999", "a==" + "1" * 16,
    ";".join(f"n=={i}" for i in range(13)), "|".join(f"n{i}" for i in range(13)),
    "(" * 5 + "n==1" + ")" * 5, "n==" + ",".join(["1"] * 51),
    ";".join(['a=="' + "x" * 250 + '"'] * 12),
    "a==1)|(b==2", "a~=" + "x" * 62 + ".*.*",
    "https://example.org/a==1", "a==１", "a>１", "a==٣",
    None, 3, [], {}, b"a==1",
]


@pytest.mark.parametrize("text", VALID_FILTERS)
def test_well_formed_filters(text):
    assert valid_filter(text) is True


@pytest.mark.parametrize("text", INVALID_FILTERS)
def test_other_filters(text):
    assert valid_filter(text) is False


@pytest.mark.parametrize(
    "unit", ["a", "1,", "a==1,", "(", "a.", '"', "a~=.*", "a==1..", "a[", ".*", "a:", "1-", " x", "a==aaaa,"]
)
def test_any_text_is_judged_quickly(unit):
    text = ("a==" + unit * 2048)[:2048]
    start = time.perf_counter()
    valid_filter(text)
    valid_filter((unit * 2048)[:2048])
    valid_filter(("a~=" + unit * 2048)[:2048])
    assert time.perf_counter() - start < 0.2


def _bad(function, args):
    with pytest.raises(InvalidQuery) as caught:
        function(args)
    return caught.value.parameter


GEO = {"georel": "near;maxDistance==100", "geometry": "Point", "coordinates": "[0,0]"}
RING = "[[[0,0],[1,0],[1,1],[0,0]]]"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"type": "LivestockAnimal,Animal", "options": "keyValues", "limit": "1000", "offset": "0"},
        {"type": "https://example.org/T"},
        {"type": "Alert", "attrs": "alertType,description", "q": "status==active", "limit": "100"},
        {"id": "urn:ngsi-ld:Device:1,urn:ngsi-ld:Device:2"},
        {"id": "https://example.org/d/1"},
        {"idPattern": "urn:ngsi-ld:Device:.*"},
        {"attrs": "https://example.org/a#b"},
        {"options": "keyValues,sysAttrs", "count": "true"},
        {"count": "false", "limit": "0"},
        {"offset": "1000000000"},
        {"attrs": ",".join(f"a{i}" for i in range(50))},
        GEO,
        {**GEO, "geoproperty": "location"},
        {**GEO, "georel": "near;minDistance==0"},
        {"georel": "within", "geometry": "Polygon", "coordinates": RING},
        {"georel": "intersects", "geometry": "LineString", "coordinates": "[[0,0],[1.5,-2e1]]"},
        {"georel": "equals", "geometry": "MultiPoint", "coordinates": "[[0,0]]"},
        {"georel": "disjoint", "geometry": "MultiLineString", "coordinates": "[[[0,0],[1,1]]]"},
        {"georel": "overlaps", "geometry": "MultiPolygon", "coordinates": f"[{RING}]"},
        {"georel": "contains", "geometry": "Point", "coordinates": "[0, 0, 5]"},
    ],
)
def test_list_parameters_are_kept(params):
    assert list_params(params.items()) == params


@pytest.mark.parametrize("name", ["datasetId", "pick", "omit", "lang", "scopeQ", "csf", "local", "foo", "Q", "q "])
def test_unknown_list_parameters(name):
    assert _bad(list_params, [(name, "x")]) == name


@pytest.mark.parametrize(
    "name, value",
    [
        ("q", "a=="), ("q", ""),
        ("attrs", ",".join(["a"] * 51)), ("attrs", "a" * 129), ("attrs", ""), ("attrs", "a,"), ("attrs", "a b"),
        ("attrs", "a\x00"), ("attrs", "https://example.org/" + "a" * 200),
        ("type", ",".join(["T"] * 21)), ("type", ""), ("type", "T|U"), ("type", "(T)"),
        ("id", ",".join(["urn:a:b"] * 51)), ("id", "x"), ("id", "urn:a:" + "b" * 300), ("id", "urn:a:b,"), ("id", "urn:a:b c"),
        ("limit", "-1"), ("limit", "1001"), ("limit", "abc"), ("limit", "1_0"), ("limit", "１"), ("limit", ""), ("limit", "9" * 5000),
        ("offset", "1000000001"), ("offset", "-1"), ("offset", "1.0"),
        ("count", "x"), ("count", "True"), ("options", "bogus"), ("options", ""), ("options", "keyValues,"),
        ("idPattern", "("), ("idPattern", "a{2}"), ("idPattern", "[a]"), ("idPattern", "a+"), ("idPattern", "a" * 129), ("idPattern", ""),
        ("idPattern", ".*.*.*"), ("geoproperty", "a b"),
    ],
)
def test_malformed_list_parameters(name, value):
    extra = list(GEO.items()) if name == "geoproperty" else []
    assert _bad(list_params, extra + [(name, value)]) == name


@pytest.mark.parametrize("name", ["type", "limit", "q"])
def test_repeated_parameters(name):
    assert _bad(list_params, [(name, "1"), (name, "1")]) == name


@pytest.mark.parametrize("missing", ["georel", "geometry", "coordinates"])
def test_geo_parameters_travel_together(missing):
    assert _bad(list_params, [(k, v) for k, v in GEO.items() if k != missing]) == missing


def test_geoproperty_needs_a_geo_query():
    assert _bad(list_params, [("geoproperty", "location")]) == "georel"


@pytest.mark.parametrize("georel", ["near", "bogus", "near;maxDistance==", "near;maxDistance==-1", "near;maxDistance==1;x", "near;maxDistance=1", ""])
def test_malformed_georel(georel):
    assert _bad(list_params, {**GEO, "georel": georel}.items()) == "georel"


def test_unknown_geometry():
    assert _bad(list_params, {**GEO, "geometry": "Circle"}.items()) == "geometry"


@pytest.mark.parametrize(
    "geometry, coordinates",
    [
        ("Point", "x"), ("Point", "[0]"), ("Point", "[0,0,0,0]"), ("Point", "[[0,0]]"), ("Point", "[NaN,0]"), ("Point", "[Infinity,0]"),
        ("Point", "[1e999,0]"), ("Point", '["0",0]'), ("Point", "[true,0]"), ("Point", "[null,0]"), ("Point", "{}"), ("Point", "[]"), ("Point", ""),
        ("Point", "[" + "0" * 5000 + ",0]"), ("Point", "[" * 2000 + "]" * 2000), ("Point", "[" + "9" * 400 + ",0]"), ("Point", "[0,0]x"), ("Point", "[181,0]"), ("Point", "[0,91]"),
        ("LineString", "[[0,0]]"), ("LineString", "[0,0]"), ("MultiPoint", "[]"),
        ("Polygon", "[[[0,0],[1,0],[1,1],[2,2]]]"), ("Polygon", "[[[0,0],[1,0],[0,0]]]"), ("Polygon", "[]"), ("Polygon", "[[]]"),
        ("MultiLineString", "[[[0,0]]]"), ("MultiPolygon", "[[]]"), ("MultiPolygon", RING),
    ],
)
def test_malformed_coordinates(geometry, coordinates):
    params = {"georel": "within", "geometry": geometry, "coordinates": coordinates}
    assert _bad(list_params, params.items()) == "coordinates"


def test_entity_parameters():
    good = {"attrs": "a,b", "options": "keyValues", "type": "T"}
    assert entity_params(good.items()) == good
    assert _bad(entity_params, [("q", "a==1")]) == "q"
    assert _bad(entity_params, [("attrs", "a" * 129)]) == "attrs"


def test_types_parameters():
    good = {"details": "true", "limit": "1000", "offset": "1"}
    assert types_params(good.items()) == good
    assert _bad(types_params, [("details", "x")]) == "details"
    assert _bad(types_params, [("q", "a==1")]) == "q"


def test_query_parameters():
    good = {"limit": "1", "offset": "2", "count": "true", "options": "keyValues"}
    assert query_params(good.items()) == good
    assert _bad(query_params, [("q", "a==1")]) == "q"


ENTITIES = [{"type": "T"}]
GEO_Q = {"geometry": "Point", "coordinates": [0, 0], "georel": "near;maxDistance==10"}


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"entities": [{"type": "T", "id": "urn:a:b"}, {"type": "U", "idPattern": ".*"}, {"id": "urn:a:b"}]},
        {"entities": ENTITIES * 20},
        {"attrs": ["a", "https://example.org/a"]},
        {"q": "a==1"},
        {"geoQ": GEO_Q},
        {"geoQ": {**GEO_Q, "geoproperty": "location"}},
    ],
)
def test_query_bodies_are_kept(extra):
    body = {"type": "Query", "entities": ENTITIES, **extra}
    assert query_body(body) == body


def test_query_body_without_entities_needs_a_filter():
    for extra in ({"q": "a==1"}, {"attrs": ["a"]}, {"geoQ": GEO_Q}):
        assert query_body({"type": "Query", **extra}) == {"type": "Query", **extra}
    assert _bad(query_body, {"type": "Query"}) == "entities"


def test_query_body_names_its_type():
    assert _bad(query_body, {"entities": ENTITIES}) == "type"


@pytest.mark.parametrize("body", [None, [], "x", 3, [{"type": "Query"}]])
def test_query_bodies_are_objects(body):
    assert _bad(query_body, body) == "body"


@pytest.mark.parametrize(
    "parameter, extra",
    [
        ("type", {"type": "Foo"}), ("type", {"type": ["Query"]}),
        ("limit", {"limit": 1}), ("@context", {"@context": {}}), ("datasetId", {"datasetId": [1]}), ("lang", {"lang": "es"}),
        ("q\x00", {"q\x00": "a==1"}),
        ("entities", {"entities": []}), ("entities", {"entities": ENTITIES * 21}), ("entities", {"entities": {}}), ("entities", {"entities": None}),
        ("entities", {"entities": ["x"]}), ("entities", {"entities": [{}]}), ("entities", {"entities": [{"type": "T", "q": "a"}]}),
        ("entities", {"entities": [{"type": ["T"]}]}), ("entities", {"entities": [{"type": "T,U"}]}), ("entities", {"entities": [{"id": "x"}]}),
        ("entities", {"entities": [{"idPattern": "("}]}), ("entities", {"entities": [{"type": "T", "id": ["urn:a:b"]}]}),
        ("entities", {"entities": [{"type": "T", "id": "urn:a:b", "idPattern": ".*"}]}),
        ("attrs", {"attrs": []}), ("attrs", {"attrs": "a"}), ("attrs", {"attrs": [1]}), ("attrs", {"attrs": ["a"] * 51}), ("attrs", {"attrs": ["a,b"]}),
        ("q", {"q": "a>true"}), ("q", {"q": 3}), ("q", {"q": None}),
        ("geoQ", {"geoQ": []}), ("geoQ", {"geoQ": {}}), ("geoQ", {"geoQ": {**GEO_Q, "x": 1}}), ("geoQ", {"geoQ": {**GEO_Q, "coordinates": "[0,0]"}}),
        ("geoQ", {"geoQ": {**GEO_Q, "georel": "near"}}), ("geoQ", {"geoQ": {**GEO_Q, "geometry": ["Point"]}}),
        ("geoQ", {"geoQ": {**GEO_Q, "coordinates": [float("inf"), 0]}}), ("geoQ", {"geoQ": {**GEO_Q, "geoproperty": "a b"}}),
        ("geoQ", {"geoQ": {**GEO_Q, "geometry": "LineString", "coordinates": [[0, 0]] * 2000}}),
    ],
)
def test_malformed_query_bodies(parameter, extra):
    assert _bad(query_body, {"type": "Query", "entities": ENTITIES, **extra}) == parameter


def test_deep_geo_bodies_are_refused():
    deep: object = [0, 0]
    for _ in range(3000):
        deep = [deep]
    assert _bad(query_body, {"type": "Query", "geoQ": {**GEO_Q, "coordinates": deep}}) == "geoQ"


@pytest.mark.parametrize(
    "path",
    ["urn:ngsi-ld:Device:1", "urn:ngsi-ld:Device:1/attrs/name", "https://example.org/d/1", "urn:ngsi-ld:Device:1/attrs/urn%3Aexample%23n"],
)
def test_entity_paths_are_accepted(path):
    assert valid_entity_path(path) is True


@pytest.mark.parametrize("path", ["", "/", "?type=T", "a?b", "#", "a#b", "\x00", "a\x00", "%00", "%2500", "a\n", "a%0A", "%" + "25" * 8])
def test_other_entity_paths_are_refused(path):
    assert valid_entity_path(path) is False
