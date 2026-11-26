"""The bridge: receives the phone's stream and passes it on to local programs.

The bridge has two UDP sockets. The phone socket receives phone messages on all interfaces and
answers the phone from the same socket. A phone links by sending the one-time code from the
pairing QR code; afterwards the bridge accepts data only from that phone. The local socket
listens on 127.0.0.1 only: local programs subscribe there with ``/bridge/subscribe`` (repeated
every 2 seconds) and receive the phone's data messages and device states, starting with the latest
state of each device, and ask for status with ``/bridge/status``. The phone's stream descriptions
stay in the bridge, for its status. An event, a command or a computed stream that one subscriber
sends, such as ``/h1/gaze/looking_left``, is passed on to the other subscribers. The bridge
computes nothing. :class:`Bridge` holds the state and returns the datagrams to send;
:func:`serve` connects it to the sockets.
"""

import asyncio
import json
import math
import secrets
import statistics
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openlinc.bridge.pairing import PROTOCOL_VERSION, new_code
from openlinc.messages import osc
from openlinc.messages.definitions import HEADBAND_RESULTS
from openlinc.messages.phone_format import (
    REJECT_CODE,
    REJECT_LINK,
    U32,
    Bye,
    Command,
    EventMessage,
    Hello,
    Message,
    Packet,
    Ping,
    decode,
    encode_pong,
    encode_reject,
    encode_welcome,
    signed_delta,
    split_address,
)

PHONE_PORT = 47100
LOCAL_PORT = 47101
LOCAL_HOST = "127.0.0.1"

SUBSCRIBE_ADDRESS = "/bridge/subscribe"
STATUS_ADDRESS = "/bridge/status"
"""Asks for the status, and carries it back as one JSON string."""
QUIT_ADDRESS = "/bridge/quit"
"""Asks the bridge to end, so that a newly started one can take its place."""

SUBSCRIPTION_TIMEOUT_S = 5.0
"""A local program stops receiving after this long without a ``/bridge/subscribe``."""
SUBSCRIBE_INTERVAL_S = 2.0
"""Local programs repeat ``/bridge/subscribe`` this often."""
RATE_WINDOW_S = 5.0
"""Measured rates average over this window."""
DELAY_WINDOW_S = 10.0
"""Arrival delay variation covers this window."""
SILENCE_S = 3.0
"""A linked phone silent for this long counts as not responding."""

WAITING = "waiting"
LINKED = "linked"
NOT_RESPONDING = "not_responding"

Address = tuple[str, int]
Outgoing = list[tuple[bytes, Address]]


def _between_programs(dgram: bytes) -> bool:
    """Return whether a datagram is something local programs exchange.

    That is a well-formed event or command, or a packet of a stream that is computed on the PC.
    """
    try:
        message = decode(dgram)
    except ValueError:
        return False
    if isinstance(message, Packet):
        parts = split_address(message.address)
        return parts is not None and parts[1] in HEADBAND_RESULTS
    return isinstance(message, EventMessage | Command)


def _forward_gap(actual: int, expected: int) -> int | None:
    """Return how far a 32-bit counter jumped ahead of its expected value.

    Parameters
    ----------
    actual : int
        Received counter value.
    expected : int
        Expected counter value.

    Returns
    -------
    int or None
        The number of skipped values, or None if the counter lies behind the expected value
        (a restarted sender, or a duplicated or reordered datagram).
    """
    gap = (actual - expected) % U32
    return gap if gap < U32 // 2 else None


