import json

MSG_REGISTER_WORKER = "register_worker"
MSG_REGISTERED = "registered"
MSG_RENDER_TILE = "render_tile"
MSG_RENDER_ABORT = "render_abort"
MSG_TILE_RESULT = "tile_result"
MSG_TILE_RESULT_START = "tile_result_start"
MSG_TILE_RESULT_CHUNK = "tile_result_chunk"
MSG_TILE_RESULT_COMPLETE = "tile_result_complete"
MSG_HEARTBEAT = "heartbeat"
MSG_PING = "ping"
MSG_INTEGRITY_PROBE = "integrity_probe"
MSG_INTEGRITY_RESULT = "integrity_probe_result"
MSG_PROJECT_SYNC_START = "project_sync_start"
MSG_PROJECT_SYNC_CHUNK = "project_sync_chunk"
MSG_PROJECT_SYNC_COMPLETE = "project_sync_complete"
MSG_PROJECT_SYNC_ACK = "project_sync_ack"

MSG_CLEAN_BLEND = "clean_blend"


def dumps(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
