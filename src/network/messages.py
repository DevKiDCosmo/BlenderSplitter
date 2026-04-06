"""Message and serialization helpers for network boundaries."""

from __future__ import annotations

import json
import logging

_log = logging.getLogger(__name__)

DISCOVERY_REQUEST_MAGIC = "BLENDER_SPLITTER_DISCOVERY_V3"
DISCOVERY_RESPONSE_MAGIC = "BLENDER_SPLITTER_SERVER_V3"


def normalize_json(data: dict[str, object]) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def parse_json(payload: str) -> dict[str, object]:
    """Parse a JSON string into a dict.

    Returns an empty dict on blank input or malformed JSON rather than
    raising, so callers can handle bad network messages gracefully.
    """
    if not payload or not payload.strip():
        return {}
    try:
        result = json.loads(payload)
        if isinstance(result, dict):
            return result
        # Wrap non-dict JSON values so the return type is always dict.
        return {"value": result}
    except json.JSONDecodeError as exc:
        _log.debug("parse_json: malformed payload ignored (%s)", exc)
        return {}
