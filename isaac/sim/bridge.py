import queue
import socket
import threading
import time
from collections.abc import Callable

from .protocol import encode, read_message

RETRY = 2.0
OUTBOX = 256


class Bridge:
    def __init__(self, host: str, port: int, hello: Callable[[], dict], on_action: Callable[[str, float, float, float], None], on_view: Callable[[str, list | None], None] | None = None) -> None:
        self.host, self.port = host, port
        self.hello = hello
        self.on_action = on_action
        self.on_view = on_view
        self.outbox: queue.Queue[bytes] = queue.Queue(OUTBOX)
        self.connected = threading.Event()
        self.running = True
        self.sent = 0
        self.dropped = 0
        self.thread = threading.Thread(target=self.run, name="bridge", daemon=True)

    def start(self) -> "Bridge":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.running = False

    def send(self, message: bytes) -> None:
        if not self.connected.is_set():
            return
        try:
            self.outbox.put_nowait(message)
        except queue.Full:
            self.dropped += 1

    def run(self) -> None:
        while self.running:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=5)
            except OSError as e:
                print(f"brain not reachable at {self.host}:{self.port} ({e}); retrying in {RETRY:.0f}s", flush=True)
                time.sleep(RETRY)
                continue
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            print(f"connected to brain at {self.host}:{self.port}", flush=True)
            while not self.outbox.empty():
                self.outbox.get_nowait()
            try:
                sock.sendall(encode(self.hello()))
                self.connected.set()
                rx = threading.Thread(target=self.receive, args=(sock,), name="bridge-rx", daemon=True)
                rx.start()
                while self.running and rx.is_alive():
                    try:
                        msg = self.outbox.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    sock.sendall(msg)
                    self.sent += 1
            except OSError as e:
                print(f"brain connection lost ({e})", flush=True)
            finally:
                self.connected.clear()
                sock.close()
            if self.running:
                time.sleep(RETRY)

    def receive(self, sock: socket.socket) -> None:
        try:
            while True:
                header, _ = read_message(sock)
                kind = header.get("type")
                if kind == "action":
                    self.on_action(str(header.get("truck")), float(header.get("throttle", 0)), float(header.get("steer", 0)), time.monotonic())
                elif kind == "view" and self.on_view:
                    self.on_view(str(header.get("camera")), header.get("box"))
        except (OSError, ValueError, ConnectionError):
            pass
