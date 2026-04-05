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

## Log
- 2026-04-05: Started 8 subagent workflow and created first-pass scaffolds under `src/`.
- 2026-04-05: Added `progress.md` tracker and initial completion state.
- 2026-04-05: Wired runtime facade calls to legacy manager with dynamic import fallback and status projection.
- 2026-04-05: Migrated `ui.py` operators for Sync/Clean/Render/Abort to `src/ui/controller.py` facade path.
- 2026-04-05: 15-agent control pass completed (12 architecture checks, 1 progress review, 2 error analyses) and consolidated in `issue.md`.

## Next Actions
1. Route remaining operators in `ui.py` through `src/ui/controller.py` (start/stop/kick/reset).
2. Move protocol/sync logic from `worker.py` into `src/sync` in small slices.
3. Introduce transport adapter implementation under `src/network` and bind it in runtime orchestration.
4. Add boundary tests for scheduler and sync ACK behavior.

## Update Rule
Update this file after each migration milestone (scaffold, wiring, tests, cleanup).
