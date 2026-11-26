"""What the wearer does with eyes and jaw, from the EEG: looks to a side, blinks, clenches.

The method needs a calibration with the wearer: a program prompts each action several times and
says so, and the method learns from what the EEG showed after each prompt.

It works in two steps. First it finds the moments something happens: both channels and their
difference pass a 1 to 10 Hz band-pass filter, which removes the drift and the fast activity and
keeps the jump of a look, and a candidate starts when one of them leaves its usual range, or
when the fast fluctuation of the unfiltered signal rises far above its usual level. Then it
names the candidate: the filtered signals around that moment and the fast fluctuation go into
linear discriminant analysis, trained on the candidates of the calibration.
"""

import math
from collections import deque

import numpy as np
from scipy.signal import butter, sosfilt
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis

RATE_HZ = 256
BAND_HZ = (1.0, 10.0)
FAST_SPAN = 26
"""Samples the fast fluctuation is taken over: 0.1 s."""
LEAVES = 4.0
"""A candidate starts when a filtered signal exceeds this many times its usual spread."""
BACK = 2.0
"""The next candidate is looked for once the signals stayed below this many times the spread."""
CALM = 26
"""Samples the signals have to stay back before the next candidate: 0.1 s."""
FAST = 4.0
"""A candidate also starts when the fast fluctuation exceeds this many times its usual level."""
BLOCK = 13
"""Samples handled at once; the usual spread and level take one value from each block."""
USUAL_BLOCKS = 197
"""Blocks the usual spread and the usual fast fluctuation are taken over: 10 s."""
PART = 14
"""Samples in one part of a candidate's signal: about 55 ms."""
BEFORE, AFTER = 2, 4
"""Parts of a candidate's signal before and after the moment it starts."""
PROMPT_S = (0.05, 1.15)
"""A candidate belongs to a prompt when it starts this long after it."""
ENOUGH = 3
"""A prompted action needs this many candidates for a calibration to succeed."""
SURE = 0.6
"""Least probability of an action for a candidate to be reported as it."""
OTHER = "other"
"""Class of the candidates of a calibration that no prompt asked for."""


class Candidates:
    """The moments something happens in the EEG, each with the numbers that describe it."""

    def __init__(self) -> None:
        self._filter = butter(2, BAND_HZ, "bandpass", fs=RATE_HZ, output="sos")
        self._state: np.ndarray | None = None
        self._first = np.zeros(3)
        self._last_raw: np.ndarray | None = None
        self._times = np.empty(0, dtype=np.int64)  # samples that do not fill a block yet
        self._levels = np.empty((0, 3))
        self._squares = np.empty((0, 2))
        self._kept_levels = np.zeros((PART * (BEFORE + AFTER), 3))
        self._kept_squares = np.zeros((FAST_SPAN, 2))
        self._usual_level: deque[np.ndarray] = deque(maxlen=USUAL_BLOCKS)
        self._usual_fast: deque[float] = deque(maxlen=USUAL_BLOCKS)
        self._wait: int | None = None  # samples until the running candidate is complete
        self._start_us = 0
        self._peak = 0.0
        self._calm = 0
        self._ready = False

    def add(self, times_us: np.ndarray, eeg: np.ndarray) -> list[tuple[int, np.ndarray]]:
        """Take the next EEG samples and return the candidates they complete.

        Parameters
        ----------
        times_us
            Headband time of each sample in microseconds.
        eeg
            The samples, one row of both channels per sample.

        Returns
        -------
        list of (int, numpy.ndarray)
            For each candidate the time it started and its numbers, oldest first. A candidate is
            complete 0.22 s after it started.
        """
        eeg = np.asarray(eeg, dtype=float).reshape(-1, 2)
        if len(eeg) == 0:
            return []
        three = np.column_stack([eeg, eeg[:, 0] - eeg[:, 1]])  # both channels, their difference
        if self._state is None:
            self._state = np.zeros((len(self._filter), 2, 3))
            self._first, self._last_raw = three[0].copy(), eeg[0]
        levels, self._state = sosfilt(self._filter, three - self._first, axis=0, zi=self._state)
        squares = np.diff(eeg, axis=0, prepend=self._last_raw[None]) ** 2
        self._last_raw = eeg[-1]
        self._times = np.concatenate([self._times, np.asarray(times_us, dtype=np.int64)])
        self._levels = np.concatenate([self._levels, levels])
        self._squares = np.concatenate([self._squares, squares])
        found = []
        whole = len(self._times) // BLOCK * BLOCK
        for start in range(0, whole, BLOCK):
            block = slice(start, start + BLOCK)
            found += self._block(self._times[block], self._levels[block], self._squares[block])
        self._times = self._times[whole:]
        self._levels, self._squares = self._levels[whole:], self._squares[whole:]
        return found

    def _block(
        self, times_us: np.ndarray, levels: np.ndarray, squares: np.ndarray
    ) -> list[tuple[int, np.ndarray]]:
        """Handle one block of samples, with the usual spread and level as they were before it."""
        recent = np.concatenate([self._kept_levels, levels])
        sums = np.cumsum(np.concatenate([self._kept_squares, squares]), axis=0)
        fast = np.sqrt((sums[FAST_SPAN:] - sums[:-FAST_SPAN]) / FAST_SPAN).max(axis=1)
        self._kept_levels = recent[BLOCK:]
        self._kept_squares = np.concatenate([self._kept_squares, squares])[BLOCK:]
        found = []
        if len(self._usual_fast) >= USUAL_BLOCKS // 5:
            spread = 1.4826 * np.median(np.abs(self._usual_level), axis=0)
            usual = float(np.median(self._usual_fast))
            far = np.max(np.abs(levels) / spread, axis=1)
            at = 0
            while at < BLOCK:
                if self._wait is not None:  # a candidate is running: wait for its end
                    take = min(self._wait, BLOCK - at)
                    self._peak = max(self._peak, float(fast[at : at + take].max()))
                    self._wait -= take
                    at += take
                    if self._wait == 0:
                        signal = recent[at : at + len(self._kept_levels)]
                        parts = signal.reshape(BEFORE + AFTER, PART, 3).mean(axis=1) / spread
                        row = np.append(parts.T.ravel(), math.log(self._peak / usual))
                        found.append((self._start_us, row))
                        self._wait = None
                elif not self._ready:  # after a candidate: wait until the signals are back
                    calm = (far[at:] < BACK) & (fast[at:] < BACK * usual)
                    count = np.arange(len(calm))
                    since = np.maximum.accumulate(np.where(calm, -1, count))
                    run = np.where(since < 0, count + 1 + self._calm, count - since)
                    reached = np.flatnonzero(run >= CALM)
                    if reached.size == 0:
                        self._calm = int(run[-1])
                        break
                    self._ready, self._calm = True, 0
                    at += int(reached[0]) + 1
                else:  # look for the next candidate
                    leaving = np.flatnonzero((far[at:] > LEAVES) | (fast[at:] > FAST * usual))
                    if leaving.size == 0:
                        break
                    at += int(leaving[0])
                    self._start_us, self._peak = int(times_us[at]), float(fast[at])
                    self._wait, self._ready = PART * AFTER, False
                    at += 1
        if self._wait is None:
            self._usual_level.append(levels[-1])
            self._usual_fast.append(float(fast[-1]))
        return found


