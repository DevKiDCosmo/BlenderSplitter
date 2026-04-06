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
| BUG-16 | `src/legacy/worker.py` | `_worker_socket` not set to `None` after `async with` context exits — stale reference | Set to `None` after each connection cycle and on all exception paths |
| BUG-19 | `src/legacy/ui.py` | "Update Information" / "Reset" buttons gated by `is_worker` — workers can't refresh state | Removed `row.enabled = not is_worker` gate from those buttons |
| BUG-20 | `src/legacy/worker.py` | `sync_project_files()` calls `force_start_server()` when not server — broken by BUG-02 fix | Returns descriptive error instead of attempting force-start |

### Medium / Pending 🔲

| ID | File | Description | Recommended Fix |
|----|------|-------------|-----------------|
| BUG-13 | `src/legacy/network.py` | `"<broadcast>"` literal not valid on Windows | Use only `"255.255.255.255"` and `"127.0.0.1"` |
| BUG-14 | `src/legacy/worker.py` | `DiscoveryResponder` silently stops if discovery port already bound | Log/surface error in `status` |
| BUG-18 | `src/legacy/worker.py` | Stale workers not purged — `last_seen` updated but no expiry timer | Add periodic sweep in `process_main_thread_queues` |
| BUG-22 | `src/legacy/network.py` | Discovery response has no version field — incompatible servers silently accepted | Add `"version"` field to `DiscoveryResponder` reply |
| BUG-24 | `src/legacy/worker.py` | `_render_tile_local` blocks Blender main thread | Needs subprocess render or post-frame handler |

---

## Sequential Phases

### Phase 0 – Safety Baseline ✅
Freeze behaviour with smoke checks (start cluster, sync, clean, render, abort).
Add minimal regression checklist in `README.md`.
Test harness for non-Blender modules (scheduler, sync) in `tests/`.

### Phase 1 – Facade Completion ✅
Route all UI operations through `src/ui/controller.py` and
`src/runtime/facade.py`.
`src/legacy/ui.py` uses `_get_mgr()` via `UiController.get_legacy_manager_for_display()`.

### Phase 2 – Wrapper Integration ✅
`src/runtime/facade.py` now imports directly from `src/legacy/worker`.
`__init__.py` now imports UI from `src/legacy/ui` directly.
Root compatibility wrappers excluded from ZIP by `compile.sh`.
`parse_json()` and `ConfigStore.load()` fixed.

### Phase 3 – Force Server & Connection Stability ✅
**Force Server** takeover flow fully implemented end-to-end:
- Old master broadcasts `MSG_NEW_MASTER` to remaining workers on takeover.
- Workers wait 2 s then reconnect to the new master.
- If new master is unreachable, `ReconnectController` eventually promotes a
  worker to self-host (existing `should_self_host()` logic).

`master` and `master_worker` modes now discover a server before self-hosting.
`master` / `worker` ZIP modes auto-start on Blender addon load.
Socket cleanup, JSON parse guard, `sync_project_files` guard fixed.
UI: Force Server has `poll()`, buttons renamed, Update Information available
for all roles.

### Phase 4 – Scheduler / Sync Extraction ✅
Dispatch eligibility and assignment rules live in `src/scheduler/core.py`.
Sync bundle and ACK handling live in `src/sync/service.py`.
Tests: immediate redispatch, capacity, worker-loss reassignment, ACK timeout,
chunk integrity, partial failure aggregation.

### Phase 5 – Network/Adapter Extraction (pending)
Implement concrete adapters for `src/network/ports.py`.
Move reconnect/discovery policy into `src/network/`.
Integrate `src/blender_adapter/bpy_adapter.py`.

### Phase 6 – Startup and Composition Root (pending)
Wire addon startup via `src/runtime` composition.
Validate behaviour parity with legacy.

### Phase 7 – Legacy Removal (pending)
Remove root compatibility wrappers after ZIP-exclusion is validated.
Remove `src/legacy/*` once parity + tests are green.

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
