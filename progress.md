# Refactor Progress

## Goal
Move runtime architecture into `src/` and split oversized modules into testable boundaries.

## Module Checklist
- [x] `src/runtime` scaffolded (facade + orchestrator)
- [x] `src/runtime` wired to legacy `worker.manager()` for start/stop/sync/clean/render/cancel/kick
- [x] `src/network` scaffolded (ports + retry + messages)
- [x] `src/scheduler` scaffolded (models + core)
- [x] `src/sync` scaffolded (models + service stubs)
- [x] `src/blender_adapter` scaffolded (`bpy` boundary)
- [x] `src/config` scaffolded (single source of truth)
- [x] `src/ui` scaffolded (facade-only controller + view model)
- [x] `src/legacy/ui.py`: all operator `execute` methods route through `UiController`/`SplitterRuntimeFacade`
- [x] `src/legacy/ui.py`: direct `manager()` import removed; display code uses `_get_mgr()` via controller
- [x] `src/runtime/facade.py`: `get_legacy_manager_for_display()` added as migration display bridge
- [x] `src/ui/controller.py`: `get_legacy_manager_for_display()` forwarded from facade
- [x] `src/blender_adapter/bpy_adapter.py`: concrete `bpy` implementations for `render_tile`, `open_scene`, `reset_to_blank`, `collect_sync_files`
- [x] `__init__.py`: startup consolidated via `SplitterRuntimeFacade` composition root; guarded by `_BPY_AVAILABLE`
- [x] `tests/test_scheduler.py`: boundary tests for plan/capacity/reassign/retry (27 tests total)
- [x] `tests/test_sync.py`: boundary tests for ACK/timeout/chunking/integrity (27 tests total)
- [x] `conftest.py`: bpy stub for running tests outside Blender
- [x] `compile.sh`: extended to also validate `src/` modules

## Log
- 2026-04-05: Started 8 subagent workflow and created first-pass scaffolds under `src/`.
- 2026-04-05: Added `progress.md` tracker and initial completion state.
- 2026-04-05: Wired runtime facade calls to legacy manager with dynamic import fallback and status projection.
- 2026-04-05: Migrated `ui.py` operators for Sync/Clean/Render/Abort to `src/ui/controller.py` facade path.
- 2026-04-05: 15-agent control pass completed (12 architecture checks, 1 progress review, 2 error analyses) and consolidated in `issue.md`.
- 2026-04-05: Phase 1 complete — all operator `execute` methods in `src/legacy/ui.py` route through `UiController`/`SplitterRuntimeFacade`; `manager()` import removed from `src/legacy/ui.py`. BpyAdapter concrete implementations added. `__init__.py` startup consolidated via facade. Boundary tests added (27 tests, all green).

## Next Actions
1. Move protocol/sync logic from `src/legacy/worker.py` into `src/sync` in small slices (Phase 3).
2. Introduce transport adapter implementation under `src/network` and bind it in runtime orchestration (Phase 4).
3. Consolidate `__init__.py` further by removing legacy fallback once parity + tests are green (Phase 5/6).
4. Remove root compatibility wrappers once all paths go through `src/` (Phase 6).

## Update Rule
Update this file after each migration milestone (scaffold, wiring, tests, cleanup).
