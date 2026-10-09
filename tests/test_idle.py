import tests  # noqa: F401  first, so HOME is a throwaway dir before anything reads it
import unittest

from paseo_machine0 import idle

NOW = 1_800_000_000.0
DAY = 86400.0


def status(**kw):
    base = {"daemon": "up", "agents": {"busy": 0, "idle": 2}, "schedules_active": 0, "load15": 0.05,
            "permissions": [], "last_activity": None}
    base.update(kw)
    return base


def check(st, keep_awake=False, seen=None):
    return idle.idle_now(st, keep_awake, 0.3, seen or {}, DAY, NOW)


class IdleNowTest(unittest.TestCase):
    def test_idle(self):
        self.assertEqual(check(status()), (True, "idle"))

    def test_keep_awake(self):
        self.assertEqual(check(status(), keep_awake=True), (False, "keep-awake"))

    def test_unknown_status_never_suspends(self):
        self.assertEqual(check(None), (False, "status unknown"))

    def test_running_agent(self):
        self.assertEqual(check(status(agents={"busy": 1})), (False, "1 agent running"))

    def test_schedule(self):
        self.assertEqual(check(status(schedules_active=2)), (False, "2 schedules active"))

    def test_load(self):
        self.assertEqual(check(status(load15=0.9)), (False, "load 0.90"))
        self.assertEqual(check(status(load15=None)), (False, "load unknown"))

    def test_permission_grace(self):
        self.assertEqual(check(status(), seen={"p": NOW - 3600}), (False, "permission pending"))
        self.assertEqual(check(status(), seen={"p": NOW - DAY - 1}), (True, "idle"))

    def test_daemon_down_counts_as_no_agents(self):
        self.assertEqual(check(status(daemon="down", agents={"busy": 0})), (True, "idle"))


class DecideTest(unittest.TestCase):
    def test_not_idle_resets(self):
        self.assertEqual(idle.decide(False, NOW - 9999, None, NOW, 120), (False, None))

    def test_first_idle_poll_starts_the_clock(self):
        self.assertEqual(idle.decide(True, None, None, NOW, 120), (False, NOW))

    def test_suspends_after_idle_minutes(self):
        self.assertEqual(idle.decide(True, NOW - 121 * 60, None, NOW, 120), (True, NOW - 121 * 60))

    def test_recent_activity_extends(self):
        self.assertEqual(idle.decide(True, NOW - 300 * 60, NOW - 60 * 60, NOW, 120), (False, NOW - 300 * 60))


class PermissionsTest(unittest.TestCase):
    def test_track(self):
        seen = idle.track_permissions({}, ["a"], NOW)
        seen = idle.track_permissions(seen, ["a", "b"], NOW + 10)
        self.assertEqual(seen, {"a": NOW, "b": NOW + 10})
        self.assertEqual(idle.track_permissions(seen, ["b"], NOW + 20), {"b": NOW + 10})


if __name__ == "__main__":
    unittest.main()
