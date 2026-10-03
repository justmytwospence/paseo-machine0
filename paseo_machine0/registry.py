"""The hub's record of spokes, plus per-spoke operation locks.

registry.json (0600, it holds pairing links):
  spokes: {name: {vm, size, region, created, keep_awake, idle_since,
                  last_push, pushed: {provider: expires}, secrets_digest,
                  pair_url, permissions_seen: {id: first_seen}, suspended}}

status.json: {name: {...latest `spoke status` plus machine0 state, polled_at}}
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from typing import Any, Dict, Iterator, Optional

from . import config


def _path() -> str:
    return config.state_path("registry.json")


def load() -> Dict[str, Any]:
    data = config.read_json(_path(), {}) or {}
    data.setdefault("spokes", {})
    return data


@contextlib.contextmanager
def locked() -> Iterator[Dict[str, Any]]:
    """Read-modify-write the registry under an exclusive lock."""
    lock = open(config.state_path("registry.lock"), "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load()
        yield data
        config.write_private(_path(), json.dumps(data, indent=2, sort_keys=True) + "\n")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def get(name: str) -> Optional[Dict[str, Any]]:
    return load()["spokes"].get(name)


def put(name: str, **fields: Any) -> Dict[str, Any]:
    with locked() as data:
        entry = data["spokes"].setdefault(name, {"created": int(time.time()), "vm": config.vm_name(name)})
        entry.update(fields)
        return dict(entry)


def drop(name: str) -> None:
    with locked() as data:
        data["spokes"].pop(name, None)
    with status_locked() as status:
        status.pop(name, None)


# ---- status cache -------------------------------------------------------------


def _status_path() -> str:
    return config.state_path("status.json")


def load_status() -> Dict[str, Any]:
    return config.read_json(_status_path(), {}) or {}


@contextlib.contextmanager
def status_locked() -> Iterator[Dict[str, Any]]:
    lock = open(config.state_path("status.lock"), "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load_status()
        yield data
        config.write_json_private(_status_path(), data)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def put_status(name: str, **fields: Any) -> None:
    with status_locked() as data:
        data.setdefault(name, {}).update(fields)


# ---- operation locks ----------------------------------------------------------


class Busy(Exception):
    pass


@contextlib.contextmanager
def operation(name: str, what: str) -> Iterator[None]:
    """One lifecycle operation per spoke at a time; hubd skips spokes held here."""
    path = config.state_path("ops", "%s.lock" % name)
    f = open(path, "a+")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.seek(0)
            raise Busy("%s is busy: %s" % (name, f.read().strip() or "another operation"))
        f.seek(0)
        f.truncate()
        f.write("%s (pid %d)" % (what, os.getpid()))
        f.flush()
        yield
    finally:
        try:
            f.seek(0)
            f.truncate()
        except OSError:
            pass
        f.close()


def operation_running(name: str) -> Optional[str]:
    path = config.state_path("ops", "%s.lock" % name)
    try:
        f = open(path, "a+")
    except OSError:
        return None
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.seek(0)
            return f.read().strip() or "busy"
        fcntl.flock(f, fcntl.LOCK_UN)
        return None
    finally:
        f.close()
