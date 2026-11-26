"""Phone message format: the OSC messages the phone app sends to the bridge.

The bridge passes data messages and device states on to local programs unchanged, so local
programs read the same format; stream descriptions stay in the bridge. Addresses start with a
device identifier the phone assigns: a letter for the device type and a number per type, ``h1`` for
the first headband. Data messages use ``/h1/eeg/raw``, ``/h1/motion/orientation``,
``/h1/motion/raw`` and ``/h1/light/lux``. Each carries one headband packet as
``seq, session, first_index, timestamp_us, values...``:

- ``seq`` counts data messages, so lost datagrams can be told apart from samples the headband
  itself dropped.
- ``session`` is the headband's acquisition session; its counters restart when it changes.
- ``first_index`` and ``timestamp_us`` belong to the packet's first sample, as the headband sent
  them.

These four are unsigned 32-bit numbers carried in the bits of OSC int32 arguments. Values are
float32 and frame-major: all channels of one sample are adjacent.

Each device's stream description is a JSON object sent as the single string argument of
``/h1/streams``. It holds only each of that device's streams' rate and channel count, by address::

    {"streams": {"/h1/eeg/raw": {"rate_hz": 256.0, "channels": 2}, ...}}

Each device's state is one word, the single string argument of ``/h1/state``: ``disconnected``,
``connecting``, ``streaming`` or ``start_failed``. The phone sends it on every change and repeats
it with the stream description.

Events do not come from the phone; they reach local programs in the same format. An event such as
``/h1/gaze/looking_left`` carries ``seq, session, timestamp_us``: a counter of that address's
events, and the headband session and clock at the moment it happened, all unsigned 32-bit. A
command such as ``/h1/calibration/running`` goes from a program to the processing and carries
one number or one string.

Pairing messages link one phone to the bridge. The phone sends ``/pair/hello version code`` after
scanning the pairing QR code, then ``/pair/ping link`` once a second and ``/pair/bye link`` when
the user stops. The bridge answers with ``/pair/welcome link name``, ``/pair/pong link`` or
``/pair/reject reason``. The link number is unsigned 32-bit, carried like the data counters; the
name is the PC's, shown on the phone.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from openlinc.messages import osc
from openlinc.messages.definitions import (
    HEADBAND_COMMANDS,
    HEADBAND_EVENTS,
    HEADBAND_RESULTS,
    HEADBAND_STREAMS,
)

STREAMS = "streams"
"""The part of a stream description's address after the device identifier."""
STATE = "state"
"""The part of a device state's address after the device identifier."""
STATES = ("disconnected", "connecting", "streaming", "start_failed")
"""The words a headband's state can take."""
META_INTERVAL_S = 5.0
"""The phone repeats each device's stream description and state this often while linked."""

HELLO_ADDRESS = "/pair/hello"
WELCOME_ADDRESS = "/pair/welcome"
REJECT_ADDRESS = "/pair/reject"
PING_ADDRESS = "/pair/ping"
PONG_ADDRESS = "/pair/pong"
BYE_ADDRESS = "/pair/bye"

REJECT_CODE = "code"
"""Rejection reason: the hello's code is not the current one."""
REJECT_LINK = "link"
"""Rejection reason: the bridge has no such link (it restarted, or another phone took over)."""

U32 = 1 << 32
_DEVICE_ADDRESS = re.compile(r"/([a-z][1-9][0-9]*)/(.+)")


@dataclass(frozen=True)
class Packet:
    """One headband packet: consecutive samples of one stream.

    Attributes
    ----------
    address : str
        Stream address, for example ``/h1/eeg/raw``.
    seq : int
        Data message counter, unsigned 32-bit.
    session : int
        Headband acquisition session, unsigned 32-bit.
    first_index : int
        Headband sample counter of the first sample, unsigned 32-bit.
    timestamp_us : int
        Headband clock at the first sample in microseconds, unsigned 32-bit.
    values : tuple of float
        Sample values, frame-major.
    """

    address: str
    seq: int
    session: int
    first_index: int
    timestamp_us: int
    values: tuple[float, ...]


@dataclass(frozen=True)
class Hello:
    """A phone asks to link with the code from the pairing QR code.

    Attributes
    ----------
    version : int
        The phone's protocol version.
    code : str
        One-time code.
    """

    version: int
    code: str


