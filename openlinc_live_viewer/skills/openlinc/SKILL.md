---
name: openlinc
description: Build real-time programs on live Somnolinc headband data from the local bridge — events for looks to the left and right (hands-free control with the eyes), raw EEG and its broadband power (muscle activity such as a jaw clench, blinks), head orientation and motion (IMU), ambient light, and whether the headband is connected and streaming. Use this whenever the user wants to read, react to, plot, record or analyse the live headband stream in this folder, or build any application that interfaces with the headband or is controlled with the eyes or the head (desktop applications and tools, hands-free control, live displays, recorders, experiments, installations, games, in Python or in any tool that speaks OSC), even if they do not mention OSC, the bridge or the phone.
---

# Live headband data

The Somnolinc phone app streams the headband's raw data over Wi-Fi to the **bridge**, a Python
program in this folder. Programs on this PC get the data from the bridge over localhost, as OSC
over UDP. The bridge also starts a processing service, which computes results from the raw data,
such as looks to the left and right, and the bridge passes those on like the raw data. In Python,
`control` from the `openlinc` package receives and buffers everything with headband times; prefer
it to writing socket or decoding code.

This serves any application that interfaces with the headband, not one kind of program: desktop
applications and tools, hands-free control of other software, live displays and recorders,
experiments and measurements, installations, and games. "Program" below means any of these.

## How a program uses it

A program subscribes to the bridge once and then deals with four kinds of things. Choose by what
the program needs, not by what is easiest to compute yourself:

| Kind | What it is | Use it for | In Python |
| --- | --- | --- | --- |
| Stream | numbers at a regular rate, raw or computed, each sample with its headband time | anything that has a value at every moment: plotting, recording, filtering, steering by head tilt | `h1.window(name, seconds)` |
| State | one word for what the headband is doing | pausing while no samples flow, and telling the wearer what to do about it | `h1.state` |
| Event | something that happened at one moment, such as a look to the left | a trigger, handled like a key press | `h1.events()` |
| Command | something only the program can know, told to the processing service, such as "the wearer is asked to blink now" | calibrating what the service computes | `h1.prompt()` |

- **A program asks and never waits.** On each pass of its own loop it reads the state, collects the
  events and takes the latest window of the streams it needs, then draws or acts. Nothing blocks the
  program's loop, be it an application's timer, a display's refresh or a game's frame, and nothing
  calls back into it.
- **Prefer what is offered ready-made.** For looks to the left and right use the events, and for
  "is there muscle activity" or "is the headband worn" the broadband power, before building a
  detector on the raw EEG.
