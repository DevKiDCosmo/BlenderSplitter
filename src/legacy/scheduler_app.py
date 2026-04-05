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


def start_desktop_ui(state: SchedulerState):
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return

    root = tk.Tk()
    root.title("BlenderSplitter Scheduler")
    root.geometry("460x220")

    status_var = tk.StringVar(value="Starting...")
    workers_var = tk.StringVar(value="Workers: 0")
    sync_var = tk.DoubleVar(value=0.0)
    render_var = tk.DoubleVar(value=0.0)

    ttk.Label(root, text="Scheduler Status").pack(anchor="w", padx=10, pady=(10, 2))
    ttk.Label(root, textvariable=status_var).pack(anchor="w", padx=10)
    ttk.Label(root, textvariable=workers_var).pack(anchor="w", padx=10, pady=(8, 2))

    ttk.Label(root, text="Sync Progress").pack(anchor="w", padx=10)
    ttk.Progressbar(root, variable=sync_var, maximum=100.0, length=420).pack(anchor="w", padx=10)

    ttk.Label(root, text="Render Progress").pack(anchor="w", padx=10, pady=(8, 0))
    ttk.Progressbar(root, variable=render_var, maximum=100.0, length=420).pack(anchor="w", padx=10)

    def refresh():
        status_var.set(state.status)
        workers_var.set(f"Workers: {len(state.workers)}")
        sync_var.set(max(0.0, min(100.0, state.sync_progress)))
        render_var.set(max(0.0, min(100.0, state.render_progress)))
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
    ui_thread = threading.Thread(target=start_desktop_ui, args=(app.state,), daemon=True)
    ui_thread.start()

    asyncio.run(app.run())


if __name__ == "__main__":
    main()
