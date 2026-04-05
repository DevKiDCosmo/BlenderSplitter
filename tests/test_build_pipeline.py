import json
from pathlib import Path


def test_version_file_matches_bl_info():
    root = Path(__file__).resolve().parent.parent
    version_file = root / "VERSION"
    assert version_file.exists(), "VERSION file must exist"
    v = version_file.read_text(encoding="utf-8").strip()
    # _bl_info_ is in __init__.py; import it and compare major.minor.patch
    import importlib.util
    spec = importlib.util.spec_from_file_location("mod", str(root / "__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    bi = getattr(mod, "bl_info", {})
    tup = bi.get("version", None)
    if tup and isinstance(tup, tuple) and len(tup) >= 3:
        assert v == f"{tup[0]}.{tup[1]}.{tup[2]}", "VERSION must match bl_info.version"
