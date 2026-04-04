import asyncio
import base64
import importlib
import os
import queue
import random
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid

import bpy

from .network import DiscoveryResponder, discover_server, json_dumps
from .robust_connection import ReconnectController, ReconnectPolicy
from .robust_protocol import (
    MSG_HEARTBEAT,
    MSG_INTEGRITY_PROBE,
    MSG_INTEGRITY_RESULT,
    MSG_PING,
    MSG_REGISTER_WORKER,
    MSG_REGISTERED,
    MSG_RENDER_TILE,
    MSG_TILE_RESULT,
    MSG_TILE_RESULT_CHUNK,
    MSG_TILE_RESULT_COMPLETE,
    MSG_TILE_RESULT_START,
)
from .robust_transfer import ChunkConfig, TileResultAssembler, TileResultChunker
from .stitch import stitch_tiles
from .tiles import collect_render_signature, generate_tiles, grid_for_worker_count, overlap_pixels

try:
    import websockets
except ImportError:
    websockets = None


def _load_websockets_module() -> bool:
    global websockets
    try:
        websockets = importlib.import_module("websockets")
        return True
    except Exception:
        websockets = None
        return False


def _safe_scene():
    return bpy.context.scene if bpy.context else None


def _local_ip() -> str:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        sock.close()


class DistributedRenderManager:
    def __init__(self):
        self.node_id = str(uuid.uuid4())
        self.status = "Idle"
        self.role = "unassigned"
        self.started = False
        self.last_error = ""
        self.last_integrity = "n/a"
        self.last_duration_seconds = 0.0

        self.server_host = ""
        self.server_port = 8765
        self.discovery_port = 8766

        self.overlap_percent = 3.0
        self.max_retries = 3
        self.auto_sync_project = False
        self.show_render_window = True
        self.server_render_tiles = True
        self.output_dir = ""

        self.connected_workers: dict[str, dict] = {}
        self.pending_jobs: dict[str, dict] = {}
        self.completed_jobs: dict[str, dict] = {}
        self.expected_jobs = 0
        self.render_plan: list[dict] = []
        self.current_render_config: dict | None = None
        self.current_render_output = ""
        self.render_start_time = 0.0
        self.job_owner: dict[str, str] = {}
        self.job_attempts: dict[str, int] = {}

        self.current_output_root = ""
        self.current_master_dir = ""
        self.current_raw_splits_dir = ""

        self.sync_active = False
        self.sync_progress = {}
        self.sync_total_bytes = 0
        self.sync_start_time = 0.0
        self.incoming_project_progress = {}
        self.received_project_dir = ""
        self.pending_project_load = None

        self.transfer_stats = {
            "tiles_inline": 0,
            "tiles_chunked": 0,
            "chunk_messages": 0,
        }

        self._task_queue: queue.Queue = queue.Queue()
        self._result_queue: queue.Queue = queue.Queue()
        self._progress_queue: queue.Queue = queue.Queue()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server = None
        self._discovery: DiscoveryResponder | None = None
        self._worker_socket = None
        self._stop_event = threading.Event()
        self._loop_ready = threading.Event()
        self._server_ready = threading.Event()
        self._timer_registered = False

        self._tile_chunker = TileResultChunker(ChunkConfig(chunk_size=512 * 1024, inline_limit=1024 * 1024))
        self._tile_assembler = TileResultAssembler()
        self._reconnect = ReconnectController(ReconnectPolicy(rediscover_after=3, self_host_after=8, max_sleep=3.0))

    def configure(
        self,
        host,
        server_port,
        discovery_port,
        overlap_percent=3.0,
        max_retries=3,
        auto_sync_project=False,
        show_render_window=True,
        server_render_tiles=True,
        output_dir="",
    ):
        self.server_host = str(host)
        self.server_port = int(server_port)
        self.discovery_port = int(discovery_port)
        self.overlap_percent = float(overlap_percent)
        self.max_retries = max(1, int(max_retries))
        self.auto_sync_project = bool(auto_sync_project)
        self.show_render_window = bool(show_render_window)
        self.server_render_tiles = bool(server_render_tiles)
        self.output_dir = bpy.path.abspath(output_dir) if output_dir else ""

    def start(self):
        if self.started:
            return

        if websockets is None:
            self.status = "websockets fehlt, versuche Installation"
            if not self.auto_install_requirements(only_modules=["websockets"]):
                if not _load_websockets_module():
                    self.last_error = "websockets nicht verfügbar"
                    self.status = self.last_error
                    return
            if not _load_websockets_module():
                self.last_error = "websockets konnte nicht geladen werden"
                self.status = self.last_error
                return

        self.started = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()
        self._ensure_timer()

    def stop(self):
        self._stop_event.set()
        self.started = False
        self.status = "Stopped"
        if self._loop:
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown_async(), self._loop)
            except RuntimeError:
                pass

    def force_start_server(self):
        if not self.started:
            self.start()
        if not self._loop_ready.wait(timeout=3.0):
            self.last_error = "Loop nicht bereit"
            self.status = self.last_error
            return False
        try:
            fut = asyncio.run_coroutine_threadsafe(self._force_server_async(), self._loop)
            _ = fut.result(timeout=8.0)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.status = f"Force server fehlgeschlagen: {exc}"
            return False

    async def _force_server_async(self):
        if self._worker_socket is not None:
            try:
                await self._worker_socket.close()
            except Exception:
                pass
            self._worker_socket = None
        await self._start_server()

    def _ensure_timer(self):
        if self._timer_registered:
            return

        def _tick():
            self.process_main_thread_queues()
            return 0.1 if self.started else None

        bpy.app.timers.register(_tick, persistent=True)
        self._timer_registered = True

    def process_main_thread_queues(self):
        while not self._progress_queue.empty():
            self.status = self._progress_queue.get_nowait()

        while not self._task_queue.empty():
            item = self._task_queue.get_nowait()
            if item.get("type") == "render_tile":
                result = self._render_tile_local(item["payload"])
                if item.get("reply_to") == "server_local":
                    self._result_queue.put(result)
                else:
                    self._send_result_to_server_async(result)

        while not self._result_queue.empty():
            self._consume_tile_result(self._result_queue.get_nowait())

    def _run_event_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_until_complete(self._auto_connect_or_host())
        self._loop.run_until_complete(self._main_loop())

    async def _main_loop(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(0.2)
        await self._shutdown_async()

    async def _auto_connect_or_host(self):
        self.status = "Suche Server..."
        for attempt in range(6):
            found = discover_server(self.discovery_port, timeout=1.5)
            if found:
                host, port = found
                self.role = "worker"
                self.status = f"Server gefunden: {host}:{port}"
                await self._connect_as_worker(host, port)
                return
            self.status = f"Suche Server ({attempt + 1}/6)"
            await asyncio.sleep(0.5 + random.random() * 0.4)

        await asyncio.sleep(0.4 + random.random() * 1.2)
        found = discover_server(self.discovery_port, timeout=1.5)
        if found:
            host, port = found
            self.role = "worker"
            self.status = f"Spät gefunden: {host}:{port}"
            await self._connect_as_worker(host, port)
            return

        self.status = "Kein Server gefunden, starte lokal"
        await self._start_server()

    async def _shutdown_async(self):
        self._server_ready.clear()
        if self._worker_socket is not None:
            try:
                await self._worker_socket.close()
            except Exception:
                pass
            self._worker_socket = None

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self._discovery is not None:
            self._discovery.stop()
            self._discovery = None

    async def _start_server(self):
        if self._server is not None:
            self._server_ready.set()
            self.status = f"Server aktiv auf {self.server_host}:{self.server_port}"
            return

        self.role = "server"
        self.connected_workers = {}
        self.pending_jobs = {}
        self.completed_jobs = {}
        self.job_owner = {}
        self.job_attempts = {}
        self.expected_jobs = 0

        self.server_host = _local_ip()
        self._server = await websockets.serve(
            self._handle_worker_socket,
            "0.0.0.0",
            self.server_port,
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
        )
        self._discovery = DiscoveryResponder(self.discovery_port, self.server_port)
        self._discovery.start()
        self._server_ready.set()
        self.status = f"Server aktiv auf {self.server_host}:{self.server_port}"

    async def _connect_as_worker(self, host, port):
        host = str(host)
        port = int(port)
        url = f"ws://{host}:{port}"
        self._reconnect.reset()

        while not self._stop_event.is_set():
            try:
                async with websockets.connect(url, ping_interval=60, ping_timeout=120, max_size=None, open_timeout=5) as ws:
                    self._reconnect.reset()
                    self._worker_socket = ws
                    self.server_host = host
                    self.server_port = port
                    self.status = f"Verbunden als Worker: {host}:{port}"

                    await ws.send(
                        json_dumps(
                            {
                                "type": MSG_REGISTER_WORKER,
                                "node_id": self.node_id,
                                "app": bpy.app.version_string,
                            }
                        )
                    )

                    async for raw in ws:
                        if isinstance(raw, (bytes, bytearray)):
                            continue
                        msg = __import__("json").loads(raw)
                        await self._handle_worker_message(ws, msg)
            except Exception as exc:
                err = str(exc)
                self.last_error = err
                self._reconnect.on_failure()

                if "Errno 61" in err or "Connect call failed" in err:
                    self.status = f"Server nicht erreichbar ({host}:{port}), suche neu..."
                else:
                    self.status = f"Reconnect: {err}"

                if self._reconnect.should_rediscover():
                    found = discover_server(self.discovery_port, timeout=1.5)
                    if found:
                        host, port = str(found[0]), int(found[1])
                        url = f"ws://{host}:{port}"
                        self.status = f"Neuer Server gefunden: {host}:{port}"
                        self._reconnect.reset()

                if self._reconnect.should_self_host():
                    self.status = "Kein Server erreichbar, starte lokal"
                    await self._start_server()
                    return

                await asyncio.sleep(self._reconnect.sleep_seconds())

    async def _handle_worker_socket(self, websocket):
        worker_id = None
        try:
            async for raw in websocket:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                msg = __import__("json").loads(raw)
                msg_type = msg.get("type")

                if msg_type == MSG_REGISTER_WORKER:
                    worker_id = msg.get("node_id") or str(uuid.uuid4())
                    self.connected_workers[worker_id] = {
                        "socket": websocket,
                        "last_seen": time.time(),
                        "app": msg.get("app", "unknown"),
                    }
                    await websocket.send(json_dumps({"type": MSG_REGISTERED, "node_id": worker_id}))
                    self.status = f"Worker verbunden: {len(self.connected_workers)}"
                    continue

                if msg_type in (MSG_TILE_RESULT, MSG_TILE_RESULT_START, MSG_TILE_RESULT_CHUNK, MSG_TILE_RESULT_COMPLETE):
                    assembled = self._tile_assembler.handle(msg)
                    if assembled:
                        self._result_queue.put(assembled)
                    continue

                if msg_type == MSG_INTEGRITY_RESULT:
                    continue

                if msg_type == MSG_HEARTBEAT and worker_id in self.connected_workers:
                    self.connected_workers[worker_id]["last_seen"] = time.time()
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            if worker_id and worker_id in self.connected_workers:
                self._reassign_jobs_from_worker(worker_id)
                del self.connected_workers[worker_id]
                self.status = f"Worker getrennt: {len(self.connected_workers)}"

    async def _handle_worker_message(self, websocket, msg):
        msg_type = msg.get("type")

        if msg_type == MSG_REGISTERED:
            self.status = "Worker registriert"
            return

        if msg_type == MSG_RENDER_TILE:
            self._task_queue.put({"type": "render_tile", "payload": msg})
            return

        if msg_type == MSG_INTEGRITY_PROBE:
            scene = _safe_scene()
            local_sig = None
            if scene is not None:
                _, local_sig = collect_render_signature(scene)
            await websocket.send(
                json_dumps(
                    {
                        "type": MSG_INTEGRITY_RESULT,
                        "worker_id": self.node_id,
                        "ok": local_sig == msg.get("render_signature"),
                        "local_signature": local_sig,
                        "expected_signature": msg.get("render_signature"),
                    }
                )
            )
            return

        if msg_type == MSG_PING:
            await websocket.send(json_dumps({"type": MSG_HEARTBEAT, "node_id": self.node_id}))
            return

    def _render_tile_local(self, payload: dict) -> dict:
        scene = _safe_scene()
        if scene is None:
            return {"type": MSG_TILE_RESULT, "tile_id": payload.get("tile_id"), "ok": False, "error": "Keine Szene"}

        _, local_sig = collect_render_signature(scene)
        expected_sig = payload.get("render_signature")
        if expected_sig != local_sig:
            self.last_integrity = "failed"
            return {
                "type": MSG_TILE_RESULT,
                "tile_id": payload.get("tile_id"),
                "ok": False,
                "error": "Integritätsprüfung fehlgeschlagen",
            }

        self.last_integrity = "ok"
        tile = payload["tile"]
        out_path = self._render_tile_to_path(scene, tile)

        with open(out_path, "rb") as fh:
            png_b64 = base64.b64encode(fh.read()).decode("ascii")

        return {
            "type": MSG_TILE_RESULT,
            "tile_id": payload["tile_id"],
            "ok": True,
            "worker_id": self.node_id,
            "tile": tile,
            "png_base64": png_b64,
        }

    def _render_tile_to_path(self, scene, tile: dict) -> str:
        render = scene.render
        original = {
            "use_border": render.use_border,
            "use_crop_to_border": render.use_crop_to_border,
            "border_min_x": render.border_min_x,
            "border_max_x": render.border_max_x,
            "border_min_y": render.border_min_y,
            "border_max_y": render.border_max_y,
            "filepath": render.filepath,
        }

        width = max(1, int(render.resolution_x * (render.resolution_percentage / 100.0)))
        height = max(1, int(render.resolution_y * (render.resolution_percentage / 100.0)))

        render.use_border = True
        render.use_crop_to_border = True
        render.border_min_x = tile["min_x"] / width
        render.border_max_x = tile["max_x"] / width
        render.border_min_y = tile["min_y"] / height
        render.border_max_y = tile["max_y"] / height

        tmp_dir = os.path.join(tempfile.gettempdir(), "blender_splitter_tiles_v3")
        os.makedirs(tmp_dir, exist_ok=True)
        path = os.path.join(tmp_dir, f"tile_{tile['id']}_{uuid.uuid4().hex}.png")
        render.filepath = path

        try:
            bpy.ops.render.render(write_still=True, use_viewport=False)
        finally:
            render.use_border = original["use_border"]
            render.use_crop_to_border = original["use_crop_to_border"]
            render.border_min_x = original["border_min_x"]
            render.border_max_x = original["border_max_x"]
            render.border_min_y = original["border_min_y"]
            render.border_max_y = original["border_max_y"]
            render.filepath = original["filepath"]

        return path

    def _send_result_to_server_async(self, result: dict):
        if self._loop is None or self._worker_socket is None:
            return

        async def _send():
            try:
                if not result.get("ok") or "png_base64" not in result:
                    await self._worker_socket.send(json_dumps(result))
                    return

                png_b64 = result.get("png_base64") or ""
                if not self._tile_chunker.should_chunk(png_b64):
                    self.transfer_stats["tiles_inline"] += 1
                    await self._worker_socket.send(json_dumps(result))
                    return

                transfer_id = uuid.uuid4().hex
                msgs = self._tile_chunker.chunk_messages(result, transfer_id=transfer_id)
                self.transfer_stats["tiles_chunked"] += 1
                self.transfer_stats["chunk_messages"] += len(msgs)
                for msg in msgs:
                    await self._worker_socket.send(json_dumps(msg))
            except Exception as exc:
                self.last_error = str(exc)

        asyncio.run_coroutine_threadsafe(_send(), self._loop)

    def _prepare_output_dirs(self, output_file: str):
        base = self.output_dir.strip() if self.output_dir else ""
        if not base:
            base = os.path.dirname(output_file) if output_file else tempfile.gettempdir()
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        run_root = os.path.join(base, f"blendersplitter_{timestamp}")
        self.current_output_root = run_root
        self.current_master_dir = os.path.join(run_root, "master")
        self.current_raw_splits_dir = os.path.join(run_root, "raw-splits")
        os.makedirs(self.current_master_dir, exist_ok=True)
        os.makedirs(self.current_raw_splits_dir, exist_ok=True)

    def start_distributed_render(self):
        if self.role != "server":
            self.status = "Starte Server vor Render"
            if not self.force_start_server():
                self.last_error = self.status
                return False

        scene = _safe_scene()
        if scene is None:
            self.last_error = "Keine aktive Szene"
            self.status = self.last_error
            return False

        if self.show_render_window:
            try:
                bpy.ops.wm.window_new()
            except Exception:
                pass

        render = scene.render
        final_width = max(1, int(render.resolution_x * (render.resolution_percentage / 100.0)))
        final_height = max(1, int(render.resolution_y * (render.resolution_percentage / 100.0)))
        _, signature = collect_render_signature(scene)

        workers = list(self.connected_workers.keys())
        node_count = len(workers) + (1 if self.server_render_tiles else 0)
        grid_x, grid_y = grid_for_worker_count(max(1, node_count))
        overlap_px = overlap_pixels(final_width, final_height, self.overlap_percent)
        tiles = generate_tiles(final_width, final_height, grid_x, grid_y, overlap=overlap_px)

        output = bpy.path.abspath(render.filepath)
        if os.path.isdir(output):
            output = os.path.join(output, "distributed_render.png")
        elif not os.path.splitext(output)[1]:
            output = output + ".png"

        self._prepare_output_dirs(output)
        output = os.path.join(self.current_master_dir, os.path.basename(output))

        self.current_render_output = output
        self.current_render_config = {
            "render_signature": signature,
            "resolution_x": final_width,
            "resolution_y": final_height,
        }
        self.pending_jobs = {}
        self.completed_jobs = {}
        self.job_owner = {}
        self.job_attempts = {}
        self.render_plan = []
        self.expected_jobs = len(tiles)
        self.render_start_time = time.time()

        targets = []
        if self.server_render_tiles:
            targets.append("MASTER")
        targets.extend(workers)
        if not targets:
            self.last_error = "Keine Ziele verfügbar"
            self.status = self.last_error
            return False

        for idx, tile in enumerate(tiles):
            target = targets[idx % len(targets)]
            job = {
                "type": MSG_RENDER_TILE,
                "tile_id": tile["id"],
                "tile": tile,
                "render_signature": signature,
            }
            self.pending_jobs[tile["id"]] = job
            self.job_attempts[tile["id"]] = 0
            self.job_owner[tile["id"]] = target
            self.render_plan.append(
                {
                    "tile_id": tile["id"],
                    "target": target,
                    "min_x": tile["min_x"],
                    "max_x": tile["max_x"],
                    "min_y": tile["min_y"],
                    "max_y": tile["max_y"],
                    "core_min_x": tile["core_min_x"],
                    "core_max_x": tile["core_max_x"],
                    "core_min_y": tile["core_min_y"],
                    "core_max_y": tile["core_max_y"],
                }
            )

            if target == "MASTER":
                self._task_queue.put({"type": "render_tile", "payload": job, "reply_to": "server_local"})
            else:
                self._send_job_to_worker(target, job)

        self.status = f"Render gestartet: {len(tiles)} Tiles, {len(targets)} Ziele"
        return True

    def _send_job_to_worker(self, worker_id: str, job: dict):
        info = self.connected_workers.get(worker_id)
        if not info:
            self._reassign_tile(job["tile_id"], f"Worker {worker_id} nicht verfügbar")
            return

        ws = info.get("socket")

        async def _send():
            try:
                await ws.send(json_dumps(job))
            except Exception as exc:
                self._reassign_tile(job["tile_id"], f"Sendefehler: {exc}")

        if self._loop:
            asyncio.run_coroutine_threadsafe(_send(), self._loop)

    def _consume_tile_result(self, message: dict):
        tile_id = message.get("tile_id")
        if tile_id not in self.pending_jobs:
            return

        if not message.get("ok"):
            self._reassign_tile(tile_id, message.get("error", "Unbekannter Fehler"))
            return

        tile = message.get("tile") or {}
        png_data = base64.b64decode(message.get("png_base64", ""))

        raw_dir = self.current_raw_splits_dir or os.path.join(tempfile.gettempdir(), "blender_splitter_received_v3")
        os.makedirs(raw_dir, exist_ok=True)
        path = os.path.join(raw_dir, f"{tile_id}_{uuid.uuid4().hex}.png")
        with open(path, "wb") as fh:
            fh.write(png_data)

        self.completed_jobs[tile_id] = {
            "tile_id": tile_id,
            "path": path,
            "min_x": tile.get("min_x", 0),
            "max_x": tile.get("max_x", 0),
            "min_y": tile.get("min_y", 0),
            "max_y": tile.get("max_y", 0),
            "core_min_x": tile.get("core_min_x", tile.get("min_x", 0)),
            "core_max_x": tile.get("core_max_x", tile.get("max_x", 0)),
            "core_min_y": tile.get("core_min_y", tile.get("min_y", 0)),
            "core_max_y": tile.get("core_max_y", tile.get("max_y", 0)),
        }
        del self.pending_jobs[tile_id]
        self.job_owner.pop(tile_id, None)

        done = len(self.completed_jobs)
        self.status = f"Tiles fertig: {done}/{self.expected_jobs}"
        if done >= self.expected_jobs:
            self._finalize_render()

    def _finalize_render(self):
        if not self.current_render_config:
            return

        try:
            stitch_tiles(
                list(self.completed_jobs.values()),
                self.current_render_config["resolution_x"],
                self.current_render_config["resolution_y"],
                self.current_render_output,
            )
            self.last_duration_seconds = time.time() - self.render_start_time
            self.last_integrity = "ok"
            self.status = f"Render abgeschlossen in {self.last_duration_seconds:.2f}s"
        except Exception as exc:
            self.last_error = f"Stitch fehlgeschlagen: {exc}"
            self.status = self.last_error
            traceback.print_exc()

    def _reassign_jobs_from_worker(self, worker_id: str):
        for tile_id, owner in list(self.job_owner.items()):
            if owner == worker_id and tile_id in self.pending_jobs:
                self._reassign_tile(tile_id, f"Worker {worker_id} getrennt")

    def _reassign_tile(self, tile_id: str, reason: str):
        if tile_id not in self.pending_jobs:
            return

        attempt = self.job_attempts.get(tile_id, 0) + 1
        self.job_attempts[tile_id] = attempt
        job = self.pending_jobs[tile_id]
        prev_owner = self.job_owner.get(tile_id)

        if attempt > self.max_retries:
            self.last_error = f"Tile {tile_id} fehlgeschlagen nach {attempt - 1} Retries: {reason}"
            self.status = self.last_error
            self.last_integrity = "failed"
            return

        candidates = [wid for wid in self.connected_workers.keys() if wid != prev_owner]
        target = random.choice(candidates) if candidates else "MASTER"
        self.job_owner[tile_id] = target

        if target == "MASTER":
            self._task_queue.put({"type": "render_tile", "payload": job, "reply_to": "server_local"})
        else:
            self._send_job_to_worker(target, job)

        self.status = f"Tile {tile_id} neu zugewiesen ({attempt}/{self.max_retries})"

    def run_integrity_check(self, timeout_seconds=5.0):
        if self.role != "server":
            self.last_error = "Integrity Check nur auf Server"
            self.status = self.last_error
            return False

        scene = _safe_scene()
        if scene is None:
            self.last_error = "Keine aktive Szene"
            self.status = self.last_error
            return False

        workers = list(self.connected_workers.keys())
        if not workers:
            self.last_integrity = "ok"
            self.status = "Integrity Check OK (nur Master)"
            return True

        _, signature = collect_render_signature(scene)
        results: dict[str, bool] = {}

        async def _broadcast():
            for worker_id in workers:
                info = self.connected_workers.get(worker_id)
                if not info:
                    results[worker_id] = False
                    continue
                try:
                    await info["socket"].send(json_dumps({"type": MSG_INTEGRITY_PROBE, "render_signature": signature}))
                except Exception:
                    results[worker_id] = False

        if self._loop:
            asyncio.run_coroutine_threadsafe(_broadcast(), self._loop)

        deadline = time.time() + float(timeout_seconds)
        while time.time() < deadline:
            if len(results) >= len(workers):
                break
            time.sleep(0.1)

        ok = all(results.get(w, True) for w in workers)
        self.last_integrity = "ok" if ok else "failed"
        self.status = "Integrity Check OK" if ok else "Integrity Check fehlgeschlagen"
        return ok

    def cancel_render(self):
        if not self.current_render_config:
            self.status = "Kein Render aktiv"
            return False
        self.pending_jobs.clear()
        self.job_owner.clear()
        self.job_attempts.clear()
        self.expected_jobs = 0
        self.current_render_config = None
        self.current_render_output = ""
        self.last_integrity = "aborted"
        self.status = "Render abgebrochen"
        return True

    def kick_all_workers(self):
        ids = list(self.connected_workers.keys())
        for worker_id in ids:
            info = self.connected_workers.get(worker_id)
            ws = info.get("socket") if info else None
            if ws and self._loop:
                try:
                    asyncio.run_coroutine_threadsafe(ws.close(), self._loop)
                except Exception:
                    pass
            self.connected_workers.pop(worker_id, None)
        self.status = f"Alle Worker getrennt ({len(ids)})"
        return True

    def auto_install_requirements(self, only_modules=None):
        required = {
            "websockets": "websockets>=12.0",
            "PIL": "pillow>=10.0.0",
            "numpy": "numpy>=1.26.0",
        }
        if only_modules:
            required = {k: v for k, v in required.items() if k in set(only_modules)}

        missing = []
        for module_name, package_name in required.items():
            try:
                importlib.import_module(module_name)
            except Exception:
                missing.append(package_name)

        if not missing:
            self.status = "Requirements installiert"
            return True

        self.status = f"Installiere: {', '.join(missing)}"
        try:
            subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"], check=False)
            subprocess.run([sys.executable, "-m", "pip", "install", *missing], check=True)
            _load_websockets_module()
            self.status = "Requirements erfolgreich installiert"
            return True
        except Exception as exc:
            self.last_error = f"Install fehlgeschlagen: {exc}"
            self.status = self.last_error
            return False


_MANAGER = DistributedRenderManager()


def manager() -> DistributedRenderManager:
    return _MANAGER
