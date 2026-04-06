"""Pytest configuration: stub out Blender-only modules so pure-Python tests run outside Blender."""

import sys
import types


def _make_bpy_stub() -> types.ModuleType:
    bpy = types.ModuleType("bpy")

    # Minimal stubs for sub-modules accessed at import time
    for sub in ("types", "props", "utils", "app", "data", "context", "ops", "path"):
        mod = types.ModuleType(f"bpy.{sub}")
        setattr(bpy, sub, mod)

    return bpy


if "bpy" not in sys.modules:
    sys.modules["bpy"] = _make_bpy_stub()

# Also stub blf, gpu, gpu_extras used by legacy ui at import time
for _mod_name in ("blf", "gpu", "gpu_extras", "gpu_extras.batch"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)
