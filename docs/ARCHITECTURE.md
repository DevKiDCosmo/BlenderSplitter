# BlenderSplitter — Architecture Reference

_Last updated: 2026-04-06_

---

## 1 Overview

BlenderSplitter is a Blender add-on that distributes tile rendering across
multiple machines.  A single **master** node coordinates the work; any number of
**worker** nodes execute tile renders and stream results back.

Every node runs the **same ZIP** — the role (master vs worker) is determined
dynamically at startup by the `startup_mode` configuration key and by the UDP
discovery protocol.

---

## 2 Thread and Process Model

A Blender instance that has started BlenderSplitter runs **two concurrent
execution contexts**:

```
┌────────────────────────────────────────────────────────────┐
│  Blender Process                                           │
│                                                            │
│  ┌─────────────────────────────────┐                       │
│  │  Blender Main Thread            │                       │
│  │  (bpy.app.timers callback)      │                       │
│  │                                 │                       │
│  │  process_main_thread_queues()   │  ← runs every ~0.1 s  │
│  │  ├─ consume progress queue      │                       │
│  │  ├─ dequeue render tasks        │                       │
│  │  │  └─ _render_tile_local()     │  ← synchronous render │
│  │  └─ consume result queue        │                       │
│  │     └─ _consume_tile_result()   │                       │
│  └──────────────┬──────────────────┘                       │
│                 │ thread-safe queues                        │
│  ┌──────────────▼──────────────────┐                       │
│  │  Background asyncio Thread      │                       │
│  │  (daemon thread, own event loop)│                       │
│  │                                 │                       │
│  │  _run_event_loop()              │                       │
│  │  ├─ WebSocket server            │  ← master only        │
│  │  ├─ UDP discovery responder     │  ← master only        │
│  │  ├─ WebSocket client            │  ← worker only        │
│  │  └─ message send/receive        │                       │
│  └─────────────────────────────────┘                       │
└────────────────────────────────────────────────────────────┘
```

### Why two threads?

Blender's Python API is **not thread-safe** and `bpy.ops.render.render()` is
**synchronous** — it blocks until the render completes.  All `bpy.*` calls
must happen on the main thread.

At the same time, the WebSocket server/client needs an `asyncio` event loop
that can handle many concurrent connections without blocking.

The solution is:

| Context | What runs there | Communication |
|---------|-----------------|---------------|
| Main thread timer | Tile render (`bpy.ops`), stitch, result dispatch | `_task_queue`, `_result_queue`, `_progress_queue` |
| asyncio thread | WebSocket server/client, UDP discovery, sync I/O | same queues + `asyncio.run_coroutine_threadsafe` |

### Current bottleneck (Issue #4/#5-C)

Because `_render_tile_local()` calls `bpy.ops.render.render(write_still=True)`
synchronously, the master **freezes Blender's UI** for the duration of each
tile render.  Workers are unaffected because they run `_render_tile_local()`
through the same mechanism — but the master is special because it also needs to
respond to incoming WebSocket messages during that time.

The asyncio thread continues to receive messages while the main thread is
blocked, but it cannot dispatch new jobs to the master until the main-thread
timer fires again.

**Planned fix (Phase 5):** offload master tile renders to a subprocess — spawn
`blender --background --python render_tile_worker.py` and communicate via stdin
/ stdout or a temporary file.  The main thread timer becomes non-blocking, the
asyncio thread can service workers continuously, and the master's throughput
matches workers'.

---

## 3 Startup and Role Assignment

### Mode map

| `startup_mode` in config.json | Behavior |
|-------------------------------|----------|
| `master` | Auto-starts on addon load. Calls `discover_server()` first; self-hosts only if no existing server is found. |
| `worker` | Auto-starts on addon load. Searches for a server indefinitely; **never** starts a server. |
| `master_worker` | User-controlled (Start Cluster button). Discovers first; self-hosts if none found. |
| `user` | User-controlled (Start Cluster button). Attempts discovery 6 times then falls back to self-hosting. |

