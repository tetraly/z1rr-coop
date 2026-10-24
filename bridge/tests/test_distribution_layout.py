from pathlib import Path


REPO_ROOT = Path(__file__).parents[2]


def test_fceux_builder_writes_to_emu_dist_with_fceux_artifact_name():
    script = (REPO_ROOT / "build-fceux.ps1").read_text(encoding="utf-8")

    assert "dist\\emu" in script
    assert '".staging"' in script
    assert "z1rr-coop-$version-fceux-win32" in script
    assert "z1rr-coop-$version-fceux-win64" in script
    assert "dist\\fceux" not in script


def test_hardware_builder_writes_to_hardware_dist_with_hardware_artifact_name():
    script = (REPO_ROOT / "bridge" / "build-hardware.ps1").read_text(encoding="utf-8")

    assert "dist\\hardware" in script
    assert "z1rr-coop-$version-hardware.exe" in script
    assert "dist\\edn8" not in script


def test_build_all_uses_generic_distribution_targets():
    script = (REPO_ROOT / "build-all.ps1").read_text(encoding="utf-8")

    assert "dist/hardware/z1rr-coop-<version>-hardware.exe" in script
    assert "dist/emu/z1rr-coop-<version>-fceux-win32.zip" in script
    assert "dist/emu/z1rr-coop-<version>-fceux-win64.zip" in script
    assert 'label = "Hardware"' in script
    assert 'script = "bridge\\build-hardware.ps1"' in script
    assert "dist/edn8" not in script
    assert "dist/fceux" not in script


def test_legacy_edn8_builder_delegates_to_hardware_builder():
    script = (REPO_ROOT / "bridge" / "build-edn8.ps1").read_text(encoding="utf-8")

    assert "build-hardware.ps1" in script


def test_fceux_lua_modes_are_z1_only():
    mode_names = sorted(path.name for path in (REPO_ROOT / "modes").glob("*.lua"))

    assert mode_names == [
        "index.lua",
        "tloz_all.lua",
        "tloz_basic.lua",
        "tloz_progress.lua",
    ]
    index = (REPO_ROOT / "modes" / "index.lua").read_text(encoding="utf-8")
    assert "modes.tloz_all" in index
    assert "modes.tloz_progress" in index
    assert "modes.tloz_basic" in index
    assert "modes.lttp" not in index
    assert "modes.super_metroid" not in index


def test_bridge_spec_bundles_every_patch_file():
    """The exe reads every shipped patch, beta 8's included (patch_revisions.py), on ROM select."""
    bridge = Path(__file__).resolve().parents[1]
    spec = (bridge / "bridge.spec").read_text(encoding="utf-8")
    patches = sorted(p.name for p in (bridge / "bridge_core" / "patches").iterdir() if p.suffix in (".ips", ".json"))
    assert len(patches) == 4
    for name in patches:
        assert f"'bridge_core/patches/{name}', 'bridge_core/patches'" in spec, name
