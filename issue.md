# Architektur-Audit (12 Kontrollen + 1 Fortschritt + 2 Fehleranalysen)

Datum: 2026-04-05

## Was bereits gut ist

- `src/`-Modulstruktur wurde angelegt (`runtime`, `network`, `scheduler`, `sync`, `blender_adapter`, `config`, `ui`).
- `src/runtime/facade.py` ist als zentrale API vorhanden und bridged bereits auf Legacy-`worker.manager()`.
- Erste UI-Operatoren wurden auf Controller/Fassade umgestellt: Sync, Clean, Render, Abort.
- `progress.md` existiert und dokumentiert Meilensteine.

## Fehler

1. Hybrid-Architektur in `ui.py`: viele direkte `manager()`-Aufrufe umgehen die Fassade.
2. `src/blender_adapter/bpy_adapter.py` ist noch überwiegend Stub (`NotImplementedError`), nicht in `worker.py` integriert.
3. `src/scheduler/core.py`: `plan()` ist faktisch leer (liefert keine echten Dispatch-Entscheidungen).
4. `src/sync/service.py`: Kernmethoden sind nur Stubs (`NotImplementedError`).
5. Startup/Composition Root ist noch legacy-lastig (`__init__.py` verwendet primär `manager()` direkt).
6. `compile.sh`/Doku sind noch nicht vollständig auf die `src/`-Migration als primäre Architektur ausgerichtet.

## Was fehlt

1. Vollständige UI-Entkopplung:
	- `ui.py` muss alle restlichen Operatoren auf `UiController`/`SplitterRuntimeFacade` umstellen.
2. Facade-API-Lücken für UI-Use-Cases:
	- z. B. effektiver Mode, Integritätscheck, Requirement-Installation, Reset/Force-Server-Pfade.
3. Echte Adapter-Implementierungen:
	- `TransportPort`/`DiscoveryPort` Produktionsadapter (WebSocket/UDP).
	- `BlenderOpsPort` produktive `bpy`-Implementierung statt Stubs.
4. Scheduler-Logik:
	- echte `plan()`-Strategie inkl. Kapazität/Fairness/Retry-Reassign.
5. Sync-Engine:
	- Bundle-Build, ACK-Waiting, Timeout/Retry, Chunk-Resend, robustes Error-Handling.
6. Testabdeckung auf Boundary-Ebene:
	- Facade-Flow, Scheduler, Sync/ACK/Timeout, Reassign-Szenarien.

## Was gemacht werden muss (priorisiert)

### P0 (Blocker)

1. UI vollständig über Fassade routen:
	- `start/stop/kick/reset/force server/install requirements/integrity` umstellen.
2. `SchedulerCore.plan()` implementieren (nicht mehr leer).
3. `SyncService`-Methoden mit realer Logik füllen (Bundle/ACK/Timeout/Chunking).

### P1 (stabilisieren)

1. `BpyAdapter` produktiv machen (`render_tile`, `open_scene`, `reset_to_blank`, `collect_sync_files`).
2. `worker.py` schrittweise auf Adapter und `src/*`-Module umverdrahten.
3. Startup in `__init__.py` als echten Composition Root konsolidieren.

### P2 (Härtung)

1. Retry/Discovery/Heartbeat in `src/network` mit echten Adaptern und klaren Fehlerpfaden.
2. Build- und README/PLAN-Dokumentation auf `src/` als primäre Architektur aktualisieren.
3. Orchestrator-State-Tracking erweitern (Operation-Status, Dauer, Fehlerzustände).

## Was passieren könnte (Risiken)

1. Wenn UI hybrid bleibt:
	- Refactoring-Kosten steigen, Fehlerursachen bleiben verteilt, Testbarkeit bleibt niedrig.
2. Wenn Scheduler/Sync Stub bleibt:
	- Render/Sync kann starten, aber Jobs werden nicht sauber verteilt oder abgeschlossen.
3. Wenn Adapter nicht integriert werden:
	- `worker.py` bleibt God-Object, Änderungen bleiben riskant und schwer zu validieren.
4. Wenn ACK/Timeout-Fälle nicht robust sind:
	- Hängende Sync-Läufe, stille Teilfehler, inkonsistente Worker-Zustände.
5. Wenn Startup/Build nicht nachgezogen werden:
	- Neue `src/`-Struktur wird umgangen oder unvollständig in Artefakte übernommen.

## Konkrete nächste 7 Arbeitspakete

1. `ui.py`: Restoperatoren auf Controller/Fassade umstellen.
2. `src/runtime/facade.py`: fehlende UI-relevante Methoden ergänzen.
3. `src/scheduler/core.py`: `plan()` + Kapazitätslogik + Tests.
4. `src/sync/service.py`: ACK/Timeout/Chunking-Flow implementieren + Tests.
5. `src/blender_adapter/bpy_adapter.py`: echte `bpy`-Umsetzung.
6. `__init__.py`: Startup über Facade/Composition Root konsolidieren.
7. `compile.sh`, `README.md`, `PLAN.md`: `src`-Migration als Standardpfad dokumentieren/validieren.

## Prüfergebnis (Zusammenfassung)

- 12 Kontrollagenten: durchgeführt.
- 1 Fortschrittsagent: durchgeführt.
- 2 Fehleragenten: durchgeführt.
- Konsolidierte Findings: in dieser Datei dokumentiert.
