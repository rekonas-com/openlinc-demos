"""Command line: ``bridge run | status | listen``.

Each command's ``--help`` text is given explicitly, so the numpy sections of its docstring stay out
of the help output.
"""

import asyncio
import errno
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import webbrowser
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

from openlinc.bridge.bridge import (
    LOCAL_HOST,
    LOCAL_PORT,
    PHONE_PORT,
    QUIT_ADDRESS,
    STATUS_ADDRESS,
    SUBSCRIBE_ADDRESS,
    SUBSCRIBE_INTERVAL_S,
    Bridge,
    serve,
)
from openlinc.bridge.network import phone_addresses
from openlinc.bridge.page import PairingPage
from openlinc.bridge.pairing import PAIRING_PAGE, pairing_link
from openlinc.messages import osc

STATUS_TIMEOUT_S = 1.0
PAGE_INTERVAL_S = 0.5
"""How often the bridge checks whether the pairing page must change, or whether to quit."""
RESTART_TIMEOUT_S = 3.0
"""How long a restart waits for the running bridge to end."""
SERVICE_END_S = 3.0
"""How long the bridge waits for the processing service to end before it forces it."""


class IfRunning(StrEnum):
    """What ``bridge run`` does when a bridge is already running on its ports."""

    ASK = "ask"
    OPEN = "open"
    RESTART = "restart"


app = typer.Typer(no_args_is_help=True, add_completion=False)

LocalPort = Annotated[int, typer.Option(help="The bridge's local port.")]


@app.command(help="Start the bridge, show the pairing page and keep running.")
def run(
    phone_host: Annotated[str, typer.Option(help="Interface for the phone socket.")] = "0.0.0.0",
    phone_port: Annotated[int, typer.Option(help="Port the phone sends to.")] = PHONE_PORT,
    local_port: LocalPort = LOCAL_PORT,
    browser: Annotated[
        bool, typer.Option("--browser/--no-browser", help="Open the pairing page in the browser.")
    ] = True,
    if_running: Annotated[
        IfRunning,
        typer.Option(
            help="If a bridge is already running: ask, open its pairing page, or restart it."
        ),
    ] = IfRunning.ASK,
    service: Annotated[
        bool, typer.Option("--service/--no-service", help="Start the processing service too.")
    ] = True,
) -> None:
    """Start the bridge, show the pairing page and keep running.

    Parameters
    ----------
    phone_host
        Interface for the phone socket.
    phone_port
        Port the phone sends to.
    local_port
        Port local programs use, on 127.0.0.1.
    browser
        Whether to open the pairing page in the browser.
    if_running
        What to do if a bridge is already running on these ports.
    service
        Whether to start the processing service too.

    Raises
    ------
    typer.Exit
        With code 1 if the ports are in use and no bridge was started.
    """
    phone, local = (phone_host, phone_port), (LOCAL_HOST, local_port)
    if _start(phone, local, browser, service):
        return
    if _choice(phone, local, if_running) is IfRunning.OPEN:
        webbrowser.open(_page_path(phone_port).as_uri())
        return
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(osc.build(QUIT_ADDRESS), local)
    give_up = time.monotonic() + RESTART_TIMEOUT_S
    while not _start(phone, local, browser, service):
        if time.monotonic() > give_up:
            print(
                "The running bridge did not end. It may be an older version: stop it in its own "
                "terminal with Ctrl+C, then start again.",
                file=sys.stderr,
            )
            raise typer.Exit(1)
        time.sleep(0.1)


@app.command(help="Print the running bridge's status as JSON.")
def status(local_port: LocalPort = LOCAL_PORT) -> None:
    """Print the running bridge's status as JSON.

    Parameters
    ----------
    local_port : int
        The bridge's local port.

    Raises
    ------
    typer.Exit
        With code 1 if no bridge answered.
    """
    found = _status((LOCAL_HOST, local_port))
    if found is None:
        print("no bridge is running", file=sys.stderr)
        raise typer.Exit(1)
    print(json.dumps(found, indent=2))


@app.command(help="Subscribe to the bridge and print messages per second and address.")
def listen(
    seconds: Annotated[float, typer.Option(help="How long to listen.")] = 5.0,
    local_port: LocalPort = LOCAL_PORT,
) -> None:
    """Subscribe to the bridge and print messages per second and address.

    Parameters
    ----------
    seconds : float
        How long to listen.
    local_port : int
        The bridge's local port.
    """
    _listen((LOCAL_HOST, local_port), seconds)


def main() -> None:
    """Run the ``bridge`` command line."""
    app()


