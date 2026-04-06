# BlenderSplitter – Migration & Refactor Plan

_Last updated: 2026-04-06_

## Goal
Consolidate all runtime logic under `src/`, remove unused root-level wrapper
files from the ZIP distribution, and harden the codebase for security,
maintainability, and readability.

## Phases

### Phase 0 – Safety Baseline ✅
- Freeze behaviour with smoke checks (start cluster, sync, clean, render, abort).
- Add minimal regression checklist in `README.md`.
- Test harness for non-Blender modules (scheduler, sync) in `tests/`.

### Phase 1 – Facade Completion ✅
- Route all UI operations through `src/ui/controller.py` and
  `src/runtime/facade.py`.
- `src/legacy/ui.py` uses `_get_mgr()` via `UiController.get_legacy_manager_for_display()`.

### Phase 2 – Wrapper Integration (current) ✅
- `src/runtime/facade.py` now imports directly from `src/legacy/worker`
  instead of going through the root `worker.py` compatibility wrapper.
- `__init__.py` now imports UI from `src/legacy/ui` directly.
- Root compatibility wrappers (`network.py`, `worker.py`, `ui.py`, …)
  excluded from ZIP by `compile.sh`.
- `src/network/messages.py` `parse_json()` now actually parses JSON.
- `src/config/store.py` `load()` now actually parses the JSON file.

### Phase 3 – Scheduler Extraction ✅
- Dispatch eligibility and assignment rules live in `src/scheduler/core.py`.
- Tests: immediate redispatch, capacity, worker-loss reassignment.

### Phase 4 – Sync/Transfer Extraction ✅
- Sync bundle and ACK handling live in `src/sync/service.py`.
- Tests: ACK timeout, chunk integrity, partial failure aggregation.

### Phase 5 – Network/Adapter Extraction (pending)
- Implement concrete adapters for `src/network/ports.py`.
- Move reconnect/discovery policy into `src/network/`.
- Integrate `src/blender_adapter/bpy_adapter.py`.

### Phase 6 – Startup and Composition Root (pending)
- Wire addon startup via `src/runtime` composition.
- Validate behaviour parity with legacy.

### Phase 7 – Legacy Removal (pending)
- Remove root compatibility wrappers after ZIP-exclusion is validated.
- Remove `src/legacy/*` once parity + tests are green.

## Compile / Distribution
`compile.sh` produces four ZIP artefacts under `dist/`:
- `*-worker.zip`
- `*-master.zip`
- `*-user.zip`
- `*-worker_master.zip`

Root wrappers, docs, tests, and shell scripts are excluded from every ZIP.

## Open Tasks
See `todo.md`.
