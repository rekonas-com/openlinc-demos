"""What a headband offers to programs.

Everything a headband offers is defined here once; nothing describes it at run time. A name is the
part of an address after the device identifier, for example ``eeg/raw`` in ``/h1/eeg/raw``.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Stream:
    """Samples at a regular rate, each with the same number of values.

    Attributes
    ----------
    rate_hz
        Samples per second.
    channels
        Values per sample.
    """

    rate_hz: float
    channels: int


HEADBAND_STREAMS = {
    "eeg/raw": Stream(rate_hz=256.0, channels=2),
    "motion/orientation": Stream(rate_hz=50.0, channels=4),
    "motion/raw": Stream(rate_hz=50.0, channels=6),
    "light/lux": Stream(rate_hz=1.0, channels=1),
}
"""A headband's streams by name.

- ``eeg/raw``: EEG left and right in µV, unfiltered.
- ``motion/orientation``: the head's orientation as a quaternion ``w, x, y, z``, device to world.
- ``motion/raw``: acceleration ``ax, ay, az`` in m/s² and rotation ``gx, gy, gz`` in rad/s.
- ``light/lux``: ambient light in lux; NaN means no valid reading.
"""

HEADBAND_RESULTS = {
    "eeg/broadband_power": Stream(rate_hz=10.0, channels=2),
}
"""A headband's streams that are computed on the PC, by name.

- ``eeg/broadband_power``: power of each EEG channel (left, right) from 1 to 45 Hz over the last
  second, in µV²; each value carries the time of the end of its second. At rest it is the power
  of the EEG itself with the sensor's noise. It rises with muscle activity such as a jaw clench,
  and with strong eye movements and blinks, and shows that the headband is worn and delivers a
  signal.
"""

HEADBAND_ACTIONS = ("gaze/looking_left", "gaze/looking_right", "eyes/blink", "jaw/clench")
"""What a headband's wearer can do that is reported as an event, by the event's name.

- ``gaze/looking_left``: the wearer's eyes jumped to the wearer's left, relative to the head.
- ``gaze/looking_right``: the same to the wearer's right.
- ``eyes/blink``: the wearer blinked.
- ``jaw/clench``: the wearer clenched the jaw.

They are for letting the wearer control something without hands. Each is reported only after a
calibration in which a program prompted the wearer to do it, see ``HEADBAND_COMMANDS``. An event
states that the action happened and when it started, not how large it was or where the eyes
point afterwards.
"""

HEADBAND_EVENTS = (*HEADBAND_ACTIONS, "calibration/done", "calibration/failed")
"""A headband's events by name: the wearer's actions, and how a calibration ended.

- ``calibration/done``: the calibration a program ended has succeeded; actions are reported
  from now on as it taught them.
- ``calibration/failed``: it has not, because nothing was prompted or a prompted action showed
  fewer than three times in the EEG; what an earlier calibration taught stays in place.
"""

HEADBAND_COMMANDS = ("calibration/running", "calibration/prompt", "calibration/cancel")
"""What a program can tell the processing of a headband's data, by name.

- ``calibration/running``: 1 starts a calibration of the wearer's actions, 0 ends it and makes
  the processing learn from it. While it runs no action is reported.
- ``calibration/prompt``: the name of the action the wearer is asked to do at this moment, one
  of ``HEADBAND_ACTIONS``. The program sends it when it shows the prompt.
- ``calibration/cancel``: 1 gives up the calibration that runs, for example when the program is
  closed in the middle of it. Nothing is learned, no event answers it, and actions are reported
  again as an earlier calibration taught them.
"""
