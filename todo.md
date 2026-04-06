# BlenderSplitter – TODO

_Last updated: 2026-04-06_

## Done ✅
- [x] Merged all markdown status files into `UNIVERSAL_STATUS.md` (previous session).
- [x] Scheduler extraction: `src/scheduler/core.py` with full test coverage.
- [x] Sync extraction: `src/sync/service.py` with full test coverage.
- [x] `src/runtime/facade.py` imports legacy manager directly from `src/legacy/worker` — root `worker.py` wrapper no longer required at runtime.
- [x] `__init__.py` imports UI directly from `src/legacy/ui` — root `ui.py` wrapper no longer required at runtime.
- [x] `src/network/messages.py` `parse_json()` now correctly parses JSON.
- [x] `src/config/store.py` `load()` now reads, parses, and merges the JSON config file.
- [x] `compile.sh` rewritten: excludes all root compatibility wrappers from ZIP.
- [x] `tests/test_messages_and_config.py` added covering fixed `parse_json()` and `ConfigStore.load()`.
- [x] **Issue #2 (scheduler bottleneck)**: `target_ready_at[owner]` now set to `0.0` instead of `time.time()`, guaranteeing immediate re-dispatch eligibility.
- [x] **Issue #4/#5-A (import diagnostics)**: `facade.py._get_legacy_module()` now records every attempted import path and its exception in `_legacy_error`.
- [x] **Issue #4/#5-B (config drift)**: Sync bundle now includes `runtime_config`; workers apply it deterministically on receive.
- [x] **Clean after Kick bug**: `kick_all_workers` now sends `MSG_CLEAN_BLEND` atomically before closing sockets; error message improved.
- [x] **UI consistency**: Sync/Clean row changed to `align=True`; row disabled when no workers connected; hint label added.
- [x] **Pre-distribution**: `start_distributed_render` pre-distributes up to `ceil(tiles/3)/targets` jobs per target at the start.
- [x] **README.md**: Added Mermaid architecture/startup/render/sync flow diagrams, Module Map table, Quick Start.
- [x] `plan.md` and `todo.md` created and kept up to date.

### Phase 3 – Force Server & Connection Stability ✅ (2026-04-06)
- [x] **BUG-01 Force Server no poll()**: Added `poll()` to `BLENDERSPLITTER_OT_start_server`; button enabled only when `role == "worker"`.
- [x] **BUG-02 Force Server wrong guard**: `force_start_server()` now returns error if `role != "worker"`.
- [x] **BUG-03/04 MSG_NEW_MASTER missing**: Added `MSG_NEW_MASTER` + `MSG_NEW_MASTER_DELAY_S` to `robust_protocol.py`.
- [x] **BUG-05 Old master doesn't notify workers**: `_handle_worker_socket` now broadcasts `MSG_NEW_MASTER` to all remaining workers on `MSG_SERVER_TAKEOVER`.
- [x] **BUG-06 set_force_server stub missing**: Added no-op `set_force_server()` to `DistributedRenderManager`.
- [x] **BUG-07/08 master/master_worker mode skips discovery**: Both modes now call `discover_server()` first; start server only if none found.
- [x] **BUG-09 Auto-start not happening**: `__init__.py` `_startup()` now calls `mgr.start()` for `master`/`worker` modes automatically.
- [x] **BUG-10 Worker doesn't reconnect to new master**: `_handle_worker_message` sets `_pending_new_master`; `_connect_as_worker` sleeps 2s then reconnects.
- [x] **BUG-11 Wrong button labels**: "Reset" → "Update Information", "Hard Reset" → "Reset".
- [x] **BUG-12/17 JSON parse crash in `_handle_worker_socket`**: Added `try/except` around `json.loads(raw)`.
- [x] **BUG-16 `_worker_socket` not cleared on disconnect**: Set `self._worker_socket = None` after `async with` context exits.
- [x] **BUG-19 Update Information disabled for workers**: Removed `row.enabled = not is_worker` gate from Update Information/Reset row.
- [x] **sync_project_files wrong fallback**: Removed invalid `force_start_server()` call; returns error if not server.
- [x] Config templates updated: `worker.json` sets `server_render_tiles: false`; `always` arrays cleared (mode-based auto-start now sufficient).

## Pending 🔲

### Short-term
- [ ] **Master render responsiveness** (Issue #4/#5-C): local tile render at master blocks Blender's main thread (`bpy.ops.render.render()` is synchronous). Needs subprocess render or async render via post-frame handler.
- [ ] **Camera selection**: Batch render camera selection UI.
- [ ] **UI status for master-defer**: Label when master is in worker-priming phase.
- [ ] **Smoke test script**: Executable E2E instructions.
- [ ] **Stale worker cleanup**: Workers whose `last_seen` heartbeat exceeds a threshold should be auto-removed from `connected_workers`.
- [ ] **Discovery port conflict detection**: `DiscoveryResponder._run` catches `OSError` and silently stops — log or surface this condition so server is discoverable.

### Medium-term
- [ ] **Phase 5 – Network/Adapter Extraction**: Concrete adapters for `src/network/ports.py`.
- [ ] **Phase 6 – Startup Composition Root**: Wire startup entirely via `src/runtime`.
- [ ] **Phase 7 – Legacy Removal**: Remove root wrappers and `src/legacy/*` after parity confirmed.

### Testing
- [ ] Tests for `clean_worker_blends` and `sync_project_files` with mocked sockets.
- [ ] Tests for `runtime_config` propagation in sync bundle.
- [ ] Tests for `MSG_NEW_MASTER` flow (takeover broadcast + worker reconnect).
