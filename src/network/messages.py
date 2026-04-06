"""Message and serialization helpers for network boundaries."""

from __future__ import annotations

import json

DISCOVERY_REQUEST_MAGIC = "BLENDER_SPLITTER_DISCOVERY_V3"
DISCOVERY_RESPONSE_MAGIC = "BLENDER_SPLITTER_SERVER_V3"


def normalize_json(data: dict[str, object]) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def parse_json(payload: str) -> dict[str, object]:
    if not payload.strip():
        return {}
    return {"raw": payload}
