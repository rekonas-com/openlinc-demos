"""control: one headband's live data inside your program.

A :class:`Headband` subscribes to the running bridge and keeps the latest seconds of each of the
device's streams, each sample with its headband time, the device's state as the phone reports it,
and the events that happened since the program last collected them. It receives in a background
thread, so the program's own loop never waits: it asks for the current state whenever it wants.

Stream and event names are the part of an address after the device identifier, for example
``eeg/raw`` and ``gaze/looking_left``.
"""

import math
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from openlinc.bridge.bridge import LOCAL_PORT
from openlinc.messages import definitions
from openlinc.messages.clock import Clock
from openlinc.messages.phone_format import (
    META_INTERVAL_S,
    Command,
    DeviceState,
    EventMessage,
    Packet,
    encode_command,
    split_address,
)
from openlinc.stream import Subscription

RECEIVING_S = 1.0
"""The device counts as receiving while its last message is at most this old."""
STATE_S = 2 * META_INTERVAL_S + 1.0
"""The phone's latest state report counts for this long; it repeats it every ``META_INTERVAL_S``."""
EVENTS_KEPT = 256
"""How many events wait for the program at most; beyond that the oldest are dropped."""


@dataclass(frozen=True)
class Event:
    """Something that happened at one moment, for example a look to the left.

    Attributes
    ----------
    kind
        What happened: the event's name, for example ``gaze/looking_left``.
    time_us
        Headband time at which it happened, in microseconds, on the same clock as the streams.
    """

    kind: str
    time_us: int


