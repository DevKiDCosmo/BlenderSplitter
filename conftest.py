"""Root conftest: stub Blender-only modules so pure-Python tests run outside Blender."""

import sys
import types


def _make_bpy_stub() -> types.ModuleType:
    bpy = types.ModuleType("bpy")
    for sub in ("types", "props", "utils", "app", "data", "context", "ops", "path"):
        mod = types.ModuleType(f"bpy.{sub}")
        setattr(bpy, sub, mod)
    # bpy.app.timers stub
    timers = types.ModuleType("bpy.app.timers")
    timers.register = lambda *a, **kw: None  # type: ignore[attr-defined]
    bpy.app.timers = timers  # type: ignore[attr-defined]
    return bpy


if "bpy" not in sys.modules:
    sys.modules["bpy"] = _make_bpy_stub()

for _mod_name in ("blf", "gpu", "gpu_extras", "gpu_extras.batch"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)
