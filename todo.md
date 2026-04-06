# BlenderSplitter – TODO

_Last updated: 2026-04-06_

## Done ✅
- [x] Merged all markdown status files into `UNIVERSAL_STATUS.md` (previous session).
- [x] Scheduler extraction: `src/scheduler/core.py` with full test coverage.
- [x] Sync extraction: `src/sync/service.py` with full test coverage.
- [x] `src/runtime/facade.py` imports legacy manager directly from
      `src/legacy/worker` — root `worker.py` wrapper no longer required at runtime.
- [x] `__init__.py` imports UI directly from `src/legacy/ui` — root `ui.py`
      wrapper no longer required at runtime.
- [x] `src/network/messages.py` `parse_json()` now correctly parses JSON
      (was returning `{"raw": payload}` unconditionally — security/correctness bug).
- [x] `src/config/store.py` `load()` now reads, parses, and merges the JSON
      config file (was reading the file but discarding the result).
- [x] `compile.sh` rewritten: syntax-check passes on failure-free `src/` only,
      excludes all root compatibility wrappers and development files from ZIP.
- [x] `plan.md` created.
- [x] `todo.md` created (this file).

## Pending 🔲

### Short-term
- [ ] **Network async** (issue e1): Network handling async to Blender render so
      it is not blocked and immediate intervention is possible.
- [ ] **Camera selection** (issue E): Batch render camera selection UI.
- [ ] **UI status for master-defer**: Visible message when master-defer is
      active ("Waiting for initial worker delegation").
- [ ] **Smoke test script**: Executable E2E smoke-test script + instructions
      for Blender run (start server → 2+ workers → Kick All → Reconnect →
      Sync → Clean → distributed render).

### Medium-term
- [ ] **Phase 5 – Network/Adapter Extraction**: Concrete adapters for
      `src/network/ports.py`; move reconnect/discovery into `src/network/`.
- [ ] **Phase 6 – Startup and Composition Root**: Wire startup entirely via
      `src/runtime`; keep legacy fallback guarded by `USE_LEGACY_RUNTIME`.
- [ ] **Phase 7 – Legacy Removal**: Remove root wrappers and `src/legacy/*`
      once parity + tests are green.

### Testing
- [ ] Tests for `clean_worker_blends` and `sync_project_files` against mocked
      worker sockets (Clean/Sync ACK integration tests).
- [ ] Tests for `ConfigStore.load()` (load from file, malformed JSON, missing
      file).
- [ ] Tests for `parse_json()` in `src/network/messages.py`.
