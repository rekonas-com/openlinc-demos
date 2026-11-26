"""The processing service: computes results from a headband's data, next to the bridge.

The bridge starts it as its own process, so that heavy computing cannot slow the bridge or a
program. It subscribes to the bridge like any local program and sends its results back to the
bridge, which passes them on to the other programs.

Run by the bridge as ``python -m openlinc.processing.service LOCAL_PORT BRIDGE_PID``.
"""

import sys
import threading
import time
from collections import Counter

import psutil

from openlinc.messages.clock import Clock
from openlinc.messages.definitions import HEADBAND_ACTIONS, HEADBAND_STREAMS
from openlinc.messages.phone_format import (
    U32,
    Command,
    EventMessage,
    Packet,
    encode_event,
    encode_packet,
    split_address,
)
from openlinc.processing.actions import Actions
from openlinc.processing.power import BroadbandPower
from openlinc.stream import Subscription

EEG_RATE_HZ = HEADBAND_STREAMS["eeg/raw"].rate_hz
BRIDGE_CHECK_S = 1.0
"""How often the service checks that its bridge still runs."""


class _Device:
    """What the service holds for one headband: its session, clock and methods."""

    def __init__(self) -> None:
        self.session: int | None = None
        self.clock = Clock()
        self.now_us = 0
        self.actions = Actions()
        self.power = BroadbandPower()


class Processing:
    """Turns the messages the bridge passes on into the results to send back.

    One session at a time: when a headband starts a new session, its methods start afresh, and
    what a calibration taught is gone.
    """

    def __init__(self) -> None:
        self._devices: dict[str, _Device] = {}
        self._sent: Counter[str] = Counter()

    def on(self, message: object) -> list[bytes]:
        """Take one message from the bridge.

        Parameters
        ----------
        message
            A decoded message; packets and commands are used, anything else is ignored.

        Returns
        -------
        list of bytes
            The datagrams to send to the bridge: an event per action and a packet per value of a
            computed stream that the message completed, and an event for a calibration it ended.
        """
        if isinstance(message, Command):
            return self._on_command(message)
        if isinstance(message, Packet):
            return self._on_packet(message)
        return []

    def _on_command(self, command: Command) -> list[bytes]:
        """Take what a program said about one device; a device without data has nothing to tell."""
        parts = split_address(command.address)
        device = None if parts is None else self._devices.get(parts[0])
        if device is None:
            return []
        if parts[1] == "calibration/prompt":
            if command.value in HEADBAND_ACTIONS:
                device.actions.prompt(command.value)
        elif parts[1] == "calibration/cancel":
            device.actions.cancel()
        elif parts[1] == "calibration/running":
            if command.value != 0:
                device.actions.start()
            else:
                ended = "done" if device.actions.finish() else "failed"
                return [self._event(f"/{parts[0]}/calibration/{ended}", device, device.now_us)]
        return []

    def _on_packet(self, packet: Packet) -> list[bytes]:
        """Give a packet's samples to the methods and return their results as datagrams."""
        parts = split_address(packet.address)
        if parts is None or parts[1] != "eeg/raw":
            return []
        device = self._devices.get(parts[0])
        if device is None or packet.session != device.session:
            device = self._devices[parts[0]] = _Device()
            device.session = packet.session
        first = device.clock.unwrap(packet.timestamp_us)
        rows = [packet.values[start : start + 2] for start in range(0, len(packet.values), 2)]
        times = [first + round(i * 1e6 / EEG_RATE_HZ) for i in range(len(rows))]
        device.now_us = times[-1]
        out = [
            self._event(f"/{parts[0]}/{name}", device, time_us)
            for name, time_us in device.actions.add(times, rows)
        ]
        address = f"/{parts[0]}/eeg/broadband_power"
        for time_us, values in device.power.add(times, rows):
            count = self._sent[address] % U32
            self._sent[address] += 1
            result = Packet(address, count, packet.session, count, time_us % U32, values)
            out.append(encode_packet(result))
        return out

    def _event(self, address: str, device: _Device, time_us: int) -> bytes:
        """Return the datagram of the next event of an address."""
        count = self._sent[address] % U32
        self._sent[address] += 1
        return encode_event(EventMessage(address, count, device.session, time_us % U32))


def run(local_port: int, bridge_pid: int) -> None:
    """Process what the bridge passes on until the bridge's process is gone.

    Parameters
    ----------
    local_port
        The bridge's local port.
    bridge_pid
        Process id of the bridge that started this service.
    """
    stop = threading.Event()

    def watch() -> None:
        while psutil.pid_exists(bridge_pid):
            time.sleep(BRIDGE_CHECK_S)
        stop.set()

    threading.Thread(target=watch, daemon=True).start()
    processing = Processing()
    with Subscription(local_port) as subscription:
        for message in subscription.messages(stop=stop):
            for dgram in processing.on(message):
                subscription.send(dgram)


if __name__ == "__main__":
    run(int(sys.argv[1]), int(sys.argv[2]))
