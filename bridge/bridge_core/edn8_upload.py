"""Send a patched ROM to the EverDrive N8 Pro's SD card and start it, with krikzz's edlink-n8.

Used by the ROM setup screen's "Send patched ROM to EDN8" button, the bridge's one way of sending a
ROM. edlink-n8 opens the cart's COM port itself, so nothing else may hold the port while it runs (no
session is running on the ROM setup screen).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Where to copy the patched ROM on the EDN8 SD card (auto-created by edlink-n8)
EDN8_TARGET_DIR = "sd:\\z1rr-coop\\"
MKDIR_TIMEOUT_S, COPY_TIMEOUT_S, LAUNCH_TIMEOUT_S = 15, 30, 30


@dataclass(frozen=True)
class UploadResult:
    level: str          # "ok", "warning" (copied but not started) or "error"
    message: str

    @property
    def ok(self) -> bool:
        return self.level == "ok"

    @property
    def color(self) -> str:
        return {"ok": "green", "warning": "orange"}.get(self.level, "red")


def find_edlink() -> Path | None:
    """Find edlink-n8.exe: PyInstaller bundle, source bridge/tools/, then PATH."""
    # PyInstaller bundle: datas extract under sys._MEIPASS
    if hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "tools" / "edlink-n8.exe"
        if bundled.is_file():
            return bundled
    # Source layout: bridge/tools/edlink-n8.exe
    bundled = Path(__file__).parent.parent / "tools" / "edlink-n8.exe"
    if bundled.is_file():
        return bundled
    # System PATH
    which = shutil.which("edlink-n8.exe") or shutil.which("edlink-n8")
    return Path(which) if which else None


def _output(result: subprocess.CompletedProcess) -> str:
    return (result.stderr or result.stdout or "(no output)").strip()[:300]


def upload_and_launch(file_path: Path, edlink: Path | None = None) -> UploadResult:
    """Copy `file_path` to EDN8_TARGET_DIR on the SD card (creating it), then start it on the cart."""
    edlink = edlink or find_edlink()
    if edlink is None:
        return UploadResult("warning", "(edlink-n8.exe not found; skipping upload)")
    # edlink target dir without the trailing slash for -mkdir
    target_dir_arg = EDN8_TARGET_DIR.rstrip("\\")
    try:
        mkdir_result = subprocess.run(
            [str(edlink), "-mkdir", target_dir_arg],
            capture_output=True, text=True, timeout=MKDIR_TIMEOUT_S,
        )
        if mkdir_result.returncode != 0:
            return UploadResult("error", f"Could not create {target_dir_arg} (rc={mkdir_result.returncode}): "
                                         f"{_output(mkdir_result)}")
        cp_result = subprocess.run(
            [str(edlink), "-cp", str(file_path), EDN8_TARGET_DIR],
            capture_output=True, text=True, timeout=COPY_TIMEOUT_S,
        )
        if cp_result.returncode != 0:
            return UploadResult("error", f"Upload failed (rc={cp_result.returncode}): {_output(cp_result)}")
        # Auto-launch the ROM on the cart so the user doesn't have to navigate
        # the EDN8 menu manually. Passing the .nes path as the command itself
        # triggers edlink's cmd_loadApp -> usb.appStart() path which uploads
        # to usb-games and boots the ROM. Yes, this is a second upload (the
        # -cp above keeps a permanent copy in z1rr-coop/), but the ROM is
        # ~128 KB so the extra USB time is negligible compared to the UX win.
        launch_result = subprocess.run(
            [str(edlink), str(file_path)],
            capture_output=True, text=True, timeout=LAUNCH_TIMEOUT_S,
        )
        if launch_result.returncode != 0:
            return UploadResult("warning", f"Uploaded to {EDN8_TARGET_DIR}{file_path.name} but auto-launch "
                                           f"failed (rc={launch_result.returncode}): {_output(launch_result)}. "
                                           f"Load it manually from your cart's menu.")
        return UploadResult("ok", f"✓ Uploaded to {EDN8_TARGET_DIR}{file_path.name} and launched on cart.")
    except Exception as e:
        return UploadResult("error", f"Upload error: {e}")
