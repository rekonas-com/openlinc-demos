"""Live view of every headband stream in the browser.

EEG left and right, acceleration, rotation and ambient light scroll on one time axis, the
headband's clock, and the pebble turns as the head does. The EEG is shown without its drift
(0.5 Hz high-pass); everything else is shown exactly as the bridge delivers it.

This program subscribes to the bridge like any other, serves one page on this PC only and sends
that page what is new, 20 times a second. A browser cannot receive the bridge's messages itself.
The page draws the signals with uPlot and the pebble with three.js, both loaded from the
internet; without a connection it says what is missing.

Run with ``uv run main.py``. It starts the bridge if none is running, and ends it again if it
started it; ``--no-bridge`` leaves the bridge to you.
"""

import argparse
import json
import math
import queue
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from openlinc import control

HERE = Path(__file__).parent
PORT = 47110
STREAMS = ("eeg/raw", "motion/orientation", "motion/raw", "light/lux")
KEPT_S = 30.0
"""How much of each stream a page gets when it opens, and can show at most."""
TICK_S = 0.05
"""How often the pages get what is new."""
ASSETS = {
    "baijamjuree_regular.ttf": "font/ttf",
    "baijamjuree_bold.ttf": "font/ttf",
    "somnolinc-wordmark.svg": "image/svg+xml",
}
"""Files of the package's pairing page that this page uses too, with their types."""
MODEL = HERE / "somnolinc_pebble.glb"
BRIDGE = [sys.executable, "-c", "from openlinc.bridge.cli import main; main()"]
"""The bridge's command line, run with the Python this program runs with."""
BRIDGE_START_S = 15.0
"""How long a bridge that was just started may take to answer."""


class Feed:
    """Collects what is new from the headband and hands it to every open page.

    Parameters
    ----------
    headband
        A running headband that keeps at least ``KEPT_S`` of each stream.
    """

    def __init__(self, headband: control.Headband) -> None:
        self._headband = headband
        self._lock = threading.Lock()
        self._pages: set[queue.SimpleQueue] = set()
        self._session: int | None = None
        self._sent_us = dict.fromkeys(STREAMS, 0)  # time of the newest sample handed out

    def _samples(self, stream: str, seconds: float, after_us: int) -> dict:
        """Return a stream's samples from the latest seconds that are newer than a time."""
        times, values = self._headband.window(stream, seconds)
        new = times > after_us
        rows = [
            [value if math.isfinite(value) else None for value in row]
            for row in values[new].tolist()
        ]
        return {"t": times[new].tolist(), "v": rows}

    def _status(self) -> dict:
        """Return the headband's state and session for a message."""
        headband = self._headband
        return {"state": headband.state, "receiving": headband.receiving, "session": self._session}

    def tick(self) -> None:
        """Hand every open page the samples that arrived since the last call."""
        with self._lock:
            message = {}
            session = self._headband.session
            if session != self._session:  # every stream started afresh
                self._session = session
                self._sent_us = dict.fromkeys(STREAMS, 0)
                message["reset"] = True
            streams = {}
            for stream in STREAMS:
                new = self._samples(stream, 2.0, self._sent_us[stream])
                if new["t"]:
                    self._sent_us[stream] = new["t"][-1]
                    streams[stream] = new
            message |= {"streams": streams} | self._status()
            text = json.dumps(message)
            for page in self._pages:
                page.put(text)

    def open(self) -> tuple[queue.SimpleQueue, str]:
        """Add a page; return its queue of messages and the message with all that is kept."""
        with self._lock:
            streams = {stream: self._samples(stream, KEPT_S, 0) for stream in STREAMS}
            for stream, kept in streams.items():  # only what the other pages have got too
                until = self._sent_us[stream]
                count = sum(1 for time_us in kept["t"] if time_us <= until)
                kept["t"], kept["v"] = kept["t"][:count], kept["v"][:count]
            first = {"reset": True, "streams": streams}
            page: queue.SimpleQueue = queue.SimpleQueue()
            self._pages.add(page)
            return page, json.dumps(first | self._status())

    def close(self, page: queue.SimpleQueue) -> None:
        """Remove a page that has gone."""
        with self._lock:
            self._pages.discard(page)

    def run(self, stop: threading.Event) -> None:
        """Hand out what is new every ``TICK_S`` until stopped."""
        while not stop.wait(TICK_S):
            self.tick()


