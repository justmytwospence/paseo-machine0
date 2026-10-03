"""Paths, role and settings shared by every command.

The hub and every spoke run the same checkout (dotfiles/plugins/paseo-machine0).
PASEO_MACHINE0_ROLE decides what a command may do: the hub owns the machine0
CLI, the credential broker and every secret; a spoke only runs a Paseo daemon
and its agents, and never connects to the hub.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict

CONFIG_DIR = os.path.expanduser(os.environ.get("PASEO_MACHINE0_CONFIG_DIR", "~/.config/paseo-machine0"))
STATE_DIR = os.path.expanduser(os.environ.get("PASEO_MACHINE0_STATE_DIR", "~/.local/state/paseo-machine0"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
SECRETS_FILE = os.path.join(CONFIG_DIR, "secrets.env")
BROKERED_FILE = os.path.join(CONFIG_DIR, "brokered-auth.json")
KNOWN_HOSTS = os.path.join(CONFIG_DIR, "known_hosts")
BROKER_DIR = os.path.join(STATE_DIR, "broker")
SSH_CONFIG = os.path.expanduser(os.environ.get("PASEO_MACHINE0_SSH_CONFIG", "~/.ssh/config.d/paseo-machine0"))
PASEO_HOME = os.path.expanduser(os.environ.get("PASEO_HOME", "~/.paseo"))

# Every spoke VM is `paseo-<name>`; the VM name is also its ssh alias and hostname.
VM_PREFIX = "paseo-"
BUILDER = "paseo-machine0-spoke-build"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")

DEFAULTS: Dict[str, Any] = {
    "region": "us-west",
    "gpu_region": "us-east",
    "default_size": "large",
    "image": "paseo-machine0-spoke",
    "base_image": "ubuntu-24-04-loaded",
    "profile": "paseo-machine0",
    "ssh_key": "paseo-machine0-hub",
    "spoke_user": "ubuntu",
    "idle_minutes": 120,
    "load_threshold": 0.3,
    "permission_grace_hours": 24,
    "push_interval_hours": 6,
    "check_interval_s": 300,
    "brokered_providers": ["openai-codex", "radius"],
    # The broker refreshes a token once less than this remains, so spokes always
    # hold at least this much. Keep it near half the token's lifetime: Codex
    # tokens last about 10 days, Radius tokens about 24 hours.
    "refresh_margin_h": {"default": 24, "radius": 12},
    "dotfiles_url": "https://github.com/justmytwospence/dotfiles.git",
}

# Only these secrets ever leave the hub.
SPOKE_SECRETS = ("ANTHROPIC_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "MODEL_API_KEY", "TYPESAFE_API_KEY")
SETUP_TOKEN_KEYS = ("ANTHROPIC_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")

# $/hour (https://docs.machine0.io/introduction/pricing), for the Spokes screen.
PRICES = {
    "small": 0.013, "medium": 0.034, "large": 0.052, "xl": 0.104, "xxl": 0.208,
    "large-nvme": 0.061, "xl-nvme": 0.121, "xxl-nvme": 0.243, "xl-premium": 0.236,
    "xxl-premium": 0.473, "xxxl": 0.825, "4xl": 1.980, "5xl": 2.970, "6xl": 3.714,
    "gpu-4000ada-1": 0.836, "gpu-l40s-1": 1.727, "gpu-6000ada-1": 1.727, "gpu-mi300x-1": 2.849,
    "gpu-h100-1": 4.851, "gpu-h200-1": 4.917,
}


def settings() -> Dict[str, Any]:
    merged = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE) as f:
            user = json.load(f)
        if isinstance(user, dict):
            merged.update(user)
    except (OSError, ValueError):
        pass
    return merged


def role() -> str:
    return os.environ.get("PASEO_MACHINE0_ROLE") or "unknown"


def vm_name(name: str) -> str:
    return VM_PREFIX + name


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name)) and vm_name(name) != BUILDER and not name.startswith("machine0-")


def state_path(*parts: str) -> str:
    path = os.path.join(STATE_DIR, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def ssh_base() -> list:
    """Every ssh to a spoke goes through the managed config only."""
    return ["ssh", "-F", SSH_CONFIG]


def refresh_margin_ms(provider: str) -> int:
    value = settings()["refresh_margin_h"]
    if isinstance(value, dict):
        value = value.get(provider, value.get("default", 24))
    return int(float(value) * 3600 * 1000)


# ---- env files ----------------------------------------------------------------


def parse_env_file(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        if not sep or not key.strip().isidentifier():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1].replace("'\"'\"'", "'")
        out[key.strip()] = value
    return out


def render_env_file(values: Dict[str, str]) -> str:
    lines = ["# written by paseo-machine0; never commit or copy this file"]
    for key in sorted(values):
        value = values[key].replace("'", "'\"'\"'")
        lines.append("export %s='%s'" % (key, value))
    return "\n".join(lines) + "\n"


def write_private(path: str, text: str) -> bool:
    """Atomically write a 0600 file. True when the content changed."""
    try:
        with open(path) as f:
            if f.read() == text:
                os.chmod(path, 0o600)
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".paseo-machine0.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    return True


def write_json_private(path: str, data: Any) -> bool:
    return write_private(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_secrets() -> Dict[str, str]:
    try:
        with open(SECRETS_FILE) as f:
            return parse_env_file(f.read())
    except OSError:
        return {}


def write_secrets(values: Dict[str, str]) -> bool:
    return write_private(SECRETS_FILE, render_env_file(values))


def spoke_secrets(values: Dict[str, str]) -> Dict[str, str]:
    return {k: v for k, v in values.items() if k in SPOKE_SECRETS and v}


def digest(values: Dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]
