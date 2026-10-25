from pathlib import Path


REPO_ROOT = Path(__file__).parents[2]


def test_gui_uses_taller_release_window_defaults():
    source = (REPO_ROOT / "bridge" / "bridge_gui" / "app.py").read_text(encoding="utf-8")

    assert 'WINDOW_GEOMETRY = "760x720"' in source
    assert "WINDOW_MIN_SIZE = (700, 560)" in source


def test_gui_uses_ganon_icon_asset():
    source = (REPO_ROOT / "bridge" / "bridge_gui" / "app.py").read_text(encoding="utf-8")

    assert (REPO_ROOT / "bridge" / "assets" / "ganon_blue.ico").is_file()
    assert 'APP_ICON = Path("assets/ganon_blue.ico")' in source
    assert "self._set_window_icon()" in source


def test_gui_window_title_uses_z1rr_coop_branding():
    source = (REPO_ROOT / "bridge" / "bridge_gui" / "app.py").read_text(encoding="utf-8")

    assert 'APP_TITLE = "Z1RR-coop Bridge"' in source
    assert "self.title(APP_TITLE)" in source
    old_title = 'self.title("emu-' + 'coop bridge")'
    assert old_title not in source


def test_rom_setup_screen_is_scrollable_for_mister_deploy_flow():
    source = (REPO_ROOT / "bridge" / "bridge_gui" / "setup_screen.py").read_text(encoding="utf-8")

    assert "class ROMSetupScreen(ctk.CTkScrollableFrame):" in source


def test_relay_setup_defaults_to_public_hostname():
    source = (REPO_ROOT / "bridge" / "bridge_gui" / "relay_setup_screen.py").read_text(encoding="utf-8")

    assert 'DEFAULT_RELAY = "coop.z1rracing.com"' in source


def test_session_screen_has_diagnostics_tab_with_copy_button():
    source = (REPO_ROOT / "bridge" / "bridge_gui" / "session_screen.py").read_text(encoding="utf-8")

    assert 'self._tabs.add("Diagnostics")' in source
    assert "self._diagnostics_text" in source
    assert 'text="Copy Diagnostics"' in source
    assert "def _copy_diagnostics" in source


def test_mister_setup_runs_helper_health_probe_after_restart():
    source = (REPO_ROOT / "bridge" / "bridge_gui" / "setup_screen.py").read_text(encoding="utf-8")

    assert "probe_mister_helper" in source
    assert "format_mister_health_summary" in source
    assert "if not health.helper_reachable" in source


def test_mister_setup_has_diagnose_and_copy_diagnostics_buttons():
    source = (REPO_ROOT / "bridge" / "bridge_gui" / "setup_screen.py").read_text(encoding="utf-8")

    assert 'text="Diagnose MiSTer"' in source
    assert 'text="Copy Diagnostics"' in source
    assert "def _diagnose_mister" in source
    assert "def _copy_mister_diagnostics" in source
    assert "run_mister_diagnostics" in source
    assert "start_helper_if_needed=True" in source
    assert 'helper.get("log_path")' in source


def test_the_rom_screen_can_send_the_rom_again():
    """One button on the ROM setup screen patches the ROM, sends it to the EverDrive and starts it,
    staying on the screen, so the game can be restarted without restarting the bridge. It is the
    only way to send: no upload checkbox, and Continue only patches. The connection and session
    screens have no such button: during a session it would lose the game's progress."""
    setup = (REPO_ROOT / "bridge" / "bridge_gui" / "setup_screen.py").read_text(encoding="utf-8")
    relay = (REPO_ROOT / "bridge" / "bridge_gui" / "relay_setup_screen.py").read_text(encoding="utf-8")
    session = (REPO_ROOT / "bridge" / "bridge_gui" / "session_screen.py").read_text(encoding="utf-8")

    assert 'text="Send patched ROM to EDN8"' in setup
    assert setup.count("edn8_upload.upload_and_launch(") == 1
    assert "threading.Thread(target=self._send_rom_worker" in setup
    assert "CTkCheckBox" not in setup and "Also upload" not in setup
    assert "self._send_rom_btn.pack_forget()" in setup             # not for MiSTer
    for source in (relay, session):
        assert "Send ROM" not in source and "upload_and_launch" not in source
