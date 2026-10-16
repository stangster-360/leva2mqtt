import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from fake_module import FakeModule  # noqa: E402

from leva2mqtt import rawlog
from leva2mqtt.config import load
from leva2mqtt.link import Link
from leva2mqtt.rawlog import RawLog


def _ts(day: str, hhmmss: str = "07:30:00") -> float:
    return datetime.fromisoformat(f"{day}T{hhmmss}").timestamp()


def _lines(path):
    return open(path, encoding="utf-8").read().splitlines()


def test_one_file_per_session_in_capture_format(tmp_path):
    t = _ts("2026-10-16")
    log = RawLog(str(tmp_path))
    log.note("before any session")          # dropped: no file outside a session
    log.start(t)
    log.line(t + 0.1, "{frame}")
    log.line(t + 0.2, "")                    # empty lines are not recorded
    log.note(">> MCr", t + 0.25)
    log.end(t + 600)
    log.note("after the session", t + 601)   # dropped
    log.start(t + 3600)                      # switched on again an hour later
    log.line(t + 3600.5, "{frame2}")
    log.end(t + 3700)
    names = sorted(os.listdir(tmp_path))
    assert names == ["2026-10-16_073000_bridge.txt", "2026-10-16_083000_bridge.txt"]
    assert _lines(tmp_path / names[0]) == [f"{t + 0.1:.3f} {{frame}}",
                                           f"{t + 0.25:.3f} # >> MCr"]
    assert _lines(tmp_path / names[1]) == [f"{t + 3600.5:.3f} {{frame2}}"]


def test_short_reconnect_continues_the_file(tmp_path):
    t = _ts("2026-10-16")
    log = RawLog(str(tmp_path))
    log.start(t)
    log.line(t + 1, "{a}")
    log.end(t + 100)
    log.start(t + 100 + rawlog.REJOIN_S - 1)    # WLAN hiccup / watchdog reconnect
    log.line(t + 160, "{b}")
    log.end(t + 200)
    (name,) = os.listdir(tmp_path)
    assert [ln.split(" ", 1)[1] for ln in _lines(tmp_path / name)] == ["{a}", "{b}"]


def test_long_session_is_split(tmp_path):
    t = _ts("2026-10-16")
    log = RawLog(str(tmp_path))
    log.start(t)
    log.line(t + 1, "{a}")
    log.line(t + rawlog.MAX_FILE_S + 1, "{b}")
    log.end(t + rawlog.MAX_FILE_S + 2)
    assert len(os.listdir(tmp_path)) == 2


def test_old_files_are_deleted_when_a_new_one_starts(tmp_path):
    today = datetime(2026, 10, 16).date()
    for back in (1, 6, 7, 9):
        day = (today - timedelta(days=back)).isoformat()
        (tmp_path / f"{day}_071500_bridge.txt").write_text("1.000 {x}\n")
    (tmp_path / "2026-10-01_090000_heatup.txt").write_text("manual capture, kept\n")
    log = RawLog(str(tmp_path), keep_days=7)
    log.start(_ts("2026-10-16"))
    log.end(_ts("2026-10-16", "08:00:00"))
    assert sorted(os.listdir(tmp_path)) == [
        "2026-10-01_090000_heatup.txt", "2026-10-10_071500_bridge.txt",
        "2026-10-15_071500_bridge.txt", "2026-10-16_073000_bridge.txt"]


def test_write_error_disables_instead_of_raising(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    log = RawLog(str(blocker))
    log.start(_ts("2026-10-16"))               # no exception
    log.line(_ts("2026-10-16"), "{frame}")
    log.note("still fine")
    assert log._broken


def test_config_section_from_env():
    cfg = load("/nonexistent", {"MACHINE_HOST": "x", "SHOTS_DIRECTORY": "/tmp/s",
                                "CAPTURE_ENABLED": "true", "CAPTURE_KEEP_DAYS": "3"})
    assert cfg.capture.enabled and cfg.capture.keep_days == 3
    assert cfg.capture.directory == "/data/captures"
    assert not load("/nonexistent", {"MACHINE_HOST": "x"}).capture.enabled


def test_link_records_a_connection(tmp_path):
    fake = FakeModule(mode="display")

    async def run():
        port = await fake.start()
        lk = Link("127.0.0.1", port, on_frame=lambda f, t: None, on_link=lambda *a: None)
        lk.tap = RawLog(str(tmp_path))
        task = asyncio.create_task(lk.run())
        await asyncio.sleep(1.2)
        lk.stop()
        await task
        await fake.close()
        assert lk.tap._fh is None              # file closed with the connection

    asyncio.run(run())
    (path,) = tmp_path.iterdir()
    lines = [ln.split(" ", 1)[1] for ln in _lines(path)]
    assert lines[0].startswith("# connected to 127.0.0.1:")
    assert lines[1] == "# >> MCr"
    assert any(ln.startswith("{") for ln in lines)
    assert lines[-1] == "# connection closed (bridge stopping)"
