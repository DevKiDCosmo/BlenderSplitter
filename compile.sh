#!/usr/bin/env bash
#!/usr/bin/env bash
set -euo pipefail

python3 -m py_compile __init__.py network.py robust_connection.py robust_protocol.py robust_transfer.py stitch.py tiles.py worker.py ui.py || true

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADDON_DIR_NAME="$(basename "$ROOT_DIR")"
PARENT_DIR="$(dirname "$ROOT_DIR")"
DIST_DIR="$ROOT_DIR/dist"

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

# Create 4 different zips (core, ui, docs, full)
declare -A EXCLUDES
EXCLUDES[core]="$ADDON_DIR_NAME/tests/* $ADDON_DIR_NAME/PLAN.md $ADDON_DIR_NAME/README.md"
EXCLUDES[ui]="$ADDON_DIR_NAME/tests/* $ADDON_DIR_NAME/README.md $ADDON_DIR_NAME/PLAN.md"
EXCLUDES[docs]="$ADDON_DIR_NAME/__pycache__/* $ADDON_DIR_NAME/*.pyc $ADDON_DIR_NAME/dist/*"
EXCLUDES[full]="$ADDON_DIR_NAME/__pycache__/* $ADDON_DIR_NAME/*.pyc $ADDON_DIR_NAME/dist/*"

pushd "$PARENT_DIR" >/dev/null
for part in core ui docs full; do
  OUT_ZIP="$DIST_DIR/${ADDON_DIR_NAME}-${VERSION}-${part}.zip"
  rm -f "$OUT_ZIP"
  # build exclude args
  EXC_OPTS=()
  for e in ${EXCLUDES[$part]}; do
    EXC_OPTS+=( -x "$e" )
  done
  # shellcheck disable=SC2086
  zip -r "$OUT_ZIP" "$ADDON_DIR_NAME" "${EXC_OPTS[@]}" >/dev/null
  echo "Created: $OUT_ZIP"
done

# Generate manifest.json with metadata (version, timestamp, uuid, files)
PYTHON_MANIFEST=$(cat <<'PY'
import json, sys, uuid, time, glob
from pathlib import Path
dist = Path(sys.argv[1])
files = sorted([p.name for p in dist.glob('*.zip')])
manifest = {
    'version': sys.argv[2],
    'timestamp': int(time.time()),
    'uuid': str(uuid.uuid4()),
    'files': files,
}
print(json.dumps(manifest, indent=2))
PY
)
python3 - <<PYOUT
${PYTHON_MANIFEST}
PYOUT
 > "$DIST_DIR/manifest.json" 2>/dev/null || true

echo "Wrote manifest: $DIST_DIR/manifest.json"
popd >/dev/null
