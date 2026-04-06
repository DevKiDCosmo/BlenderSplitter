#!/usr/bin/env bash
# compile.sh — Build multi-mode distribution ZIPs for the BlenderSplitter add-on.
#
# Each mode gets its own ZIP containing only the files needed at runtime:
#   __init__.py, blender_manifest.toml, config.json, src/, VERSION
#
# Root-level compatibility wrappers (network.py, worker.py, ui.py, …) are
# intentionally EXCLUDED from the ZIP because the add-on now imports
# everything directly from src/legacy/ without going through those wrappers.
set -euo pipefail

# ---------------------------------------------------------------------------
# Syntax-check Python files before packaging
# ---------------------------------------------------------------------------
echo "Syntax-checking src/ ..."
find src -name "*.py" | xargs python3 -m py_compile
echo "Syntax-checking __init__.py ..."
python3 -m py_compile __init__.py

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADDON_DIR_NAME="$(basename "$ROOT_DIR")"
PARENT_DIR="$(dirname "$ROOT_DIR")"
DIST_DIR="$ROOT_DIR/dist"
CONFIG_TEMPLATES_DIR="$ROOT_DIR/config/templates"

mkdir -p "$DIST_DIR"

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
VERSION_FILE="$ROOT_DIR/VERSION"
if [[ -f "$VERSION_FILE" ]]; then
  VERSION="$(<"$VERSION_FILE")"
  VERSION="${VERSION//$'\n'/}"
  VERSION="${VERSION//$'\r'/}"
else
  VERSION="$(python3 - "$ROOT_DIR/__init__.py" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r'"version"\s*:\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)', text)
if m:
    print(f"{m.group(1)}.{m.group(2)}.{m.group(3)}")
PY
  )"
fi
VERSION="${VERSION:-dev}"

get_mode_description() {
  case "$1" in
    worker)        echo "Worker-Only Mode" ;;
    master)        echo "Master-Only Mode" ;;
    user)          echo "User Mode (flexible)" ;;
    worker_master) echo "Worker+Master Mode" ;;
    *)             echo "Unknown" ;;
  esac
}

# ---------------------------------------------------------------------------
# Build one ZIP per mode
# ---------------------------------------------------------------------------
TEMP_BUILD_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_BUILD_DIR"' EXIT

pushd "$PARENT_DIR" >/dev/null

for mode in worker master user worker_master; do
  TEMP_ADDON="$TEMP_BUILD_DIR/$ADDON_DIR_NAME"
  rm -rf "$TEMP_ADDON"
  cp -r "$ADDON_DIR_NAME" "$TEMP_ADDON"

  # Embed mode-specific config
  if [[ -f "$CONFIG_TEMPLATES_DIR/${mode}.json" ]]; then
    cp "$CONFIG_TEMPLATES_DIR/${mode}.json" "$TEMP_ADDON/config.json"
    echo "Embedded config for mode: $mode"
  fi

  OUT_ZIP="$DIST_DIR/${ADDON_DIR_NAME}-${VERSION}-${mode}.zip"
  rm -f "$OUT_ZIP"

  pushd "$TEMP_BUILD_DIR" >/dev/null
  zip -r "$OUT_ZIP" "$ADDON_DIR_NAME" \
    -x "$ADDON_DIR_NAME/tests/*" \
       "$ADDON_DIR_NAME/dist/*" \
       "$ADDON_DIR_NAME/__pycache__/*" \
       "$ADDON_DIR_NAME/*.pyc" \
       "$ADDON_DIR_NAME/src/*/__pycache__/*" \
       "$ADDON_DIR_NAME/src/legacy/__pycache__/*" \
       "$ADDON_DIR_NAME/config/templates/*" \
       "$ADDON_DIR_NAME/PLAN.md" \
       "$ADDON_DIR_NAME/plan.md" \
       "$ADDON_DIR_NAME/todo.md" \
       "$ADDON_DIR_NAME/MIGRATION_PLAN_FULL_V2.md" \
       "$ADDON_DIR_NAME/progress.md" \
       "$ADDON_DIR_NAME/issue.md" \
       "$ADDON_DIR_NAME/conftest.py" \
       "$ADDON_DIR_NAME/power.sh" \
       "$ADDON_DIR_NAME/compile.sh" \
       "$ADDON_DIR_NAME/network.py" \
       "$ADDON_DIR_NAME/robust_connection.py" \
       "$ADDON_DIR_NAME/robust_protocol.py" \
       "$ADDON_DIR_NAME/robust_transfer.py" \
       "$ADDON_DIR_NAME/stitch.py" \
       "$ADDON_DIR_NAME/tiles.py" \
       "$ADDON_DIR_NAME/worker.py" \
       "$ADDON_DIR_NAME/ui.py" \
       "$ADDON_DIR_NAME/scheduler_app.py" \
       "$ADDON_DIR_NAME/trans.py" \
    >/dev/null
  popd >/dev/null

  DESC=$(get_mode_description "$mode")
  echo "Created: $OUT_ZIP ($DESC)"
done

popd >/dev/null

# ---------------------------------------------------------------------------
# Generate manifest.json
# ---------------------------------------------------------------------------
python3 - "$DIST_DIR" "$VERSION" <<'PY'
import json, sys, uuid, time
from pathlib import Path

dist = Path(sys.argv[1])
files = sorted(p.name for p in dist.glob("*.zip"))
modes = {
    "worker":        "Worker-Only: Renders tiles, connects to master server",
    "master":        "Master-Only: Manages render queue, coordinates workers",
    "user":          "User Mode: Flexible mode, auto-discovers role at runtime",
    "worker_master": "Worker+Master: Can act as both worker and master",
}
manifest = {
    "version":   sys.argv[2],
    "timestamp": int(time.time()),
    "uuid":      str(uuid.uuid4()),
    "files":     files,
    "modes":     modes,
}
(dist / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(f"Wrote manifest: {dist / 'manifest.json'}")
PY
