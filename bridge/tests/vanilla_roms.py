"""Where the tests find vanilla Legend of Zelda ROMs. A ROM is recognized by its SHA-1, so any file
name works:

- Z1RR_VANILLA_PRG0 / Z1RR_VANILLA_PRG1: one file each;
- Z1RR_ROM_DIR: a folder searched for both;
- then the paths on the maintainer's machine.

A test that needs a ROM none of them gives is skipped."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from extend_patch import VANILLA_SHA1S

MAINTAINER_PATHS = {
    "prg0": (r"N:\Games\ROMs\NES\Legend of Zelda, The (U) (PRG0) [!].nes",
             "D:/Downloads/Games/ROMs/Z1R/Legend of Zelda, The (USA).nes"),
    "prg1": (r"N:\Games\ROMs\NES\Legend of Zelda, The (U) (PRG1) [!].nes",),
}


def _variable(release: str) -> str:
    return f"Z1RR_VANILLA_{release.upper()}"


def _is_release(path: Path, release: str) -> bool:
    return path.is_file() and hashlib.sha1(path.read_bytes()).hexdigest() == VANILLA_SHA1S[release]


def find(release: str) -> Path | None:
    """The vanilla ROM of `release` ("prg0" or "prg1"), or None."""
    named = os.environ.get(_variable(release))
    if named:
        assert _is_release(Path(named), release), f"{_variable(release)}={named} is not vanilla {release.upper()}"
        return Path(named)
    folder = os.environ.get("Z1RR_ROM_DIR")
    candidates = [*sorted(Path(folder).glob("*.nes"))] if folder else []
    candidates += [Path(path) for path in MAINTAINER_PATHS[release]]
    return next((path for path in candidates if _is_release(path, release)), None)


def vanilla(release: str) -> bytes:
    """The vanilla ROM's bytes; skips the test without one."""
    path = find(release)
    if path is None:
        pytest.skip(f"no vanilla {release.upper()} ROM (set {_variable(release)} or Z1RR_ROM_DIR)")
    return path.read_bytes()
