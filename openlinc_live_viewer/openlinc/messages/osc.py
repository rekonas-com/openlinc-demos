"""Building and parsing single OSC messages."""

from typing import Any

from pythonosc.osc_message import OscMessage, ParseError
from pythonosc.osc_message_builder import OscMessageBuilder


def build(address: str, *args: int | float | str) -> bytes:
    """Build one OSC message.

    Parameters
    ----------
    address : str
        OSC address, for example ``/h1/eeg/raw``.
    *args : int or float or str
        Arguments. Python ints within the int32 range become int32, floats become float32 and
        strings become UTF-8 OSC strings.

    Returns
    -------
    bytes
        The encoded datagram.
    """
    builder = OscMessageBuilder(address)
    for arg in args:
        builder.add_arg(arg)
    return builder.build().dgram


def parse(dgram: bytes) -> tuple[str, list[Any]]:
    """Parse one OSC message.

    Parameters
    ----------
    dgram : bytes
        A received datagram.

    Returns
    -------
    tuple of (str, list)
        The address and the decoded arguments.

    Raises
    ------
    ValueError
        If the datagram is not a valid OSC message.
    """
    try:
        message = OscMessage(dgram)
    except ParseError as error:
        raise ValueError(f"not an OSC message: {error}") from error
    return message.address, list(message.params)
