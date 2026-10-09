import tests  # noqa: F401  first, so HOME is a throwaway dir before anything reads it
import os
import unittest

from paseo_machine0 import cli, config, registry, sshconf


class ConfigTest(unittest.TestCase):
    def test_env_roundtrip_and_filter(self):
        values = {"ANTHROPIC_OAUTH_TOKEN": "sk-ant-oat01-x'y", "MACHINE0_API_TOKEN": "m0", "MODEL_API_KEY": "k"}
        parsed = config.parse_env_file(config.render_env_file(values))
        self.assertEqual(parsed, values)
        self.assertEqual(config.spoke_secrets(values).keys(), {"ANTHROPIC_OAUTH_TOKEN", "MODEL_API_KEY"})

    def test_private_files(self):
        self.assertTrue(config.write_secrets({"A": "b"}))
        self.assertFalse(config.write_secrets({"A": "b"}))
        self.assertEqual(os.stat(config.SECRETS_FILE).st_mode & 0o777, 0o600)
        self.assertEqual(config.read_secrets(), {"A": "b"})

    def test_names(self):
        self.assertTrue(config.valid_name("demo"))
        self.assertTrue(config.valid_name("my-app2"))
        for bad in ("Demo", "1x", "machine0-spoke-build", "machine0-x", "a" * 40, "a_b"):
            self.assertFalse(config.valid_name(bad), bad)
        self.assertEqual(config.vm_name("demo"), "paseo-demo")

    def test_refresh_margin(self):
        self.assertEqual(config.refresh_margin_ms("radius"), 12 * 3600 * 1000)
        self.assertEqual(config.refresh_margin_ms("openai-codex"), 24 * 3600 * 1000)

    def test_app_url(self):
        self.assertEqual(cli.app_url("https://app.paseo.sh/#offer=abc"), "paseo://pair#offer=abc")


class SshConfTest(unittest.TestCase):
    def test_update_parse_remove(self):
        self.assertTrue(sshconf.update("paseo-demo", "1.2.3.4", "ubuntu"))
        self.assertFalse(sshconf.update("paseo-demo", "1.2.3.4", "ubuntu"))
        sshconf.update("paseo-other", "5.6.7.8", "ubuntu")
        self.assertTrue(sshconf.update("paseo-demo", "9.9.9.9", "ubuntu"))
        text = sshconf.read()
        parsed = sshconf.parse(text)
        self.assertEqual((parsed["paseo-demo"], parsed["paseo-other"]), ("9.9.9.9", "5.6.7.8"))
        self.assertIn("HostKeyAlias paseo-demo", text)
        self.assertIn("ForwardAgent no", text)
        sshconf.remove("paseo-demo")
        parsed = sshconf.parse(sshconf.read())
        self.assertNotIn("paseo-demo", parsed)
        self.assertEqual(parsed["paseo-other"], "5.6.7.8")

    def test_include(self):
        sshconf.ensure_include()
        sshconf.ensure_include()
        with open(os.path.expanduser("~/.ssh/config")) as f:
            text = f.read()
        self.assertTrue(text.startswith(sshconf.INCLUDE))
        self.assertEqual(text.count(sshconf.INCLUDE), 1)


class RegistryTest(unittest.TestCase):
    def test_put_get_drop(self):
        registry.put("alpha", size="large")
        registry.put("alpha", keep_awake=True)
        self.assertEqual(registry.get("alpha")["vm"], "paseo-alpha")
        self.assertTrue(registry.get("alpha")["keep_awake"])
        registry.put_status("alpha", machine="RUNNING")
        registry.drop("alpha")
        self.assertIsNone(registry.get("alpha"))
        self.assertNotIn("alpha", registry.load_status())

    def test_registry_is_private(self):
        registry.put("beta", pair_url="https://app.paseo.sh/#offer=x")
        self.assertEqual(os.stat(config.state_path("registry.json")).st_mode & 0o777, 0o600)
        registry.drop("beta")

    def test_operation_lock(self):
        self.assertIsNone(registry.operation_running("gamma"))
        with registry.operation("gamma", "wake"):
            self.assertIn("wake", registry.operation_running("gamma"))
            with self.assertRaises(registry.Busy):
                with registry.operation("gamma", "suspend"):
                    pass
        self.assertIsNone(registry.operation_running("gamma"))


if __name__ == "__main__":
    unittest.main()
