"""ssh aliases for spokes: `paseo-<name>` in ~/.ssh/config.d/paseo-machine0.

A spoke's IP changes on every resume, and a resumed or cloned VM may present new
host keys, so each alias pins its host key (HostKeyAlias) in a private
known_hosts that is reset whenever the IP changes: trust on first use after
each create or resume. The hub reaches spokes only through these aliases;
spokes never reach the hub.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Dict

from . import config

BEGIN = "# >>> paseo-machine0 %s"
END = "# <<< paseo-machine0 %s"
INCLUDE = "Include ~/.ssh/config.d/*"


def block(vm: str, ip: str, user: str) -> str:
    return "\n".join([
        BEGIN % vm,
        "Host %s" % vm,
        "  HostName %s" % ip,
        "  User %s" % user,
        "  HostKeyAlias %s" % vm,
        "  UserKnownHostsFile %s" % config.KNOWN_HOSTS,
        "  StrictHostKeyChecking accept-new",
        "  IdentitiesOnly yes",
        "  IdentityFile ~/.ssh/id_ed25519",
        "  ServerAliveInterval 15",
        "  ServerAliveCountMax 4",
        "  ControlMaster no",
        "  ControlPath none",
        "  ForwardAgent no",
        END % vm,
        "",
    ])


def parse(text: str) -> Dict[str, str]:
    """vm -> ip of every managed block."""
    out: Dict[str, str] = {}
    for m in re.finditer(r"# >>> paseo-machine0 (\S+)\n.*?HostName (\S+)", text, re.S):
        out[m.group(1)] = m.group(2)
    return out


def without(text: str, vm: str) -> str:
    pattern = re.escape(BEGIN % vm) + r".*?" + re.escape(END % vm) + r"\n?"
    return re.sub(pattern, "", text, flags=re.S)


def read() -> str:
    try:
        with open(config.SSH_CONFIG) as f:
            return f.read()
    except OSError:
        return ""


def write(text: str) -> None:
    config.write_private(config.SSH_CONFIG, text)


def forget_host_key(vm: str) -> None:
    if os.path.exists(config.KNOWN_HOSTS):
        subprocess.run(["ssh-keygen", "-R", vm, "-f", config.KNOWN_HOSTS], capture_output=True)


def ensure_include() -> None:
    """~/.ssh/config must include config.d first (ssh uses the first match)."""
    path = os.path.expanduser("~/.ssh/config")
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    if INCLUDE in text:
        return
    if os.path.islink(path):
        raise RuntimeError("~/.ssh/config is a symlink; add `%s` to it by hand" % INCLUDE)
    config.write_private(path, INCLUDE + "\n\n" + text)


def update(vm: str, ip: str, user: str = "") -> bool:
    """Point the alias at ip. True when it changed (and the host key was forgotten)."""
    text = read()
    if parse(text).get(vm) == ip:
        return False
    write(without(text, vm) + block(vm, ip, user or config.settings()["spoke_user"]))
    forget_host_key(vm)
    return True


def remove(vm: str) -> None:
    write(without(read(), vm))
    forget_host_key(vm)
