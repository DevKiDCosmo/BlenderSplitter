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

## Pending 🔲

### Short-term
- [ ] **Master render responsiveness** (Issue #4/#5-C): local tile render at master blocks Blender's main thread (`bpy.ops.render.render()` is synchronous). Needs subprocess render or async render via post-frame handler.
- [ ] **Camera selection**: Batch render camera selection UI.
- [ ] **UI status for master-defer**: Label when master is in worker-priming phase.
- [ ] **Smoke test script**: Executable E2E instructions.

### Medium-term
- [ ] **Phase 5 – Network/Adapter Extraction**: Concrete adapters for `src/network/ports.py`.
- [ ] **Phase 6 – Startup Composition Root**: Wire startup entirely via `src/runtime`.
- [ ] **Phase 7 – Legacy Removal**: Remove root wrappers and `src/legacy/*` after parity confirmed.

### Testing
- [ ] Tests for `clean_worker_blends` and `sync_project_files` with mocked sockets.
- [ ] Tests for `runtime_config` propagation in sync bundle.
