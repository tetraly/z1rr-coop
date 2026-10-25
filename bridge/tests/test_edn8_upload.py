"""edn8_upload: the edlink-n8 calls that copy a patched ROM to the SD card and start it."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bridge_core import edn8_upload

EDLINK = Path("C:/bridge/tools/edlink-n8.exe")
ROM = Path("C:/roms/zelda_emucoop.nes")


@pytest.fixture
def edlink_runs(monkeypatch):
    """edlink-n8's calls, answered with the return codes queued in `codes` (0 when empty)."""
    calls: list[list[str]] = []
    codes: list[int] = []

    def run(args, **_kwargs):
        calls.append(args)
        code = codes.pop(0) if codes else 0
        return subprocess.CompletedProcess(args, code, stdout="", stderr="cart busy" if code else "")

    monkeypatch.setattr(edn8_upload.subprocess, "run", run)
    return calls, codes


def test_copies_then_starts_the_rom(edlink_runs):
    calls, _codes = edlink_runs
    result = edn8_upload.upload_and_launch(ROM, edlink=EDLINK)
    assert result.ok and result.color == "green"
    assert calls == [
        [str(EDLINK), "-mkdir", "sd:\\z1rr-coop"],
        [str(EDLINK), "-cp", str(ROM), edn8_upload.EDN8_TARGET_DIR],
        [str(EDLINK), str(ROM)],
    ]


@pytest.mark.parametrize("failing_call, level, says", [
    (0, "error", "Could not create"),
    (1, "error", "Upload failed"),
    (2, "warning", "auto-launch failed"),
])
def test_a_failed_step_stops_and_says_which(edlink_runs, failing_call, level, says):
    calls, codes = edlink_runs
    codes.extend([0] * failing_call + [1])
    result = edn8_upload.upload_and_launch(ROM, edlink=EDLINK)
    assert (result.level, len(calls)) == (level, failing_call + 1)
    assert says in result.message and "cart busy" in result.message


def test_without_edlink_nothing_runs(edlink_runs, monkeypatch):
    calls, _codes = edlink_runs
    monkeypatch.setattr(edn8_upload, "find_edlink", lambda: None)
    result = edn8_upload.upload_and_launch(ROM)
    assert result.level == "warning" and "not found" in result.message
    assert calls == []


def test_a_timeout_is_reported_not_raised(monkeypatch):
    def run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(edn8_upload.subprocess, "run", run)
    result = edn8_upload.upload_and_launch(ROM, edlink=EDLINK)
    assert result.level == "error" and "Upload error" in result.message


def test_the_bundled_edlink_is_found():
    assert edn8_upload.find_edlink() is not None     # bridge/tools/edlink-n8.exe, in the repository
