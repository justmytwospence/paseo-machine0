import json
import os
import time
import unittest
from unittest import mock

from paseo_machine0 import config, hubd, lifecycle, machine0, registry, spoke

RUNNING = {"name": "paseo-demo", "status": "RUNNING", "ip": "10.0.0.5", "size": "large"}


class Recorder:
    def __init__(self):
        self.calls = []

    def hook(self, name, result=None):
        def fn(*args, **kw):
            self.calls.append(name)
            return result
        return fn


class WakeTest(unittest.TestCase):
    def setUp(self):
        registry.put("demo", size="large", idle_since=123.0, suspended={"reason": "idle"})

    def tearDown(self):
        registry.drop("demo")

    def test_order_is_start_push_sync_then_ready(self):
        r = Recorder()
        states = iter([{"name": "paseo-demo", "status": "SUSPENDED"}])
        with mock.patch.object(machine0, "get", side_effect=lambda vm: next(states, RUNNING)), \
                mock.patch.object(machine0, "start", r.hook("start")), \
                mock.patch.object(lifecycle, "bring_up", r.hook("bring_up")), \
                mock.patch.object(lifecycle, "push", r.hook("push")), \
                mock.patch.object(lifecycle, "sync", r.hook("sync")):
            out = lifecycle.wake("demo")
        self.assertEqual(r.calls, ["start", "bring_up", "push", "sync"])
        self.assertEqual(out["status"], "RUNNING")
        info = registry.get("demo")
        self.assertIsNone(info["idle_since"])
        self.assertIsNone(info["suspended"])

    def test_unknown_spoke(self):
        with self.assertRaises(ValueError):
            lifecycle.wake("nope")


class NewTest(unittest.TestCase):
    def tearDown(self):
        registry.drop("fresh")

    def test_order_and_pairing(self):
        r = Recorder()
        remotes = []
        with mock.patch.object(machine0, "get", return_value=None), \
                mock.patch.object(machine0, "new", r.hook("machine0.new")), \
                mock.patch.object(lifecycle, "bring_up", r.hook("bring_up")), \
                mock.patch.object(lifecycle, "settle_cloud_init", r.hook("settle_cloud_init")), \
                mock.patch.object(lifecycle, "remote", side_effect=lambda vm, script, **kw: remotes.append(script)), \
                mock.patch.object(lifecycle, "push", r.hook("push")), \
                mock.patch.object(lifecycle, "sync", r.hook("sync")), \
                mock.patch.object(lifecycle, "clone", return_value=["~/Projects/app"]), \
                mock.patch.object(lifecycle, "fetch_pair_url", return_value="https://app.paseo.sh/#offer=x"):
            out = lifecycle.new("fresh", None, ["me/app"])
        self.assertEqual(r.calls, ["machine0.new", "bring_up", "settle_cloud_init", "push", "sync"])
        self.assertEqual(remotes, ["paseo-machine0 spoke init paseo-fresh", "paseo-machine0 spoke restart-paseo"])
        self.assertEqual(out["vm"], "paseo-fresh")
        self.assertEqual(registry.get("fresh")["size"], "large")

    def test_failed_create_forgets_the_spoke(self):
        with mock.patch.object(machine0, "get", return_value=None), \
                mock.patch.object(machine0, "new", side_effect=machine0.Machine0Error("no capacity")):
            with self.assertRaises(machine0.Machine0Error):
                lifecycle.new("fresh", None, [])
        self.assertIsNone(registry.get("fresh"))

    def test_rejects_bad_and_existing_names(self):
        with self.assertRaises(ValueError):
            lifecycle.new("Bad", None, [])
        registry.put("fresh")
        with self.assertRaises(ValueError):
            lifecycle.new("fresh", None, [])


class RmTest(unittest.TestCase):
    def setUp(self):
        registry.put("old")

    def tearDown(self):
        registry.drop("old")

    def test_refuses_unpushed_work(self):
        with mock.patch.object(machine0, "get", return_value=dict(RUNNING, name="paseo-old")), \
                mock.patch.object(lifecycle, "remote_json", return_value=["/home/ubuntu/Projects/app/ dirty "]), \
                mock.patch.object(machine0, "destroy") as destroy:
            with self.assertRaises(RuntimeError):
                lifecycle.rm("old", force=False)
        destroy.assert_not_called()
        self.assertIsNotNone(registry.get("old"))

    def test_clean_spoke_is_archived_then_destroyed(self):
        r = Recorder()
        with mock.patch.object(machine0, "get", return_value=dict(RUNNING, name="paseo-old")), \
                mock.patch.object(lifecycle, "remote_json", return_value=[]), \
                mock.patch.object(lifecycle, "archive", r.hook("archive", "/tmp/a.tgz")), \
                mock.patch.object(machine0, "destroy", r.hook("destroy")):
            out = lifecycle.rm("old", force=False)
        self.assertEqual(r.calls, ["archive", "destroy"])
        self.assertTrue(out["removed"])
        self.assertIsNone(registry.get("old"))

    def test_suspended_needs_force(self):
        with mock.patch.object(machine0, "get", return_value={"name": "paseo-old", "status": "SUSPENDED"}):
            with self.assertRaises(RuntimeError):
                lifecycle.rm("old", force=False)


