"""Grammar and parameters of the entity reads the gateway forwards."""

import time

import pytest
from ngsi_read_guard import valid_filter

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