class Headband:
    """The latest seconds of one device's streams, with headband times, filled in the background.

    Times are the headband's clock in microseconds, made continuous across its 32-bit wrap, so
    they line the device's streams up directly. They are comparable only within one acquisition
    session: when a new session starts, every stream starts afresh. Missing samples show as jumps
    in the times. A packet older than what a stream already holds is dropped.

    Events wait until the program collects them with :meth:`events`. An event counts only in the
    session the streams are in; one of another session is dropped.

    Use it as a context manager, or call :meth:`start` and :meth:`stop`.

    Parameters
    ----------
    device : str, optional
        Device identifier, for example ``h1`` for the first headband.
    seconds : float, optional
        How much of each stream is kept.
    local_port : int, optional
        The bridge's local port.
    look_pause_s
        After a look, further looks are left out for this many seconds. With a pause, the
        movement that brings the eyes back after a look is not reported as a look of its own.
    """

    def __init__(
        self,
        device: str = "h1",
        seconds: float = 10.0,
        local_port: int = LOCAL_PORT,
        look_pause_s: float = 0.0,
    ):
        self.device = device
        self._look_pause_us = look_pause_s * 1e6
        self._last_look_us: int | None = None
        self._local_port = local_port
        self._lock = threading.Lock()
        self._streams = {
            name: _Stream(stream.rate_hz, stream.channels, math.ceil(stream.rate_hz * seconds))
            for name, stream in (
                definitions.HEADBAND_STREAMS | definitions.HEADBAND_RESULTS
            ).items()
        }
        self._events: deque[Event] = deque(maxlen=EVENTS_KEPT)
        self._session: int | None = None
        self._clock = Clock()
        self._last_message: float | None = None
        self._state: tuple[str, float] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._subscription: Subscription | None = None

    @property
    def running(self) -> bool:
        """Whether the background thread is receiving from the bridge."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def receiving(self) -> bool:
        """Whether a message of this device arrived within the last ``RECEIVING_S``."""
        with self._lock:
            last = self._last_message
        return last is not None and time.monotonic() - last <= RECEIVING_S

    @property
    def session(self) -> int | None:
        """The headband's acquisition session the streams are in, or None before any sample.

        It changes when the headband starts measuring anew. Every stream then starts afresh, and
        the calibration of the wearer's actions has to be done again.
        """
        with self._lock:
            return self._session

    @property
    def state(self) -> str | None:
        """The device's state as the phone last reported it, or None without a recent report.

        ``disconnected``: the phone has no headband connected. ``connecting``: it is connecting,
        or connected and starting. ``streaming``: samples flow. ``start_failed``: connected, but
        the headband did not start measuring. None: no report within ``STATE_S``, for example
        because the bridge has no phone linked.
        """
        with self._lock:
            latest = self._state
        if latest is None or time.monotonic() - latest[1] > STATE_S:
            return None
        return latest[0]

    def start(self) -> "Headband":
        """Start receiving in a background thread.

        Returns
        -------
        Headband
            This headband.
        """
        if not self.running:
            self._stop.clear()
            self._subscription = Subscription(self._local_port)
            self._thread = threading.Thread(target=self._receive, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        """Stop receiving and wait for the background thread to end."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def __enter__(self) -> "Headband":
        """Start receiving."""
        return self.start()

    def __exit__(self, *_: object) -> None:
        """Stop receiving."""
        self.stop()

    def _receive(self) -> None:
        """Feed every message from the bridge in until stopped."""
        with self._subscription as subscription:
            for message in subscription.messages(stop=self._stop):
                self.add(message)

    def start_calibration(self) -> None:
        """Start a calibration of the wearer's actions: looks to a side, blinks, clenches.

        The actions are reported as events only after a calibration. In it the program asks the
        wearer for each action several times, in mixed order, and calls :meth:`prompt` each time
        it shows such a request; :meth:`finish_calibration` ends it, and
        :meth:`cancel_calibration` gives it up. While it runs no action is reported. The headband
        has to be started.
        """
        self._send("calibration/running", 1)

    def prompt(self, action: str) -> None:
        """Say that the wearer is asked to do an action at this moment.

        Call it when the request appears. What the EEG shows within about a second counts as an
        example of the action, so leave two to three seconds between two requests.

        Parameters
        ----------
        action
            Name of the action's event, one of ``definitions.HEADBAND_ACTIONS``, for example
            ``gaze/looking_left``.

        Raises
        ------
        ValueError
            If it is not the name of an action.
        """
        if action not in definitions.HEADBAND_ACTIONS:
            raise ValueError(f"{action} is not one of {definitions.HEADBAND_ACTIONS}")
        self._send("calibration/prompt", action)

    def finish_calibration(self) -> None:
        """End the calibration; call it two seconds or more after the last prompt.

        How it ended arrives as an event: ``calibration/done``, and the actions are reported
        from then on, or ``calibration/failed``, when a prompted action showed fewer than three
        times in the EEG.
        """
        self._send("calibration/running", 0)

    def cancel_calibration(self) -> None:
        """Give up the calibration that runs, for example when the program closes during it.

        Nothing is learned and no event answers it. Actions are reported again as an earlier
        calibration taught them. Without it the service would wait for the calibration's end and
        report no action meanwhile.
        """
        self._send("calibration/cancel", 1)

    def _send(self, name: str, value: int | str) -> None:
        """Send a command for this device to the bridge."""
        command = Command(f"/{self.device}/{name}", value)
        self._subscription.send(encode_command(command))

    def add(self, message: Packet | DeviceState | EventMessage) -> None:
        """Take one message from the bridge; messages of other devices are ignored.

        Parameters
        ----------
        message
            A packet, a device state or an event.
        """
        with self._lock:
            if isinstance(message, Packet):
                self._add_packet(message)
            elif isinstance(message, EventMessage):
                self._add_event(message)
            elif isinstance(message, DeviceState) and message.device == self.device:
                self._last_message = time.monotonic()
                self._state = (message.state, self._last_message)

    def _add_packet(self, packet: Packet) -> None:
        """Store a packet's samples with their headband times.

        Parameters
        ----------
        packet : Packet
            A packet from the bridge.
        """
        name = self._own(packet.address)
        if name is None:
            return
        self._last_message = time.monotonic()
        stream = self._streams.get(name)
        if stream is None or len(packet.values) % stream.channels:
            return  # an unknown stream, or a packet that ends in the middle of a sample
        if packet.session != self._session:
            self._session, self._clock = packet.session, Clock()
            for each in self._streams.values():
                each.clear()
        first = self._clock.unwrap(packet.timestamp_us)
        if stream.count and first <= stream.newest_us():
            return
        values = np.asarray(packet.values, dtype=float).reshape(-1, stream.channels)
        step_us = 1e6 / stream.rate_hz
        times = first + np.rint(np.arange(len(values)) * step_us).astype(np.int64)
        stream.append(times, values)

    def _add_event(self, message: EventMessage) -> None:
        """Keep an event of this device and the current session until the program collects it."""
        kind = self._own(message.address)
        if kind is None or message.session != self._session:
            return
        self._last_message = time.monotonic()
        time_us = self._clock.unwrap(message.timestamp_us)
        if kind.startswith("gaze/looking_"):
            last = self._last_look_us
            if last is not None and 0 <= time_us - last < self._look_pause_us:
                return
            self._last_look_us = time_us
        self._events.append(Event(kind, time_us))

    def _own(self, address: str) -> str | None:
        """Return the name in an address of this device, or None for other addresses."""
        parts = split_address(address)
        return parts[1] if parts is not None and parts[0] == self.device else None

    def events(self) -> list[Event]:
        """Return the events that arrived since the last call, and forget them.

        Never waits: without new events the list is empty. Call it on each pass of the program's
        loop, as a game collects its key presses.

        Returns
        -------
        list of Event
            Oldest first.
        """
        with self._lock:
            pending = list(self._events)
            self._events.clear()
        return pending

    def rate_hz(self, stream: str) -> float | None:
        """Return a stream's sample rate, or None for a stream the headband does not have.

        Parameters
        ----------
        stream
            Stream name, for example ``eeg/raw``.

        Returns
        -------
        float or None
            Samples per second.
        """
        with self._lock:
            found = self._streams.get(stream)
            return None if found is None else found.rate_hz

    def window(self, stream: str, seconds: float) -> tuple[np.ndarray, np.ndarray]:
        """Return a stream's samples from the latest ``seconds`` of headband time, oldest first.

        Parameters
        ----------
        stream : str
            Stream name, for example ``eeg/raw``.
        seconds : float
            How much headband time to cover, ending at the latest sample.

        Returns
        -------
        times : numpy.ndarray
            Headband time of each sample in microseconds, shape ``(samples,)``.
        values : numpy.ndarray
            Shape ``(samples, channels)``; ``(0, 0)`` for a stream the headband does not have.
        """
        with self._lock:
            found = self._streams.get(stream)
            if found is None:
                return np.empty(0, dtype=np.int64), np.empty((0, 0))
            return found.latest(seconds)


