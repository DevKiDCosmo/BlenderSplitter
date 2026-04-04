import base64
from dataclasses import dataclass

from .robust_protocol import (
    MSG_TILE_RESULT,
    MSG_TILE_RESULT_CHUNK,
    MSG_TILE_RESULT_COMPLETE,
    MSG_TILE_RESULT_START,
)


@dataclass
class ChunkConfig:
    chunk_size: int = 512 * 1024
    inline_limit: int = 1024 * 1024


class TileResultChunker:
    def __init__(self, config: ChunkConfig | None = None):
        self.config = config or ChunkConfig()

    def should_chunk(self, png_b64: str) -> bool:
        return len(png_b64 or "") > self.config.inline_limit

    def chunk_messages(self, result: dict, transfer_id: str) -> list[dict]:
        raw = base64.b64decode(result["png_base64"])
        chunk_size = self.config.chunk_size
        total_chunks = (len(raw) + chunk_size - 1) // chunk_size

        messages = [
            {
                "type": MSG_TILE_RESULT_START,
                "transfer_id": transfer_id,
                "tile_id": result.get("tile_id"),
                "worker_id": result.get("worker_id"),
                "tile": result.get("tile"),
                "total_size": len(raw),
                "total_chunks": total_chunks,
            }
        ]

        for idx in range(total_chunks):
            start = idx * chunk_size
            end = min(len(raw), start + chunk_size)
            messages.append(
                {
                    "type": MSG_TILE_RESULT_CHUNK,
                    "transfer_id": transfer_id,
                    "index": idx,
                    "data": base64.b64encode(raw[start:end]).decode("ascii"),
                }
            )

        messages.append(
            {
                "type": MSG_TILE_RESULT_COMPLETE,
                "transfer_id": transfer_id,
                "tile_id": result.get("tile_id"),
                "worker_id": result.get("worker_id"),
                "tile": result.get("tile"),
                "ok": bool(result.get("ok", True)),
            }
        )
        return messages


class TileResultAssembler:
    def __init__(self):
        self._incoming: dict[str, dict] = {}

    def handle(self, msg: dict) -> dict | None:
        msg_type = msg.get("type")

        if msg_type == MSG_TILE_RESULT:
            return msg

        if msg_type == MSG_TILE_RESULT_START:
            transfer_id = msg.get("transfer_id")
            if not transfer_id:
                return None
            self._incoming[transfer_id] = {
                "tile_id": msg.get("tile_id"),
                "worker_id": msg.get("worker_id"),
                "tile": msg.get("tile"),
                "total_chunks": int(msg.get("total_chunks", 0)),
                "chunks": {},
            }
            return None

        if msg_type == MSG_TILE_RESULT_CHUNK:
            transfer_id = msg.get("transfer_id")
            entry = self._incoming.get(transfer_id)
            if entry is None:
                return None
            try:
                entry["chunks"][int(msg.get("index", 0))] = msg.get("data", "")
            except Exception:
                return None
            return None

        if msg_type == MSG_TILE_RESULT_COMPLETE:
            transfer_id = msg.get("transfer_id")
            entry = self._incoming.pop(transfer_id, None)
            if entry is None:
                return None
            ordered = [entry["chunks"].get(i, "") for i in range(entry["total_chunks"])]
            return {
                "type": MSG_TILE_RESULT,
                "tile_id": entry.get("tile_id"),
                "worker_id": entry.get("worker_id"),
                "tile": entry.get("tile"),
                "ok": bool(msg.get("ok", True)),
                "png_base64": "".join(ordered),
            }

        return None
