"""Pre-seed the pure `common.*` helpers that blueprints import at module level.

Several test files replace `common` with a MagicMock, which is not a package; a submodule
imported afterwards is only found if it is already in `sys.modules`.
"""
import importlib.util
import os
import sys

_COMMON = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "..", "common"))


def seed_real_common(names=("unit_codes", "context_aliases", "jexl_expressions", "csrf_guard",
                                "observation_context", "window_statistics")):
    for name in names:
        key = f"common.{name}"
        if key in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(key, os.path.join(_COMMON, f"{name}.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod  # dataclasses look the module up while it executes
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    # Built on the helpers above, which are in `sys.modules` by now; blueprints import it at
    # module level, so every caller needs it whatever names it asked for.
    key = "common.device_measurement_shape"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            key, os.path.join(_COMMON, "device_measurement_shape.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod  # dataclasses look the module up while it executes
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
