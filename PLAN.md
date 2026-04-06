Übersicht — aktueller Stand & nächste Schritte

Statusdatum: 2026-04-05

Vollmigrationsplan (neu): siehe `MIGRATION_PLAN_FULL_V2.md`.

Kurz: Ich habe die Codebasis verglichen, die Regressionen in `clean`/`sync` analysiert und gezielt in `worker.py` gehärtet. Packaging und Konfigurationspfad wurden ebenfalls bereinigt. Nachfolgend eine knappe Zusammenfassung des Erledigten und eine priorisierte Liste der verbleibenden Arbeiten mit konkreten nächsten Schritten.

Erledigt
- Diff-Analyse gegenüber stabilem Release durchgeführt (relevante Diffs: `worker.py`, `__init__.py`, `compile.sh`, `robust_protocol.py`).
- `worker.py`: aktive Socket-Filterung, `MSG_CLEAN_BLEND_ACK`-Warte-Logik, Clearing von stale state nach `Kick All` implementiert.
- Scheduling: Master-defer-Mechanismus implementiert (jeder Worker erhält initial ein Job, Master erst danach).
- Packaging: `compile.sh` erzeugt multimode ZIPs + `manifest`.
- Konfiguration: `__init__.py` auf config-driven Startup angepasst.
- Infrastruktur: Backoff/Jitter (`robust_connection.py`) und Protokoll-ACK (`robust_protocol.py`) angepasst.
- Sanity: `python -m py_compile` für geänderte Dateien — keine Syntaxfehler.

Offene / Priorisierte Aufgaben
E) Kamera Selektion für Batch Render.
e1) Network Handling async zum Rendern, damit nicht occupied und sofortige intervention möglich.
1) Worker Runtime Adapter (Blender-boundary): Ziel: `DistributedRenderManager` entkoppeln, klare Adapter-Schnittstelle für Blender-spezifische Calls. Nächster Schritt: RFC/Interface-Definition + eine kleine Refactor-PR, damit Tests möglich werden.
2) Integration Smoke Tests (Blender): Manuelle E2E-Prüfung (Start Server → verbinde 2+ Worker → `Kick All` → Reconnect → `Sync` → `Clean` → verteiltes Rendern). Nächster Schritt: Smoke-Test-Skript + Anweisungen für Blender-Run.
3) Unit/Integration Tests für Clean/Sync ACKs: automatisierte Tests, die `clean_worker_blends` und `sync_project_files` gegen gemockte/simulierte Worker-Sockets prüfen.
4) UI-Status: Sichtbare Meldung, wenn Master-defer aktiv ist (z. B. "Warte auf initiale Worker-Delegation"), damit Nutzer nicht verwirrt sind.
5) Repository Layout / Modulgrenzen: Optionaler Refactor in `src/`-Module (build, runtime, scheduler, config) zur Verbesserung der Testbarkeit und klaren Grenzen.

Empfohlene Reihenfolge (kurzfristig):
- A: Smoke-Test-Skript + Ausführen (prüft realen Integrations-Flow)
- B: Kleine UI-Status-Änderung (sichtbares Feedback für Master-defer)
- C: Tests für Clean/Sync ACKs (stabilisiert Deployment)
- D: Worker-Adapter RFC und schrittweiser Refactor
- E: Optionaler Repo-Layout-Refactor

Konkrete nächste Schritte, die ich für dich ausführen kann (choose one):
- Erstelle ein ausführbares Smoke-Test-Skript + Anleitung für Blender (empfohlen erster Schritt).
- Implementiere die UI-Statusmeldung für Master-defer (kleiner PR).
- Beginne das RFC für die Worker Runtime Adapter-Schnittstelle (erstes Design-Dokument).

Wenn du auswählst, starte ich sofort mit der gewählten Aufgabe und lege dazu die detaillierten Sub‑TODOs an.