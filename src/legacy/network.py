import json
import socket
import threading
import time

DISCOVERY_MAGIC = "BLENDER_SPLITTER_DISCOVERY_V3"
DISCOVERY_REPLY = "BLENDER_SPLITTER_SERVER_V3"


def json_dumps(data: dict) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def _best_local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def discover_server(discovery_port: int, timeout: float = 1.5) -> tuple[str, int] | None:
    msg = DISCOVERY_MAGIC.encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)

    try:
        # "<broadcast>" is not a valid literal on Windows; use numeric addresses only.
        targets = [
            ("255.255.255.255", int(discovery_port)),
            ("127.0.0.1", int(discovery_port)),
        ]
        for target in targets:
            try:
                _ = sock.sendto(msg, target)
            except OSError:
                continue

        deadline = time.time() + timeout
        while time.time() < deadline:
            data, addr = sock.recvfrom(1024)
            decoded = data.decode("utf-8", errors="ignore")
            if not decoded.startswith(DISCOVERY_REPLY):
                continue

            payload = decoded[len(DISCOVERY_REPLY) :]
            details = json.loads(payload)
            # Ignore replies from incompatible server versions.
            reply_version = details.get("version", "")
            if reply_version and reply_version != "v3":
                continue
            advertised_host = details.get("host")
            if advertised_host in (None, "", "127.0.0.1", "localhost", "0.0.0.0", "::1"):
                host = str(addr[0])
            else:
                host = str(advertised_host)
            return host, int(details["port"])
    except Exception:
        return None
    finally:
        sock.close()


class DiscoveryResponder:
    def __init__(self, discovery_port: int, websocket_port: int):
        self.discovery_port = int(discovery_port)
        self.websocket_port = int(websocket_port)
        self.host = _best_local_ip()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        # Set to a non-empty string if the responder failed to start (e.g. port
        # already bound).  Callers may surface this in the UI status field.
        self.bind_error: str = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.bind_error = ""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                # Bind to all interfaces intentionally so the responder receives
                # broadcast packets regardless of which network interface the
                # discovery client uses.  The responder only replies to valid
                # DISCOVERY_MAGIC packets — it does not expose any sensitive data.
                sock.bind(("", self.discovery_port))
            except OSError as exc:
                self.bind_error = f"DiscoveryResponder: UDP-Port {self.discovery_port} konnte nicht gebunden werden: {exc}"
                return
            sock.settimeout(0.5)

            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(1024)
                except socket.timeout:
                    continue
                except OSError:
                    break

                if data.decode("utf-8", errors="ignore") != DISCOVERY_MAGIC:
                    continue

                response = (
                    DISCOVERY_REPLY
                    + json_dumps({"host": self.host, "port": self.websocket_port, "version": "v3"})
                ).encode("utf-8")
                try:
                    _ = sock.sendto(response, addr)
                except OSError:
                    continue
        finally:
            sock.close()
