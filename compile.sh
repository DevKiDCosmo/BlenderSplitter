#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADDON_DIR_NAME="$(basename "$ROOT_DIR")"
PARENT_DIR="$(dirname "$ROOT_DIR")"
DIST_DIR="$ROOT_DIR/dist"

mkdir -p "$DIST_DIR"

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
if [[ -z "$VERSION" ]]; then
  VERSION="dev"
fi

OUT_ZIP="$DIST_DIR/${ADDON_DIR_NAME}-${VERSION}.zip"
rm -f "$OUT_ZIP"

pushd "$PARENT_DIR" >/dev/null
zip -r "$OUT_ZIP" "$ADDON_DIR_NAME" \
  -x "$ADDON_DIR_NAME/dist/*" \
     "$ADDON_DIR_NAME/.git/*" \
     "$ADDON_DIR_NAME/__pycache__/*" \
     "$ADDON_DIR_NAME/*.pyc" \
     "$ADDON_DIR_NAME/.DS_Store"
popd >/dev/null

echo "Created: $OUT_ZIP"