async def _run(
    phone: tuple[str, int], local: tuple[str, int], browser: bool, service: bool
) -> None:
    """Run the bridge and keep its pairing page up to date until interrupted or asked to quit.

    Parameters
    ----------
    phone : tuple of (str, int)
        Host and port of the phone socket.
    local : tuple of (str, int)
        Host and port of the local socket.
    browser : bool
        Whether to open the pairing page in the browser.
    service
        Whether to start the processing service and keep it running.
    """
    name = socket.gethostname()
    bridge = Bridge(name)
    transports = await serve(bridge, phone, local)
    print(f"bridge: phone socket {phone[0]}:{phone[1]}, local socket {local[0]}:{local[1]}")
    addresses = phone_addresses()
    if not addresses:
        print("No network address found: connect this PC to the same Wi-Fi as the phone.")
    page = PairingPage(_page_path(phone[1]))

    def show(now: float) -> None:
        _, code = bridge.pairing(now)
        page.update(None if code is None else pairing_link(PAIRING_PAGE, addresses, phone[1], code))

    loop = asyncio.get_running_loop()
    show(loop.time())
    print(f"Pairing page: {page.path.as_uri()}")
    if browser:
        webbrowser.open(page.path.as_uri())
    sys.stdout.flush()
    child = _start_service(local[1]) if service else None
    try:
        while not bridge.asked_to_quit:
            if child is not None and child.poll() is not None:
                print("bridge: the processing service ended; starting it again")
                child = _start_service(local[1])
            bridge.service_running = child is not None
            await asyncio.sleep(PAGE_INTERVAL_S)
            show(loop.time())
        print("bridge: ended, because another start asked for it")
    finally:
        for transport in transports:
            transport.close()
        if child is not None:
            _end_service(child)


def _start_service(local_port: int) -> subprocess.Popen:
    """Start the processing service for the bridge on a local port, as a process of its own."""
    command = [
        sys.executable,
        "-m",
        "openlinc.processing.service",
        str(local_port),
        str(os.getpid()),
    ]
    return subprocess.Popen(command)


def _end_service(child: subprocess.Popen) -> None:
    """End the processing service, by force if it does not end by itself."""
    child.terminate()
    try:
        child.wait(SERVICE_END_S)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def _page_path(phone_port: int) -> Path:
    """Return where the bridge on a phone port keeps its pairing page."""
    return Path(tempfile.gettempdir()) / f"openlinc-pairing-{phone_port}.html"


def _start(phone: tuple[str, int], local: tuple[str, int], browser: bool, service: bool) -> bool:
    """Run the bridge until it ends.

    Parameters
    ----------
    phone
        Host and port of the phone socket.
    local
        Host and port of the local socket.
    browser
        Whether to open the pairing page in the browser.
    service
        Whether to start the processing service too.

    Returns
    -------
    bool
        False, at once, if the ports are in use; True once the bridge has run and ended.
    """
    try:
        asyncio.run(_run(phone, local, browser, service))
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        return False
    return True


def _interactive() -> bool:
    """Return whether someone at a terminal can be asked."""
    return sys.stdin.isatty()


def _choice(phone: tuple[str, int], local: tuple[str, int], if_running: IfRunning) -> IfRunning:
    """Decide what a start does that found its ports in use.

    Parameters
    ----------
    phone
        Host and port of the phone socket.
    local
        Host and port of the local socket.
    if_running
        What was asked for on the command line.

    Returns
    -------
    IfRunning
        ``OPEN`` or ``RESTART``.

    Raises
    ------
    typer.Exit
        With code 1 if the ports are used by another program, or if nothing was chosen and
        nobody can be asked; with code 0 if the person chose to do nothing.
    """
    if _status(local) is None:
        print(
            f"Port {phone[1]} or {local[1]} is used by another program; the bridge cannot start.",
            file=sys.stderr,
        )
        raise typer.Exit(1)
    page = _page_path(phone[1]).as_uri()
    print(f"A bridge is already running. Pairing page: {page}", flush=True)
    if if_running is not IfRunning.ASK:
        return if_running
    if not _interactive():
        print(
            "Start with --if-running open to open the page, or with --if-running restart to "
            "replace the running bridge.",
            file=sys.stderr,
        )
        raise typer.Exit(1)
    answer = ""
    while answer not in ("open", "restart", "nothing"):
        answer = typer.prompt(
            "Open its pairing page, restart it (the phone has to scan again), or do nothing? "
            "[open/restart/nothing]"
        )
    if answer == "nothing":
        raise typer.Exit(0)
    return IfRunning(answer)


def _status(local: tuple[str, int]) -> dict[str, Any] | None:
    """Ask the running bridge for its status.

    Parameters
    ----------
    local
        The bridge's local socket.

    Returns
    -------
    dict or None
        The status, or None if no bridge answered.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(STATUS_TIMEOUT_S)
        sock.sendto(osc.build(STATUS_ADDRESS), local)
        try:
            address, args = osc.parse(sock.recv(65536))
        except (TimeoutError, ConnectionError, ValueError):
            return None
    return json.loads(args[0]) if address == STATUS_ADDRESS else None


def _listen(local: tuple[str, int], seconds: float) -> None:
    """Subscribe to the bridge and print messages per address once per second.

    Parameters
    ----------
    local : tuple of (str, int)
        The bridge's local socket.
    seconds : float
        How long to listen.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.1)
        start = time.monotonic()
        next_subscribe = next_print = start
        counts: Counter[str] = Counter()
        while (now := time.monotonic()) < start + seconds:
            if now >= next_subscribe:
                sock.sendto(osc.build(SUBSCRIBE_ADDRESS), local)
                next_subscribe = now + SUBSCRIBE_INTERVAL_S
            try:
                counts[osc.parse(sock.recv(65536))[0]] += 1
            except (TimeoutError, ConnectionError, ValueError):
                pass
            if now >= next_print + 1.0:
                print(json.dumps(dict(sorted(counts.items()))))
                counts.clear()
                next_print += 1.0
