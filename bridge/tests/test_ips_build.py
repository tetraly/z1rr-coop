"""Round-trip tests: make_ips + apply == identity transformation."""
import pytest

from bridge_core.ips import apply, parse
from bridge_core.ips_build import make_ips
import io


def test_no_changes_produces_minimal_ips():
    orig = b"\x00" * 100
    ips = make_ips(orig, orig)
    assert ips == b"PATCH" + b"EOF"


def test_single_byte_change_roundtrips():
    orig = bytearray(100)
    modified = bytearray(orig)
    modified[42] = 0xAB
    ips = make_ips(bytes(orig), bytes(modified))
    assert apply(bytes(orig), ips) == bytes(modified)


def test_multiple_runs_roundtrip():
    orig = bytearray(b"\x00" * 1000)
    modified = bytearray(orig)
    modified[10:13] = b"\xAA\xBB\xCC"
    modified[100] = 0xFF
    modified[500:550] = b"\xDE\xAD\xBE\xEF" * 12 + b"\xCA\xFE"
    ips = make_ips(bytes(orig), bytes(modified))
    assert apply(bytes(orig), ips) == bytes(modified)


def test_rle_run_roundtrips():
    orig = b"\x00" * 100
    modified = bytearray(orig)
    modified[20:60] = b"\xFF" * 40  # long run of repeated byte → RLE candidate
    ips = make_ips(bytes(orig), bytes(modified))
    assert apply(bytes(orig), ips) == bytes(modified)


def test_extension_beyond_original_is_supported():
    """IPS allows extending the file (records past original length)."""
    orig = b"\x00" * 100
    modified = orig + b"\xAB" * 50
    ips = make_ips(orig, modified)
    out = apply(orig, ips)
    assert out == modified


def test_shrinking_raises():
    """IPS cannot shrink; building one should refuse."""
    with pytest.raises(ValueError, match="at least as long"):
        make_ips(b"\x00" * 100, b"\x00" * 50)


def test_eof_offset_collision_raises():
    """An IPS record cannot start at offset 0x454F46 (== 'EOF')."""
    orig = b"\x00" * 0x500000
    modified = bytearray(orig)
    modified[0x454F46] = 0xAB
    with pytest.raises(ValueError, match="EOF"):
        make_ips(bytes(orig), bytes(modified))


def test_real_zelda_cc_patch_roundtrips():
    """Sanity check: applying our existing zelda_z1rr_coop.ips to the original ROM,
    then rebuilding an IPS from (orig, patched), should produce a patch that
    when applied to the original yields the same patched ROM. We can't assert
    byte-equality of the IPS itself (different valid encodings exist), but we
    can assert the round-trip semantic equivalence."""
    from pathlib import Path

    from .vanilla_roms import vanilla
    orig = vanilla("prg0")
    patch = (Path(__file__).parent.parent / "bridge_core" / "patches" / "zelda_z1rr_coop.ips").read_bytes()
    patched = apply(orig, patch)
    rebuilt_ips = make_ips(orig, patched)
    rebuilt_patched = apply(orig, rebuilt_ips)
    assert rebuilt_patched == patched
