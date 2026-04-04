# BlenderSplitter - Full Recode Blueprint

This document describes the system in implementation detail so the whole codebase can be rebuilt from scratch with the same behavior and stronger reliability.

## 1. Goal and Scope

BlenderSplitter is a distributed tile renderer for Blender:

- One node acts as `server` (scheduler + stitcher).
- Multiple nodes act as `workers` (tile render executors).
- All nodes auto-discover via UDP.
- Tiles are rendered by region borders and returned to server.
- Server writes a run folder containing final image (`master`) and per-tile images (`raw-splits`).

## 2. Runtime Architecture

### 2.1 Main Components

- `ui.py`
   - Blender operators, panels, progress display, tile preview, partition image window.
   - Reads and writes settings into `Scene.blendersplitter_settings`.
- `worker.py`
   - Central runtime manager (`DistributedRenderManager`).
   - Role handling (`server`, `worker`, `unassigned`).
   - Render scheduling, project sync, tile result collection, stitching.
- `network.py`
   - UDP discovery broadcast + responder.
- `robust_connection.py`
   - Reconnect policy (`ReconnectController`) with backoff and failover thresholds.
- `robust_protocol.py`
   - Transfer message constants and constructors.
- `robust_transfer.py`
   - Chunking and assembly for large tile result payloads.
- `tiles.py`
   - Tile generation and overlap calculations.
- `stitch.py`
   - Tile compositing into one final image.

### 2.2 Threading Model

- Blender main thread:
   - UI drawing, operator execution, Blender API calls that must stay on main thread.
   - Timer (`bpy.app.timers`) pumps manager queues.
- Background thread:
   - Dedicated `asyncio` event loop.
   - WebSocket server/client and async network IO.

## 3. State Model

`DistributedRenderManager` tracks:

- Connection state
   - `started`, `role`, `server_host`, `server_port`, `discovery_port`.
   - `connected_workers` map with socket and heartbeat info.
- Render state
   - `pending_jobs`, `completed_jobs`, `job_owner`, `job_attempts`.
   - `current_render_config`, `render_plan`, `expected_jobs`.
- Sync state
   - `sync_active`, `sync_progress`, `project_sync_results`, `sync_total_bytes`.
- Output state
   - `output_dir`, `current_output_root`, `current_master_dir`, `current_raw_splits_dir`.
- Transfer diagnostics
   - `transfer_stats` (inline tiles, chunked tiles, chunk message count).

## 4. Network and Discovery Flow

### 4.1 Startup

1. Node starts manager.
2. Repeated UDP discovery attempts for an existing server.
3. If found, node connects as worker via WebSocket.
4. If not found after retries + jitter, node starts local server.

### 4.2 Discovery Contract

- Discovery request magic: `BLENDER_SPLITTER_DISCOVERY_V1`.
- Discovery reply prefix: `BLENDER_SPLITTER_SERVER_V1` + compact JSON.
- Worker validates reply and uses sender IP as fallback when advertised host is invalid (loopback/empty).

## 5. WebSocket Message Protocol

### 5.1 Core Messages

- Worker registration: `register_worker` -> `registered`.
- Render job dispatch: `render_tile`.
- Render result: `tile_result`.
- Heartbeat: `ping` / `heartbeat`.
- Integrity check: `integrity_probe` / `integrity_probe_result`.

### 5.2 Chunked Tile Result Protocol

Used when encoded tile payload exceeds inline threshold.

- `tile_result_start`
   - `transfer_id`, `tile_id`, `worker_id`, `tile`, `total_size`, `total_chunks`
- `tile_result_chunk`
   - `transfer_id`, `index`, `data` (base64 chunk)
- `tile_result_complete`
   - `transfer_id`, `tile_id`, `worker_id`, `tile`, `ok`

Server-side assembler rehydrates these messages into one canonical `tile_result` object.

## 6. Render Scheduling and Execution

### 6.1 Plan Build

1. Read scene render resolution.
2. Compute node count:
   - `connected_workers` + optional server if `server_render_tiles` enabled.
3. Compute grid via `grid_for_worker_count`.
4. Compute overlap in pixels via `overlap_pixels`.
5. Build tiles via `generate_tiles`.
6. Assign tiles round-robin across targets.

### 6.2 Worker Tile Render

For each assigned tile:

1. Validate render signature integrity.
2. Set border region (`use_border`, `use_crop_to_border`, `border_*`).
3. Render still frame to temp PNG.
4. Encode PNG base64.
5. Return tile data to server (inline or chunked).

### 6.3 Server Collection and Reassignment

- On successful result:
   - write tile PNG into `raw-splits`.
   - append metadata to `completed_jobs`.
- On worker failure/disconnect:
   - increment attempt counter.
   - reassign tile to another worker or `MASTER` until retry limit.

### 6.4 Finalization

1. Sort completed tiles deterministically by core y/x.
2. Stitch using `stitch_tiles(...)`.
3. Write final image into `master` folder.

## 7. Project Sync Pipeline

### 7.1 Bundle Creation

- Zip project directory from saved `.blend` root.
- Exclude transient files (`.git`, `__pycache__`, `.DS_Store`, `.pyc`).
- Split into parts (default max part size ~512 MB).
- Compute SHA256 per part.

### 7.2 Transfer

- Server sends `project_sync_start` metadata.
- Streams binary chunks.
- Worker accumulates bytes into temporary zip.
- Worker verifies checksum, extracts project, acknowledges with `project_sync_ack`.

### 7.3 Worker Project Activation

- Worker finds received `.blend`.
- Schedules load in main thread via queue/timer path.

## 8. Filesystem Output Contract

Per render run:

```text
<output_base>/blendersplitter_<timestamp>/
   master/
      distributed_render.png
   raw-splits/
      <tile_id>_<uuid>.png
```

Rules:

- If `Output Folder` is set, use it as `<output_base>`.
- Else fallback to scene output directory (or temp if missing).

## 9. UI and Progress Contract

### 9.1 Panels and Operators

- Start/Stop network.
- Force server mode.
- Run integrity check.
- Start distributed render.
- Abort render, kick workers.
- Tile preview (overlay + generated partition image window).

### 9.2 Progress Fields

- Sync progress per worker:
   - current bytes, total bytes, part index, status, speed estimate.
- Transfer stats:
   - inline tiles, chunked tiles, chunk messages.
- Output paths:
   - run root, master dir, raw-splits dir.

## 10. Reliability Requirements for Recode

### 10.1 Connection Robustness

- Reconnect with bounded backoff.
- Rediscover server after configurable failures.
- Optional fallback to self-host mode after prolonged unreachable server.
- Heartbeat update and stale worker cleanup.

### 10.2 Transfer Robustness

- Chunk large payloads.
- Preserve ordering by index during assembly.
- Validate payload integrity where practical.
- Avoid oversized single WebSocket messages.

### 10.3 Crash Safety

- Keep Blender UI operations on main thread.
- Never run modal redraw loops from background network thread.
- Keep network loop isolated in background thread.

## 11. Recommended Recode Plan

### Phase 1: Core Contracts

1. Re-implement protocol constants/builders.
2. Re-implement chunker/assembler unit-tested without Blender.
3. Re-implement reconnect policy class.

### Phase 2: Runtime Core

1. Build manager state machine.
2. Implement startup/discovery/server-worker role logic.
3. Implement job queue and reassignment.

### Phase 3: Data Pipeline

1. Project sync with split parts + checksums.
2. Tile render return path with chunk fallback.
3. Stitch final output with deterministic ordering.

### Phase 4: Blender Integration

1. UI settings and operator wiring.
2. Main-thread timer queue pump.
3. Progress and diagnostics panels.

### Phase 5: Hardening

1. Fault injection tests (disconnect, timeout, corrupted chunk).
2. Large scene transfer soak tests.
3. Multi-worker long-run stability tests.

## 12. Minimal Test Matrix

- Single machine, server-only render.
- One worker, no project sync.
- One worker, project sync enabled.
- Two or more workers, mixed tile sizes.
- Forced worker disconnect mid-render.
- Large tile forcing chunked return.
- Discovery race (two machines start simultaneously).

## 13. Build and Packaging

Create add-on archive:

```bash
./compile.sh
```

Install ZIP in Blender Preferences -> Add-ons.

## 14. Practical Notes

- Ensure the `.blend` is saved before project sync.
- Keep all nodes on compatible Blender and add-on versions.
- Use wired LAN for best transfer stability.
- Tune chunk size and inline limits depending on network quality.
