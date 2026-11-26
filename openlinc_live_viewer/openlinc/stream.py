"""Receive the bridge's stream in a Python program, message by message."""

import socket
import threading
import time
from collections.abc import Iterator

from openlinc.bridge.bridge import LOCAL_HOST, LOCAL_PORT, SUBSCRIBE_ADDRESS, SUBSCRIBE_INTERVAL_S
from openlinc.messages import osc
from openlinc.messages.phone_format import Command, DeviceState, EventMessage, Packet, decode

_RECEIVE_TIMEOUT_S = 0.1
"""How long a receive waits before the subscription is checked again."""


class Subscription:
    """A subscription to the running bridge: receives what it passes on, and can send to it.

    Use it as a context manager. The bridge takes messages only from subscribers, so a program
    that sends, such as the processing service, sends through its subscription.

    Parameters
    ----------
    local_port
        The bridge's local port.
    """

    def __init__(self, local_port: int = LOCAL_PORT) -> None:
        self._bridge = (LOCAL_HOST, local_port)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.settimeout(_RECEIVE_TIMEOUT_S)

    def __enter__(self) -> "Subscription":
        """Return this subscription."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the socket."""
        self._socket.close()

    def send(self, dgram: bytes) -> None:
        """Send a datagram to the bridge from the subscribed address."""
        self._socket.sendto(dgram, self._bridge)

    def messages(
        self, stop: threading.Event | None = None
    ) -> Iterator[Packet | DeviceState | EventMessage | Command]:
        """Yield what the bridge passes on, as it arrives, renewing the subscription meanwhile.

        Parameters
        ----------
        stop
            Ends the generator once set, checked at least every ``_RECEIVE_TIMEOUT_S``.

        Yields
        ------
        Packet or DeviceState or EventMessage or Command
            Headband packets, device states, events and commands. Datagrams that are not such
            messages are skipped.
        """
        next_subscribe = 0.0
        while True:
            if time.monotonic() >= next_subscribe:
                self.send(osc.build(SUBSCRIBE_ADDRESS))
                next_subscribe = time.monotonic() + SUBSCRIBE_INTERVAL_S
            if stop is not None and stop.is_set():
                return
            try:
                message = decode(self._socket.recv(65536))
            except (TimeoutError, ConnectionError, ValueError):
                continue
            if message is not None:
                yield message


def packets(
    local_port: int = LOCAL_PORT, stop: threading.Event | None = None
) -> Iterator[Packet | DeviceState | EventMessage | Command]:
    """Subscribe to the running bridge and yield what it passes on, as it arrives.

    The subscription is renewed every ``SUBSCRIBE_INTERVAL_S`` while the generator runs; closing
    the generator ends it. Datagrams that are not phone messages are skipped.

    Parameters
    ----------
    local_port
        The bridge's local port.
    stop
        Ends the generator once set, checked at least every ``_RECEIVE_TIMEOUT_S``.

    Yields
    ------
    Packet or DeviceState or EventMessage or Command
        Headband packets, each device's state whenever the phone sends it, events and commands.
    """
    with Subscription(local_port) as subscription:
        yield from subscription.messages(stop)