def handler(feed: Feed, port: int) -> type[BaseHTTPRequestHandler]:
    """Return the class that answers the page's requests.

    Parameters
    ----------
    feed
        Where the messages for the page come from.
    port
        The port the server listens on; requests that name another host are refused.
    """
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - the name the standard library calls
            # A page of another site that reaches this server under a name of its own must not
            # get the data.
            if self.headers.get("Host") not in hosts:
                self.send_error(HTTPStatus.FORBIDDEN)
            elif self.path == "/":
                self._file((HERE / "page.html").read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/stream":
                self._stream()
            elif self.path == "/model.glb" and MODEL.exists():
                self._file(MODEL.read_bytes(), "model/gltf-binary")
            elif self.path.removeprefix("/assets/") in ASSETS:
                name = self.path.removeprefix("/assets/")
                data = resources.files("openlinc.bridge").joinpath("assets", name).read_bytes()
                self._file(data, ASSETS[name])
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def _file(self, data: bytes, kind: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _stream(self) -> None:
            """Send the page everything kept, then what is new, until the page goes."""
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            page, text = feed.open()
            try:
                while True:
                    self.wfile.write(f"data: {text}\n\n".encode())
                    self.wfile.flush()
                    text = page.get()
            except OSError:
                pass  # the page was closed
            finally:
                feed.close(page)
                self.close_connection = True

        def log_message(self, *_: object) -> None:
            """Write no line per request."""

    return Handler


def bridge_status(more: tuple[str, ...] = ()) -> dict | None:
    """Return the running bridge's status, or None if no bridge answers.

    Parameters
    ----------
    more
        Further options for the bridge's command line, for example another local port.
    """
    asked = subprocess.run([*BRIDGE, "status", *more], capture_output=True, text=True, check=False)
    return json.loads(asked.stdout) if asked.returncode == 0 else None


def ensure_bridge(more: tuple[str, ...] = ()) -> subprocess.Popen | None:
    """Make sure a bridge runs and the wearer gets to its QR code.

    Without a bridge, one is started; it opens its pairing page itself. A bridge that runs but
    has no phone linked is asked to open its pairing page again. A linked bridge is left alone.

    Parameters
    ----------
    more
        Further options for the bridge's command line, for example other ports.

    Returns
    -------
    subprocess.Popen or None
        The bridge this call started, which the caller ends; None if one was running already.
    """
    status = bridge_status(more)
    if status is not None:
        if status.get("link") != "linked":
            subprocess.run([*BRIDGE, "run", "--if-running", "open", *more], check=False)
        return None
    started = subprocess.Popen([*BRIDGE, "run", *more])
    waited = time.monotonic()
    while bridge_status(more) is None and started.poll() is None:
        if time.monotonic() - waited > BRIDGE_START_S:
            break
        time.sleep(0.2)
    return started


def open_when_linked(address: str, stop: threading.Event, at_once: bool = False) -> None:
    """Open the page in the browser once a phone is linked, so the pairing page comes first.

    Parameters
    ----------
    address
        The page's address.
    stop
        Set when the program ends; the page is then not opened any more.
    at_once
        Whether to open the page without waiting for a phone.
    """
    if not at_once and (bridge_status() or {}).get("link") != "linked":
        print("Scan the QR code with the app; the live view opens once the phone is linked.")
        while (bridge_status() or {}).get("link") != "linked":
            if stop.wait(1.0):
                return
    webbrowser.open(address)


def end_bridge(bridge: subprocess.Popen) -> None:
    """End a bridge that :func:`ensure_bridge` started."""
    if bridge.poll() is None:
        bridge.terminate()
        try:
            bridge.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bridge.kill()


def main() -> None:
    """Start the bridge if needed, serve the page and open it once a phone is linked."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--port", type=int, default=PORT, help="port of the page (default %(default)s)"
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open the page")
    parser.add_argument("--no-bridge", action="store_true", help="do not start or end a bridge")
    args = parser.parse_args()

    def ended(*_: object) -> None:  # end like Ctrl+C when asked to end, so that the bridge ends too
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, ended)
    bridge = None if args.no_bridge else ensure_bridge()
    try:
        with control.Headband("h1", seconds=KEPT_S) as headband:
            feed = Feed(headband)
            try:
                server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(feed, args.port))
            except OSError:
                raise SystemExit(
                    f"Port {args.port} is in use: the viewer may already run. Try --port."
                ) from None
            server.daemon_threads = True
            stop = threading.Event()
            threading.Thread(target=feed.run, args=(stop,), daemon=True).start()
            address = f"http://127.0.0.1:{args.port}/"
            print(f"Live view: {address}  (Ctrl+C ends it)")
            if not args.no_browser:
                threading.Thread(
                    target=open_when_linked, args=(address, stop, args.no_bridge), daemon=True
                ).start()
            try:
                server.serve_forever()
            finally:
                stop.set()
                server.server_close()
                time.sleep(TICK_S)
    except KeyboardInterrupt:
        pass
    finally:
        if bridge is not None:
            end_bridge(bridge)


if __name__ == "__main__":
    main()
