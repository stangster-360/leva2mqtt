"""A small fake ito module that reacts to MCr / MC@ (global report mode, like the real one)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from framegen import frame  # noqa: E402

MAIN = "<102GEN.LEVER       1: 90.1ß \x04 90.0ß\x03             9% 0.9b-  0s   0ml>"


def main_screen(alert: str = "", temp: float = 90.1) -> str:
    line3 = (alert.ljust(12) + "  9%")[:16].rjust(16)
    return f"<102GEN.LEVER       1: {temp:4.1f}ß \x04 90.0ß{line3} 0.9b-  0s   0ml>"


class FakeModule:
    def __init__(self, mode: str = "display"):
        self.mode = mode
        self.mcr = frame()
        self.display = MAIN
        self.received: list[str] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.server = None
        self._task = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self._task = asyncio.create_task(self._broadcast())
        return self.server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        self._task.cancel()
        for w in self.writers:
            w.close()
        self.server.close()

    def send_line(self, line: str) -> None:
        for w in self.writers:
            w.write(line.encode("latin-1") + b"\r\n")

    def switch_by_other_client(self) -> None:
        """What the Leva Companion app does on connect: MC?, MCu, MC@."""
        self.send_line("XML=leva!-00000000")
        self.send_line("PID 1...       ")
        self.send_line("| Control     PID")
        self.mode = "display"

    async def _client(self, reader, writer):
        self.writers.append(writer)
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                cmd = data.decode("latin-1").strip()
                if not cmd:
                    continue
                self.received.append(cmd)
                if cmd == "MCr":
                    self.mode = "mcr"
                elif cmd == "MC@":
                    self.mode = "display"
        except (ConnectionError, OSError):
            pass
        finally:
            if writer in self.writers:
                self.writers.remove(writer)

    async def _broadcast(self):
        n = 0
        while True:
            await asyncio.sleep(0.1)
            n += 1
            if self.mode == "display":
                self.send_line(self.display)
            elif n % 2 == 0:
                self.send_line(self.mcr)