@dataclass
class _StreamStats:
    """Counters for one stream.

    A counter that lies behind its expected value restarts tracking instead of counting as a loss.
    """

    packets: int = 0
    samples: int = 0
    missing_samples: int = 0
    session: int | None = None
    next_index: int | None = None
    first_seen: float | None = None
    recent: deque[tuple[float, int]] = field(default_factory=deque)
    headband_us: int | None = None
    delays: deque[tuple[float, float]] = field(default_factory=deque)

    def add(self, packet: Packet, samples: int | None, rate_hz: float | None, now: float) -> None:
        """Count a packet.

        Parameters
        ----------
        packet : Packet
            The received packet.
        samples : int or None
            Samples in the packet; None while the stream description is unknown.
        rate_hz : float or None
            Stream rate from the stream description; None while it is unknown.
        now : float
            Current time in seconds.
        """
        self.packets += 1
        if self.first_seen is None:
            self.first_seen = now
        if samples is None or not rate_hz:
            return
        if packet.session != self.session:
            self.session, self.next_index, self.headband_us = packet.session, None, None
            self.delays.clear()
        if self.next_index is not None:
            self.missing_samples += _forward_gap(packet.first_index, self.next_index) or 0
        self.next_index = (packet.first_index + samples) % U32
        self.samples += samples
        self.recent.append((now, samples))
        self._trim(now)
        self.headband_us = (
            packet.timestamp_us
            if self.headband_us is None
            else self.headband_us + signed_delta(packet.timestamp_us, self.headband_us)
        )
        last_sample_ms = (self.headband_us + (samples - 1) * 1e6 / rate_hz) / 1e3
        self.delays.append((now, now * 1e3 - last_sample_ms))

    def arrival_delay_ms(self, now: float) -> dict[str, float] | None:
        """Summarize how much later than the fastest packet packets arrived.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        dict or None
            Median, 95th percentile (nearest rank) and maximum in milliseconds over the last
            ``DELAY_WINDOW_S``, each relative to the fastest packet in that window, or None when
            the window is empty.
        """
        while self.delays and self.delays[0][0] <= now - DELAY_WINDOW_S:
            self.delays.popleft()
        if not self.delays:
            return None
        raw = sorted(delay for _, delay in self.delays)
        relative = [delay - raw[0] for delay in raw]
        p95 = relative[math.ceil(0.95 * len(relative)) - 1]
        return {
            "median": round(statistics.median(relative), 1),
            "p95": round(p95, 1),
            "max": round(relative[-1], 1),
        }

    def rate_hz(self, now: float) -> float | None:
        """Return the measured sample rate.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        float or None
            Samples per second over the last ``RATE_WINDOW_S``, or None before the first packet.
        """
        if self.first_seen is None or now <= self.first_seen:
            return None
        self._trim(now)
        span = min(RATE_WINDOW_S, now - self.first_seen)
        return round(sum(n for _, n in self.recent) / span, 1)

    def _trim(self, now: float) -> None:
        """Drop counts older than the rate window.

        Parameters
        ----------
        now : float
            Current time in seconds.
        """
        while self.recent and self.recent[0][0] <= now - RATE_WINDOW_S:
            self.recent.popleft()


