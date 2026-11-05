# Shared OS-portability helpers for the GFI gates.
#
# Nothing here may assume Linux paths, /tmp/opencode, bash, or a particular
# host OS: the gates must run unchanged for anyone cloning this repo on
# Windows, macOS, or Linux (Docker Engine or Docker Desktop + uv + cargo).

from __future__ import annotations

import sys
import tempfile
import time
import uuid
from pathlib import Path

_RUN_ID = uuid.uuid4().hex


def config_env(repo: Path) -> dict:
    """The environment a `docker compose` call needs, rendered from the ONE config
    file (A2 §4 A-4).

    Before this, each gate carried its own copy of the stack coordinates, so a port
    moved in `compose/docker-compose.yml` left the gates probing the old one. Now
    `configs/gfti.toml` decides and this helper is the only reader.
    """
    here = str(Path(repo) / "python" / "gfti")
    if here not in sys.path:
        sys.path.insert(0, here)
    # lazy on purpose: keeps gate startup fast and avoids a cycle
    import gfti_config

    return gfti_config.process_env(gfti_config.load())


def scratch_dir() -> Path:
    """A per-RUN scratch directory on ANY host OS (created on demand).

    Replaces the historical hardwired /tmp/opencode/ paths (an editor-tool
    directory that does not exist on other machines). Uses the OS temp
    directory, namespaced to this project AND this process run, so two
    concurrent gate runs can never trade artifacts. Stale run dirs
    (older than one day) are pruned opportunistically.
    """
    base = Path(tempfile.gettempdir()) / "gfti-gates"
    base.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 24 * 3600
    for stale in base.iterdir():
        try:
            if stale.is_dir() and stale.stat().st_mtime < cutoff:
                import shutil

                shutil.rmtree(stale, ignore_errors=True)
        except OSError:  # concurrent prune race; never fatal
            pass
    run_dir = base / _RUN_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


class ReleaseDir:
    """Cargo's target/release output dir, independent of the host OS.

    `cargo build --release` emits `ledger_init` on Unix but
    `ledger_init.exe` on Windows. Keeping the `dir / "name"` call shape
    intact lets every existing gate line resolve the right file itself.
    """

    def __init__(self, path: Path):
        self._path = path

    def __truediv__(self, name: str) -> Path:
        if not self._path.is_dir():
            raise RuntimeError(
                f"Rust release binaries not found at {self._path} — "
                f"run `cargo build --release` in rust/ first"
            )
        native = self._path / name
        if native.exists():
            return native
        if native.with_suffix(".exe").exists():  # Windows build
            return native.with_suffix(".exe")
        raise RuntimeError(
            f"binary {name!r} (or {name}.exe) not found in {self._path} — "
            f"run `cargo build --release` in rust/ first"
        )


def release_dir(repo: Path) -> ReleaseDir:
    return ReleaseDir(repo / "rust" / "target" / "release")
