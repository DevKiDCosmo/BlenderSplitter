import argparse
import asyncio
import json
import threading
from pathlib import Path

import websockets


class SchedulerState:
    def __init__(self):
        self.workers = {}
        self.sync_progress = 0.0
        self.render_progress = 0.0
        self.status = "Idle"
        self.shutdown_requested = False
        # Set by main() so the UI kick button can schedule coroutines on it.
        self.loop = None


class SchedulerApp:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.state = SchedulerState()
        self.render_queue = []

    def enqueue_render_job(self, job: dict):
        self.render_queue.append(dict(job))

    def dequeue_next_job(self):
        if not self.render_queue:
            return None
        return self.render_queue.pop(0)

    async def run(self):
        async with websockets.serve(self._handle_client, self.host, self.port, max_size=None):
            self.state.status = f"Scheduler listening on {self.host}:{self.port}"
            await asyncio.Future()

    async def _handle_client(self, ws):
        worker_id = None
        try:
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "register_worker":
                    worker_id = msg.get("node_id", "unknown")
                    self.state.workers[worker_id] = {"last_status": "connected"}
                    self.state.status = f"Worker connected: {len(self.state.workers)}"
                    await ws.send(json.dumps({"type": "registered", "node_id": worker_id}))
                    continue

                if msg_type == "worker_status":
                    wid = msg.get("worker_id") or worker_id or "unknown"
                    self.state.workers.setdefault(wid, {})["last_status"] = msg.get("status", "")
                    self.state.status = msg.get("status", self.state.status)
                    continue

                if msg_type == "worker_ready":
                    next_job = self.dequeue_next_job()
                    if next_job is None:
                        await ws.send(json.dumps({"type": "no_job"}))
                        continue
                    payload = {
                        "type": "render_tile",
                        "tile_id": next_job.get("tile_id"),
                        "tile": next_job.get("tile", {}),
                    }
                    await ws.send(json.dumps(payload))
                    continue

                if msg_type == "sync_progress":
                    self.state.sync_progress = float(msg.get("progress", 0.0))
                    continue

                if msg_type == "render_progress":
                    self.state.render_progress = float(msg.get("progress", 0.0))
                    continue
        finally:
            if worker_id and worker_id in self.state.workers:
                self.state.workers.pop(worker_id, None)
                self.state.status = f"Worker disconnected: {len(self.state.workers)}"


