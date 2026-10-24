"""The EDN8 patch this bridge ships, and ROMs an earlier bridge patched.

A ROM patched by an earlier release (2.0 beta 8: patch revision 0) is re-patched from its
original: that release's manifest holds the bytes each of its patch sites held before patching,
which apply_validated required the ROM to hold, so writing them back gives the ROM as the player
chose it. Cart-side, both revisions answer the same commands; only the patch files differ.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from bridge_core import ips

CURRENT_PATCH = "zelda_z1rr_coop"
# Earlier releases' patches the bridge recognizes, by file stem, with the release that shipped them.
OLDER_PATCHES = {"zelda_z1rr_coop_beta8": "2.0 beta 8"}
PATCHED_SUFFIX = "_emucoop"


def _patch_file(name: str) -> bytes:
    return files("bridge_core").joinpath(f"patches/{name}").read_bytes()


def load_patch(stem: str = CURRENT_PATCH) -> tuple[bytes, dict[int, bytes]]:
    """An IPS and its expected-vanilla manifest."""
    return _patch_file(f"{stem}.ips"), ips.load_expected_manifest(_patch_file(f"{stem}.expected.json"))


def older_patch_of(rom: bytes) -> tuple[str, bytes] | None:
    """(the release, the original ROM) when an earlier release's patch is on `rom`, else None."""
    for stem, release in OLDER_PATCHES.items():
        patch, expected = load_patch(stem)
        if ips.is_patched(rom, patch):
            original = bytearray(rom)
            for offset, vanilla in expected.items():
                original[offset:offset + len(vanilla)] = vanilla
            return release, bytes(original)
    return None


@dataclass(frozen=True)
class PreparedRom:
    rom: bytes                       # with this bridge's patch
    already_patched: bool            # `rom` is the input, unchanged
    replaced_release: str | None     # the earlier release whose patch was replaced, if any


def prepare_rom(rom: bytes) -> PreparedRom:
    """The ROM with this bridge's patch: as it is when already patched, re-patched from its
    original when an earlier release patched it, else patched. Raises ips.RomConflict."""
    patch, expected = load_patch()
    if ips.is_patched(rom, patch):
        return PreparedRom(rom, already_patched=True, replaced_release=None)
    older = older_patch_of(rom)
    source = older[1] if older else rom
    return PreparedRom(ips.apply_validated(source, patch, expected), already_patched=False,
                       replaced_release=older[0] if older else None)


def patched_path(rom_path: Path, replacing_older: bool = False) -> Path:
    """Where the patched ROM goes: <name>_emucoop.nes beside the input. A ROM an earlier bridge
    patched (`replacing_older`) keeps its <name>_emucoop.nes (replaced in place, on disk and on the
    SD card); any other input, whatever its name, is never overwritten."""
    stem = rom_path.stem
    if replacing_older and stem.lower().endswith(PATCHED_SUFFIX):
        return rom_path.with_suffix(".nes")
    return rom_path.with_name(stem + PATCHED_SUFFIX + ".nes")
