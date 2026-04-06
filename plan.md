# BlenderSplitter – Migration & Refactor Plan

_Last updated: 2026-04-06_

## Goal
Consolidate all runtime logic under `src/`, remove unused root-level wrapper
files from the ZIP distribution, and harden the codebase for security,
maintainability, and readability.

## Open GitHub Issues

| # | Title | Status |
|---|-------|--------|
| 2 | Workers wait between jobs (scheduler cooldown) | **Fixed** – `target_ready_at` now set to `0.0` instead of `time.time()` |
| 4 | Legacy manager loading + worker config drift + master responsiveness | **Fixed** – structured import diagnostics, `runtime_config` in sync bundle |
| 5 | (same as #4, duplicate) | **Fixed** |

---

## Bug Audit — Full Report (2026-04-06)

### Critical / Fixed ✅

| ID | File | Description | Fix |
|----|------|-------------|-----|
| BUG-01 | `src/legacy/ui.py` | Force Server button always enabled regardless of role | Added `poll()` — enabled only when `role == "worker"` |
| BUG-02 | `src/legacy/worker.py` | `force_start_server()` starts server even on uninitialized node | Guard added: returns error if `role != "worker"` |
| BUG-03 | `src/legacy/worker.py` | `MSG_SERVER_TAKEOVER` handler on old master doesn't notify remaining workers | Broadcasts `MSG_NEW_MASTER` to all connected workers before releasing |
| BUG-04 | `src/legacy/robust_protocol.py` | `MSG_NEW_MASTER` message type missing | Added `MSG_NEW_MASTER = "new_master"` and `MSG_NEW_MASTER_DELAY_S = 2.0` |
| BUG-05 | `src/legacy/worker.py` | Workers don't handle `MSG_NEW_MASTER` / don't reconnect to new master | `_handle_worker_message` sets `_pending_new_master`; `_connect_as_worker` sleeps 2s then reconnects |
| BUG-06 | `src/legacy/worker.py` | `set_force_server()` declared in Protocol but not implemented | No-op stub added to `DistributedRenderManager` |
| BUG-07 | `src/legacy/worker.py` | `master` mode starts server immediately without discovery | Now calls `discover_server()` first; becomes server only if none found |
| BUG-08 | `src/legacy/worker.py` | `master_worker` mode starts server immediately without discovery | Same fix as BUG-07 |
| BUG-09 | `__init__.py` | `master`/`worker` modes never auto-start despite config intent | `_startup()` now calls `mgr.start()` for `mode in ("master", "worker")` or if `"NETWORK"` in `always_flags` |
| BUG-11 | `src/legacy/ui.py` | Button labels wrong: "Reset" / "Hard Reset" | Renamed: "Update Information" / "Reset" |
| BUG-12 | `src/legacy/worker.py` | `json.loads(raw)` in `_handle_worker_socket` not guarded — malformed frame drops connection | Wrapped in `try/except`; continues without dropping connection |
| BUG-15 | `src/legacy/worker.py` | `start_distributed_render()` calls `force_start_server()` when not server (broken by BUG-02 fix) | Returns descriptive error instead |
| BUG-16 | `src/legacy/worker.py` | `_worker_socket` not set to `None` after `async with` context exits — stale reference | Set to `None` after each connection cycle and on all exception paths |
| BUG-19 | `src/legacy/ui.py` | "Update Information" / "Reset" buttons gated by `is_worker` — workers can't refresh state | Removed `row.enabled = not is_worker` gate from those buttons |
| BUG-20 | `src/legacy/worker.py` | `sync_project_files()` calls `force_start_server()` when not server | Returns descriptive error instead |
| BUG-25 | `src/legacy/worker.py` | `dispatch_cooldown_seconds = 1.0` field declared but never used (dead code) | Removed |
| BUG-13 | `src/legacy/network.py` | `"<broadcast>"` literal not valid on Windows | Removed; using `"255.255.255.255"` only |
| BUG-14 | `src/legacy/network.py` | `DiscoveryResponder` silently stops if discovery port already bound | `bind_error` attribute + surfaced in `status` |
| BUG-18 | `src/legacy/worker.py` | Stale workers not purged — `last_seen` updated but no expiry timer | `_purge_stale_workers()` sweep every 30s, 90s timeout |
| BUG-22 | `src/legacy/network.py` | Discovery response has no version field — incompatible servers silently accepted | `"version": "v3"` added to reply; client validates |
| BUG-26 | `src/legacy/worker.py` | Project sync was sequential (O(n·size)); bottleneck on large clusters | `asyncio.gather` — all workers download in parallel |
| BUG-27 | `src/legacy/worker.py` | No tile audit before stitch — missing tiles could leave holes in output | Tile audit pass in `_finalize_render`; re-queues missing tiles |

### Medium / Pending 🔲

| ID | File | Description | Recommended Fix |
|----|------|-------------|-----------------|
| BUG-24 | `src/legacy/worker.py` | `_render_tile_local` blocks Blender main thread | Subprocess render (Phase 6) |

---

## Sequential Phases

### Phase 0 – Safety Baseline ✅
Freeze behaviour with smoke checks (start cluster, sync, clean, render, abort).
Add minimal regression checklist in `README.md`.
Test harness for non-Blender modules (scheduler, sync) in `tests/`.

### Phase 1 – Facade Completion ✅
Route all UI operations through `src/ui/controller.py` and `src/runtime/facade.py`.
`src/legacy/ui.py` uses `_get_mgr()` via `UiController.get_legacy_manager_for_display()`.

### Phase 2 – Wrapper Integration ✅
`src/runtime/facade.py` now imports directly from `src/legacy/worker`.
`__init__.py` now imports UI from `src/legacy/ui` directly.
Root compatibility wrappers excluded from ZIP by `compile.sh`.
`parse_json()` and `ConfigStore.load()` fixed.

### Phase 3 – Force Server & Connection Stability ✅
Full `MSG_NEW_MASTER` takeover broadcast protocol implemented.
`master` and `master_worker` modes discover first, self-host only if needed.
`master` / `worker` ZIP modes auto-start on Blender addon load.
Socket cleanup, JSON parse guard, `sync_project_files` guard, `start_distributed_render` guard fixed.
UI: Force Server has `poll()`, buttons renamed, Update Information available for all roles.

### Phase 4 – Scheduler / Sync Extraction ✅
Dispatch eligibility and assignment rules live in `src/scheduler/core.py`.
Sync bundle and ACK handling live in `src/sync/service.py`.
Tests: immediate redispatch, capacity, worker-loss reassignment, ACK timeout,
chunk integrity, partial failure aggregation.

### Phase 4b – Batch Camera & Scheduler Monitor ✅
Batch camera render: `start_batch_camera_render(camera_names)` iterates cameras,
renders tiles + stitches per camera, then advances automatically.
`BLENDERSPLITTER_OT_batch_camera_render` operator + `batch_cameras` property in settings.
Scheduler monitor desktop app enhanced: worker table, Stop/Kick buttons, live refresh.
Dead `dispatch_cooldown_seconds` field removed.
`docs/ARCHITECTURE.md` created documenting dual-thread model, startup modes, force server,
render pipeline, sync protocol, module map, config keys.

### Phase 4c – Async Sync, Stale Expiry, Discovery Hardening, Tile Audit ✅
BUG-13: `"<broadcast>"` removed — discovery uses `255.255.255.255` only (Windows safe).
BUG-14: `DiscoveryResponder.bind_error` surfaces port-bind failure in master `status`.
BUG-18: `_purge_stale_workers()` sweep every 30s; 90s heartbeat timeout; jobs reassigned.
BUG-22: `"version": "v3"` added to discovery reply; client skips incompatible replies.
BUG-26: `_sync_project_to_workers_async` now uses `asyncio.gather` — all workers receive
         chunks in parallel (from O(n·size) to O(size)).
BUG-27: Tile audit pass in `_finalize_render` — any tile not in `completed_jobs` is
         re-queued before stitching proceeds.
`docs/SEQUENCE_DIAGRAMS.md` created with Mermaid diagrams for all protocol flows.
`docs/ARCHITECTURE.md` updated.

### Phase 5 – Network/Adapter Extraction (pending)
Implement concrete adapters for `src/network/ports.py`.
Move reconnect/discovery policy into `src/network/`.
Integrate `src/blender_adapter/bpy_adapter.py`.

### Phase 6 – Master Subprocess Render (pending)
Offload master tile renders to a subprocess (`blender --background --python`).
Main-thread timer becomes non-blocking; master throughput matches workers.
No more dispatch stall between master tiles (BUG-24).

### Phase 7 – Startup and Composition Root (pending)
Wire addon startup via `src/runtime` composition.
Validate behaviour parity with legacy.

### Phase 8 – Legacy Removal (pending)
Remove root compatibility wrappers after ZIP-exclusion is validated.
Remove `src/legacy/*` once parity + tests are green.

### Utils / Misc (pending)
- `src/legacy/trans.py` — standalone CLI tool; document usage in README.
- `src/legacy/stitch.py`: add overlap-crop option to avoid visible seams.
- `compile.sh`: include `docs/` in ZIP optionally (for documentation bundles).

---

## Compile / Distribution
`compile.sh` produces four ZIP artefacts under `dist/`:
- `*-worker.zip` — worker-only, `server_render_tiles: false`, auto-starts
- `*-master.zip` — master-only, auto-starts, searches for server then self-hosts
- `*-user.zip` — flexible, user-controlled via N-Panel
- `*-worker_master.zip` — searches first, user clicks "Start Cluster"

Root wrappers, docs, tests, and shell scripts are excluded from every ZIP.

## Open Tasks
See `todo.md`.
