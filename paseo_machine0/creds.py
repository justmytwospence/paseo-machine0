"""Credential pushes: the hub builds a bundle, `spoke apply-creds` writes it.

The bundle is {"secrets": {...}, "brokered": {provider: credential}}. Spokes
never connect to the hub: hubd pushes after `new` and `wake`, whenever the
broker's expiry for a provider changes, and at least every push_interval_hours.

On the receiving machine:
  ~/.config/paseo-machine0/secrets.env          spoke only; Paseo restarts when it changes
  ~/.config/paseo-machine0/brokered-auth.json   read by pi-brokered-auth on every refresh
  ~/.pi/agent/auth.json                         an entry per brokered provider (refresh = sentinel)
  ~/.codex/auth.json                            Codex, from the openai-codex credential
"""

from __future__ import annotations

import datetime
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from . import config

SENTINEL = "brokered"


def build_bundle(errors: Optional[List[str]] = None) -> Dict[str, Any]:
    """Hub only: the secrets spokes may hold and a fresh credential per brokered provider."""
    from . import broker
    brokered: Dict[str, Any] = {}
    for provider in config.settings()["brokered_providers"]:
        try:
            brokered[provider] = broker.get(provider)
        except broker.BrokerError as e:
            if errors is not None:
                errors.append("%s: %s" % (provider, e))
    return {"secrets": config.spoke_secrets(config.read_secrets()), "brokered": brokered}


def expiries(bundle: Dict[str, Any]) -> Dict[str, Any]:
    return {p: c.get("expires") for p, c in (bundle.get("brokered") or {}).items()}


def push_due(info: Dict[str, Any], bundle: Dict[str, Any], now: float, interval_h: float) -> Tuple[bool, str]:
    """Whether a running spoke needs a push. Pure, for tests."""
    last = info.get("last_push")
    if not last:
        return True, "never pushed"
    if info.get("secrets_digest") != config.digest(bundle.get("secrets") or {}):
        return True, "secrets changed"
    pushed = info.get("pushed") or {}
    for provider, expires in expiries(bundle).items():
        if pushed.get(provider) != expires:
            return True, "%s refreshed" % provider
    if now - float(last) >= interval_h * 3600:
        return True, "interval"
    return False, "current"


# ---- receiving side -----------------------------------------------------------


def _pi_auth_path() -> str:
    agent_dir = os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")
    return os.path.join(agent_dir, "auth.json")


def _codex_dir() -> str:
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def codex_auth(cred: Dict[str, Any], now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Codex reads ~/.codex/auth.json. last_refresh=now keeps it from refreshing
    (which would fail on the sentinel) until the next push."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    access = str(cred.get("access") or "")
    return {
        "OPENAI_API_KEY": None,
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": access,
            "access_token": access,
            "refresh_token": SENTINEL,
            "account_id": cred.get("accountId"),
        },
        "last_refresh": now.isoformat().replace("+00:00", "Z"),
    }


def apply(bundle: Dict[str, Any], write_secrets: bool) -> Dict[str, Any]:
    """Write a bundle on this machine. Returns what changed."""
    changed: Dict[str, Any] = {"secrets": False, "brokered": False, "pi": [], "codex": False}
    if write_secrets:
        changed["secrets"] = config.write_secrets(dict(bundle.get("secrets") or {}))

    brokered = {p: {k: v for k, v in c.items() if k != "refresh"}
                for p, c in (bundle.get("brokered") or {}).items() if isinstance(c, dict) and c.get("access")}
    if not brokered:
        return changed
    current = config.read_json(config.BROKERED_FILE, {}) or {}
    merged = {k: v for k, v in current.items() if not k.startswith("_")}
    merged.update(brokered)
    if merged != {k: v for k, v in current.items() if not k.startswith("_")}:
        merged["_pushed_at"] = int(time.time() * 1000)
        changed["brokered"] = config.write_json_private(config.BROKERED_FILE, merged)

    # pi only treats a provider as logged in when auth.json has an entry. Entries
    # with the sentinel are refreshed through pi-brokered-auth; anything else (a
    # real login) would be a second refresher, so it is replaced.
    path = _pi_auth_path()
    store = config.read_json(path, {}) or {}
    dirty = False
    for provider, cred in brokered.items():
        entry = dict(cred, type="oauth", refresh=SENTINEL)
        if store.get(provider) != entry:
            store[provider] = entry
            changed["pi"].append(provider)
            dirty = True
    if dirty:
        config.write_json_private(path, store)

    codex = brokered.get("openai-codex")
    if codex and os.path.isdir(_codex_dir()):
        # Rewritten on every push, so last_refresh never ages into Codex's own refresh.
        changed["codex"] = config.write_json_private(os.path.join(_codex_dir(), "auth.json"), codex_auth(codex))
    return changed