class Bridge:
    """Bridge state: the phone link, stream counters, stream description and local subscribers.

    Handlers take the time as an argument and return the datagrams to send, so the state can be
    driven without sockets.

    Parameters
    ----------
    name : str
        The PC's name, sent to the phone in the welcome.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.phone: Address | None = None
        self.link: int | None = None
        self.last_phone_time: float | None = None
        self.malformed_datagrams = 0
        self.asked_to_quit = False
        """Whether a local program asked the bridge to end."""
        self.service_running = False
        """Whether the processing service runs; set by whoever starts it."""
        self._code: str | None = None
        self._linked_code: str | None = None
        self._subscribers: dict[Address, float] = {}
        self._reset_streams()

    def pairing(self, now: float) -> tuple[str, str | None]:
        """Return the link state and the code the pairing page shows.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        tuple of (str, str or None)
            ``waiting``, ``linked`` or ``not_responding``, and the current one-time code, or None
            while a phone is linked and answering.
        """
        state = self._state(now)
        if state == LINKED:
            return state, None
        if self._code is None:
            self._code = new_code()
        return state, self._code

    def on_phone(self, dgram: bytes, sender: Address, now: float) -> tuple[Outgoing, Outgoing]:
        """Handle a datagram on the phone socket.

        Parameters
        ----------
        dgram : bytes
            The datagram.
        sender : Address
            Its sender.
        now : float
            Current time in seconds.

        Returns
        -------
        tuple of Outgoing
            Replies to the phone, and datagrams for local subscribers.
        """
        try:
            message = decode(dgram)
        except ValueError:
            self.malformed_datagrams += 1
            return [], []
        if message is None:
            return [], []
        if isinstance(message, Hello | Ping | Bye):
            reply = self._on_link_message(message, sender, now)
            return ([] if reply is None else [(reply, sender)]), []
        return [], self._on_stream(message, dgram, sender, now)

    def on_local(self, dgram: bytes, sender: Address, now: float) -> Outgoing:
        """Handle a datagram from a local program.

        Parameters
        ----------
        dgram : bytes
            The datagram.
        sender : Address
            The local program's socket.
        now : float
            Current time in seconds.

        Returns
        -------
        Outgoing
            The status reply for ``/bridge/status``; for a new subscriber, the latest state of
            each device; an event or command from a subscriber, for the other subscribers;
            nothing otherwise. ``/bridge/quit`` sets ``asked_to_quit``.
        """
        try:
            address, _ = osc.parse(dgram)
        except ValueError:
            return []
        if address == SUBSCRIBE_ADDRESS:
            new = sender not in self._live_subscribers(now)
            self._subscribers[sender] = now
            if new:  # so it need not wait for the phone to repeat them
                return [(dgram, sender) for dgram in self._latest_states.values()]
        elif address == STATUS_ADDRESS:
            return [(osc.build(STATUS_ADDRESS, json.dumps(self.status(now))), sender)]
        elif address == QUIT_ADDRESS:
            self.asked_to_quit = True
        elif sender in self._live_subscribers(now) and _between_programs(dgram):
            return self._publish([dgram], now, skip=sender)
        return []

    def status(self, now: float) -> dict[str, Any]:
        """Summarize the link and the streams.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        dict
            JSON-ready status.
        """
        expected = self.stream_descriptions
        return {
            "link": self._state(now),
            "phone": None if self.phone is None else f"{self.phone[0]}:{self.phone[1]}",
            "seconds_since_last_message": None
            if self.last_phone_time is None
            else round(now - self.last_phone_time, 3),
            "lost_datagrams": self.lost_datagrams,
            "malformed_datagrams": self.malformed_datagrams,
            "subscribers": len(self._live_subscribers(now)),
            "service": "running" if self.service_running else "not_running",
            "streams": {
                address: {
                    "rate_hz": stats.rate_hz(now),
                    "expected_rate_hz": expected.get(address, {}).get("rate_hz"),
                    "packets": stats.packets,
                    "samples": stats.samples,
                    "missing_samples": stats.missing_samples,
                    "session": stats.session,
                    "arrival_delay_ms": stats.arrival_delay_ms(now),
                }
                for address, stats in sorted(self._streams.items())
            },
        }

    def _state(self, now: float) -> str:
        """Return the link state.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        str
            ``waiting``, ``linked`` or ``not_responding``.
        """
        if self.link is None:
            return WAITING
        assert self.last_phone_time is not None
        return NOT_RESPONDING if now - self.last_phone_time >= SILENCE_S else LINKED

    def _on_link_message(
        self, message: Hello | Ping | Bye, sender: Address, now: float
    ) -> bytes | None:
        """Handle a hello, ping or bye.

        Parameters
        ----------
        message : Hello or Ping or Bye
            The message.
        sender : Address
            Its sender.
        now : float
            Current time in seconds.

        Returns
        -------
        bytes or None
            The reply to the sender, if any.
        """
        if isinstance(message, Hello):
            if message.version != PROTOCOL_VERSION:
                return None
            if self.link is not None and (sender, message.code) == (self.phone, self._linked_code):
                return encode_welcome(
                    self.link, self.name
                )  # the phone repeats its hello until a welcome
            if self._state(now) == LINKED or message.code != self._code:
                return encode_reject(REJECT_CODE)
            self._start_link(message.code, sender, now)
            assert self.link is not None
            return encode_welcome(self.link, self.name)
        if message.link != self.link:
            return encode_reject(REJECT_LINK) if isinstance(message, Ping) else None
        if isinstance(message, Bye):
            self.link = self.phone = self._linked_code = None
            return None
        self.phone, self.last_phone_time = sender, now
        return encode_pong(message.link)

    def _start_link(self, code: str, phone: Address, now: float) -> None:
        """Link a phone, replacing any previous one, and start counting afresh.

        Parameters
        ----------
        code : str
            The code the phone sent; it stays valid for this phone's repeated hellos only.
        phone : Address
            The phone's address.
        now : float
            Current time in seconds.
        """
        previous = self.link
        while self.link == previous:
            self.link = secrets.randbits(32)
        self.phone, self.last_phone_time = phone, now
        self._linked_code, self._code = code, None
        self._reset_streams()

    def _reset_streams(self) -> None:
        """Forget the stream descriptions, device states and all stream counters."""
        self.stream_descriptions: dict[str, dict[str, Any]] = {}
        """Rate and channels by stream address, from the latest description of each device."""
        self._latest_states: dict[str, bytes] = {}
        """The latest state datagram of each device by address, for new subscribers."""
        self.lost_datagrams = 0
        self._next_seq: int | None = None
        self._streams: dict[str, _StreamStats] = {}

    def _on_stream(self, message: Message, dgram: bytes, sender: Address, now: float) -> Outgoing:
        """Handle a data message, stream description or device state.

        Parameters
        ----------
        message : Message
            The decoded message: a packet, a stream description or a device state.
        dgram : bytes
            The datagram as received.
        sender : Address
            Its sender.
        now : float
            Current time in seconds.

        Returns
        -------
        Outgoing
            Datagrams for local subscribers; none unless the sender is the linked phone. A stream
            description is used here only and not passed on.
        """
        if self.link is None or sender != self.phone:
            return []
        self.last_phone_time = now
        if isinstance(message, Packet):
            self._count(message, now)
            return self._publish([dgram], now)
        if isinstance(message, dict):
            streams = message.get("streams")
            if isinstance(streams, dict):
                self.stream_descriptions.update(streams)
            return []
        self._latest_states[osc.parse(dgram)[0]] = dgram
        return self._publish([dgram], now)

    def _count(self, packet: Packet, now: float) -> None:
        """Update datagram and stream counters for a packet.

        Parameters
        ----------
        packet : Packet
            The received packet.
        now : float
            Current time in seconds.
        """
        if self._next_seq is not None:
            self.lost_datagrams += _forward_gap(packet.seq, self._next_seq) or 0
        self._next_seq = (packet.seq + 1) % U32
        stream = self.stream_descriptions.get(packet.address, {})
        channels, rate_hz = stream.get("channels"), stream.get("rate_hz")
        samples = len(packet.values) // channels if isinstance(channels, int) and channels else None
        rate = float(rate_hz) if isinstance(rate_hz, int | float) else None
        self._streams.setdefault(packet.address, _StreamStats()).add(packet, samples, rate, now)

    def _publish(self, messages: list[bytes], now: float, skip: Address | None = None) -> Outgoing:
        """Address messages to every live subscriber.

        Parameters
        ----------
        messages
            Messages to send.
        now
            Current time in seconds.
        skip
            A subscriber that gets nothing, for a message it sent itself.

        Returns
        -------
        Outgoing
            One datagram per message and subscriber.
        """
        return [(m, s) for s in self._live_subscribers(now) if s != skip for m in messages]

    def _live_subscribers(self, now: float) -> list[Address]:
        """Drop expired subscribers and return the rest.

        Parameters
        ----------
        now : float
            Current time in seconds.

        Returns
        -------
        list of Address
            Subscribers heard from within ``SUBSCRIPTION_TIMEOUT_S``.
        """
        self._subscribers = {
            s: seen for s, seen in self._subscribers.items() if now - seen < SUBSCRIPTION_TIMEOUT_S
        }
        return list(self._subscribers)


class _Endpoint(asyncio.DatagramProtocol):
    """Feeds received datagrams to a bridge handler and sends what it returns.

    The handler returns replies, sent from this endpoint's own socket, and messages for local
    programs, sent from the local socket.
    """

    def __init__(
        self,
        handler: Callable[[bytes, Address, float], tuple[Outgoing, Outgoing]],
        local: asyncio.DatagramTransport | None = None,
    ) -> None:
        self._handler = handler
        self._local = local
        self._own: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Keep this endpoint's socket for replies and, unless another was given, local output.

        Parameters
        ----------
        transport : asyncio.BaseTransport
            This endpoint's transport.
        """
        assert isinstance(transport, asyncio.DatagramTransport)
        self._own = transport
        self._local = self._local or transport

    def datagram_received(self, data: bytes, addr: Address) -> None:
        """Pass a datagram to the handler and send its output.

        Parameters
        ----------
        data : bytes
            The datagram.
        addr : Address
            Its sender.
        """
        assert self._own is not None and self._local is not None
        replies, local = self._handler(data, addr, asyncio.get_running_loop().time())
        for dgram, target in replies:
            self._own.sendto(dgram, target)
        for dgram, target in local:
            self._local.sendto(dgram, target)


async def serve(
    bridge: Bridge, phone: Address, local: Address
) -> tuple[asyncio.DatagramTransport, asyncio.DatagramTransport]:
    """Open the bridge's sockets.

    Parameters
    ----------
    bridge : Bridge
        Bridge state.
    phone : Address
        Host and port of the phone socket.
    local : Address
        Host and port of the local socket.

    Returns
    -------
    tuple of asyncio.DatagramTransport
        The phone and local transports; close them to stop. Messages for local programs leave
        through the local socket.
    """
    loop = asyncio.get_running_loop()
    local_transport, _ = await loop.create_datagram_endpoint(
        lambda: _Endpoint(lambda *a: (bridge.on_local(*a), [])), local_addr=local
    )
    phone_transport, _ = await loop.create_datagram_endpoint(
        lambda: _Endpoint(bridge.on_phone, local_transport), local_addr=phone
    )
    return phone_transport, local_transport
