import asyncio
import base64
import hashlib
import importlib
import json
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
import zipfile
import shutil

import bpy

from .network import DiscoveryResponder, discover_server, json_dumps
from .robust_connection import ReconnectController, ReconnectPolicy
from .robust_protocol import (
    MSG_HEARTBEAT,
    MSG_INTEGRITY_PROBE,
    MSG_INTEGRITY_RESULT,
    MSG_PING,
    MSG_PROJECT_SYNC_ACK,
    MSG_PROJECT_SYNC_CHUNK,
    MSG_PROJECT_SYNC_COMPLETE,
    MSG_PROJECT_SYNC_START,
    MSG_REGISTER_WORKER,
    MSG_REGISTERED,
    MSG_RENDER_TILE,
    MSG_RENDER_ABORT,
    MSG_TILE_RESULT,
    MSG_TILE_RESULT_CHUNK,
    MSG_TILE_RESULT_COMPLETE,
    MSG_TILE_RESULT_START,
    MSG_CLEAN_BLEND,
)
from .robust_transfer import ChunkConfig, TileResultAssembler, TileResultChunker
from .stitch import stitch_tiles
from .tiles import collect_render_signature, generate_tiles, grid_for_tile_count, tile_target_for_workers, overlap_pixels

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
        self.tile_coefficient = 1
        self.output_dir = ""

        self.connected_workers = {}
        self.pending_jobs = {}
        self.completed_jobs = {}
        self.expected_jobs = 0
        self.render_plan = []
        self.current_render_config = None
        self.current_render_output = ""
        self.render_start_time = 0.0
        self.job_owner = {}
        self.job_attempts = {}
        self.job_queue = []
        self.target_inflight = {}
        self.target_ready_at = {}
        self.dispatch_cooldown_seconds = 1.0
        self.dispatch_targets = []

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
        self.pending_project_load_attempts = 0
        self.pending_project_load_retry_at = 0.0
        self.pending_blank_reset = False
        self.pending_blank_reset_attempts = 0
        self.pending_blank_reset_retry_at = 0.0
        self.pending_sync_context = None
        self.render_abort_requested = False
        self.project_sync_results = {}
        self.integrity_probe_results = {}
        self._incoming_project = None
        self.sync_package_info = {}
        self.worker_sync_state = {}

        self.transfer_stats = {
            "tiles_inline": 0,
            "tiles_chunked": 0,
            "chunk_messages": 0,
        }

        self._task_queue = queue.Queue()
        self._result_queue = queue.Queue()
        self._progress_queue = queue.Queue()

        self._loop = None
        self._thread = None
        self._server = None
        self._discovery = None
        self._worker_socket = None
        self._stop_event = threading.Event()
        self._loop_ready = threading.Event()
        self._server_ready = threading.Event()
        self._timer_registered = False

        self._tile_chunker = TileResultChunker(ChunkConfig(chunk_size=512 * 1024, inline_limit=1024 * 1024))
        self._tile_assembler = TileResultAssembler()
        self._reconnect = ReconnectController(ReconnectPolicy(rediscover_after=3, self_host_after=8, max_sleep=3.0))
        self._worker_render_view_opened = False

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
        tile_coefficient=1,
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
        self.tile_coefficient = max(1, int(tile_coefficient))
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
        if self.pending_blank_reset and time.time() >= float(self.pending_blank_reset_retry_at):
            try:
                try:
                    bpy.ops.wm.read_homefile(use_empty=True)
                except Exception:
                    bpy.ops.wm.read_factory_settings(use_empty=True)
                self._worker_render_view_opened = False
                self.pending_blank_reset = False
                self.pending_blank_reset_attempts = 0
                self.pending_blank_reset_retry_at = 0.0
                self.status = "Blank Instanz geladen"
            except Exception as exc:
                self.pending_blank_reset_attempts += 1
                if self.pending_blank_reset_attempts >= 5:
                    self.pending_blank_reset = False
                    self.last_error = f"Blank-Reset fehlgeschlagen: {exc}"
                    self.status = self.last_error
                else:
                    self.pending_blank_reset_retry_at = time.time() + min(1.5, 0.2 * self.pending_blank_reset_attempts)
        if self.pending_project_load and time.time() >= float(self.pending_project_load_retry_at):
            try:
                blend_file = self.pending_project_load
                self.status = f"Lade Projekt: {os.path.basename(blend_file)}"
                bpy.ops.wm.open_mainfile(filepath=blend_file)
                self._activate_synced_job_context()
                self.status = f"Projekt geladen: {os.path.basename(blend_file)}"
                self.pending_project_load = None
                self.pending_project_load_attempts = 0
                self.pending_project_load_retry_at = 0.0
            except Exception as exc:
                self.pending_project_load_attempts += 1
                self.last_error = f"Projekt laden fehlgeschlagen: {exc}"
                if self.pending_project_load_attempts >= 5:
                    self.status = f"Projekt laden endgültig fehlgeschlagen ({self.pending_project_load_attempts}): {exc}"
                    self.pending_project_load = None
                    self.pending_project_load_attempts = 0
                    self.pending_project_load_retry_at = 0.0
                else:
                    self.pending_project_load_retry_at = time.time() + 2.0
                    self.status = (
                        f"Projekt laden Retry {self.pending_project_load_attempts}/5 in 2s: {os.path.basename(self.pending_project_load)}"
                    )

        while not self._progress_queue.empty():
            self.status = self._progress_queue.get_nowait()

        while not self._task_queue.empty():
            item = self._task_queue.get_nowait()
            if item.get("type") == "render_tile":
                if self.render_abort_requested:
                    continue
                result = self._render_tile_local(item["payload"])
                if item.get("reply_to") == "server_local":
                    self._result_queue.put(result)
                else:
                    self._send_result_to_server_async(result)

        while not self._result_queue.empty():
            self._consume_tile_result(self._result_queue.get_nowait())

        # Try dispatching queued jobs continuously; cooldown is enforced per target.
        if self.role == "server" and self.current_render_config and not self.render_abort_requested:
            for target in list(self.dispatch_targets):
                if self.target_inflight.get(target, 0) <= 0:
                    self._dispatch_next_job_for_target(target)

    def _capture_sync_context(self, scene):
        render = scene.render
        camera_name = scene.camera.name if scene.camera else ""
        context = {
            "scene_name": scene.name,
            "camera_name": camera_name,
            "frame_current": int(scene.frame_current),
            "frame_start": int(scene.frame_start),
            "frame_end": int(scene.frame_end),
            "render_engine": render.engine,
            "resolution_x": int(render.resolution_x),
            "resolution_y": int(render.resolution_y),
            "resolution_percentage": int(render.resolution_percentage),
            "seed": None,
            "cycles_samples": None,
            "eevee_samples": None,
        }

        if hasattr(scene, "cycles") and hasattr(scene.cycles, "seed"):
            try:
                context["seed"] = int(scene.cycles.seed)
            except Exception:
                context["seed"] = None
            if hasattr(scene.cycles, "samples"):
                try:
                    context["cycles_samples"] = int(scene.cycles.samples)
                except Exception:
                    context["cycles_samples"] = None

        if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
            try:
                context["eevee_samples"] = int(scene.eevee.taa_render_samples)
            except Exception:
                context["eevee_samples"] = None

        return context

    def _collect_sync_files(self, blend_path):
        base_dir = os.path.dirname(blend_path)
        file_map = {os.path.abspath(blend_path): os.path.basename(blend_path)}

        def _add_file(abs_path):
            if not abs_path:
                return
            path = os.path.abspath(abs_path)
            if not os.path.isfile(path):
                return
            if path in file_map:
                return
            try:
                rel = os.path.relpath(path, base_dir)
                if rel.startswith(".."):
                    rel = os.path.join("external", os.path.basename(path))
            except Exception:
                rel = os.path.join("external", os.path.basename(path))
            file_map[path] = rel

        for lib in bpy.data.libraries:
            try:
                _add_file(bpy.path.abspath(lib.filepath))
            except Exception:
                continue

        for image in bpy.data.images:
            try:
                if getattr(image, "source", "") != "FILE":
                    continue
                _add_file(bpy.path.abspath(image.filepath_raw or image.filepath))
            except Exception:
                continue

        for sound in bpy.data.sounds:
            try:
                _add_file(bpy.path.abspath(sound.filepath))
            except Exception:
                continue

        for clip in bpy.data.movieclips:
            try:
                _add_file(bpy.path.abspath(clip.filepath))
            except Exception:
                continue

        return file_map

    def _activate_synced_job_context(self):
        ctx = self.pending_sync_context or {}
        if not ctx:
            return

        scene_name = ctx.get("scene_name")
        scene = bpy.data.scenes.get(scene_name) if scene_name else None
        if scene is None:
            scene = bpy.context.scene if bpy.context else None
        if scene is None:
            return

        if bpy.context and bpy.context.window:
            try:
                bpy.context.window.scene = scene
            except Exception:
                pass

        camera_name = ctx.get("camera_name")
        if camera_name and camera_name in bpy.data.objects:
            obj = bpy.data.objects.get(camera_name)
            if obj and obj.type == "CAMERA":
                scene.camera = obj

        render = scene.render
        engine = ctx.get("render_engine")
        if engine:
            try:
                render.engine = engine
            except Exception:
                pass

        for key in ("resolution_x", "resolution_y", "resolution_percentage"):
            value = ctx.get(key)
            if value is None:
                continue
            try:
                setattr(render, key, int(value))
            except Exception:
                pass

        for key in ("frame_start", "frame_end", "frame_current"):
            value = ctx.get(key)
            if value is None:
                continue
            try:
                setattr(scene, key, int(value))
            except Exception:
                pass

        seed = ctx.get("seed")
        if seed is not None and hasattr(scene, "cycles") and hasattr(scene.cycles, "seed"):
            try:
                scene.cycles.seed = int(seed)
            except Exception:
                pass

        cycles_samples = ctx.get("cycles_samples")
        if cycles_samples is not None and hasattr(scene, "cycles") and hasattr(scene.cycles, "samples"):
            try:
                scene.cycles.samples = int(cycles_samples)
            except Exception:
                pass

        eevee_samples = ctx.get("eevee_samples")
        if eevee_samples is not None and hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
            try:
                scene.eevee.taa_render_samples = int(eevee_samples)
            except Exception:
                pass

        self.pending_sync_context = None

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
            ping_interval=30,
            ping_timeout=300,
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
                async with websockets.connect(url, ping_interval=30, ping_timeout=300, max_size=None, open_timeout=10) as ws:
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
                        msg = json.loads(raw)
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
                msg = json.loads(raw)
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
                    worker_key = msg.get("worker_id") or worker_id
                    self.integrity_probe_results[worker_key] = bool(msg.get("ok"))
                    continue

                if msg_type == MSG_PROJECT_SYNC_ACK:
                    worker_key = msg.get("worker_id") or worker_id
                    self.project_sync_results[worker_key] = {
                        "ok": bool(msg.get("ok")),
                        "error": msg.get("error", ""),
                    }
                    state = self.worker_sync_state.setdefault(worker_key, {})
                    state["phase"] = "done" if msg.get("ok") else "failed"
                    if "received_bytes" in msg:
                        state["received_bytes"] = int(msg.get("received_bytes") or 0)
                    if msg.get("error"):
                        state["error"] = msg.get("error")
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

        if msg_type == MSG_CLEAN_BLEND:
            # Server requested worker to remove any received .blend copies
            deleted = 0
            try:
                if self.received_project_dir and os.path.exists(self.received_project_dir):
                    for root, _, files in os.walk(self.received_project_dir):
                        for fn in files:
                            if fn.lower().endswith(".blend"):
                                p = os.path.join(root, fn)
                                try:
                                    os.remove(p)
                                    deleted += 1
                                except Exception:
                                    pass

                if self.pending_project_load and os.path.exists(self.pending_project_load):
                    try:
                        os.remove(self.pending_project_load)
                        deleted += 1
                    except Exception:
                        pass

                # clear state
                self.received_project_dir = ""
                self.pending_project_load = None
                self.pending_project_load_attempts = 0
                self.pending_project_load_retry_at = 0.0
                self.pending_blank_reset = True
                self.pending_blank_reset_attempts = 0
                self.pending_blank_reset_retry_at = 0.0
                self.pending_sync_context = None
                self.status = f"Cleaned {deleted} blend(s)"
            except Exception as exc:
                self.last_error = f"Clean failed: {exc}"
                self.status = self.last_error
            return

        if msg_type == MSG_REGISTERED:
            self.status = "Worker registriert"
            return

        if msg_type == MSG_RENDER_TILE:
            if self.render_abort_requested:
                return
            self._task_queue.put({"type": "render_tile", "payload": msg})
            return

        if msg_type == MSG_RENDER_ABORT:
            self.render_abort_requested = True
            self.pending_jobs = {}
            self.job_owner = {}
            self.job_attempts = {}
            self.job_queue = []
            self.target_inflight = {target: 0 for target in self.dispatch_targets}
            try:
                bpy.ops.render.cancel()
            except Exception:
                pass
            self.status = "Render-Abbruch empfangen"
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

        if msg_type == MSG_PROJECT_SYNC_START:
            self._incoming_project = {
                "transfer_id": msg.get("transfer_id") or uuid.uuid4().hex,
                "project_name": msg.get("project_name", "project"),
                "blend_name": msg.get("blend_name", ""),
                "sync_context": msg.get("sync_context", {}),
                "total_size": int(msg.get("total_size", 0)),
                "total_chunks": int(msg.get("total_chunks", 0)),
                "sha256": msg.get("sha256", ""),
                "chunks": {},
                "received_bytes": 0,
            }
            self.sync_active = True
            self.sync_start_time = time.time()
            self.incoming_project_progress = {
                "current_bytes": 0,
                "total_bytes": int(self._incoming_project.get("total_size", 0)),
                "total_chunks": int(self._incoming_project.get("total_chunks", 0)),
                "received_chunks": 0,
            }
            self.status = f"Empfange Projekt: {self._incoming_project['project_name']}"
            return

        if msg_type == MSG_PROJECT_SYNC_CHUNK:
            transfer = self._incoming_project
            if not transfer:
                return
            if msg.get("transfer_id") != transfer.get("transfer_id"):
                return
            idx = int(msg.get("chunk_index", -1))
            if idx < 0:
                return
            try:
                chunk_bytes = base64.b64decode(msg.get("data_b64", ""))
                transfer["chunks"][idx] = chunk_bytes
                transfer["received_bytes"] = int(transfer.get("received_bytes", 0)) + len(chunk_bytes)
                self.incoming_project_progress["current_bytes"] = int(transfer.get("received_bytes", 0))
                self.incoming_project_progress["received_chunks"] = len(transfer.get("chunks", {}))
                total = max(1, int(transfer.get("total_size", 0)))
                pct = (float(self.incoming_project_progress["current_bytes"]) / float(total)) * 100.0
                self.status = (
                    f"Download {pct:.1f}% "
                    f"({self.incoming_project_progress['received_chunks']}/{self.incoming_project_progress.get('total_chunks', 0)} Chunks)"
                )
            except Exception:
                pass
            return

        if msg_type == MSG_PROJECT_SYNC_COMPLETE:
            transfer = self._incoming_project
            self._incoming_project = None

            if not transfer:
                await websocket.send(
                    json_dumps(
                        {
                            "type": MSG_PROJECT_SYNC_ACK,
                            "worker_id": self.node_id,
                            "ok": False,
                            "error": "missing transfer state",
                        }
                    )
                )
                return

            try:
                result = self._apply_received_project_bundle(transfer)
                await websocket.send(
                    json_dumps(
                        {
                            "type": MSG_PROJECT_SYNC_ACK,
                            "worker_id": self.node_id,
                            "ok": True,
                            "transfer_id": result.get("transfer_id"),
                            "received_bytes": result.get("received_bytes", 0),
                        }
                    )
                )
                self.sync_active = False
                self.status = "Projekt-Sync OK"
            except Exception as exc:
                await websocket.send(
                    json_dumps(
                        {
                            "type": MSG_PROJECT_SYNC_ACK,
                            "worker_id": self.node_id,
                            "ok": False,
                            "error": str(exc),
                        }
                    )
                )
                self.sync_active = False
                self.last_error = f"Projekt-Sync Fehler: {exc}"
                self.status = self.last_error
            return

    def _render_tile_local(self, payload):
        if self.render_abort_requested:
            return {"type": MSG_TILE_RESULT, "tile_id": payload.get("tile_id"), "ok": False, "error": "Render abgebrochen"}

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
        if self.show_render_window:
            self._open_worker_render_view(tile)
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

    def _open_worker_render_view(self, tile):
        self.status = f"Worker rendert Tile {tile.get('id')}"

        # Try to keep one persistent live view instead of spawning windows per tile.
        if self._worker_render_view_opened:
            self._bind_render_result_to_image_editors()
            return

        try:
            bpy.ops.render.view_show("INVOKE_DEFAULT")
            self._worker_render_view_opened = True
            self._bind_render_result_to_image_editors()
            return
        except Exception:
            pass

        try:
            bpy.ops.wm.window_new()
            self._worker_render_view_opened = True
            self._bind_render_result_to_image_editors()
            return
        except Exception:
            pass

    def _bind_render_result_to_image_editors(self):
        img = bpy.data.images.get("Render Result")
        if img is None or bpy.context is None:
            return
        wm = bpy.context.window_manager
        if wm is None:
            return
        for window in wm.windows:
            screen = window.screen
            if not screen:
                continue
            for area in screen.areas:
                if area.type == "IMAGE_EDITOR":
                    try:
                        area.spaces.active.image = img
                    except Exception:
                        pass

    def _render_tile_to_path(self, scene, tile):
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

    def _send_result_to_server_async(self, result):
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

    def _prepare_output_dirs(self, output_file):
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

    def _show_final_image_in_editor(self, output_path):
        try:
            if bpy.context is None:
                return
            image = bpy.data.images.load(output_path, check_existing=True)
            wm = bpy.context.window_manager
            if wm is None:
                return

            target_window = None
            for window in wm.windows:
                screen = window.screen
                if not screen:
                    continue
                for area in screen.areas:
                    if area.type == "IMAGE_EDITOR":
                        target_window = window
                        try:
                            area.spaces.active.image = image
                        except Exception:
                            pass
                        return

            if wm.windows:
                target_window = wm.windows[0]
                screen = target_window.screen
                if screen and screen.areas:
                    area = screen.areas[0]
                    area.type = "IMAGE_EDITOR"
                    try:
                        area.spaces.active.image = image
                    except Exception:
                        pass
        except Exception:
            pass

    def _build_project_bundle(self):
        blend_path = bpy.data.filepath
        if not blend_path:
            raise RuntimeError("Projekt ist nicht gespeichert")
        if not os.path.exists(blend_path):
            raise RuntimeError("Blend-Datei existiert nicht")

        root_dir = os.path.dirname(blend_path)
        blend_name = os.path.basename(blend_path)
        scene = _safe_scene()
        sync_context = self._capture_sync_context(scene) if scene is not None else {}
        file_map = self._collect_sync_files(blend_path)
        buf = tempfile.NamedTemporaryFile(prefix="blendersplitter_project_", suffix=".zip", delete=False)
        tmp_zip = buf.name
        buf.close()

        try:
            with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for abs_path, rel_path in sorted(file_map.items(), key=lambda kv: kv[1]):
                    zf.write(abs_path, arcname=rel_path)

            with open(tmp_zip, "rb") as fh:
                payload = fh.read()

            total_source_size = 0
            for path in file_map.keys():
                try:
                    total_source_size += os.path.getsize(path)
                except Exception:
                    pass

            return {
                "payload": payload,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "project_name": os.path.basename(root_dir),
                "blend_name": blend_name,
                "sync_context": sync_context,
                "file_count": len(file_map),
                "source_total_size": total_source_size,
                "archive_total_size": len(payload),
            }
        finally:
            try:
                os.remove(tmp_zip)
            except Exception:
                pass

    def _sync_project_to_workers(self, workers, timeout_seconds=180.0):
        if not workers:
            return True
        if self._loop is None:
            self.last_error = "Event-Loop nicht verfügbar"
            self.status = self.last_error
            return False

        try:
            bundle = self._build_project_bundle()
        except Exception as exc:
            self.last_error = f"Projekt-Sync Build fehlgeschlagen: {exc}"
            self.status = self.last_error
            return False

        self.sync_active = True
        self.sync_start_time = time.time()
        self.sync_total_bytes = len(bundle["payload"])
        self.project_sync_results = {}
        chunk_size = 256 * 1024
        total_chunks = (len(bundle["payload"]) + chunk_size - 1) // chunk_size if bundle["payload"] else 1
        self.worker_sync_state = {
            wid: {"phase": "queued", "current_bytes": 0, "total_bytes": len(bundle["payload"])} for wid in workers
        }
        self.sync_package_info = {
            "file_count": int(bundle.get("file_count", 0)),
            "source_total_size": int(bundle.get("source_total_size", 0)),
            "archive_total_size": int(bundle.get("archive_total_size", 0)),
            "chunk_count": int(total_chunks),
        }
        self.sync_progress = {
            wid: {"current_bytes": 0, "total_bytes": len(bundle["payload"])} for wid in workers
        }

        fut = asyncio.run_coroutine_threadsafe(
            self._sync_project_to_workers_async(workers, bundle, timeout_seconds),
            self._loop,
        )

        try:
            ok = bool(fut.result(timeout=timeout_seconds + 30.0))
            self.sync_active = False
            return ok
        except Exception as exc:
            self.sync_active = False
            self.last_error = f"Projekt-Sync fehlgeschlagen: {exc}"
            self.status = self.last_error
            return False

    async def _sync_project_to_workers_async(self, workers, bundle, timeout_seconds):
        payload = bundle["payload"]
        total = len(payload)
        chunk_size = 256 * 1024
        total_chunks = (total + chunk_size - 1) // chunk_size if total else 1
        transfer_id = uuid.uuid4().hex

        for worker_id in workers:
            info = self.connected_workers.get(worker_id)
            if not info:
                self.project_sync_results[worker_id] = {"ok": False, "error": "worker missing"}
                self.worker_sync_state.setdefault(worker_id, {})["phase"] = "missing"
                continue

            ws = info.get("socket")
            if ws is None:
                self.project_sync_results[worker_id] = {"ok": False, "error": "socket missing"}
                self.worker_sync_state.setdefault(worker_id, {})["phase"] = "socket-missing"
                continue

            try:
                self.worker_sync_state.setdefault(worker_id, {})["phase"] = "sending"
                await ws.send(
                    json_dumps(
                        {
                            "type": MSG_PROJECT_SYNC_START,
                            "transfer_id": transfer_id,
                            "project_name": bundle["project_name"],
                            "blend_name": bundle["blend_name"],
                            "sync_context": bundle.get("sync_context", {}),
                            "total_size": total,
                            "total_chunks": total_chunks,
                            "sha256": bundle["sha256"],
                        }
                    )
                )

                sent = 0
                for index in range(total_chunks):
                    chunk = payload[index * chunk_size : (index + 1) * chunk_size]
                    await ws.send(
                        json_dumps(
                            {
                                "type": MSG_PROJECT_SYNC_CHUNK,
                                "transfer_id": transfer_id,
                                "chunk_index": index,
                                "data_b64": base64.b64encode(chunk).decode("ascii"),
                            }
                        )
                    )
                    sent += len(chunk)
                    self.sync_progress.setdefault(worker_id, {})["current_bytes"] = sent
                    state = self.worker_sync_state.setdefault(worker_id, {})
                    state["phase"] = "sending"
                    state["current_bytes"] = sent
                    state["total_bytes"] = total

                await ws.send(json_dumps({"type": MSG_PROJECT_SYNC_COMPLETE, "transfer_id": transfer_id}))
                self.worker_sync_state.setdefault(worker_id, {})["phase"] = "waiting-ack"
            except Exception as exc:
                self.project_sync_results[worker_id] = {"ok": False, "error": str(exc)}
                state = self.worker_sync_state.setdefault(worker_id, {})
                state["phase"] = "failed"
                state["error"] = str(exc)

        deadline = time.time() + float(timeout_seconds)
        while time.time() < deadline:
            if len(self.project_sync_results) >= len(workers):
                break
            await asyncio.sleep(0.1)

        missing = [wid for wid in workers if wid not in self.project_sync_results]
        for worker_id in missing:
            self.project_sync_results[worker_id] = {"ok": False, "error": "ack timeout"}

        failed = [wid for wid, res in self.project_sync_results.items() if not res.get("ok")]
        if failed:
            self.status = f"Projekt-Sync fehlgeschlagen: {len(failed)}/{len(workers)}"
            self.last_error = "; ".join(
                f"{wid}: {self.project_sync_results[wid].get('error', 'unknown')}" for wid in failed
            )
            return False

        self.status = f"Projekt-Sync OK: {len(workers)} Worker"
        return True

    def clean_worker_blends(self):
        """Ask all connected workers to delete received .blend copies."""
        if self._loop is None:
            self.last_error = "Event loop nicht verfügbar"
            return False

        async def _send():
            for wid, info in list(self.connected_workers.items()):
                ws = info.get("socket")
                if ws is None:
                    continue
                try:
                    await ws.send(json_dumps({"type": MSG_CLEAN_BLEND}))
                except Exception:
                    pass

        try:
            asyncio.run_coroutine_threadsafe(_send(), self._loop)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def _apply_received_project_bundle(self, transfer):
        transfer_id = transfer.get("transfer_id")
        chunks = transfer.get("chunks", {})
        total_chunks = int(transfer.get("total_chunks", 0))
        if total_chunks <= 0:
            raise RuntimeError("Ungültige Chunk-Anzahl")

        ordered = []
        for idx in range(total_chunks):
            if idx not in chunks:
                raise RuntimeError(f"Chunk fehlt: {idx}")
            ordered.append(chunks[idx])
        payload = b"".join(ordered)

        expected_sha = transfer.get("sha256")
        local_sha = hashlib.sha256(payload).hexdigest()
        if expected_sha and expected_sha != local_sha:
            raise RuntimeError("SHA256 passt nicht")

        tmp_root = os.path.join(tempfile.gettempdir(), "blendersplitter_sync_v3", transfer_id)
        os.makedirs(tmp_root, exist_ok=True)
        zip_path = os.path.join(tmp_root, "project.zip")
        with open(zip_path, "wb") as fh:
            fh.write(payload)

        extract_dir = os.path.join(tmp_root, "project")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        self.received_project_dir = extract_dir
        blend_name = transfer.get("blend_name") or ""
        blend_path = os.path.join(extract_dir, blend_name) if blend_name else ""

        if not blend_path or not os.path.exists(blend_path):
            for current_root, _, files in os.walk(extract_dir):
                for filename in files:
                    if filename.lower().endswith(".blend"):
                        blend_path = os.path.join(current_root, filename)
                        break
                if blend_path and os.path.exists(blend_path):
                    break

        if not blend_path or not os.path.exists(blend_path):
            raise RuntimeError("Keine .blend Datei im synchronisierten Projekt gefunden")

        worker_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in self.node_id[:8]) or "worker"
        stamp = time.strftime("%Y%m%d_%H%M%S")
        unique_blend_name = f"{worker_name}_{stamp}.blend"
        unique_blend_path = os.path.join(os.path.dirname(blend_path), unique_blend_name)
        if os.path.abspath(unique_blend_path) != os.path.abspath(blend_path):
            shutil.copy2(blend_path, unique_blend_path)
            blend_path = unique_blend_path

        self.pending_sync_context = transfer.get("sync_context", {})
        self.pending_project_load = blend_path
        self.pending_project_load_attempts = 0
        self.pending_project_load_retry_at = 0.0

        return {
            "ok": True,
            "transfer_id": transfer_id,
            "received_bytes": len(payload),
            "sha256": local_sha,
        }

    def start_distributed_render(self):
        if self.role != "server":
            self.status = "Starte Server vor Render"
            if not self.force_start_server():
                self.last_error = self.status
                return False

        # Refresh runtime settings from the current UI so Output Folder changes apply immediately.
        scene = _safe_scene()
        if scene is not None and hasattr(scene, "blendersplitter_settings"):
            cfg = scene.blendersplitter_settings
            self.configure(
                cfg.host,
                cfg.server_port,
                cfg.discovery_port,
                cfg.overlap_percent,
                cfg.max_retries,
                cfg.auto_sync_project,
                cfg.show_render_window,
                cfg.server_render_tiles,
                cfg.tile_coefficient,
                cfg.output_dir,
            )

        if scene is None:
            self.last_error = "Keine aktive Szene"
            self.status = self.last_error
            return False

        if self.show_render_window:
            self._open_live_render_view()

        render = scene.render
        final_width = max(1, int(render.resolution_x * (render.resolution_percentage / 100.0)))
        final_height = max(1, int(render.resolution_y * (render.resolution_percentage / 100.0)))
        _, signature = collect_render_signature(scene)

        workers = list(self.connected_workers.keys())

        if self.auto_sync_project and workers:
            self.status = "Projekt-Sync läuft..."
            if not self._sync_project_to_workers(workers, timeout_seconds=180.0):
                if not self.last_error:
                    self.last_error = "Projekt-Sync fehlgeschlagen"
                self.status = self.last_error
                return False

        self.status = "Integrity Check läuft..."
        if not self.run_integrity_check(timeout_seconds=12.0):
            if not self.last_error:
                self.last_error = "Integrity Check fehlgeschlagen"
            self.status = self.last_error
            return False

        node_count = len(workers) + (1 if self.server_render_tiles else 0)
        tile_count = tile_target_for_workers(max(1, node_count), self.tile_coefficient)
        grid_x, grid_y = grid_for_tile_count(tile_count, final_width, final_height)
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
        self.render_abort_requested = False
        self.pending_jobs = {}
        self.completed_jobs = {}
        self.job_owner = {}
        self.job_attempts = {}
        self.job_queue = []
        self.target_inflight = {}
        self.target_ready_at = {}
        self.dispatch_targets = []
        self.render_plan = []
        self.expected_jobs = len(tiles)
        self.render_start_time = time.time()

        targets = list(workers)
        if self.server_render_tiles:
            targets.append("MASTER")
        if not workers and self.server_render_tiles:
            targets = ["MASTER"]
        if not targets:
            self.last_error = "Keine Ziele verfügbar"
            self.status = self.last_error
            return False

        self.dispatch_targets = list(targets)
        self.target_inflight = {target: 0 for target in self.dispatch_targets}
        self.target_ready_at = {target: 0.0 for target in self.dispatch_targets}

        # Queue all jobs first; dispatch continuously as targets become free.
        for tile in tiles:
            job = {
                "type": MSG_RENDER_TILE,
                "tile_id": tile["id"],
                "tile": tile,
                "render_signature": signature,
            }
            self.job_attempts[tile["id"]] = 0
            self.job_queue.append(job)

        # Prime each target with one job.
        for target in self.dispatch_targets:
            self._dispatch_next_job_for_target(target)

        self.status = f"Render gestartet: {len(tiles)} Tiles, {len(self.dispatch_targets)} Ziele"
        return True

    def _dispatch_next_job_for_target(self, target):
        if self.render_abort_requested:
            return False
        if target not in self.dispatch_targets:
            return False
        if time.time() < float(self.target_ready_at.get(target, 0.0)):
            return False
        if self.target_inflight.get(target, 0) > 0:
            return False

        if target != "MASTER" and target not in self.connected_workers:
            return False

        if not self.job_queue:
            return False

        job = self.job_queue.pop(0)
        tile = job.get("tile", {})
        tile_id = job.get("tile_id")
        self.pending_jobs[tile_id] = job
        self.job_owner[tile_id] = target
        self.target_inflight[target] = self.target_inflight.get(target, 0) + 1
        self.render_plan.append(
            {
                "tile_id": tile_id,
                "target": target,
                "min_x": tile.get("min_x", 0),
                "max_x": tile.get("max_x", 0),
                "min_y": tile.get("min_y", 0),
                "max_y": tile.get("max_y", 0),
                "core_min_x": tile.get("core_min_x", tile.get("min_x", 0)),
                "core_max_x": tile.get("core_max_x", tile.get("max_x", 0)),
                "core_min_y": tile.get("core_min_y", tile.get("min_y", 0)),
                "core_max_y": tile.get("core_max_y", tile.get("max_y", 0)),
            }
        )

        if target == "MASTER":
            self._task_queue.put({"type": "render_tile", "payload": job, "reply_to": "server_local"})
        else:
            self._send_job_to_worker(target, job)
        return True

    def _open_live_render_view(self):
        try:
            bpy.ops.render.view_show("INVOKE_DEFAULT")
            return
        except Exception:
            pass

        try:
            bpy.ops.wm.window_new()
            wm = bpy.context.window_manager
            new_window = wm.windows[-1] if wm.windows else None
            if new_window and new_window.screen and new_window.screen.areas:
                area = new_window.screen.areas[0]
                area.type = "IMAGE_EDITOR"
                if "Render Result" in bpy.data.images:
                    area.spaces.active.image = bpy.data.images["Render Result"]
        except Exception:
            pass

    def sync_project_files(self, timeout_seconds=180.0):
        if self.role != "server":
            self.status = "Starte Server vor Projekt-Sync"
            if not self.force_start_server():
                self.last_error = self.status
                return False

        workers = list(self.connected_workers.keys())
        if not workers:
            self.status = "Keine Worker für Projekt-Sync verbunden"
            return False

        self.status = "Projekt-Sync läuft..."
        ok = self._sync_project_to_workers(workers, timeout_seconds=timeout_seconds)
        if not ok and not self.last_error:
            self.last_error = "Projekt-Sync fehlgeschlagen"
        return ok

    def _send_job_to_worker(self, worker_id, job):
        info = self.connected_workers.get(worker_id)
        if not info:
            self._retry_or_requeue_job(job, f"Worker {worker_id} nicht verfügbar")
            return

        ws = info.get("socket")

        async def _send():
            try:
                await ws.send(json_dumps(job))
            except Exception as exc:
                self._retry_or_requeue_job(job, f"Sendefehler: {exc}")

        if self._loop:
            asyncio.run_coroutine_threadsafe(_send(), self._loop)

    def _retry_or_requeue_job(self, job, reason):
        tile_id = job.get("tile_id")
        if tile_id is None:
            return

        attempts = int(job.get("send_attempts", 0)) + 1
        job["send_attempts"] = attempts

        prev_owner = self.job_owner.get(tile_id)
        if prev_owner:
            self.target_inflight[prev_owner] = max(0, int(self.target_inflight.get(prev_owner, 1)) - 1)
            self.job_owner.pop(tile_id, None)

        if attempts > self.max_retries:
            self.last_error = f"Tile {tile_id} konnte nicht gesendet werden nach {attempts - 1} Retries: {reason}"
            self.status = self.last_error
            self.last_integrity = "failed"
            self.pending_jobs.pop(tile_id, None)
            return

        self.job_queue.insert(0, job)
        self.status = f"Tile {tile_id} erneut eingeplant ({attempts}/{self.max_retries})"
        for target in list(self.dispatch_targets):
            if self.target_inflight.get(target, 0) == 0:
                self._dispatch_next_job_for_target(target)
                break

    def _consume_tile_result(self, message):
        tile_id = message.get("tile_id")
        if tile_id not in self.pending_jobs:
            return

        owner = self.job_owner.get(tile_id)

        if not message.get("ok"):
            self._reassign_tile(tile_id, message.get("error", "Unbekannter Fehler"))
            return

        tile = message.get("tile") or {}
        try:
            png_data = base64.b64decode(message.get("png_base64", ""))
        except Exception:
            self._reassign_tile(tile_id, "PNG Base64 dekodierung fehlgeschlagen")
            return

        # Fast PNG integrity guard to avoid stitch-time truncation crashes.
        if len(png_data) < 16 or not png_data.startswith(b"\x89PNG\r\n\x1a\n") or b"IEND" not in png_data[-64:]:
            self._reassign_tile(tile_id, "PNG unvollständig oder beschädigt")
            return
        worker_name = str(message.get("worker_id") or "unknown")
        worker_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in worker_name)[:24] or "unknown"

        raw_dir = self.current_raw_splits_dir or os.path.join(tempfile.gettempdir(), "blender_splitter_received_v3")
        os.makedirs(raw_dir, exist_ok=True)
        path = os.path.join(raw_dir, f"{tile_id}_{worker_name}_{uuid.uuid4().hex}.png")
        with open(path, "wb") as fh:
            fh.write(png_data)

        self.completed_jobs[tile_id] = {
            "tile_id": tile_id,
            "worker_id": message.get("worker_id"),
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
        if owner:
            self.target_inflight[owner] = max(0, int(self.target_inflight.get(owner, 1)) - 1)
            self.target_ready_at[owner] = time.time() + float(self.dispatch_cooldown_seconds)
            self._dispatch_next_job_for_target(owner)

        done = len(self.completed_jobs)
        self.status = f"Tiles fertig: {done}/{self.expected_jobs}"
        if done >= self.expected_jobs and not self.pending_jobs and not self.job_queue:
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
            self._show_final_image_in_editor(self.current_render_output)
        except Exception as exc:
            self.last_error = f"Stitch fehlgeschlagen: {exc}"
            self.status = self.last_error
            traceback.print_exc()

    def _reassign_jobs_from_worker(self, worker_id):
        if worker_id in self.dispatch_targets:
            self.dispatch_targets = [t for t in self.dispatch_targets if t != worker_id]
            self.target_inflight.pop(worker_id, None)
            self.target_ready_at.pop(worker_id, None)

        for tile_id, owner in list(self.job_owner.items()):
            if owner == worker_id and tile_id in self.pending_jobs:
                self._reassign_tile(tile_id, f"Worker {worker_id} getrennt")

        for target in list(self.dispatch_targets):
            self._dispatch_next_job_for_target(target)

    def _reassign_tile(self, tile_id, reason):
        if tile_id not in self.pending_jobs:
            return

        attempt = self.job_attempts.get(tile_id, 0) + 1
        self.job_attempts[tile_id] = attempt
        job = self.pending_jobs[tile_id]
        prev_owner = self.job_owner.get(tile_id)
        if prev_owner:
            self.target_inflight[prev_owner] = max(0, int(self.target_inflight.get(prev_owner, 1)) - 1)

        if attempt > self.max_retries:
            self.last_error = f"Tile {tile_id} fehlgeschlagen nach {attempt - 1} Retries: {reason}"
            self.status = self.last_error
            self.last_integrity = "failed"
            return

        active_workers = [wid for wid in self.connected_workers.keys() if wid in self.dispatch_targets and wid != prev_owner]
        candidates = list(active_workers)
        if "MASTER" in self.dispatch_targets:
            candidates.append("MASTER")
        if not candidates:
            candidates = ["MASTER"] if self.server_render_tiles else []
        if not candidates:
            self.last_error = f"Tile {tile_id} kann nicht neu zugewiesen werden: {reason}"
            self.status = self.last_error
            return

        target = min(candidates, key=lambda t: int(self.target_inflight.get(t, 0)))
        self.job_owner[tile_id] = target
        self.target_inflight[target] = int(self.target_inflight.get(target, 0)) + 1

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
        self.integrity_probe_results = {}

        async def _broadcast():
            for worker_id in workers:
                info = self.connected_workers.get(worker_id)
                if not info:
                    self.integrity_probe_results[worker_id] = False
                    continue
                try:
                    await info["socket"].send(json_dumps({"type": MSG_INTEGRITY_PROBE, "render_signature": signature}))
                except Exception:
                    self.integrity_probe_results[worker_id] = False

        if self._loop:
            asyncio.run_coroutine_threadsafe(_broadcast(), self._loop)

        deadline = time.time() + float(timeout_seconds)
        while time.time() < deadline:
            if len(self.integrity_probe_results) >= len(workers):
                break
            time.sleep(0.1)

        for worker_id in workers:
            if worker_id not in self.integrity_probe_results:
                self.integrity_probe_results[worker_id] = False

        ok = all(self.integrity_probe_results.get(w, False) for w in workers)
        self.last_integrity = "ok" if ok else "failed"
        self.status = "Integrity Check OK" if ok else "Integrity Check fehlgeschlagen"
        if not ok:
            bad = [w for w in workers if not self.integrity_probe_results.get(w, False)]
            self.last_error = f"Integrity Fehler bei: {', '.join(bad)}"
        return ok

    def cancel_render(self):
        if not self.current_render_config:
            self.status = "Kein Render aktiv"
            return False
        self.render_abort_requested = True
        self.pending_jobs.clear()
        self.job_owner.clear()
        self.job_attempts.clear()
        self.job_queue = []
        self.target_inflight = {target: 0 for target in self.dispatch_targets}
        self.expected_jobs = 0
        self.current_render_config = None
        self.current_render_output = ""
        self.last_integrity = "aborted"
        self.status = "Render abgebrochen"

        async def _broadcast_abort():
            for worker_id in list(self.connected_workers.keys()):
                info = self.connected_workers.get(worker_id)
                ws = info.get("socket") if info else None
                if ws is None:
                    continue
                try:
                    await ws.send(json_dumps({"type": MSG_RENDER_ABORT}))
                except Exception:
                    pass

        if self._loop:
            try:
                asyncio.run_coroutine_threadsafe(_broadcast_abort(), self._loop)
            except Exception:
                pass
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


def manager():
    return _MANAGER
