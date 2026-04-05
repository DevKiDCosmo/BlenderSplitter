import asyncio
import json
import socket
import unittest

import websockets

from scheduler_app import SchedulerApp


class SchedulerDispatchUnitTests(unittest.TestCase):
    def test_dequeue_returns_next_job(self):
        app = SchedulerApp(host="127.0.0.1", port=9999)
        app.enqueue_render_job({"tile_id": "t1", "tile": {"id": "t1"}})

        job = app.dequeue_next_job()

        self.assertIsNotNone(job)
        self.assertEqual("t1", job["tile_id"])
        self.assertIsNone(app.dequeue_next_job())


class SchedulerDispatchIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.port = self._free_port()
        self.app = SchedulerApp(host="127.0.0.1", port=self.port)
        self.server = await websockets.serve(self.app._handle_client, self.app.host, self.app.port, max_size=None)

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def test_worker_ready_receives_render_job(self):
        self.app.enqueue_render_job({"tile_id": "tile-1", "tile": {"id": "tile-1"}})

        uri = f"ws://127.0.0.1:{self.port}"
        async with websockets.connect(uri, max_size=None) as ws:
            await ws.send(json.dumps({"type": "register_worker", "node_id": "worker-a"}))
            registered = json.loads(await ws.recv())
            self.assertEqual("registered", registered["type"])

            await ws.send(json.dumps({"type": "worker_ready", "worker_id": "worker-a"}))
            assigned = json.loads(await ws.recv())

        self.assertEqual("render_tile", assigned["type"])
        self.assertEqual("tile-1", assigned["tile_id"])

    async def test_progress_is_broadcast_to_subscriber(self):
        uri = f"ws://127.0.0.1:{self.port}"
        async with websockets.connect(uri, max_size=None) as monitor, websockets.connect(uri, max_size=None) as worker:
            await monitor.send(json.dumps({"type": "subscribe_status"}))
            subscribed = json.loads(await monitor.recv())
            self.assertEqual("subscribed", subscribed["type"])

            await worker.send(json.dumps({"type": "register_worker", "node_id": "worker-b"}))
            _ = json.loads(await worker.recv())

            await worker.send(json.dumps({"type": "sync_progress", "progress": 22.5}))
            await worker.send(json.dumps({"type": "render_progress", "progress": 33.0}))
            pushed = json.loads(await monitor.recv())

        self.assertEqual("scheduler_status", pushed["type"])
        self.assertAlmostEqual(22.5, float(pushed["sync_progress"]))
        self.assertAlmostEqual(33.0, float(pushed["render_progress"]))

    @staticmethod
    def _free_port() -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port


if __name__ == "__main__":
    unittest.main()