class Actions:
    """The wearer's actions in one headband's EEG, by the names a calibration gave them.

    Nothing is reported before a calibration succeeded. A calibration starts with :meth:`start`;
    each :meth:`prompt` says that the wearer is asked to do an action now; :meth:`finish` learns
    from what followed the prompts, and :meth:`cancel` gives it up. While a calibration runs
    nothing is reported.
    """

    def __init__(self) -> None:
        self._candidates = Candidates()
        self._model: LinearDiscriminantAnalysis | None = None
        self._now_us = 0
        self._seen: list[tuple[int, np.ndarray]] | None = None  # candidates of the calibration
        self._prompts: list[tuple[int, str]] = []

    def add(self, times_us: list[int], eeg: list) -> list[tuple[str, int]]:
        """Take the next EEG samples and return the actions they complete.

        Parameters
        ----------
        times_us
            Headband time of each sample in microseconds.
        eeg
            One row of both channels per sample.

        Returns
        -------
        list of (str, int)
            Each action's name and the headband time it started, oldest first.
        """
        if len(times_us) == 0:
            return []
        self._now_us = int(times_us[-1])
        found = self._candidates.add(np.asarray(times_us, dtype=np.int64), np.asarray(eeg))
        if self._seen is not None:
            self._seen += found
            return []
        return [(name, time_us) for time_us, row in found if (name := self._name(row)) is not None]

    def start(self) -> None:
        """Start a calibration; one that is running starts over."""
        self._seen, self._prompts = [], []

    def prompt(self, name: str) -> None:
        """Say that the wearer is asked to do an action now.

        Parameters
        ----------
        name
            The name under which the action is reported afterwards.
        """
        self._prompts.append((self._now_us, name))

    def cancel(self) -> None:
        """Give up the calibration that runs; what was learned before stays in place."""
        self._seen, self._prompts = None, []

    def finish(self) -> bool:
        """End the calibration and learn from it.

        A candidate that started shortly after a prompt is an example of the prompted action;
        every other candidate of the calibration is an example of what is not to be reported.

        Returns
        -------
        bool
            Whether it succeeded. It fails when no calibration runs, when nothing was prompted,
            or when a prompted action has fewer than ``ENOUGH`` examples; what was learned
            before then stays in place.
        """
        seen, prompts = self._seen, self._prompts
        self._seen, self._prompts = None, []
        if not seen or not prompts:
            return False
        starts = np.array([time_us for time_us, _ in seen])
        names = np.full(len(seen), OTHER, dtype=object)
        for time_us, name in prompts:
            late = (starts - time_us) / 1e6
            free = np.flatnonzero((late >= PROMPT_S[0]) & (late <= PROMPT_S[1]) & (names == OTHER))
            if free.size:
                names[free[0]] = name
        names = names.astype(str)
        if any(np.sum(names == name) < ENOUGH for _, name in prompts) or len(set(names)) < 2:
            return False
        model = LinearDiscriminantAnalysis(solver="lsqr", shrinkage="auto")
        self._model = model.fit(np.array([row for _, row in seen]), names)
        return True

    def _name(self, row: np.ndarray) -> str | None:
        """Return the action a candidate is, or None for another or an unclear one."""
        if self._model is None:
            return None
        chances = self._model.predict_proba(row[None])[0]
        best = int(chances.argmax())
        name = str(self._model.classes_[best])
        return name if name != OTHER and chances[best] >= SURE else None
