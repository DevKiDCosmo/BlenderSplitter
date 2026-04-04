import json

MSG_REGISTER_WORKER = "register_worker"
MSG_REGISTERED = "registered"
MSG_RENDER_TILE = "render_tile"
MSG_TILE_RESULT = "tile_result"
MSG_TILE_RESULT_START = "tile_result_start"
MSG_TILE_RESULT_CHUNK = "tile_result_chunk"
MSG_TILE_RESULT_COMPLETE = "tile_result_complete"
MSG_HEARTBEAT = "heartbeat"
MSG_PING = "ping"
MSG_INTEGRITY_PROBE = "integrity_probe"
MSG_INTEGRITY_RESULT = "integrity_probe_result"


def dumps(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
