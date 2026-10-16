import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fake_module import FakeModule, main_screen  # noqa: E402

from leva2mqtt import link as link_mod
from leva2mqtt.display import parse_display
from leva2mqtt.link import Link
from leva2mqtt.modes import ModeController

# real lines from the captures (heatup, heatup3, heatup-cold)
REAL = {
    "main": "<102GEN.LEVER       1: 86.6ß \x04 90.0ß\x03             9% 3.3b-  0s   0ml>",
    "eco": "<002GEN.LEVER       1: 90.1ß     ECO\x01             3% 0.8b-  0s   0ml>",
    "standby": "<002                                    STANDBY                     >",
    "ready": "<102GEN.LEVER       1: 89.9ß   READY\x02             6% 0.1b-  0s   0ml>",
    "full": "<102GEN.LEVER       1: 35.6ß \x04 90.0ß" + "\x03" * 12 + "100% 0.0b-  0s   0ml>",
    "menu": "<121MENU             Dose...        \x04Setup...      \x05 Barista light \x05>",
    "click": "<132GEN.LEVER       1: 90.3ß \x04 90.0ß\x03            10% 1.7b-  0s   0ml>",
}


def test_display_main_screen():
    d = parse_display(REAL["main"])
    assert d.main and d.display_on and not d.input_event
    assert (d.profile, d.temperature, d.setpoint, d.heater, d.pressure, d.volume) == \
        ("GEN.LEVER", 86.6, 90.0, 9, 3.3, 0)
    assert d.alerts == () and not d.shot and not d.eco and not d.ready


def test_display_states():
    assert parse_display(REAL["eco"]).eco and parse_display(REAL["eco"]).setpoint is None
    assert parse_display(REAL["standby"]).standby
    r = parse_display(REAL["ready"])
    assert r.ready and r.temperature == 89.9
    assert parse_display(REAL["full"]).heater == 100
    m = parse_display(REAL["menu"])
    assert not m.main and m.input_event and m.alerts == ()
    c = parse_display(REAL["click"])
    assert c.main and c.input_event and c.display_on


def test_display_alert_and_shot_screen():
    d = parse_display(main_screen("BACKFL"))
    assert d.alerts == ("BACKFL",) and d.temperature == 90.1
    assert parse_display(main_screen("ERR:PT1")).alerts == ("ERR:PT1",)
    s = parse_display("<102 12s 24.7g    1: 88.8ß  90.0ßR=3.4 7.3 7.3b 24.7g    >")
    assert s.shot and s.profile is None


# --- controller ------------------------------------------------------------

def poll(mc, t, screens):
    """Run one poll at time t with the given display lines (0.1 s apart)."""
    assert mc.tick(t, idle=True) == "display"
    for i, line in enumerate(screens):
        mc.display(parse_display(line), t + 0.3 + i * 0.1)
    return mc.tick(t + 0.3 + len(screens) * 0.1, idle=True)


def test_poll_schedule_and_alerts():
    mc = ModeController(interval=300, first_delay=30)
    mc.link_up(0)
    assert mc.tick(10, idle=True) is None              # first poll after 30 s
    assert mc.tick(40, idle=False) is None             # not while busy
    assert poll(mc, 40, [main_screen()] * 12) == "mcr"
    assert mc.alerts == () and mc.take_changed()
    assert mc.tick(100, idle=True) is None             # next one after 300 s
    assert poll(mc, 340, [main_screen("BACKFL"), main_screen()] * 6) == "mcr"
    assert mc.alerts == ("BACKFL",)
    assert poll(mc, 640, [main_screen()] * 12) == "mcr"
    assert mc.alerts == ("BACKFL",)                    # one miss: kept (blinking)
    assert poll(mc, 940, [main_screen()] * 12) == "mcr"
    assert mc.alerts == ()