@dataclass(frozen=True)
class Ping:
    """A linked phone checks that the bridge still answers.

    Attributes
    ----------
    link : int
        Link number, unsigned 32-bit.
    """

    link: int


@dataclass(frozen=True)
class Bye:
    """A linked phone ends its link.

    Attributes
    ----------
    link : int
        Link number, unsigned 32-bit.
    """

    link: int


@dataclass(frozen=True)
class DeviceState:
    """What a device is doing, as the phone sees it.

    Attributes
    ----------
    device : str
        Device identifier, for example ``h1``.
    state : str
        One of ``STATES`` for a headband: ``disconnected`` (no headband connected),
        ``connecting`` (connecting, or connected and starting), ``streaming`` (samples flow) or
        ``start_failed`` (connected, but it did not start measuring).
    """

    device: str
    state: str


@dataclass(frozen=True)
class EventMessage:
    """Something that happened at one moment, for example a look to the left.

    Attributes
    ----------
    address
        Event address, for example ``/h1/gaze/looking_left``.
    seq
        Counter of this address's events, unsigned 32-bit, so that a lost one is noticed.
    session
        Headband acquisition session the time belongs to, unsigned 32-bit.
    timestamp_us
        Headband clock at the moment it happened, in microseconds, unsigned 32-bit.
    """

    address: str
    seq: int
    session: int
    timestamp_us: int


@dataclass(frozen=True)
class Command:
    """What a program tells the processing of a device's data.

    Attributes
    ----------
    address
        Command address, for example ``/h1/calibration/running``.
    value
        The command's number, for example 1 for on and 0 for off, or its string.
    """

    address: str
    value: int | str


Message = Packet | dict[str, Any] | DeviceState | EventMessage | Command | Hello | Ping | Bye


def encode_state(device: str, state: str) -> bytes:
    """Encode a device's state.

    Parameters
    ----------
    device : str
        Device identifier, for example ``h1``.
    state : str
        The state word.

    Returns
    -------
    bytes
        The datagram.
    """
    return osc.build(f"/{device}/{STATE}", state)


def encode_command(command: Command) -> bytes:
    """Encode a command.

    Parameters
    ----------
    command
        The command to encode.

    Returns
    -------
    bytes
        The datagram.
    """
    return osc.build(command.address, command.value)


def encode_event(event: EventMessage) -> bytes:
    """Encode an event.

    Parameters
    ----------
    event
        The event to encode.

    Returns
    -------
    bytes
        The datagram.
    """
    numbers = (event.seq, event.session, event.timestamp_us)
    return osc.build(event.address, *(_to_int32(n) for n in numbers))


def encode_welcome(link: int, name: str) -> bytes:
    """Encode the bridge's acceptance of a hello.

    Parameters
    ----------
    link : int
        The new link number, unsigned 32-bit.
    name : str
        The PC's name, shown on the phone.

    Returns
    -------
    bytes
        The datagram.
    """
    return osc.build(WELCOME_ADDRESS, _to_int32(link), name)


def encode_pong(link: int) -> bytes:
    """Encode the bridge's answer to a ping.

    Parameters
    ----------
    link : int
        The ping's link number, unsigned 32-bit.

    Returns
    -------
    bytes
        The datagram.
    """
    return osc.build(PONG_ADDRESS, _to_int32(link))


def encode_reject(reason: str) -> bytes:
    """Encode the bridge's rejection of a hello or ping.

    Parameters
    ----------
    reason : str
        ``REJECT_CODE`` or ``REJECT_LINK``.

    Returns
    -------
    bytes
        The datagram.
    """
    return osc.build(REJECT_ADDRESS, reason)


def encode_packet(packet: Packet) -> bytes:
    """Encode a packet as one OSC data message.

    Parameters
    ----------
    packet : Packet
        The packet to encode.

    Returns
    -------
    bytes
        The datagram.
    """
    header = (packet.seq, packet.session, packet.first_index, packet.timestamp_us)
    return osc.build(
        packet.address, *(_to_int32(n) for n in header), *(float(v) for v in packet.values)
    )


