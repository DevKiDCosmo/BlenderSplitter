#!/usr/bin/env bash
set -euo pipefail

python3 -m py_compile __init__.py network.py robust_connection.py robust_protocol.py robust_transfer.py stitch.py tiles.py worker.py ui.py || true

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADDON_DIR_NAME="$(basename "$ROOT_DIR")"
PARENT_DIR="$(dirname "$ROOT_DIR")"
DIST_DIR="$ROOT_DIR/dist"
CONFIG_TEMPLATES_DIR="$ROOT_DIR/config/templates"

mkdir -p "$DIST_DIR"

# Read centralized VERSION file if present, otherwise fall back to existing parsing
VERSION_FILE="$ROOT_DIR/VERSION"
if [[ -f "$VERSION_FILE" ]]; then
  VERSION="$(cat "$VERSION_FILE" | tr -d '\n' | tr -d '\r')"
else
  VERSION="$(python3 - "$ROOT_DIR/__init__.py" <<'PY'
import re
import sys

path = sys.argv[1]
text = open(path, 'r', encoding='utf-8').read()
m = re.search(r'"version"\s*:\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)', text)
if m:
  print(f"{m.group(1)}.{m.group(2)}.{m.group(3)}")
PY
  )"
fi
if [[ -z "$VERSION" ]]; then
  VERSION="dev"
fi

# Create temporary build directory for mode-specific packaging
TEMP_BUILD_DIR=$(mktemp -d)
trap "rm -rf $TEMP_BUILD_DIR" EXIT

# Mode descriptions (POSIX-compatible, no associative arrays)
get_mode_description() {
  case "$1" in
    worker) echo "Worker-Only Mode" ;;
    master) echo "Master-Only Mode" ;;
    user) echo "User Mode (flexible)" ;;
    worker_master) echo "Worker+Master Mode" ;;
    *) echo "Unknown" ;;
  esac
}

# Common exclude patterns for all builds
COMMON_EXCLUDES="$ADDON_DIR_NAME/tests/* $ADDON_DIR_NAME/dist/* $ADDON_DIR_NAME/__pycache__/* $ADDON_DIR_NAME/*.pyc $ADDON_DIR_NAME/PLAN.md"

pushd "$PARENT_DIR" >/dev/null

for mode in worker master user worker_master; do
  # Copy addon to temp dir
  TEMP_ADDON="$TEMP_BUILD_DIR/$ADDON_DIR_NAME"
  rm -rf "$TEMP_ADDON"
  cp -r "$ADDON_DIR_NAME" "$TEMP_ADDON"
  
  # Copy mode-specific config into the temp addon
  if [[ -f "$CONFIG_TEMPLATES_DIR/${mode}.json" ]]; then
    cp "$CONFIG_TEMPLATES_DIR/${mode}.json" "$TEMP_ADDON/config.json"
    echo "Embedded config for mode: $mode"
  fi
  
  OUT_ZIP="$DIST_DIR/${ADDON_DIR_NAME}-${VERSION}-${mode}.zip"
  rm -f "$OUT_ZIP"
  
  # Create zip from temp with exclusions
  pushd "$TEMP_BUILD_DIR" >/dev/null
  zip -r "$OUT_ZIP" "$ADDON_DIR_NAME" \
    -x "$ADDON_DIR_NAME/tests/*" \
        "$ADDON_DIR_NAME/dist/*" \
        "$ADDON_DIR_NAME/__pycache__/*" \
        "$ADDON_DIR_NAME/*.pyc" \
        "$ADDON_DIR_NAME/PLAN.md" \
        "$ADDON_DIR_NAME/config/templates/*" \
    >/dev/null
  popd >/dev/null
  
  DESC=$(get_mode_description "$mode")
  echo "Created: $OUT_ZIP ($DESC)"
done

popd >/dev/null

# Generate manifest.json with metadata (version, timestamp, uuid, files, modes)
PYTHON_MANIFEST=$(cat <<'PY'
import json, sys, uuid, time
from pathlib import Path

dist = Path(sys.argv[1])
files = sorted([p.name for p in dist.glob('*.zip')])
modes = {
    "worker": "Worker-Only: Renders tiles, connects to master server",
    "master": "Master-Only: Manages render queue, coordinates workers",
    "user": "User Mode: Flexible mode, auto-discovers role at runtime",
    "worker_master": "Worker+Master: Can act as both worker and master"
}

manifest = {
    'version': sys.argv[2],
    'timestamp': int(time.time()),
    'uuid': str(uuid.uuid4()),
    'files': files,
    'modes': modes,
}
print(json.dumps(manifest, indent=2))
PY
)
python3 - "$DIST_DIR" "$VERSION" <<PYOUT
${PYTHON_MANIFEST}
PYOUT
 > "$DIST_DIR/manifest.json" 2>/dev/null || true

echo "Wrote manifest: $DIST_DIR/manifest.json"
