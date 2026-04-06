# Migration Plan V2 - Full Codebase to src

Date: 2026-04-05

## Goal
Consolidate all runtime code under `src/`, remove legacy duplication safely, and keep addon behavior stable during migration.

## Current State
- Runtime code exists in `src/*` (new architecture) and `src/legacy/*` (migrated legacy implementation).
- Root modules (`worker.py`, `ui.py`, `network.py`, etc.) are compatibility wrappers pointing to `src/legacy/*`.
- Dispatch bottleneck fix has been applied in `src/legacy/worker.py`.

## Scope
- In scope:
  - Complete migration from `src/legacy/*` into target modules (`src/runtime`, `src/network`, `src/scheduler`, `src/sync`, `src/blender_adapter`, `src/ui`, `src/config`).
  - Remove root wrappers at the end.
  - Add boundary tests for critical scheduler/sync behavior.
- Out of scope:
  - UI redesign and non-functional visual changes.

## Phase 0 - Baseline and Safety
1. Freeze behavior with smoke checks in Blender:
   - Start cluster
   - Sync project
   - Clean workers
   - Distributed render
   - Abort render
2. Add a minimal regression checklist in `README.md`.
3. Add test harness for non-Blender modules.

## Phase 1 - Facade Completion (No Behavior Change)
1. Route all remaining `ui.py` operations through `src/ui/controller.py` and `src/runtime/facade.py`.
2. Stop direct `manager()` usage in `ui.py`.
3. Keep root wrappers active.

Exit criteria:
- `ui.py` no longer accesses `manager()` directly.

## Phase 2 - Scheduler Extraction
1. Move dispatch eligibility and assignment rules from `src/legacy/worker.py` into `src/scheduler/core.py`.
2. Keep one scheduling contract only (single source of truth).
3. Add tests:
   - immediate redispatch after completion
   - capacity respected
   - worker-loss reassignment

Exit criteria:
- Scheduler decisions are produced by `src/scheduler/core.py`.

## Phase 3 - Sync/Transfer Extraction
1. Move sync bundle and ACK handling from `src/legacy/worker.py` into `src/sync/service.py`.
2. Keep chunk assembly/verification deterministic.
3. Add tests:
   - ACK timeout handling
   - chunk integrity mismatch
   - partial worker ACK failure aggregation

Exit criteria:
- Sync/Clean flow runs through `src/sync/service.py` contracts.

## Phase 4 - Network/Adapter Extraction
1. Implement concrete adapters for `src/network/ports.py`.
2. Move reconnect/discovery orchestration into `src/network/*`.
3. Integrate `src/blender_adapter/bpy_adapter.py` for bpy boundary usage.

Exit criteria:
- `src/legacy/worker.py` no longer contains transport/discovery policy logic.

## Phase 5 - Startup and Composition Root
1. Make addon startup (`__init__.py`) wire the runtime via `src/runtime` composition.
2. Keep legacy fallback guarded by feature flag (`USE_LEGACY_RUNTIME`).
3. Validate behavior parity.

Exit criteria:
- Startup path defaults to `src` runtime stack.

## Phase 6 - Legacy Removal and Cleanup
1. Remove root compatibility wrappers (`worker.py`, `ui.py`, `network.py`, etc.).
2. Remove `src/legacy/*` once parity + tests are green.
3. Keep cleanup script for caches/artifacts.

Exit criteria:
- No runtime imports from root wrappers or `src/legacy`.

## Test Plan (TDD)
1. RED: worker completion should trigger immediate eligibility for next job.
   GREEN: scheduling gate allows next assignment without artificial delay.
2. RED: sync ACK timeout should report partial failure cleanly.
   GREEN: aggregate status from worker-level ACK outcomes.
3. RED: disconnected worker jobs should be reassigned.
   GREEN: reassign via scheduler contract.

## Cleanup Checklist
- [ ] No `__pycache__` directories
- [ ] No `.DS_Store`
- [ ] No direct `manager()` calls in UI layer
- [ ] No imports from root runtime files in `src/*`
- [ ] `py_compile` passes for all Python files

## Rollback Strategy
- Keep root wrappers and `src/legacy` until Phase 6.
- If a phase breaks Blender runtime, switch to legacy via `USE_LEGACY_RUNTIME` and revert only phase-local changes.
