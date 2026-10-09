"""Tests run against a throwaway HOME so nothing touches real config, ssh or state.

Every test module imports this package first, so the sandbox holds however the
tests are run (discover with or without -t, a single module, pytest). Run from
anywhere but the repo root, `import tests` fails before anything is written.
"""

import os
import sys
import tempfile

if any(m == "paseo_machine0" or m.startswith("paseo_machine0.") for m in sys.modules):
    raise RuntimeError("paseo_machine0 was imported before the test sandbox; its paths point at the real HOME")

_HOME = tempfile.mkdtemp(prefix="paseo-machine0-test-")
os.environ["HOME"] = _HOME
os.environ["PASEO_MACHINE0_CONFIG_DIR"] = os.path.join(_HOME, ".config", "paseo-machine0")
os.environ["PASEO_MACHINE0_STATE_DIR"] = os.path.join(_HOME, ".local", "state", "paseo-machine0")
os.environ["PASEO_MACHINE0_SSH_CONFIG"] = os.path.join(_HOME, ".ssh", "config.d", "paseo-machine0")
os.environ["PASEO_HOME"] = os.path.join(_HOME, ".paseo")
os.environ["PASEO_MACHINE0_ROLE"] = "hub"
for key in ("PI_CODING_AGENT_DIR", "CODEX_HOME", "PI_BROKERED_AUTH_FILE"):
    os.environ.pop(key, None)
