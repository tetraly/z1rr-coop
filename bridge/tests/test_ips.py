import io
import struct
from pathlib import Path

import pytest

from bridge_core import ips


def make_ips(records: list[tuple[int, bytes]]) -> bytes:
    """Build a minimal IPS file from (offset, data) records."""
    buf = b"PATCH"
    for offset, data in records:
        buf += offset.to_bytes(3, "big")
        buf += len(data).to_bytes(2, "big")
        buf += data
    buf += b"EOF"
    return buf


def test_parse_simple_patch():
    raw = make_ips([(0x0010, b"\xab\xcd"), (0x0100, b"\xff")])
    records = list(ips.parse(io.BytesIO(raw)))
    assert records == [(0x0010, b"\xab\xcd"), (0x0100, b"\xff")]


def test_apply_simple_patch():
    src = bytearray(b"\x00" * 256)
    raw = make_ips([(0x0010, b"\xab\xcd"), (0x0100, b"\xff")])
    out = ips.apply(bytes(src), raw)
    assert out[0x0010:0x0012] == b"\xab\xcd"
    assert out[0x0100] == 0xFF
    assert out[0x0000] == 0x00  # unchanged


def test_apply_extends_rom_if_needed():
    src = bytearray(b"\x00" * 256)
    raw = make_ips([(0x0300, b"\x42")])  # past end of src
    out = ips.apply(bytes(src), raw)
    assert len(out) >= 0x0301
    assert out[0x0300] == 0x42


def test_invalid_header_raises():
    with pytest.raises(ips.IPSError):
        ips.apply(b"\x00" * 16, b"NOPATCH...")


def test_zelda_cc_patch_loads():
    """The vendored Z1 CC patch should be parseable."""
    patch_path = Path(__file__).parent.parent / "bridge_core" / "patches" / "zelda_z1rr_coop.ips"
    raw = patch_path.read_bytes()
    records = list(ips.parse(io.BytesIO(raw)))
    assert len(records) > 0


def test_zelda_patch_rebrands_title_to_z1rrcoop():
    patch_path = Path(__file__).parent.parent / "bridge_core" / "patches" / "zelda_z1rr_coop.ips"
    records = dict(ips.parse(io.BytesIO(patch_path.read_bytes())))

    assert records[0x01AAF8] == bytes([0x23, 0x01, 0x1B, 0x1B, 0x0C, 0x18, 0x18, 0x19])


def test_is_patched_detects_applied():
    """If an IPS has been applied, is_patched should return True."""
    src = bytearray(b"\x00" * 1024)
    raw = make_ips([(0x0100, b"\xab\xcd")])
    patched = ips.apply(bytes(src), raw)
    assert ips.is_patched(patched, raw) is True
    assert ips.is_patched(bytes(src), raw) is False


def test_apply_validated_passes_when_input_matches_expected():
    src = bytearray(b"\x00" * 1024)
    src[0x0100:0x0103] = b"\xde\xad\xbe"
    raw = make_ips([(0x0100, b"\xab\xcd\xef")])
    expected = {0x0100: b"\xde\xad\xbe"}
    out = ips.apply_validated(bytes(src), raw, expected)
    assert out[0x0100:0x0103] == b"\xab\xcd\xef"


def test_apply_validated_rejects_modified_input():
    src = bytearray(b"\x00" * 1024)
    src[0x0100:0x0103] = b"\xde\xad\xbe"
    src[0x0200:0x0202] = b"\x11\x22"
    raw = make_ips([(0x0100, b"\xab\xcd\xef"), (0x0200, b"\x33\x44")])
    # Pretend vanilla had different bytes at 0x0100 — input has been modified
    expected = {0x0100: b"\xff\xff\xff", 0x0200: b"\x11\x22"}
    with pytest.raises(ips.RomConflict) as excinfo:
        ips.apply_validated(bytes(src), raw, expected)
    assert len(excinfo.value.mismatches) == 1
    assert excinfo.value.mismatches[0][0] == 0x0100


def test_apply_validated_rejects_input_with_multiple_mismatches():
    src = bytearray(b"\x00" * 1024)
    raw = make_ips([(0x10, b"\xaa"), (0x20, b"\xbb"), (0x30, b"\xcc")])
    expected = {0x10: b"\x99", 0x20: b"\x99", 0x30: b"\x00"}  # 0x30 matches src
    with pytest.raises(ips.RomConflict) as excinfo:
        ips.apply_validated(bytes(src), raw, expected)
    bad_offsets = sorted(o for o, _, _ in excinfo.value.mismatches)
    assert bad_offsets == [0x10, 0x20]


def test_load_expected_manifest_decodes_offsets_and_hex():
    payload = b'{"expected": {"0x010010": "abcdef", "0x000020": "ff"}}'
    out = ips.load_expected_manifest(payload)
    assert out == {0x10010: b"\xab\xcd\xef", 0x20: b"\xff"}


def test_real_manifest_validates_against_vanilla_prg0_and_prg1():
    """The shipped manifest should accept both PRG0 and PRG1 vanillas."""
    from .vanilla_roms import vanilla
    prg0 = vanilla("prg0")
    prg1 = vanilla("prg1")
    patches = Path(__file__).parent.parent / "bridge_core" / "patches"
    patch_bytes = (patches / "zelda_z1rr_coop.ips").read_bytes()
    expected = ips.load_expected_manifest(
        (patches / "zelda_z1rr_coop.expected.json").read_bytes()
    )
    out0 = ips.apply_validated(prg0, patch_bytes, expected)
    out1 = ips.apply_validated(prg1, patch_bytes, expected)
    assert len(out0) == 131088
    assert len(out1) == 131088
