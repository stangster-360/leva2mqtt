#!/usr/bin/env python3
"""
replay.py – run a capture file (from capture.py) through the bridge's detection logic.

    python3 tools/replay.py captures/2026-10-12_122739_shots.txt [--frames]

Prints state changes, the moments the bridge would send `MCcREPORT SP1`, and every
finished pump run with its classification. Nothing is sent anywhere. Use it to check
the detection rules against your own machine.

A gap of more than 3 s between frames counts as a lost link, like the bridge's
watchdog. The first frame of the file counts as the bridge start.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from leva2mqtt.frame import parse  # noqa: E402
from leva2mqtt.machine import Tracker, WakeReporter  # noqa: E402
from leva2mqtt.shots import ShotRecorder  # noqa: E402

WATCHDOG_S = 3.0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("capture")
    ap.add_argument("--frames", action="store_true", help="also print the decoded state per second")
    ap.add_argument("--min-offline", type=float, default=10.0)
    a = ap.parse_args()

    tracker, reporter, recorder = Tracker(), WakeReporter(a.min_offline), ShotRecorder()
    t0 = last_frame = None
    up = False
    last_print = None
    state = None

    def ts(t):
        return f"{t - t0:8.1f}s"

    for line in open(a.capture, encoding="utf-8", errors="replace"):
        stamp, _, rest = line.rstrip("\n").partition(" ")
        try:
            t = float(stamp)
        except ValueError:
            continue
        if t0 is None:
            t0 = t
        if rest.startswith("#"):
            print(f"{ts(t)}  {rest}")
            continue
        frame = parse(rest)
        if frame is None:
            continue
        if up and t - last_frame > WATCHDOG_S:
            print(f"{ts(last_frame + WATCHDOG_S)}  link lost (no frame for {t - last_frame:.1f} s)")
            run = recorder.abort()
            if run:
                print(f"{ts(t)}  RUN (aborted) {run.summary()}")
            tracker.link_down()
            up = False
        if not up:
            offline_since = last_frame  # None for the first frame = bridge start
            reporter.link_up(t, offline_since)
            print(f"{ts(t)}  link up" + (f" after {t - offline_since:.1f} s" if offline_since else " (bridge start)"))
            up = True
        last_frame = t
        change = tracker.update(frame, t)
        if change:
            reporter.mode_change(change[0], change[1], t)
            print(f"{ts(t)}  mode {change[0]} -> {change[1]}")
        if reporter.due(tracker.mode):
            print(f"{ts(t)}  >> MCcREPORT SP1, then MCr")
        if tracker.state != state:
            state = tracker.state
            print(f"{ts(t)}  state {state}  (T={frame.temperature} SP={frame.setpoint})")
        run = recorder.update(frame, t)
        if run:
            print(f"{ts(t)}  RUN {run.summary()}")
        if a.frames and (last_print is None or t - last_print >= 1):
            last_print = t
            print(f"{ts(t)}    T={frame.temperature} p={frame.pressure} w={frame.weight} "
                  f"vol={frame.volume} rate={frame.flow_rate} pump={frame.pump}")
    run = recorder.abort()
    if run:
        print(f"end        RUN (open at end of file) {run.summary()}")


if __name__ == "__main__":
    main()