def test_poll_ends_on_shot_screen_and_timeout():
    mc = ModeController(first_delay=0)
    mc.link_up(0)
    assert mc.tick(0, idle=True) == "display"
    assert mc.display(parse_display("<102 12s 24.7g    1: 88.8ß  90.0ß                   >"), 0.3) == "mcr"
    assert not mc.polling and mc.alerts is None
    mc.next_poll = 10
    assert mc.tick(10, idle=True) == "display"
    assert mc.tick(13.1, idle=True) == "mcr"          # no display frames within 3 s


def test_other_client_phase():
    mc = ModeController(first_delay=0, quiet=180)
    mc.link_up(0)
    assert mc.foreign("XML=", 5) and mc.other_client and mc.take_changed()
    assert not mc.foreign("setup dump", 6)
    assert mc.tick(100, idle=True) is None             # no polls while it is active
    mc.display(parse_display(REAL["click"]), 150)      # input on the display = activity
    assert mc.tick(300, idle=True) is None
    assert mc.tick(331, idle=True) == "mcr" and not mc.other_client


def test_alerts_read_while_other_client_is_active():
    mc = ModeController(first_delay=0)
    mc.link_up(0)
    mc.foreign("display frames", 0)
    for i in range(50):
        mc.display(parse_display(main_screen("DSCALE")), i * 0.1)
    mc.tick(10.1, idle=True)
    assert mc.alerts == ("DSCALE",)


def test_foreign_before_first_frame_survives_link_up():
    # capture 16.10.2026: the app's XML= reply and setup dump came before the first
    # frame; link_up() must not reset that, or the wake report sends MCr over the app
    mc = ModeController()
    mc.foreign("identify reply (XML=)", 0.1)
    mc.link_up(3.9)
    assert mc.other_client
    mc.link_down()
    mc.link_up(10.0)
    assert not mc.other_client


def test_foreign_cancels_poll():
    mc = ModeController(first_delay=0)
    mc.link_up(0)
    assert mc.tick(0, idle=True) == "display"
    mc.foreign("XML=", 0.2)
    assert mc.other_client and not mc.polling


# --- link against a fake module --------------------------------------------

async def _run_link(fake, scenario, foreign=True):
    port = await fake.start()
    events = {"frames": 0, "displays": 0, "foreign": []}
    lk = Link("127.0.0.1", port,
              on_frame=lambda f, t: events.__setitem__("frames", events["frames"] + 1),
              on_link=lambda *a: None,
              on_display=lambda d, t: events.__setitem__("displays", events["displays"] + 1),
              on_foreign=(lambda r, t: (events["foreign"].append(r),
                                        setattr(lk, "hold", True))) if foreign else None)
    task = asyncio.create_task(lk.run())
    try:
        await scenario(lk, events)
    finally:
        lk.stop()
        await task
        await fake.close()
    return events


def test_link_switches_and_detects_other_client():
    fake = FakeModule(mode="display")

    async def scenario(lk, ev):
        await asyncio.sleep(1.0)
        assert fake.received[0] == "MCr" and fake.mode == "mcr" and ev["frames"] > 0
        await lk.request("display")
        await asyncio.sleep(0.5)
        assert fake.mode == "display" and ev["displays"] > 0 and not ev["foreign"]
        await lk.request("mcr")
        await asyncio.sleep(2.0)
        assert not ev["foreign"]                       # own switch is not foreign
        fake.switch_by_other_client()
        await asyncio.sleep(2.0)
        assert ev["foreign"][0] == "identify reply (XML=)"
        assert fake.mode == "display"                 # the link did not switch back
        assert fake.received.count("MCr") == 2

    asyncio.run(_run_link(fake, scenario))


def test_link_display_frames_without_handler_resend_mcr(monkeypatch):
    monkeypatch.setattr(link_mod, "SWITCH_GRACE_S", 0.3)
    fake = FakeModule(mode="mcr")

    async def scenario(lk, ev):
        await asyncio.sleep(0.8)
        fake.mode = "display"                          # switched by someone else
        await asyncio.sleep(1.0)
        assert fake.mode == "mcr" and fake.received.count("MCr") >= 2

    asyncio.run(_run_link(fake, scenario, foreign=False))
