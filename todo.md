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
- [x] **BUG-20 `sync_project_files` wrong fallback**: Returns error if not server.
- [x] Config templates updated.

### Phase 4b – Batch Camera, Monitor, Docs ✅ (2026-04-06)
- [x] **BUG-15 `start_distributed_render` wrong fallback**: Removed `force_start_server()` call when not server; returns error instead.
- [x] **BUG-25 Dead `dispatch_cooldown_seconds` field**: Removed.
- [x] **Batch camera render**: `start_batch_camera_render(camera_names)` + `_render_batch_camera_at_index()` + `_advance_batch_camera()` in `worker.py`.
- [x] **Batch camera UI**: `batch_cameras` property + `BLENDERSPLITTER_OT_batch_camera_render` operator in `ui.py`.
- [x] **Scheduler Monitor UI**: Enhanced Tkinter app in `scheduler_app.py` — worker table, Stop Scheduler, Kick All Workers, loop reference for async kick.
- [x] **`docs/ARCHITECTURE.md`**: Created — dual-thread model, startup modes, force server protocol, render pipeline (single + batch camera), sync protocol, module map, config reference, known limitations.
- [x] **`plan.md`** updated with Phase 4b, Phase 5-8, Utils/Misc section, full bug audit table.

### Phase 4c – Async Sync, Stale Expiry, Discovery Hardening, Tile Audit ✅ (2026-04-06)
- [x] **BUG-13 Windows broadcast**: Removed `"<broadcast>"` literal from `network.py`; using `"255.255.255.255"` only.
- [x] **BUG-14 DiscoveryResponder silent fail**: `bind_error` attribute added; master surfaces it in `status` after 150ms startup delay.
- [x] **BUG-18 Stale worker expiry**: `_purge_stale_workers()` called every 30s in `process_main_thread_queues`; 90s heartbeat timeout; tiles reassigned on expiry.
- [x] **BUG-22 Discovery version field**: `"version": "v3"` added to `DiscoveryResponder` reply; `discover_server()` skips replies with wrong version.
- [x] **BUG-26 Sequential project sync**: `_sync_project_to_workers_async` now uses `asyncio.gather` — all workers download in parallel (O(size) instead of O(n·size)).
- [x] **BUG-27 Missing tile audit**: `_finalize_render` now runs a tile audit pass before stitching; re-queues any tiles not in `completed_jobs`.
- [x] **`docs/SEQUENCE_DIAGRAMS.md`**: Created — Mermaid diagrams for normal job distribution, worker failure/reassign, stale-worker expiry, parallel sync, sync failure, render finalization/tile audit, force-server takeover, UDP discovery, integrity check.
- [x] **`docs/ARCHITECTURE.md`** updated: parallel sync, tile audit, stale expiry, discovery version/broadcast fixes, BUG table updated.
- [x] **`plan.md`** updated: Phase 4c added, BUG-13/14/18/22 moved to fixed, BUG-26/27 added.

## Pending 🔲

### Short-term (Medium Priority)
- [ ] **Camera selection**: UI for selecting which cameras appear in "Batch Cameras" (currently free-text). Add an enum/multi-select operator.
- [ ] **UI status for master-defer**: Show a label when master is in worker-priming phase.
- [ ] **Smoke test script**: Executable E2E instructions.

### Medium-term
- [ ] **Phase 5 – Network/Adapter Extraction**: Concrete adapters for `src/network/ports.py`.
- [ ] **Phase 6 – Master Subprocess Render**: Offload `_render_tile_local` to subprocess; fix BUG-24 (main-thread blocking).
- [ ] **Phase 7 – Startup Composition Root**: Wire startup entirely via `src/runtime`.
- [ ] **Phase 8 – Legacy Removal**: Remove root wrappers and `src/legacy/*` after parity confirmed.

### Utils / Misc
- [ ] Document `src/legacy/trans.py` CLI usage in README (black-pixel transparency tool).
- [ ] `src/legacy/stitch.py`: add overlap-crop option to avoid visible seams.
- [ ] `compile.sh`: include `docs/` in ZIP optionally (for documentation bundles).

### Testing
- [ ] Tests for `clean_worker_blends` and `sync_project_files` with mocked sockets.
- [ ] Tests for `runtime_config` propagation in sync bundle.
- [ ] Tests for `MSG_NEW_MASTER` flow (takeover broadcast + worker reconnect).
- [ ] Tests for batch camera render (mock `_render_batch_camera_at_index`, verify queue advance).
- [ ] Tests for `_purge_stale_workers` (mock `last_seen`, verify reassignment).
- [ ] Tests for parallel sync (verify `asyncio.gather` called, verify per-worker progress tracking).
