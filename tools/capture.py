#!/usr/bin/env python3
"""
capture.py – record the raw telnet stream of an ito / leva! module with timestamps.

    python3 capture.py <ito-ip> <name> [--mode mcr|display|both|none] [--minutes N]

- Writes ./captures/<date>_<time>_<name>.txt, one line per received line:
      <unix time> <line>
  plus event lines starting with '#' (connect, disconnect, commands you sent).
- Sends a bare newline every 5 s so the module keeps the connection open.
- Reconnects automatically (every 2 s) when the machine is switched off or
  unreachable, and logs when it comes back. On reconnect the start command for
  the chosen mode is sent again.
- Anything you type in the terminal and confirm with Enter is sent to the module
  as-is (e.g. MCa, MCE, MCu) and logged as '# >> MCa'. Lines starting with '#'
  are only written to the log as notes (e.g. '# flush starts'). Type 'q' to stop.

Modes: mcr = MCr (sensor frames), display = MC@ (display frames),
       both = MCr followed by MC@, none = send nothing (observe default output).

Python 3.8+, standard library only.
"""
import argparse
import os
import socket
import sys
import threading
import time
from datetime import datetime

PORT = 23
KEEPALIVE_S = 5
RETRY_S = 2
START = {"mcr": ["MCr"], "display": ["MC@"], "both": ["MCr", "MC@"], "none": []}


class Capture:
    def __init__(self, host, path, mode):
        self.host, self.mode = host, mode
        self.out = open(path, "w", encoding="utf-8", buffering=1)
        self.sock = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.lines = 0

    def log(self, text):
        self.out.write(f"{time.time():.3f} {text}\n")

    def event(self, text):
        self.log(f"# {text}")
        print(f"{datetime.now():%H:%M:%S} {text}")

    def send(self, cmd, quiet=False):
        with self.lock:
            if not self.sock:
                if not quiet:
                    self.event(f"not connected, '{cmd}' not sent")
                return
            try:
                self.sock.sendall(cmd.encode("latin-1") + b"\r\n")
                if not quiet:
                    self.event(f">> {cmd}")
            except OSError:
                pass

    def keepalive(self):
        while not self.stop.wait(KEEPALIVE_S):
            with self.lock:
                if self.sock:
                    try:
                        self.sock.sendall(b"\n")
                    except OSError:
                        pass

    def stdin_reader(self):
        for line in sys.stdin:
            cmd = line.rstrip("\r\n")
            if cmd.lower() == "q":
                self.stop.set()
                break
            if cmd.startswith("#"):
                self.event(f"note: {cmd[1:].strip()}")
            elif cmd:
                self.send(cmd)

    def run(self, minutes):
        threading.Thread(target=self.keepalive, daemon=True).start()
        threading.Thread(target=self.stdin_reader, daemon=True).start()
        end = time.time() + minutes * 60 if minutes else None
        was_offline_since = None
        last_status = 0
        while not self.stop.is_set() and (end is None or time.time() < end):
            try:
                s = socket.create_connection((self.host, PORT), timeout=3)
            except OSError as e:
                if was_offline_since is None:
                    was_offline_since = time.time()
                    self.event(f"offline ({e.__class__.__name__}), retrying every {RETRY_S} s")
                self.stop.wait(RETRY_S)
                continue
            s.settimeout(1.0)
            with self.lock:
                self.sock = s
            if was_offline_since is not None:
                self.event(f"connected again after {time.time() - was_offline_since:.0f} s offline")
            else:
                self.event(f"connected to {self.host}:{PORT}")
            was_offline_since = None
            for cmd in START[self.mode]:
                self.send(cmd)
                time.sleep(0.3)
            buf = b""
            try:
                while not self.stop.is_set() and (end is None or time.time() < end):
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        chunk = None
                    if chunk == b"":
                        raise ConnectionError("closed by module")
                    if chunk:
                        buf += chunk
                        while b"\n" in buf:
                            raw, buf = buf.split(b"\n", 1)
                            text = raw.rstrip(b"\r").decode("latin-1")
                            if text:
                                self.log(text)
                                self.lines += 1
                    if time.time() - last_status > 30:
                        last_status = time.time()
                        print(f"{datetime.now():%H:%M:%S} {self.lines} lines recorded")
            except (OSError, ConnectionError) as e:
                self.event(f"connection lost ({e})")
                was_offline_since = time.time()
            finally:
                with self.lock:
                    self.sock = None
                s.close()
        self.event("capture finished")
        self.out.close()


def main():
    ap = argparse.ArgumentParser(description="Record the ito / leva! telnet stream.")
    ap.add_argument("host")
    ap.add_argument("name", help="short label, e.g. shot or heatup")
    ap.add_argument("--mode", choices=START, default="mcr")
    ap.add_argument("--minutes", type=float, default=0, help="stop after N minutes (0 = until 'q' or Ctrl+C)")
    a = ap.parse_args()
    os.makedirs("captures", exist_ok=True)
    path = os.path.join("captures", f"{datetime.now():%Y-%m-%d_%H%M%S}_{a.name}.txt")
    print(f"writing {path}  (command + Enter = send, '# text' = note, 'q' = stop)")
    cap = Capture(a.host, path, a.mode)
    try:
        cap.run(a.minutes)
    except KeyboardInterrupt:
        cap.stop.set()
        cap.event("stopped with Ctrl+C")
        cap.out.close()
    print(f"saved {path}")


if __name__ == "__main__":
    main()