class ScrubTest(unittest.TestCase):
    def test_scrub_removes_identity_and_credentials(self):
        s = lifecycle.SCRUB_SCRIPT
        self.assertLess(s.index("systemctl --user stop paseo.service"), s.index("daemon-keypair.json"))
        for item in ("daemon-keypair.json", "server-id", "push-tokens.json", "local-credential", "cli-client-id",
                     "~/.paseo/agents", "~/.paseo/projects", "secrets.env", "brokered-auth.json",
                     "~/.pi/agent/auth.json", "~/.codex/auth.json", "~/.claude/.credentials.json",
                     "mcp-auth.json", "hosts.yml", "zsh_history"):
            self.assertIn(item, s)


class HubdTest(unittest.TestCase):
    def setUp(self):
        registry.put("idler", keep_awake=False, idle_since=None, last_push=time.time(), pushed={},
                     secrets_digest=config.digest({}))

    def tearDown(self):
        registry.drop("idler")

    def status(self, **kw):
        base = {"daemon": "up", "agents": {"busy": 0}, "schedules_active": 0, "load15": 0.0,
                "permissions": [], "last_activity": None}
        base.update(kw)
        return base

    def run_check(self, status, now, bundle=None):
        bundle = bundle or {"secrets": {}, "brokered": {}}
        with mock.patch.object(lifecycle, "remote_json", return_value=status), \
                mock.patch.object(lifecycle, "push") as push, \
                mock.patch.object(lifecycle, "suspend") as suspend, \
                mock.patch.object(hubd, "log"):
            hubd.check_spoke("idler", registry.get("idler"), dict(RUNNING, name="paseo-idler"), bundle, now)
        return push, suspend

    def test_idle_spoke_is_suspended_after_idle_minutes(self):
        now = time.time()
        _, suspend = self.run_check(self.status(), now)
        suspend.assert_not_called()
        self.assertEqual(registry.get("idler")["idle_since"], now)
        _, suspend = self.run_check(self.status(), now + 121 * 60)
        suspend.assert_called_once_with("idler", reason="idle")

    def test_busy_spoke_resets(self):
        registry.put("idler", idle_since=time.time() - 9999)
        _, suspend = self.run_check(self.status(agents={"busy": 1}), time.time())
        suspend.assert_not_called()
        self.assertIsNone(registry.get("idler")["idle_since"])
        self.assertEqual(registry.get("idler")["idle_reason"], "1 agent running")

    def test_pushes_when_a_token_was_refreshed(self):
        bundle = {"secrets": {}, "brokered": {"radius": {"access": "a", "expires": 5}}}
        push, _ = self.run_check(self.status(), time.time(), bundle)
        push.assert_called_once()

    def test_unreachable_spoke_is_never_suspended(self):
        registry.put("idler", idle_since=0.0)
        with mock.patch.object(lifecycle, "remote_json", side_effect=RuntimeError("ssh")), \
                mock.patch.object(lifecycle, "suspend") as suspend, \
                mock.patch.object(lifecycle, "push") as push, \
                mock.patch.object(hubd, "log"):
            hubd.check_spoke("idler", registry.get("idler"), dict(RUNNING, name="paseo-idler"),
                             {"secrets": {}, "brokered": {}}, time.time())
        suspend.assert_not_called()
        push.assert_not_called()
        self.assertFalse(registry.load_status()["idler"]["reachable"])

    def test_skips_spokes_mid_operation(self):
        with registry.operation("idler", "wake"):
            with mock.patch.object(lifecycle, "remote_json") as rj:
                hubd.check_spoke("idler", registry.get("idler"), dict(RUNNING, name="paseo-idler"),
                                 {"secrets": {}, "brokered": {}}, time.time())
            rj.assert_not_called()


class SpokeStatusTest(unittest.TestCase):
    def test_records_on_disk(self):
        home = config.PASEO_HOME
        os.makedirs(os.path.join(home, "agents", "-home-ubuntu-app"), exist_ok=True)
        os.makedirs(os.path.join(home, "schedules"), exist_ok=True)
        with open(os.path.join(home, "agents", "-home-ubuntu-app", "a1.json"), "w") as f:
            json.dump({"updatedAt": "2026-10-03T10:00:00.000Z", "lastActivityAt": "2026-10-03T11:00:00Z"}, f)
        with open(os.path.join(home, "agents", "-home-ubuntu-app", "a2.json"), "w") as f:
            json.dump({"updatedAt": "2026-10-01T10:00:00Z"}, f)
        for i, st in enumerate(("active", "paused", "completed", "active")):
            with open(os.path.join(home, "schedules", "s%d.json" % i), "w") as f:
                json.dump({"status": st}, f)
        self.assertEqual(spoke.last_activity(), 1791025200.0)
        self.assertEqual(spoke.active_schedules(), 2)

    def test_session_files_count_as_activity(self):
        # An agent Paseo does not run (started by hand over ssh) only shows in its session store.
        d = os.path.expanduser("~/.claude/projects/-home-ubuntu-app")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "s.jsonl")
        with open(path, "w") as f:
            f.write("{}\n")
        later = 1791025200.0 + 3600
        os.utime(path, (later, later))
        try:
            self.assertEqual(spoke.session_activity(), later)
            self.assertGreaterEqual(spoke.last_activity(), later)
        finally:
            os.unlink(path)

    def test_summarize(self):
        counts = spoke.summarize_agents([{"status": "running"}, {"status": "initializing"}, {"status": "idle"},
                                         {"status": "error"}, {"status": "closed"}])
        self.assertEqual(counts, {"busy": 2, "idle": 1, "error": 1, "total": 5})
        self.assertEqual(spoke.summarize_agents(None)["busy"], 0)


if __name__ == "__main__":
    unittest.main()
