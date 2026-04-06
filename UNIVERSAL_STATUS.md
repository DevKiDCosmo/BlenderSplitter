# BlenderSplitter - Universeller Projektstatus

Stand: 2026-04-06

## Zielbild
Migration der Runtime-Architektur nach `src/` mit klaren Modulgrenzen, testbaren Boundaries und stabilem Blender-Addon-Verhalten. Alle wrappers entweder beseitigen oder anders verwerten, aber nicht mehr in root. Entweder in wrapper/ oder in src/ integrieren. DAnn muss auch Pipeline nochmal geupdatet werden. Updaten von pipeline und compile, sodass in der zip nur die nötigen dateien sind. Es sollte nicht einfach das ganze Projekt dort gespeichert sein, sondern nur das nötige. Also nur py code + alle configuration files, die nötig sind. Die Issue auf Github sollten durch den Plan automatisch beseitig sein. D.h., dass du gleich die Fehler beheben solltest, du auch in den Github Issues da sind.

## Konsolidierter Ist-Stand (aus allen bisherigen Markdown-Dateien)

### Architektur und Migration
- `src/`-Modulstruktur ist vorhanden: `runtime`, `network`, `scheduler`, `sync`, `blender_adapter`, `config`, `ui`.
- Runtime-Fassade (`src/runtime/facade.py`) und Orchestrierung sind als zentrale Einstiegspunkte vorhanden.
- Legacy-Pfade existieren weiterhin unter `src/legacy/*` als Migrationsbruecke.
- Root-Dateien (`worker.py`, `ui.py`, `network.py`, usw.) sind weiterhin als Kompatibilitaets-/Wrapper-Ebene im Projekt vorhanden.

### Bereits umgesetzt (Feature-/Implementierungsstand)
- UI-Route auf Controller/Fassade fuer die zentralen Operatoren (u. a. Sync/Clean/Render/Abort) wurde umgesetzt.
- Direkte `manager()`-Abhaengigkeit aus relevanten UI-Pfaden wurde reduziert bzw. entfernt.
- `src/blender_adapter/bpy_adapter.py` wurde mit konkreten `bpy`-Operationen fuer Kernfaelle erweitert.
- Startup wurde auf Fassade/Composition-Root-Pfad konsolidiert (mit Blender-Guard `_BPY_AVAILABLE`).
- Scheduler- und Sync-Boundary-Tests wurden aufgebaut:
  - `tests/test_scheduler.py` (Plan/Kapazitaet/Reassign/Retry)
  - `tests/test_sync.py` (ACK/Timeout/Chunking/Integritaet)
- `compile.sh` wurde erweitert, damit auch `src/`-Module geprueft werden.
- Packaging-Pipeline inkl. multimode ZIP/Manifest-Flow wurde angepasst.
- Netzwerk-/Protokoll-Robustheit wurde gehaertet (Backoff/Jitter und ACK-Verhalten).

### Laufzeit- und Funktionsbild
- Discovery ueber UDP und Rollenfindung (Server/Worker) sind Teil des Flows.
- Tile-Rendering erfolgt border-basiert mit Queue-Dispatch und Reassign-Logik.
- Sync-Pipeline nutzt Bundle/Transfer/ACK-Prinzip.
- Ausgabe erfolgt pro Lauf in Run-Ordnerstruktur mit `master` und `raw-splits`.

## GitHub-Issue-Status
- Repository: `DevKiDCosmo/BlenderSplitter`
- Abfrage: offene Issues (`is:issue is:open`)
- Ergebnis: **0 offene Issues**

## Offene Aufgaben (priorisiert)

### P0 - Kritisch / Blocker
1. Scheduler-Entscheidungslogik als eindeutige Quelle in `src/scheduler/core.py` komplettieren und final verdrahten.
2. Sync-/Transfer-Kernlogik aus Legacy vollstaendig nach `src/sync/service.py` uebernehmen (ACK/Timeout/Chunking/Fehleraggregation).
3. Restliche direkte Legacy-Zugriffe in UI-Routen entfernen und vollstaendig ueber `src/ui/controller.py` + Fassade fuehren.

### P1 - Stabilisierung
1. Transport-/Discovery-Adapter in `src/network` produktiv finalisieren und in Runtime-Orchestrierung binden.
2. `__init__.py` weiter vereinfachen und Legacy-Fallback nach Paritaetsnachweis reduzieren.
3. Blender-Integrationstests (Smoke-E2E) als wiederholbare Checkliste/Skript festziehen.

### P2 - Abschluss / Cleanup
1. Root-Kompatibilitaetswrapper entfernen, sobald alle Pfade auf `src/` laufen.
2. `src/legacy/*` entfernen, sobald Verhalten + Tests gruen sind.
3. Cleanup-Hygiene absichern (`__pycache__`, `.DS_Store`, Importpfade, Dokumentation).

### Zusatzthemen aus Planung
1. Kamera-Selektion fuer Batch-Render ergaenzen.
2. Asynchrones Network-Handling waehrend Render verbessern, damit UI/Interventionen nicht blockieren.
3. Sichtbarer UI-Status fuer Master-Defer/Delegationsphase.

## Empfohlene naechste Reihenfolge
1. Blender-Smoke-Test-Flow als reproduzierbare Routine ausfuehren.
2. Scheduler/Sync-Endverdrahtung in `src/*` abschliessen.
3. Legacy-Fallback reduzieren und danach Wrapper/Legacy entfernen.

## Build, Test, Validierung
- Build/Package: `./compile.sh`
- Python-Syntaxcheck: `python -m py_compile ...`
- Boundary-Tests: `tests/test_scheduler.py`, `tests/test_sync.py`

## Abschlusskriterien fuer Vollmigration
- Keine Runtime-Imports mehr ueber Root-Wrapper oder `src/legacy/*`.
- UI nutzt keine direkten `manager()`-Zugriffe mehr.
- Scheduler- und Sync-Entscheidungen laufen ueber `src/scheduler` und `src/sync`.
- Blender-Smoke-Flow erfolgreich: Start Cluster, Sync, Clean, Distributed Render, Abort.
