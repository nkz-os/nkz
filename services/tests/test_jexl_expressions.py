import pytest

from common.jexl_expressions import validate_jexl_expression


@pytest.mark.parametrize("expr", ["", "val", "r3 * 0.1", "r1 + 273", "100 - r2",
                                  "r1 * 0.1 + 5", "r1 > 100 ? 100 : r1", "processed * 2"])
def test_valid(expr):
    assert validate_jexl_expression(expr)[0]


@pytest.mark.parametrize("expr", ["${@r1} * 2", "process.exit()", "r1 ** 2", "a; b",
                                  "r1 > 100 ? 100 : r2", "r1 * 1.2.3", "r1 * .", "x.y",
                                  "f(r1)", "r1 * '2'"])
def test_invalid(expr):
    assert not validate_jexl_expression(expr)[0]


def test_variables_restrict_identifiers():
    assert validate_jexl_expression("r3 * 0.1", variables={"r3"})[0]
    assert not validate_jexl_expression("foo * 2", variables={"r3"})[0]
    assert not validate_jexl_expression("r3 > 5 ? 5 : r3 + x", variables={"r3"})[0]


def test_sdm_integration_keeps_val_only():
    assert validate_jexl_expression("val * 0.1", variables={"val"})[0]
    assert not validate_jexl_expression("r1 * 2", variables={"val"})[0]
