"""Hub-side access to subscription credentials (see broker/broker.mjs)."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict

from . import config

PI_PACKAGE = "@earendil-works/pi-coding-agent"
# A private SDK copy for hosts whose pi is a compiled binary with no JavaScript
# inside (exe.dev's exeuntu build); the dotfiles' chezmoi setup installs it on the hub.
SDK_DIR = os.path.expanduser("~/.local/share/paseo-machine0/pi-sdk/node_modules/" + PI_PACKAGE)


class BrokerError(Exception):
    pass


def _is_pi_package(path: str) -> bool:
    try:
        with open(os.path.join(path, "package.json")) as f:
            if json.load(f).get("name") != PI_PACKAGE:
                return False
    except (OSError, ValueError):
        return False
    return os.path.isfile(os.path.join(path, "dist", "index.js"))


def find_pi_package() -> str:
    """An importable pi SDK: the private copy, else the installed pi's package."""
    override = config.settings().get("pi_package_dir")
    if override and _is_pi_package(os.path.expanduser(override)):
        return os.path.expanduser(override)
    if _is_pi_package(SDK_DIR):
        return SDK_DIR
    binary = shutil.which("pi")
    if not binary:
        raise BrokerError("pi is not on PATH and %s is missing" % SDK_DIR)
    candidates = [os.path.realpath(binary)]
    try:
        with open(candidates[0], "rb") as f:
            head = f.read(4096).decode(errors="ignore")
        for m in re.finditer(r'exec\s+"?([^"\s]+)', head):
            candidates.append(os.path.realpath(m.group(1)))
    except OSError:
        pass
    for start in candidates:
        d = os.path.dirname(start)
        for _ in range(8):
            for probe in (d, os.path.join(d, "lib", "node_modules", PI_PACKAGE),
                          os.path.join(d, "node_modules", PI_PACKAGE)):
                if _is_pi_package(probe):
                    return probe
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    raise BrokerError("cannot locate the pi SDK; set pi_package_dir in config.json")


def _run(args: list, timeout: float = 120) -> str:
    os.makedirs(config.BROKER_DIR, mode=0o700, exist_ok=True)
    lock = open(os.path.join(config.BROKER_DIR, ".lock"), "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        proc = subprocess.run(
            ["node", os.path.join(config.ROOT, "broker", "broker.mjs"), find_pi_package(), config.BROKER_DIR] + args,
            capture_output=True, text=True, timeout=timeout,
        )
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    if proc.returncode != 0:
        raise BrokerError(proc.stderr.strip() or "broker failed")
    return proc.stdout


def get(provider: str) -> Dict[str, Any]:
    """A credential with at least the provider's refresh margin left, without its refresh token."""
    if config.role() != "hub":
        raise BrokerError("the broker only runs on the hub")
    cred = json.loads(_run(["get", provider, str(config.refresh_margin_ms(provider))]))
    if "refresh" in cred:
        raise BrokerError("broker returned a refresh token; refusing")
    return cred


def status() -> Dict[str, Any]:
    store = config.read_json(os.path.join(config.BROKER_DIR, "auth.json"), {}) or {}
    now = time.time() * 1000
    out: Dict[str, Any] = {}
    for k, v in store.items():
        if not isinstance(v, dict):
            continue
        hours = round((v.get("expires", now) - now) / 3.6e6, 1) if v.get("type") == "oauth" else None
        out[k] = {"type": v.get("type"), "hours_left": hours}
    return out