- **Subscribing** is what makes the bridge send to a program. `control.Headband` does it; a program
  in another language does it itself, see [Programs in other languages](#programs-in-other-languages).
- **The bridge's status** (`uv run bridge status`) is for you, before building and when nothing
  arrives: it says whether a phone is linked, what arrives at which rate and what was lost. A
  program rarely needs it; inside a program, `h1.state` says what to tell the wearer.

## Before building

1. Check that data arrives: `uv run bridge status`. It prints JSON with these keys:
   - `link`: `waiting`, `linked` or `not_responding` (see step 3).
   - `phone`: the linked phone's address and port, or null.
   - `seconds_since_last_message`: time since the phone last sent anything, or null.
   - `lost_datagrams`: data messages lost between phone and bridge.
   - `malformed_datagrams`: messages the bridge could not read.
   - `subscribers`: local programs currently receiving the stream.
   - `service`: `running` or `not_running`, the processing service that computes the events.
   - `streams`: one entry per address, with `rate_hz` (measured), `expected_rate_hz`, `packets`,
     `samples`, `missing_samples` (samples the headband itself dropped), `session` and
     `arrival_delay_ms` (`median`, `p95`, `max`: how much later than the fastest packet the others
     arrived over the last seconds).
2. If no bridge answers, start it in the background with `uv run bridge run`. It opens a pairing
   page with a QR code in the browser. Ask the user to tap "Scan QR code" on the Live screen's
   "Stream to PC" card and scan the code; data flows once the headband is connected too.
   `/h1/state` says whether it is (see [What arrives](#what-arrives)).
3. `link` in the status says what to tell the user: `waiting` means scan the code on the page
   (reload it for the current code); `not_responding` means the phone went quiet (Wi-Fi, app in the
   background) and continues by itself when it is back.
4. If a bridge is already running, `uv run bridge run` does not start a second one: it prints the
   pairing page's path and ends with exit code 1. Say what you want with `--if-running`:
   - `uv run bridge run --if-running open` opens the running bridge's pairing page again, for
     example when the user has closed it and needs the QR code. The bridge keeps running.
   - `uv run bridge run --if-running restart` replaces the running bridge. Use it only when the
     user asks for it or the bridge misbehaves: it ends the phone's link, so the user has to scan
     the QR code again.

Never print the raw stream into the conversation; it is hundreds of values per second. Use
`uv run bridge listen --seconds 3` for message rates, or compute and show summaries.

## Writing and running a program

This folder is a uv project with the `openlinc` package in it. Write the user's program next to
`README.md`, as one file or as a folder of its own, and leave the `openlinc` folder unchanged.

- **Run it** with `uv run my_program.py`, while the bridge runs.
- **Already there:** numpy and scipy, enough for filtering and spectra.
- **Add a library** with `uv add <package>`. Nothing for windows, drawing or controlling other
  software comes with the folder, so add what the program needs.

Suggestions by kind of program, where the user names no library:

| Kind of program | Library |
| --- | --- |
| live plot of signals | pyqtgraph, with PySide6 |
| application with a window, buttons and settings | PySide6 |
| game or full-screen visuals | pygame |
| pressing keys or moving the mouse in other software | pynput |
| output for a terminal only | none |

Choose a library whose loop the program owns or that has a timer, and read the headband from
there: once per frame in pygame, from a `QTimer` in PySide6 and pyqtgraph. The headband is asked
on each pass and never calls back.

## What arrives

| Address | Values per sample | Rate | Unit and meaning |
| --- | --- | --- | --- |
| `/h1/eeg/raw` | left, right | 256 Hz | µV, unfiltered; see [EEG](#eeg) |
| `/h1/motion/orientation` | w, x, y, z | 50 Hz | orientation quaternion, device-to-world, scalar first; see [Head axes](#head-axes-and-orientation) |
| `/h1/motion/raw` | ax, ay, az, gx, gy, gz | 50 Hz | acceleration in m/s² (includes gravity), rotation in rad/s |
| `/h1/light/lux` | lux | 1 Hz | ambient light; `0.0` is darkness, NaN is a second without a reading |

The processing service adds one computed stream, which arrives and is read like the raw ones:

| Address | Values per sample | Rate | What it is | What it is for |
| --- | --- | --- | --- | --- |
| `/h1/eeg/broadband_power` | left, right | 10 Hz | power of each EEG channel from 1 to 45 Hz over the last second, in µV²; each value carries the time of the end of its second | a value that rises with muscle activity such as a jaw clench, and with strong eye movements and blinks; a check that the headband is worn and delivers a signal |

At rest it is the power of the EEG itself, together with the sensor's own noise; it does not fall
to zero. In Python: `h1.window("eeg/broadband_power", seconds=5)`.

`h1` is the first headband; more devices would follow as `h2` and so on. Each message is one
headband packet (a few samples). The streams, their rates and their numbers of values are fixed as
in the table; nothing announces them at run time.

`/h1/state` carries one word for what the headband is doing, sent on every change and every 5 s.
Tell the user what to do from it:

| State | Meaning | Tell the user |
| --- | --- | --- |
| `disconnected` | the phone has no headband connected | connect the headband in the app |
| `connecting` | connecting, or connected and starting | nothing; wait a few seconds |
| `streaming` | the headband measures and samples flow | nothing |
| `start_failed` | connected, but the headband did not start measuring | disconnect and reconnect the headband in the app |

Without a phone linked to the bridge, nothing arrives, not even a state; `uv run bridge status`
then says why.

## Head axes and orientation

`/h1/motion/orientation` and `/h1/motion/raw` use the same device axes, and samples of the two
with the same timestamp come from the same reading. As worn:

| Axis | Points |
| --- | --- |
| `+X` | to the wearer's left |
| `+Y` | toward the crown |
| `+Z` | out of the forehead |

These directions are nominal: the forehead's slope and how the band sits tilt them by a few
degrees. When a program needs a neutral pose, calibrate with the user (for example "look straight
ahead for 3 seconds").

- **Quaternion** `(w, x, y, z)`: rotates device axes into world axes; world `+Z` is up. The
  identity means lying flat on the back, face up. The headband computes it from accelerometer and
  gyroscope only:
  - Tilt (which way is up) is reliable. It settles within a few seconds after the headband
    switches on, and fast movements skew it briefly.
  - Heading (turning about the vertical) has no fixed reference and drifts. Use it only for
    changes over a few seconds, never as a compass direction.
- **World up in head axes** answers "how is the head tilted" and ignores heading. For a normalized
  quaternion: `up = (2(xz − wy), 2(yz + wx), 1 − 2(x² + y²))`. Sitting upright gives about
  `(0, 1, 0)` and lying on the back `(0, 0, 1)`. Tilting toward the left shoulder makes `up.x`
  negative, toward the right shoulder positive; nodding forward makes `up.z` negative, leaning the
  head back positive.
- **Acceleration** includes gravity: at rest about 9.81 m/s² points along world up (sitting
  upright, `ay` reads about +9.81). It clips at about ±2 g (±19.6 m/s²) per axis, so knocks and
  fast nods can saturate it.
- **Rotation** is in rad/s about the device axes, right-handed.
- **Failed reads:** if the headband cannot read its motion sensor, it still sends a sample: all six
  `/h1/motion/raw` values exactly `0.0`, and the identity quaternion with the same timestamp. Treat such
  samples as missing.

## Timing

All four streams share one headband clock, so their timestamps line them up directly; EEG and
motion can still be about 10 ms apart. Never time samples by when packets arrive on the PC: Wi-Fi
delivers them in bursts and late at times.

- `timestamp_us` and `first_index` describe the packet's first sample. Both are unsigned 32-bit;
  `timestamp_us` wraps every 2³² µs (about 71.6 minutes), so compute differences modulo 2³².
- Each `session` is one acquisition run. Indices restart at 0 with a new session. Timestamps keep
  counting, but the jump from the last timestamp of one session to the first of the next does not
  equal the real pause between them, so compare timestamps only within one session.
- **EEG:** sample `i` of a packet has index `first_index + i` and lies on a 256 Hz grid (steps of
  3906 or 3907 µs).
- **Motion (`motion/orientation`, `motion/raw`):** sample `i` lies about `i × 20 ms` after the
  first. The index counts samples sent: if the headband falls behind, the lost time shows only in
  `timestamp_us`, not as an index gap.
- **Light:** value `i` lies `i` seconds after the first.
- An index gap means samples the headband itself dropped (`missing_samples` in the status), not
  data lost on the way.

## EEG

| Channel | Electrode |
| --- | --- |
| left | left forehead, roughly Fp1 |
| right | right forehead, roughly Fp2 |

Both channels are measured against one reference electrode in the middle of the forehead, between
the two (roughly Fpz). The ground electrode sits between the left electrode and the reference.

- **Polarity:** each value is reference minus electrode, the opposite of the usual EEG convention
  (electrode minus reference). This follows from the headband's design but has not been confirmed
  by a measurement, so calibrate direction-dependent features with the user instead of relying on
  the sign. Looks to the left and right need none of this: the events in
  [Looks, blinks and clenches](#looks-blinks-and-clenches) have their own calibration.
- **Unfiltered:** electrode offsets and slow drift are part of the values, and nothing removes mains
  hum; add a 50 Hz (Europe) or 60 Hz notch if it matters.
- **Muscle activity** (jaw clenching, frowning, talking) shows as broadband noise across the whole
  band; `eeg/broadband_power` rises with it.
- The electrodes sit just above the eyes, so eye movements and blinks show strongly.

## Reading the stream in Python

`control.Headband` subscribes to the bridge and keeps the latest seconds of each of one device's
streams, each sample with its headband time. It receives in a background thread, so the
program's own loop never waits: it asks for the latest data whenever it wants.

```python
import time

from openlinc import control

with control.Headband("h1", seconds=10) as h1:
    started = time.monotonic()
    while True:
        times, eeg = h1.window("eeg/raw", seconds=4)  # times in µs; eeg shape (samples, 2)
        rate = h1.rate_hz("eeg/raw")  # samples per second
        if h1.state != "streaming" and time.monotonic() - started > 1:
            print(f"headband: {h1.state}")  # None: no phone, see `uv run bridge status`
        time.sleep(0.25)
```

- Stream names are the address without the device: `eeg/raw`, `motion/orientation`, `motion/raw`,
  `light/lux`.
- `window()` covers the latest `seconds` of headband time, oldest first. Times are continuous
  across the clock's wrap and line the device's streams up directly. A window never mixes two
  sessions: a new session starts every stream afresh. Missing samples show as jumps in the times.
- **Start:** the bridge sends a new subscriber each device's latest state at once, and windows
  fill with the first packets. Until something arrives, a window is empty, `state` is None and
  `receiving` is false; that is the normal first moment, not an error.
- **Receiving:** `h1.receiving` is true while a message of this device arrived within the last
  second: samples, a state or an event. It stays true while samples flow. Without samples it is
  false nearly all the time, because the state arrives only every 5 s; `state` then says why.
- **State:** `state` is the phone's latest `/h1/state` word, or None when none arrived within the
  last 11 s (no phone linked). Windows fill only while it is `streaming`.
- **Late packets:** a packet older than what its stream already holds (Wi-Fi delivered it out of
  order) is dropped, so windows always stay in time order.
- **Session:** `h1.session` is the acquisition session the streams are in, or None before any
  sample. When it changes, every stream has started afresh.
- **Events:** `h1.events()` returns what happened since the last call, oldest first, each exactly
  once, and never waits. Call it on every pass of the loop, as an application collects its key presses.
  Each event has a `kind`, for example `gaze/looking_left`, and a `time_us` on the same clock as
  the windows. See [Looks, blinks and clenches](#looks-blinks-and-clenches).
- Without `with`, call `h1.start()` and `h1.stop()`, for example in a notebook.

`openlinc.stream.packets()` yields each message as it arrives instead, unbuffered. Use it to record
raw messages or to see every packet's fields. The messages are these classes from
`openlinc.messages.phone_format`:

| Class | What it is | Fields |
| --- | --- | --- |
| `Packet` | one packet of a stream | see below |
| `DeviceState` | a device's state | `device`, `state` |
| `EventMessage` | an event | `address`, `seq` (counter of that address's events), `session`, `timestamp_us` |
| `Command` | a command another program sent | `address`, `value` |

A `Packet` has:

| Field | Meaning |
| --- | --- |
| `address` | stream, for example `/h1/eeg/raw` |
| `seq` | the phone's counter of data messages; a gap means messages lost on the way to the PC |
| `session` | acquisition run; see [Timing](#timing) |
| `first_index` | headband sample index of the first sample |
| `timestamp_us` | headband time of the first sample in µs |
| `values` | the samples as a flat tuple, frame-major: `np.asarray(values).reshape(-1, channels)` |


## Looks, blinks and clenches

Four events say what the wearer did with eyes or jaw:

| Event | Address | What it is | What it is for |
| --- | --- | --- | --- |
| `gaze/looking_left` | `/h1/gaze/looking_left` | the eyes jumped to the wearer's left, relative to the head | a control the wearer triggers with a look to the left |
| `gaze/looking_right` | `/h1/gaze/looking_right` | the same to the wearer's right | a control the wearer triggers with a look to the right |
| `eyes/blink` | `/h1/eyes/blink` | the wearer blinked | a control triggered with a deliberate blink, or leaving blinks out of something else |
| `jaw/clench` | `/h1/jaw/clench` | the wearer clenched the jaw | a control triggered with a short clench |

An event says that the action happened and when it started, not how large it was or where the eyes
point afterwards. Looking to one side and back gives one event for each direction. An event
arrives about 0.3 s after the action started.

**Calibrate first.** Nothing is reported before a calibration. The program asks the wearer for
each action several times and says so; the processing service learns from what the EEG showed
after each request:

1. `h1.start_calibration()`.
2. Show one request at a time and call `h1.prompt(action)` at the moment it appears, with the
   event's name, for example `h1.prompt("gaze/looking_left")`.
3. Two seconds or more after the last request, `h1.finish_calibration()`.
4. Wait for the event `calibration/done` or `calibration/failed`. It fails when a requested
   action showed fewer than three times in the EEG; then run it again. What an earlier calibration
   taught stays in place until a new one succeeds.

A program that stops in the middle, because the wearer quits or the headband went away, calls
`h1.cancel_calibration()`. Otherwise the service keeps waiting for the end of the calibration and
reports no action meanwhile, also not to other programs.

How to ask, so that it works:

- **About two minutes, ten or more requests of each action,** in mixed order. Fewer makes the
  actions harder to tell apart.
- **One request every 2.5 to 3.5 s.** Show it for about a second, then pause 1.5 to 2.5 s of
  varying length, so that the wearer cannot fall into a rhythm.
- **A look is a target that jumps.** Let the point the wearer looks at jump to the side, stay a
  second and jump back. The jump out and the jump back are each a look: prompt
  `gaze/looking_left` when it jumps left and `gaze/looking_right` when it jumps back, and the
  other way round for the other side. Make the jumps as large as the looks the program will use,
  a third of the screen width or more; small looks are found less reliably.
- **Blinks and clenches without reading.** Show the request where the wearer already looks, for
  example the point changes colour with one short word beside it. A request that has to be read
  elsewhere causes eye movements of its own.
- **Request a blink regularly,** about every 8 s, and ask the wearer to blink only then. A blink
  between two requests counts as something that is not to be reported, which is fine now and
  then.
- **Head still, eyes on the point** otherwise.
- **Request all four actions,** also when the program uses only some. What was never requested
  cannot be told apart and may be reported as one of the requested actions.

```python
import random
import time

from openlinc import control

LEFT, RIGHT, BLINK, CLENCH = "gaze/looking_left", "gaze/looking_right", "eyes/blink", "jaw/clench"
REQUESTS = [
    ("left", LEFT, RIGHT),
    ("right", RIGHT, LEFT),
    ("blink", BLINK, None),
    ("clench", CLENCH, None),
]
"""What to show, the action when it appears, and the action when it goes away again."""


def calibrate(h1, show, rounds=10):
    """Run one calibration; return the session it belongs to, or None when it failed.

    show("left") moves the point the wearer looks at to the left, show("right") to the right,
    show("blink") and show("clench") mark the point with that request, and show(None) puts the
    plain point back in the middle.
    """
    while h1.state != "streaming":  # a calibration without samples teaches nothing
        time.sleep(0.1)
    others = [request for request in REQUESTS if request[0] != "blink"] * rounds
    random.shuffle(others)
    order = []
    for request in others:  # every third request is a blink
        order.append(request)
        if len(order) % 3 == 2:
            order.append(REQUESTS[2])
    h1.start_calibration()
    for what, appears, goes in order:
        time.sleep(random.uniform(1.5, 2.5))
        show(what)
        h1.prompt(appears)
        time.sleep(1.0)
        show(None)
        if goes is not None:
            h1.prompt(goes)
    time.sleep(2.0)
    h1.finish_calibration()
    while True:  # how it ended arrives as an event
        for event in h1.events():
            if event.kind in ("calibration/done", "calibration/failed"):
                return h1.session if event.kind == "calibration/done" else None
        time.sleep(0.02)
```

The waiting in `calibrate` suits a script. An application with a window runs the same steps from
its own loop, so that it keeps drawing. With `rounds=10` it takes a little over two minutes.

Afterwards the program collects the events on each pass of its loop:

```python
with control.Headband("h1", look_pause_s=0.5) as h1:
    calibrated = calibrate(h1, show)
    while True:
        if h1.session != calibrated and h1.state == "streaming":
            calibrated = calibrate(h1, show)  # failed, or a new session started
        for event in h1.events():
            if event.kind == "gaze/looking_left":
                ...
            elif event.kind == "jaw/clench":
                ...
        time.sleep(0.02)
```

- **`look_pause_s`** leaves out further looks for that long after a look. With it, the movement
  that brings the eyes back after a look is not reported as a look of its own. Without it, a
  look to the side and back gives two events.
- **Recalibrate** whenever the headband was moved or taken off, and after a new session started.
  `h1.session` is the session the streams are in, or None before any sample; remember it at the
  calibration and recalibrate when it has changed.
- **Expect a wrong or missing event now and then.** Make a single action cheap to undo, or ask
  for a deliberate pattern such as two looks to the same side.
- **Other languages:** the events and the calibration commands are OSC messages, see
  [Programs in other languages](#programs-in-other-languages).

## Programs in other languages

Anything that sends and receives OSC over UDP can use the bridge without Python: a game engine, a
creative coding tool, a program in any language. In Python, use `control.Headband` instead.

**Subscribe.** Send `/bridge/subscribe`, without arguments, to `127.0.0.1:47101` and repeat it
every 2 seconds. The bridge sends everything to the address and port the message came from, until
it has heard nothing from there for 5 seconds. So send and receive with the same UDP socket; a tool
that sends from one port and listens on another receives nothing. A new subscriber gets each
device's latest state at once.

**What arrives,** all on that socket; pick what you need by address:

| Address | Arguments | Meaning |
| --- | --- | --- |
| `/h1/eeg/raw`, `/h1/motion/orientation`, `/h1/motion/raw`, `/h1/light/lux`, `/h1/eeg/broadband_power` | int32 `seq`, int32 `session`, int32 `first_index`, int32 `timestamp_us`, then float32 values | one packet of a stream; the values are frame-major, all values of one sample next to each other, as many per sample as in [What arrives](#what-arrives) |
| `/h1/state` | one string | the headband's state, see [What arrives](#what-arrives) |
| `/h1/gaze/looking_left`, `/h1/gaze/looking_right`, `/h1/eyes/blink`, `/h1/jaw/clench`, `/h1/calibration/done`, `/h1/calibration/failed` | int32 `seq`, int32 `session`, int32 `timestamp_us` | an event; `seq` counts that address's events, so a gap means a lost one |

The integers are unsigned 32-bit numbers carried in the bits of an OSC int32: add 2³² to a
negative one. [Timing](#timing) says how `session`, `first_index` and `timestamp_us` give each
sample its time; `control.Headband` does that work in Python, here the program does it.

**What a program sends,** to the same port and from the socket it subscribed with, because the
bridge takes messages only from subscribers:

| Address | Arguments | Meaning |
| --- | --- | --- |
| `/h1/calibration/running` | one int32, 1 or 0 | 1 starts a calibration of the wearer's actions, 0 ends it; see [Looks, blinks and clenches](#looks-blinks-and-clenches) |
| `/h1/calibration/prompt` | one string, an action's name such as `gaze/looking_left` | the wearer is asked to do this action now |
| `/h1/calibration/cancel` | one int32, 1 | the calibration that runs is given up; nothing is learned and no event answers |

**Status.** `/bridge/status`, without arguments, is answered with `/bridge/status` and one JSON
string with the keys listed in [Before building](#before-building). It needs no subscription.

## Real-time rules

- Filter forward with saved state (`scipy.signal.sosfilt` with `zi`), never over the whole
  recording at once (`filtfilt`): live data has no future samples.
- Keep heavy work out of the receiving thread; `control.Headband` already receives in its own.
- Expect packets to arrive a few tens of milliseconds apart and late at times; time samples by
  their headband timestamps as described in [Timing](#timing), never by arrival.
