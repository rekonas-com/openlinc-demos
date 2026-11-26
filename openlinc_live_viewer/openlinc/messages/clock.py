"""The headband's clock, made continuous across its 32-bit wrap."""

from openlinc.messages.phone_format import signed_delta


class Clock:
    """Places 32-bit headband timestamps of one session on one continuous clock."""

    def __init__(self) -> None:
        self._latest: tuple[int, int] | None = None

    def unwrap(self, timestamp_us: int) -> int:
        """Return the continuous time of a headband timestamp.

        Parameters
        ----------
        timestamp_us
            Headband timestamp in microseconds, unsigned 32-bit.

        Returns
        -------
        int
            The continuous time in microseconds; a timestamp may lie before the latest one.
        """
        if self._latest is None:
            self._latest = (timestamp_us, timestamp_us)
            return timestamp_us
        raw, unwrapped = self._latest
        time_us = unwrapped + signed_delta(timestamp_us, raw)
        if time_us > unwrapped:
            self._latest = (timestamp_us, time_us)
        return time_us