### Discovery protocol

Discovery uses **UDP broadcast** on `discovery_port` (default 8766).

```
Worker               LAN              Master
  │                                      │
  │── UDP broadcast: BLENDER_SPLITTER_DISCOVERY_V3 ──▶│
  │                                      │── reply: BLENDER_SPLITTER_SERVER_V3{host, port} ──▶│
  │◀──────────────────────────────────── │
  │── WebSocket connect ws://host:port ──▶│
  │                                      │
  │── MSG_REGISTER_WORKER ───────────────▶│
  │◀── MSG_REGISTERED ────────────────── │
  │                   (cluster ready)     │
```

If **no reply** arrives within the timeout, the node self-hosts (unless it is
configured as worker-only, in which case it retries indefinitely).

---

## 4 Force Server (Master Takeover)

The Force Server feature lets a connected worker voluntarily request that the
current master transfer its server role.

```
Worker A (wants master)      Old Master B           Other Workers
        │                         │                      │
        │── MSG_SERVER_TAKEOVER ──▶│                      │
        │                         │── MSG_NEW_MASTER{A_ip, A_port} ──▶│
        │                         │── close socket for A ──          │
        │                         │                      │ (wait 2 s)│
        │                         │                      │──────────▶│
        │── _start_server() ──    │                      │── connect to A ──▶│
        │   (becomes master)      │                      │
```

After the takeover:
- Worker A starts the WebSocket server and UDP discovery responder.
- All other workers receive `MSG_NEW_MASTER` with A's host/port, wait
  `MSG_NEW_MASTER_DELAY_S` (2 s) then reconnect.
- If A's server fails to start, each other worker's `ReconnectController`
  increments its failure count and, after `self_host_after` (8) attempts,
  promotes itself to server.

**UI guard:** The "Force Server" button uses `poll()` — it is only enabled when
`role == "worker"` and `_worker_socket is not None`.

---

## 5 Render Pipeline

### Single-camera

```
start_distributed_render()
  ├─ sync_project_to_workers()      (if auto_sync_project=True)
  ├─ run_integrity_check()           (render signature match)
  ├─ generate_tiles()                (grid split with overlap)
  ├─ queue job per tile
  ├─ pre-distribute initial batch    (ceil(tiles/3)/targets jobs)
  └─ timer loop: _dispatch_next_job_for_target()
       ├─ MASTER target → _task_queue → _render_tile_local() (main thread)
       └─ worker target → WebSocket MSG_RENDER_TILE
            └─ worker: _render_tile_local() → MSG_TILE_RESULT
                         └─ master: _consume_tile_result()
                                     └─ (all done) → _finalize_render()
                                                      └─ stitch_tiles()
```

### Batch camera

```
start_batch_camera_render(camera_names=[...])
  └─ for each camera:
       ├─ scene.camera = next camera
       └─ start_distributed_render()
            └─ _finalize_render()
                 └─ _advance_batch_camera()  ← hooks _finalize_render
                      └─ scene.camera = next camera
                         start_distributed_render()  (loop)
```

Each camera's stitched result is saved independently.  Workers are never
restarted between cameras.

---

## 6 Sync Protocol

Project files (`.blend` + assets) are transferred **master → workers** before
each distributed render when `auto_sync_project = True`.

```
Master                           Worker
  │── MSG_PROJECT_SYNC_START{transfer_id, total_size, sha256, runtime_config} ──▶│
  │── MSG_PROJECT_SYNC_CHUNK{chunk_index, data_b64} ──▶│ (repeated)
  │── MSG_PROJECT_SYNC_COMPLETE ──▶│
  │                                │── MSG_PROJECT_SYNC_ACK{ok, received_bytes} ──▶│
```