def start_desktop_ui(state: SchedulerState, app: "SchedulerApp"):
    """Launch the Tkinter desktop monitor window.

    Displays:
    * Status line and worker count
    * Per-worker status table (worker ID, last status)
    * Sync and Render progress bars
    * Action buttons: Stop Scheduler, Kick All Workers

    The window polls ``state`` every 250 ms for live updates.
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return

    root = tk.Tk()
    root.title("BlenderSplitter – Cluster Monitor")
    root.geometry("560x480")
    root.resizable(True, True)

    # ── Top status strip ──────────────────────────────────────────────────
    status_frame = ttk.LabelFrame(root, text="Status", padding=6)
    status_frame.pack(fill="x", padx=10, pady=(8, 4))

    status_var = tk.StringVar(value="Starting…")
    workers_var = tk.StringVar(value="Workers online: 0")
    ttk.Label(status_frame, textvariable=status_var, anchor="w").pack(fill="x")
    ttk.Label(status_frame, textvariable=workers_var, anchor="w").pack(fill="x")

    # ── Progress bars ─────────────────────────────────────────────────────
    prog_frame = ttk.LabelFrame(root, text="Progress", padding=6)
    prog_frame.pack(fill="x", padx=10, pady=4)

    sync_var = tk.DoubleVar(value=0.0)
    render_var = tk.DoubleVar(value=0.0)

    ttk.Label(prog_frame, text="Sync").grid(row=0, column=0, sticky="w", padx=(0, 6))
    ttk.Progressbar(prog_frame, variable=sync_var, maximum=100.0, length=440).grid(row=0, column=1, sticky="ew")
    ttk.Label(prog_frame, text="Render").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
    ttk.Progressbar(prog_frame, variable=render_var, maximum=100.0, length=440).grid(row=1, column=1, sticky="ew", pady=(4, 0))
    prog_frame.columnconfigure(1, weight=1)

    # ── Worker table ──────────────────────────────────────────────────────
    table_frame = ttk.LabelFrame(root, text="Connected Workers", padding=6)
    table_frame.pack(fill="both", expand=True, padx=10, pady=4)

    columns = ("worker_id", "status")
    tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)
    tree.heading("worker_id", text="Worker ID")
    tree.heading("status", text="Last Status")
    tree.column("worker_id", width=280, stretch=True)
    tree.column("status", width=200, stretch=True)

    vsb = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")

    # ── Action buttons ────────────────────────────────────────────────────
    btn_frame = ttk.Frame(root, padding=6)
    btn_frame.pack(fill="x", padx=10, pady=(4, 8))

    def stop_scheduler():
        state.shutdown_requested = True
        root.after(300, root.destroy)

    def kick_workers():
        failed = []
        for wid in list(state.workers.keys()):
            ws = state.workers[wid].get("socket") if isinstance(state.workers.get(wid), dict) else None
            if ws is not None:
                try:
                    import asyncio
                    loop = state.loop
                    if loop and loop.is_running():
                        asyncio.run_coroutine_threadsafe(ws.close(), loop)
                except Exception as exc:
                    failed.append(wid)
                    import logging
                    logging.getLogger(__name__).debug("kick_workers: close failed for %s: %s", wid, exc)
        state.workers.clear()
        if failed:
            state.status = f"Kick incomplete – {len(failed)} socket(s) failed to close"
        else:
            state.status = "All workers kicked"

    ttk.Button(btn_frame, text="Stop Scheduler", command=stop_scheduler).pack(side="left", padx=4)
    ttk.Button(btn_frame, text="Kick All Workers", command=kick_workers).pack(side="left", padx=4)

    # ── Polling refresh ───────────────────────────────────────────────────
    _prev_worker_keys: list = []

    def refresh():
        status_var.set(state.status)
        workers_var.set(f"Workers online: {len(state.workers)}")
        sync_var.set(max(0.0, min(100.0, state.sync_progress)))
        render_var.set(max(0.0, min(100.0, state.render_progress)))

        # Rebuild worker table if the worker set changed
        current_keys = sorted(state.workers.keys())
        if current_keys != _prev_worker_keys:
            _prev_worker_keys.clear()
            _prev_worker_keys.extend(current_keys)
            for item in tree.get_children():
                tree.delete(item)
            for wid in current_keys:
                info = state.workers.get(wid, {})
                last_status = str(info.get("last_status", "connected")) if isinstance(info, dict) else "connected"
                tree.insert("", "end", values=(wid, last_status))

        if not getattr(state, "shutdown_requested", False):
            root.after(250, refresh)

    refresh()
    root.mainloop()


def parse_args():
    parser = argparse.ArgumentParser(description="BlenderSplitter external scheduler")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--host", default="127.0.0.1", help="Scheduler host")
    parser.add_argument("--port", type=int, default=9876, help="Scheduler port")
    return parser.parse_args()


def load_config(path: str):
    cfg_path = Path(path)
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main():
    args = parse_args()
    cfg = load_config(args.config)

    scheduler_cfg = cfg.get("external_scheduler", {}) if isinstance(cfg, dict) else {}
    host = str(scheduler_cfg.get("host", args.host))
    port = int(scheduler_cfg.get("port", args.port))

    app = SchedulerApp(host=host, port=port)

    # Pass the asyncio event loop reference to state so the UI can schedule
    # coroutines (e.g. kick-worker close calls) on it safely.
    loop = asyncio.new_event_loop()
    app.state.loop = loop

    ui_thread = threading.Thread(target=start_desktop_ui, args=(app.state, app), daemon=True)
    ui_thread.start()

    asyncio.set_event_loop(loop)
    loop.run_until_complete(app.run())


if __name__ == "__main__":
    main()
