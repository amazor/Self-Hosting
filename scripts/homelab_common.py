"""Shared helpers for homelab bootstrap and deploy scripts."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("homelab")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _Formatter(logging.Formatter):
    """Plain for INFO, prefixed for WARNING/ERROR/DEBUG."""

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno >= logging.WARNING:
            return f"[{record.levelname}] {record.getMessage()}"
        if record.levelno <= logging.DEBUG:
            return f"[DEBUG] {record.getMessage()}"
        return record.getMessage()


def setup_logging(*, verbose: bool = False) -> None:
    """Configure console logging with a consistent homelab format."""
    logger = logging.getLogger("homelab")
    if logger.handlers:
        return
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setFormatter(_Formatter())
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(handler)


# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------

_PLACEHOLDER_VALUES = frozenset({"example.com", "0.0.0.0"})
_PLACEHOLDER_SUFFIXES = (".example.com",)


def is_placeholder(val: str | None) -> bool:
    """Return True if *val* is empty or looks like an unfilled placeholder."""
    if not val:
        return True
    if val.startswith("CHANGE_ME"):
        return True
    if val in _PLACEHOLDER_VALUES:
        return True
    return any(val.endswith(s) for s in _PLACEHOLDER_SUFFIXES)


def load_env(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict (comments and blanks are skipped).

    Handles ``KEY=VALUE``, optional surrounding quotes, and empty values.
    Variable expansion is *not* performed.
    """
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        env[key] = value
    return env


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def resolve_config_base(config_root: str, script_dir: Path) -> Path:
    """Resolve CONFIG_ROOT to an absolute path relative to *script_dir*."""
    p = Path(config_root)
    if not p.is_absolute():
        p = script_dir / p
    return p.resolve()


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------


def require_docker() -> None:
    """Exit if Docker Engine or the Compose v2 plugin is unavailable."""
    if not shutil.which("docker"):
        log.error("Docker is not installed. Install Docker Engine first.")
        raise SystemExit(1)

    try:
        subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        log.error(
            "docker compose plugin not found. Install Docker Compose v2 plugin."
        )
        raise SystemExit(1)

    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True)
    except subprocess.CalledProcessError:
        log.error(
            "Cannot connect to the Docker daemon (permission denied on socket).\n"
            "Add your user to the docker group and start a new login session:\n"
            "  sudo usermod -aG docker $USER\n"
            "  # then log out and back in, or run: newgrp docker"
        )
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# System / user helpers
# ---------------------------------------------------------------------------


def get_real_user() -> tuple[str, Path]:
    """Return (username, home_dir) for the real (pre-sudo) user."""
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"
    try:
        result = subprocess.run(
            ["getent", "passwd", user],
            capture_output=True,
            text=True,
            check=True,
        )
        home = result.stdout.strip().split(":")[5]
    except (subprocess.CalledProcessError, IndexError):
        home = os.environ.get("HOME", f"/home/{user}")
    return user, Path(home)


def try_chown(
    path: Path, uid: int, gid: int, *, recursive: bool = False
) -> bool:
    """Attempt ``chown``; return True on success."""
    cmd = ["chown"]
    if recursive:
        cmd.append("-R")
    cmd += [f"{uid}:{gid}", str(path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, PermissionError, FileNotFoundError):
        return False


def try_sudo_chown(
    path: Path, uid: int, gid: int, *, recursive: bool = False
) -> bool:
    """Attempt ``sudo -n chown``; return True on success."""
    cmd = ["sudo", "-n", "chown"]
    if recursive:
        cmd.append("-R")
    cmd += [f"{uid}:{gid}", str(path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, PermissionError, FileNotFoundError):
        return False


def get_user_shell(user: str) -> str:
    """Return the login shell for *user* (e.g. '/bin/bash')."""
    try:
        result = subprocess.run(
            ["getent", "passwd", user],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().split(":")[-1]
    except (subprocess.CalledProcessError, IndexError):
        return "/bin/bash"