class _Stream:
    """Recent samples of one stream, in a ring buffer: the newest overwrite the oldest.

    Parameters
    ----------
    rate_hz : float
        Sample rate.
    channels : int
        Values per sample.
    capacity : int
        How many samples are kept at most.
    """

    def __init__(self, rate_hz: float, channels: int, capacity: int) -> None:
        self.rate_hz = rate_hz
        self.channels = channels
        self.count = 0
        self._times = np.zeros(capacity, dtype=np.int64)
        self._values = np.zeros((capacity, channels))
        self._end = 0

    def clear(self) -> None:
        """Forget every sample."""
        self.count = self._end = 0

    def newest_us(self) -> int:
        """Return the time of the newest sample; the stream must hold one."""
        return int(self._times[self._end - 1])

    def append(self, times: np.ndarray, values: np.ndarray) -> None:
        """Store samples after the ones held; the oldest fall out beyond the capacity.

        Parameters
        ----------
        times : numpy.ndarray
            Headband time of each sample in microseconds, shape ``(samples,)``, increasing and
            later than the newest sample held.
        values : numpy.ndarray
            Shape ``(samples, channels)``.
        """
        capacity = len(self._times)
        kept = min(len(times), capacity)
        if kept == 0:
            return
        times, values = times[len(times) - kept :], values[len(values) - kept :]
        fitting = min(kept, capacity - self._end)
        self._times[self._end : self._end + fitting] = times[:fitting]
        self._values[self._end : self._end + fitting] = values[:fitting]
        if fitting < kept:
            self._times[: kept - fitting] = times[fitting:]
            self._values[: kept - fitting] = values[fitting:]
        self._end = (self._end + kept) % capacity
        self.count = min(self.count + kept, capacity)

    def latest(self, seconds: float) -> tuple[np.ndarray, np.ndarray]:
        """Return copies of the samples from the latest ``seconds`` of headband time.

        Parameters
        ----------
        seconds : float
            How much headband time to cover, ending at the newest sample.

        Returns
        -------
        times : numpy.ndarray
            Headband time of each sample in microseconds, shape ``(samples,)``, oldest first.
        values : numpy.ndarray
            Shape ``(samples, channels)``.
        """
        if self.count == 0:
            return np.empty(0, dtype=np.int64), np.empty((0, self.channels))
        # The samples lie in two runs, each in order: up to the arrays' end, then from their start.
        oldest = (self._end - self.count) % len(self._times)
        older = slice(oldest, min(oldest + self.count, len(self._times)))
        newer = slice(0, self.count - (older.stop - older.start))
        start_us = self.newest_us() - seconds * 1e6
        skipped = int(np.searchsorted(self._times[older], start_us, side="right"))
        if skipped < older.stop - older.start:
            older = slice(older.start + skipped, older.stop)
        else:
            older = slice(0, 0)
            newer = slice(
                int(np.searchsorted(self._times[newer], start_us, side="right")), newer.stop
            )
        times = np.concatenate((self._times[older], self._times[newer]))
        values = np.concatenate((self._values[older], self._values[newer]))
        return times, values
