import tests  # noqa: F401  first, so HOME is a throwaway dir before anything reads it
import json
import os
import time
import unittest
from unittest import mock

from paseo_machine0 import broker, config, creds, spoke

NOW = 1_800_000_000.0
FUTURE = int((NOW + 86400) * 1000)


def bundle(**secrets):
    return {
        "secrets": secrets or {"MODEL_API_KEY": "k"},
        "brokered": {
            "openai-codex": {"access": "codex-access", "expires": FUTURE, "accountId": "acct", "type": "oauth"},
            "radius": {"access": "radius-access", "expires": FUTURE, "scope": "s", "type": "oauth"},
        },
    }


class PushDueTest(unittest.TestCase):
    def info(self, b, last=NOW - 60):
        return {"last_push": last, "pushed": creds.expiries(b), "secrets_digest": config.digest(b["secrets"])}

    def test_never_pushed(self):
        self.assertEqual(creds.push_due({}, bundle(), NOW, 6), (True, "never pushed"))

    def test_current(self):
        b = bundle()
        self.assertEqual(creds.push_due(self.info(b), b, NOW, 6), (False, "current"))

    def test_refreshed_token(self):
        b = bundle()
        info = self.info(b)
        b["brokered"]["radius"]["expires"] += 1000
        self.assertEqual(creds.push_due(info, b, NOW, 6), (True, "radius refreshed"))

    def test_secrets_changed(self):
        b = bundle()
        info = self.info(b)
        self.assertEqual(creds.push_due(info, bundle(MODEL_API_KEY="new"), NOW, 6), (True, "secrets changed"))

    def test_interval_covers_a_resumed_spoke(self):
        b = bundle()
        self.assertEqual(creds.push_due(self.info(b, last=NOW - 7 * 3600), b, NOW, 6), (True, "interval"))


class BuildBundleTest(unittest.TestCase):
    def test_only_spoke_secrets_and_no_refresh_tokens(self):
        config.write_secrets({"ANTHROPIC_OAUTH_TOKEN": "a", "CLAUDE_CODE_OAUTH_TOKEN": "a",
                              "MACHINE0_API_TOKEN": "never", "TYPESAFE_API_KEY": "t"})
        fake = {"openai-codex": {"access": "x", "expires": FUTURE}}
        errors = []
        with mock.patch.object(broker, "get", side_effect=lambda p: fake[p] if p in fake else
                               (_ for _ in ()).throw(broker.BrokerError("no login"))):
            b = creds.build_bundle(errors)
        self.assertNotIn("MACHINE0_API_TOKEN", json.dumps(b))
        self.assertEqual(set(b["secrets"]), {"ANTHROPIC_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "TYPESAFE_API_KEY"})
        self.assertEqual(list(b["brokered"]), ["openai-codex"])
        self.assertEqual(errors, ["radius: no login"])

    def test_broker_refuses_refresh_tokens(self):
        with mock.patch.object(broker, "_run", return_value=json.dumps({"access": "a", "refresh": "r"})):
            with self.assertRaises(broker.BrokerError):
                broker.get("radius")


class ApplyTest(unittest.TestCase):
    def setUp(self):
        for path in (config.BROKERED_FILE, os.path.expanduser("~/.pi/agent/auth.json"),
                     os.path.expanduser("~/.codex/auth.json")):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_writes_every_file_with_the_sentinel(self):
        os.makedirs(os.path.expanduser("~/.codex"), exist_ok=True)
        os.makedirs(os.path.expanduser("~/.pi/agent"), exist_ok=True)
        with open(os.path.expanduser("~/.pi/agent/auth.json"), "w") as f:
            json.dump({"openai-codex": {"type": "oauth", "access": "old", "refresh": "REAL", "expires": 1},
                       "anthropic": {"type": "oauth", "refresh": "keep"}}, f)
        b = bundle()
        b["brokered"]["radius"]["refresh"] = "leaked"
        changed = creds.apply(b, write_secrets=False)
        self.assertTrue(changed["brokered"])
        self.assertEqual(sorted(changed["pi"]), ["openai-codex", "radius"])
        self.assertTrue(changed["codex"])
        self.assertFalse(changed["secrets"])

        brokered = config.read_json(config.BROKERED_FILE)
        self.assertEqual(brokered["radius"]["access"], "radius-access")
        self.assertNotIn("refresh", brokered["radius"])
        self.assertIn("_pushed_at", brokered)
        self.assertEqual(os.stat(config.BROKERED_FILE).st_mode & 0o777, 0o600)

        pi = config.read_json(os.path.expanduser("~/.pi/agent/auth.json"))
        self.assertEqual(pi["openai-codex"]["refresh"], creds.SENTINEL)
        self.assertEqual(pi["openai-codex"]["access"], "codex-access")
        self.assertEqual(pi["anthropic"]["refresh"], "keep")

        codex = config.read_json(os.path.expanduser("~/.codex/auth.json"))
        self.assertEqual(codex["tokens"]["refresh_token"], creds.SENTINEL)
        self.assertEqual(codex["tokens"]["account_id"], "acct")
        self.assertNotIn("leaked", json.dumps([brokered, pi, codex]))

    def test_unchanged_push_rewrites_nothing_but_codex(self):
        creds.apply(bundle(), write_secrets=False)
        changed = creds.apply(bundle(), write_secrets=False)
        self.assertFalse(changed["brokered"])
        self.assertEqual(changed["pi"], [])

    def test_secrets_only_when_asked(self):
        config.write_secrets({"MACHINE0_API_TOKEN": "hub"})
        creds.apply(bundle(), write_secrets=False)
        self.assertEqual(config.read_secrets(), {"MACHINE0_API_TOKEN": "hub"})
        creds.apply(bundle(), write_secrets=True)
        self.assertEqual(config.read_secrets(), {"MODEL_API_KEY": "k"})


class ApplyCredsRestartTest(unittest.TestCase):
    def test_restart_now_when_idle_else_deferred(self):
        config.write_secrets({})
        with mock.patch.object(spoke, "busy_agents", return_value=0), \
                mock.patch.object(spoke, "restart_paseo", return_value=True) as restart:
            out = spoke.apply_creds(json.dumps(bundle(MODEL_API_KEY="a")))
        self.assertTrue(out["restarted"])
        restart.assert_called_once()

        with mock.patch.object(spoke, "busy_agents", return_value=2), \
                mock.patch.object(spoke, "restart_paseo") as restart:
            out = spoke.apply_creds(json.dumps(bundle(MODEL_API_KEY="b")))
        restart.assert_not_called()
        self.assertTrue(out["restart_pending"])
        self.assertTrue(os.path.exists(spoke.restart_flag()))
        os.unlink(spoke.restart_flag())

        with mock.patch.object(spoke, "busy_agents") as busy:
            out = spoke.apply_creds(json.dumps(bundle(MODEL_API_KEY="b")))
        busy.assert_not_called()
        self.assertFalse(out["secrets"])


if __name__ == "__main__":
    unittest.main()
