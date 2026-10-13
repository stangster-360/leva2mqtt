#!/usr/bin/env python3
"""
fake_ito.py – serve a capture file over telnet as if it were the ito module.

    python3 tools/fake_ito.py captures/2026-10-12_122739_shots.txt [--port 2300]
                              [--from 200] [--to 260] [--speed 1]

For developing the bridge without the machine. Every connection gets the `{…}` and
`<…>` lines of the file with their original timing (scaled by --speed), from offset
--from to --to seconds. Commands the client sends are printed. The fake does not
react to commands. Standard library only.
"""
import argparse
import asyncio


def load(path, start, end):
    lines, t0 = [], None
    for line in open(path, encoding="utf-8", errors="replace"):
        stamp, _, rest = line.rstrip("\n").partition(" ")
        try:
            t = float(stamp)
        except ValueError:
            continue
        t0 = t if t0 is None else t0
        rel = t - t0
        if rest[:1] in "{<$" and start <= rel and (end is None or rel <= end):
            lines.append((rel, rest))
    return lines


async def serve(reader, writer, lines, speed):
    peer = writer.get_extra_info("peername")
    print(f"client {peer} connected")

    async def read_commands():
        while True:
            data = await reader.readline()
            if not data:
                return
            cmd = data.decode("latin-1").strip()
            if cmd:
                print(f"client sent: {cmd}")

    task = asyncio.create_task(read_commands())
    try:
        loop = asyncio.get_running_loop()
        start = loop.time()
        base = lines[0][0] if lines else 0
        for rel, text in lines:
            delay = (rel - base) / speed - (loop.time() - start)
            if delay > 0:
                await asyncio.sleep(delay)
            if task.done():
                break
            writer.write(text.encode("latin-1") + b"\r\n")
            await writer.drain()
        print("end of capture, holding the connection silently (like a module without power)")
        await task
    except (ConnectionError, OSError):
        pass
    finally:
        task.cancel()
        writer.close()
        print(f"client {peer} gone")


async def main():
    ap = argparse.ArgumentParser(description="Replay a capture as a fake ito module.")
    ap.add_argument("capture")
    ap.add_argument("--port", type=int, default=2300)
    ap.add_argument("--from", dest="start", type=float, default=0)
    ap.add_argument("--to", dest="end", type=float, default=None)
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()
    lines = load(a.capture, a.start, a.end)
    print(f"{len(lines)} lines, listening on port {a.port}")
    server = await asyncio.start_server(lambda r, w: serve(r, w, lines, a.speed), "0.0.0.0", a.port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