def encode_streams(device: str, description: dict[str, Any]) -> bytes:
    """Encode a device's stream description.

    Parameters
    ----------
    device : str
        Device identifier, for example ``h1``.
    description : dict
        The stream description.

    Returns
    -------
    bytes
        The datagram.
    """
    return osc.build(f"/{device}/{STREAMS}", json.dumps(description))


def split_address(address: str) -> tuple[str, str] | None:
    """Split an address into its device identifier and the rest.

    Parameters
    ----------
    address : str
        An OSC address, for example ``/h1/eeg/raw``.

    Returns
    -------
    tuple of (str, str) or None
        The device identifier and the rest, for example ``("h1", "eeg/raw")``, or None when the
        address does not start with a device identifier.
    """
    match = _DEVICE_ADDRESS.fullmatch(address)
    return None if match is None else (match[1], match[2])


def decode(dgram: bytes) -> Message | None:
    """Decode one message of the phone message format.

    Parameters
    ----------
    dgram : bytes
        A received datagram.

    Returns
    -------
    Message or None
        A packet for data messages, the stream description for a device's ``streams`` address,
        the device state for its ``state`` address, an event or a command for their addresses,
        a pairing message, and None for addresses this version does not know, which receivers
        ignore.

    Raises
    ------
    ValueError
        If the datagram is not OSC or a known message has invalid arguments.
    """
    address, args = osc.parse(dgram)
    device_part = split_address(address)
    part = None if device_part is None else device_part[1]
    if part == STREAMS:
        if len(args) != 1 or not isinstance(args[0], str):
            raise ValueError("stream description must be one string")
        meta = json.loads(args[0])
        if not isinstance(meta, dict):
            raise ValueError("stream description must be a JSON object")
        return meta
    if part == STATE:
        if [type(a) for a in args] != [str]:
            raise ValueError("device state must be one string")
        assert device_part is not None
        return DeviceState(device_part[0], args[0])
    if address == HELLO_ADDRESS:
        if [type(a) for a in args] != [int, str]:
            raise ValueError("hello must carry a version and a code")
        return Hello(args[0], args[1])
    if address in (PING_ADDRESS, BYE_ADDRESS):
        if [type(a) for a in args] != [int]:
            raise ValueError(f"{address} must carry a link number")
        return (Ping if address == PING_ADDRESS else Bye)(args[0] % U32)
    if part in HEADBAND_EVENTS:
        if [type(a) for a in args] != [int, int, int]:
            raise ValueError(f"{address} must carry a counter, a session and a time")
        return EventMessage(address, *(n % U32 for n in args))
    if part in HEADBAND_COMMANDS:
        if [type(a) for a in args] not in ([int], [str]):
            raise ValueError(f"{address} must carry one number or one string")
        return Command(address, args[0])
    if part not in HEADBAND_STREAMS and part not in HEADBAND_RESULTS:
        return None
    header, values = args[:4], args[4:]
    if (
        len(header) != 4
        or not values
        or not all(type(n) is int for n in header)
        or not all(type(v) is float for v in values)
    ):
        raise ValueError(f"invalid data message arguments for {address}")
    seq, session, first_index, timestamp_us = (n % U32 for n in header)
    return Packet(address, seq, session, first_index, timestamp_us, tuple(values))


def _to_int32(value: int) -> int:
    """Reinterpret an unsigned 32-bit number as the signed int32 with the same bits.

    Parameters
    ----------
    value : int
        Unsigned 32-bit number.

    Returns
    -------
    int
        Signed value for an OSC int32 argument.

    Raises
    ------
    ValueError
        If the value is not an unsigned 32-bit number.
    """
    if not 0 <= value < U32:
        raise ValueError(f"{value} is not an unsigned 32-bit number")
    return value - U32 if value >= U32 // 2 else value


def signed_delta(value: int, reference: int) -> int:
    """Return the shortest signed distance from a reference to a wrapping 32-bit value.

    Parameters
    ----------
    value : int
        Unsigned 32-bit value, for example a headband timestamp.
    reference : int
        Reference, unwrapped or not.

    Returns
    -------
    int
        Signed difference, within half the 32-bit range.
    """
    delta = (value - reference) % U32
    return delta - U32 if delta >= U32 // 2 else delta
