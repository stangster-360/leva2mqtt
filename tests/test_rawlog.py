import asyncio
import gzip
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from fake_module import FakeModule  # noqa: E402

from leva2mqtt.config import load
from leva2mqtt.link import Link
from leva2mqtt.rawlog import RawLog


def _ts(day: str, hour: int = 12) -> float:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00").timestamp()


def test_capture_format_and_append_after_restart(tmp_path):
    t = _ts("2026-10-16")
    log = RawLog(str(tmp_path), background=False, clock=lambda: t)
    log.line(t, "{frame}")
    log.line(t, "")                          # empty lines are not recorded
    log.note(">> MCr", t + 0.25)
    log.close()
    log2 = RawLog(str(tmp_path), background=False, clock=lambda: t + 1)
    log2.note("bridge started")
    log2.close()
    text = (tmp_path / "2026-10-16_bridge.txt").read_text()
    assert text.splitlines() == [f"{t:.3f} {{frame}}", f"{t + 0.25:.3f} # >> MCr",
                                 f"{t + 1:.3f} # bridge started"]


def test_new_day_compresses_old_files_and_prunes(tmp_path):
    today = datetime(2026, 10, 16).date()
    for back in (1, 6, 7, 9):
        day = (today - timedelta(days=back)).isoformat()
        (tmp_path / f"{day}_bridge.txt").write_text(f"1.000 {day}\n")
    (tmp_path / "2026-10-12_090000_heatup.txt").write_text("other file\n")
    log = RawLog(str(tmp_path), keep_days=7, background=False)
    log.line(_ts("2026-10-16"), "{frame}")
    log.close()
    names = sorted(os.listdir(tmp_path))
    assert names == ["2026-10-10_bridge.txt.gz", "2026-10-12_090000_heatup.txt",
                     "2026-10-15_bridge.txt.gz", "2026-10-16_bridge.txt"]
    with gzip.open(tmp_path / "2026-10-15_bridge.txt.gz", "rt") as fh:
        assert fh.read() == "1.000 2026-10-15\n"


def test_rotation_at_midnight(tmp_path):
    log = RawLog(str(tmp_path), background=False)
    log.line(_ts("2026-10-16", 23), "{a}")
    log.line(_ts("2026-10-17", 0), "{b}")
    log.close()
    assert sorted(os.listdir(tmp_path)) == ["2026-10-16_bridge.txt.gz", "2026-10-17_bridge.txt"]


def test_write_error_disables_instead_of_raising(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    log = RawLog(str(blocker), background=False)
    log.line(_ts("2026-10-16"), "{frame}")      # no exception
    log.note("still fine")
    assert log._broken


def test_config_section_from_env():
    cfg = load("/nonexistent", {"MACHINE_HOST": "x", "SHOTS_DIRECTORY": "/tmp/s",
                                "CAPTURE_ENABLED": "true", "CAPTURE_KEEP_DAYS": "3"})
    assert cfg.capture.enabled and cfg.capture.keep_days == 3
    assert cfg.capture.directory == "/data/captures"
    assert not load("/nonexistent", {"MACHINE_HOST": "x"}).capture.enabled


def test_link_records_lines_commands_and_connection(tmp_path):
    fake = FakeModule(mode="display")

    async def run():
        port = await fake.start()
        lk = Link("127.0.0.1", port, on_frame=lambda f, t: None, on_link=lambda *a: None)
        lk.tap = RawLog(str(tmp_path), background=False)
        task = asyncio.create_task(lk.run())
        await asyncio.sleep(1.2)
        lk.stop()
        await task
        await fake.close()
        lk.tap.close()

    asyncio.run(run())
    (path,) = tmp_path.iterdir()
    lines = [ln.split(" ", 1)[1] for ln in path.read_text().splitlines()]
    assert lines[0].startswith("# connected to 127.0.0.1:")
    assert lines[1] == "# >> MCr"
    assert any(ln.startswith("{") for ln in lines)
