import pytest

from common.jexl_expressions import validate_jexl_expression


@pytest.mark.parametrize("expr", ["", "val", "r3 * 0.1", "r1 + 273", "100 - r2",
                                  "r1 * 0.1 + 5", "r1 > 100 ? 100 : r1"])
def test_valid(expr):
    assert validate_jexl_expression(expr)[0]


@pytest.mark.parametrize("expr", ["${@r1} * 2", "process.exit()", "r1 ** 2", "a; b",
                                  "r1 > 100 ? 100 : r2"])
def test_invalid(expr):
    assert not validate_jexl_expression(expr)[0]
