"""Tests run against a throwaway HOME so nothing touches real config, ssh or state."""

import os
import tempfile

_HOME = tempfile.mkdtemp(prefix="paseo-machine0-test-")
os.environ["HOME"] = _HOME
os.environ["PASEO_MACHINE0_CONFIG_DIR"] = os.path.join(_HOME, ".config", "paseo-machine0")
os.environ["PASEO_MACHINE0_STATE_DIR"] = os.path.join(_HOME, ".local", "state", "paseo-machine0")
os.environ["PASEO_MACHINE0_SSH_CONFIG"] = os.path.join(_HOME, ".ssh", "config.d", "paseo-machine0")
os.environ["PASEO_HOME"] = os.path.join(_HOME, ".paseo")
os.environ["PASEO_MACHINE0_ROLE"] = "hub"
for key in ("PI_CODING_AGENT_DIR", "CODEX_HOME", "PI_BROKERED_AUTH_FILE"):
    os.environ.pop(key, None)
