"""Broadband power of the EEG."""

from collections import deque

import numpy as np

from openlinc.messages.definitions import HEADBAND_RESULTS, HEADBAND_STREAMS

BAND_HZ = (1.0, 45.0)
"""The band: above the slow drift of the offset, below the mains frequency."""
SPAN_S = 1.0
"""Each value covers this much EEG, which also sets the frequency steps to 1 Hz."""


class BroadbandPower:
    """Power of each EEG channel from 1 to 45 Hz over the last second, ten times a second."""

    def __init__(self) -> None:
        self._step_us = 1e6 / HEADBAND_STREAMS["eeg/raw"].rate_hz
        self._every_us = 1e6 / HEADBAND_RESULTS["eeg/broadband_power"].rate_hz
        self._samples = round(SPAN_S * HEADBAND_STREAMS["eeg/raw"].rate_hz)
        self._recent: deque[tuple[int, np.ndarray]] = deque(maxlen=self._samples)
        self._due_us: float | None = None

    def add(self, times_us: list[int], rows: list) -> list[tuple[int, tuple[float, ...]]]:
        """Take the next EEG samples and return the values they complete.

        A value needs a full second of samples without a gap; after a gap the method starts over.

        Parameters
        ----------
        times_us
            Headband time of each sample in microseconds.
        rows
            One row per sample, in µV.

        Returns
        -------
        list of (int, tuple of float)
            For each value its headband time, the end of its second, and the power of each
            channel in µV².
        """
        values = []
        for time_us, row in zip(times_us, rows, strict=True):
            if self._recent and time_us - self._recent[-1][0] > 1.5 * self._step_us:
                self._recent.clear()
                self._due_us = None
            self._recent.append((int(time_us), np.asarray(row, dtype=float)))
            if len(self._recent) < self._samples or (
                self._due_us is not None and time_us < self._due_us
            ):
                continue
            self._due_us = (time_us if self._due_us is None else self._due_us) + self._every_us
            values.append((int(time_us), self._power()))
        return values

    def _power(self) -> tuple[float, ...]:
        """Return the power of each channel in the band, over the samples held."""
        signal = np.array([row for _, row in self._recent])
        spectrum = np.fft.rfft(signal, axis=0)
        hz = np.fft.rfftfreq(len(signal), self._step_us / 1e6)
        band = (hz >= BAND_HZ[0]) & (hz <= BAND_HZ[1])
        return tuple(2 * np.sum(np.abs(spectrum[band]) ** 2, axis=0) / len(signal) ** 2)