The `runtime_config` field in `MSG_PROJECT_SYNC_START` carries
`overlap_percent`, `tile_coefficient`, `max_retries`, `server_render_tiles`,
and `startup_mode` to prevent configuration drift between nodes.

---

## 7 Module Map

| Path | Responsibility |
|------|----------------|
| `__init__.py` | Blender addon entry point; loads config, registers UI, auto-starts for dedicated modes |
| `src/legacy/worker.py` | `DistributedRenderManager` — all runtime logic (server, worker, render, sync) |
| `src/legacy/ui.py` | All Blender operator and panel classes |
| `src/legacy/network.py` | UDP discovery (broadcast + responder) |
| `src/legacy/robust_connection.py` | `ReconnectController` — exponential backoff policy |
| `src/legacy/robust_protocol.py` | Message type constants |
| `src/legacy/robust_transfer.py` | Tile chunking/assembly for large PNG results |
| `src/legacy/stitch.py` | Tile image stitching (Pillow) |
| `src/legacy/tiles.py` | Tile grid generation with overlap |
| `src/legacy/trans.py` | CLI utility: make black pixels transparent |
| `src/legacy/scheduler_app.py` | Standalone external scheduler with Tkinter cluster monitor |
| `src/runtime/facade.py` | Thin typed facade over the legacy manager |
| `src/ui/controller.py` | UI controller routing operator calls through facade |
| `src/scheduler/core.py` | Pure dispatch/assignment logic (tested without bpy) |
| `src/sync/service.py` | Pure sync bundle helpers (tested without bpy) |
| `src/network/messages.py` | JSON serialisation helpers |
| `src/config/store.py` | JSON config loading/merging |
| `src/blender_adapter/bpy_adapter.py` | Future concrete adapter isolating bpy calls |
| `compile.sh` | Builds four per-mode ZIP artefacts |

Root-level `*.py` files (`network.py`, `worker.py`, `ui.py`, …) are **compatibility
wrappers only** (`from .src.legacy.X import *`).  They are excluded from the
distribution ZIP by `compile.sh`.

---

## 8 Configuration Keys (config.json)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | string | `"user"` | Startup mode: `master`, `worker`, `master_worker`, `user` |
| `user_mode` | string | `"master_worker"` | Effective mode when `mode == "user"` |
| `always` | list[string] | `[]` | Feature flags always enabled (legacy; unused now) |
| `network.host` | string | `"0.0.0.0"` | Listen address for the WebSocket server |
| `network.server_port` | int | `8765` | WebSocket server port |
| `network.discovery_port` | int | `8766` | UDP discovery port |
| `render.overlap_percent` | float | `3.0` | Tile overlap percentage |
| `render.tile_coefficient` | int | `1` | Multiplier for tile count |
| `render.max_retries` | int | `3` | Max tile render retry attempts |
| `render.auto_sync_project` | bool | `true` | Sync project before each render |
| `render.show_render_window` | bool | `true` | Open render view during render |
| `render.server_render_tiles` | bool | `true` | Master renders tiles locally too |
| `render.output_dir` | string | `""` | Override output directory |
| `external_scheduler.enabled` | bool | `false` | Start external scheduler process |
| `external_scheduler.host` | string | `"127.0.0.1"` | External scheduler host |
| `external_scheduler.port` | int | `9876` | External scheduler port |

---

## 9 Known Limitations / Open Items

| ID | Area | Description |
|----|------|-------------|
| BUG-13 | Discovery | `"<broadcast>"` literal is invalid on Windows; use only `"255.255.255.255"` |
| BUG-14 | Discovery | `DiscoveryResponder` silently stops if port is already bound |
| BUG-18 | Workers | Stale workers not purged — `last_seen` tracked but no expiry sweep |
| BUG-22 | Discovery | Discovery response has no version field — incompatible server versions silently accepted |
| BUG-24 | Render | `_render_tile_local()` blocks Blender main thread (see §2 for planned fix) |
