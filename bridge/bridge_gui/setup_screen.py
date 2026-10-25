"""ROM Setup screen: pick a ROM, apply z1rr-coop patch, optionally upload to EDN8."""
from __future__ import annotations

import json
import queue
import threading
from importlib.resources import files
from pathlib import Path
from tkinter import filedialog
from typing import Any

import customtkinter as ctk

from bridge_core import edn8_upload, ips, patch_revisions
from bridge_core.mister_diagnostics import MisterDiagnosticReport, run_mister_diagnostics
from bridge_core.mister_health import format_mister_health_summary, probe_mister_helper
from bridge_core.mister_deploy import DeployAsset, MisterDeployService, MisterSshConfig
from bridge_core.modes import tloz_progress
from bridge_core.user_settings import UserSettings
from bridge_gui.device_flow import (
    DEVICE_EDN8,
    DEVICE_MISTER,
    build_mister_rom_setup_config,
)




SEND_POLL_MS = 100


class ROMSetupScreen(ctk.CTkScrollableFrame):
    def __init__(self, master, controller) -> None:
        super().__init__(master)
        self.controller = controller
        self._rom_path: Path | None = None
        self._patched_path: Path | None = None
        self._active_endpoint: str | None = None
        self._settings = UserSettings.load()
        self._deploy_running = False
        self._last_mister_diagnostics = ""

        self._title_label = ctk.CTkLabel(
            self,
            text="ROM Setup",
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        self._title_label.pack(pady=(20, 10))

        self._intro_label = ctk.CTkLabel(
            self,
            text="Choose your Z1 ROM (vanilla or Z1R seed).\n"
                 "We'll apply the z1rr-coop patch if it's not already patched.",
            justify="center",
        )
        self._intro_label.pack(pady=(0, 20))

        self._file_label = ctk.CTkLabel(self, text="(no file selected)", wraplength=500)
        self._file_label.pack(pady=10)

        self._browse_button = ctk.CTkButton(self, text="Browse...", command=self._pick_file)
        self._browse_button.pack(pady=10)

        # Patches the ROM, copies it to the EDN8's SD card (edlink-n8.exe, auto-detected) and starts
        # it, staying on this screen, as often as wanted: the one way the bridge sends a ROM (not
        # offered once a session is running, where it would lose the game's progress)
        edlink_present = self._edlink_path() is not None
        self._send_rom_btn = ctk.CTkButton(
            self, text="Send patched ROM to EDN8", command=self._send_rom, state="disabled",
        )
        self._send_rom_btn.pack(pady=8)
        self._edlink_missing_label = None
        if not edlink_present:
            self._edlink_missing_label = ctk.CTkLabel(
                self,
                text="(edlink-n8.exe not found in bridge/tools/ or PATH; sending disabled)",
                text_color="gray",
                font=ctk.CTkFont(size=10),
            )
            self._edlink_missing_label.pack()

        self._mister_frame = ctk.CTkFrame(self)
        self._mister_frame.pack(pady=8, padx=40, fill="x")

        self._mister_host_var = ctk.StringVar()
        self._mister_username_var = ctk.StringVar(value="root")
        self._mister_password_var = ctk.StringVar(value="1")
        self._mister_host_var.trace_add("write", lambda *_: self._update_mister_deploy_state())

        ctk.CTkLabel(self._mister_frame, text="MiSTer host/IP:").grid(
            row=0,
            column=0,
            sticky="e",
            padx=10,
            pady=6,
        )
        ctk.CTkEntry(self._mister_frame, textvariable=self._mister_host_var, width=220).grid(
            row=0,
            column=1,
            sticky="w",
            padx=10,
            pady=6,
        )

        ctk.CTkLabel(self._mister_frame, text="Username:").grid(
            row=1,
            column=0,
            sticky="e",
            padx=10,
            pady=6,
        )
        ctk.CTkEntry(self._mister_frame, textvariable=self._mister_username_var, width=140).grid(
            row=1,
            column=1,
            sticky="w",
            padx=10,
            pady=6,
        )

        ctk.CTkLabel(self._mister_frame, text="Password:").grid(
            row=2,
            column=0,
            sticky="e",
            padx=10,
            pady=6,
        )
        ctk.CTkEntry(
            self._mister_frame,
            textvariable=self._mister_password_var,
            show="*",
            width=140,
        ).grid(row=2, column=1, sticky="w", padx=10, pady=6)

        self._deploy_btn = ctk.CTkButton(
            self._mister_frame,
            text="Test & Deploy",
            command=self._deploy_to_mister,
            state="disabled",
        )
        self._deploy_btn.grid(row=3, column=0, sticky="ew", padx=(10, 5), pady=(10, 6))

        self._diagnose_btn = ctk.CTkButton(
            self._mister_frame,
            text="Diagnose MiSTer",
            command=self._diagnose_mister,
            state="disabled",
        )
        self._diagnose_btn.grid(row=3, column=1, sticky="ew", padx=(5, 10), pady=(10, 6))

        self._copy_diagnostics_btn = ctk.CTkButton(
            self._mister_frame,
            text="Copy Diagnostics",
            command=self._copy_mister_diagnostics,
            state="disabled",
        )
        self._copy_diagnostics_btn.grid(row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=6)

        self._mister_log = ctk.CTkTextbox(self._mister_frame, height=132, width=480)
        self._mister_log.grid(row=5, column=0, columnspan=2, sticky="ew", padx=10, pady=6)
        self._mister_log.configure(state="disabled")
        self._mister_frame.columnconfigure(0, weight=1)
        self._mister_frame.columnconfigure(1, weight=1)

        self._status_label = ctk.CTkLabel(self, text="", wraplength=500, text_color="gray")
        self._status_label.pack(pady=10)

        self._continue_btn = ctk.CTkButton(
            self,
            text="Continue",
            state="disabled",
            command=self._continue,
        )
        self._continue_btn.pack(pady=20)

    def _pick_file(self) -> None:
        filename = filedialog.askopenfilename(
            title="Choose Z1 ROM",
            filetypes=[("NES ROMs", "*.nes"), ("All files", "*.*")],
        )
        if not filename:
            return
        self._rom_path = Path(filename)
        self._file_label.configure(text=str(self._rom_path))
        self._inspect_rom()
        self._update_send_rom_state()

    def _inspect_rom(self) -> None:
        assert self._rom_path is not None
        try:
            rom_bytes = self._rom_path.read_bytes()
        except OSError as e:
            self._status_label.configure(text=f"Error reading file: {e}", text_color="red")
            self._continue_btn.configure(state="disabled")
            return
        if len(rom_bytes) < 16 or rom_bytes[:4] != b"NES\x1a":
            self._status_label.configure(
                text="This doesn't look like a NES ROM (.nes header missing).",
                text_color="red",
            )
            self._continue_btn.configure(state="disabled")
            return
        if self._endpoint_type() == DEVICE_MISTER:
            self._patched_path = self._rom_path
            self.controller.mister_config = None
            self._status_label.configure(
                text="ROM selected. Test & Deploy to continue.",
                text_color="gray",
            )
            self._continue_btn.configure(state="disabled")
            self._update_mister_deploy_state()
            return

        patch_bytes, _expected = patch_revisions.load_patch()
        older = patch_revisions.older_patch_of(rom_bytes)
        if ips.is_patched(rom_bytes, patch_bytes):
            self._status_label.configure(
                text="✓ ROM is already patched. Ready.", text_color="green"
            )
            self._patched_path = self._rom_path
        elif older:
            self._status_label.configure(
                text=(
                    f"ROM was patched by the {older[0]} bridge. Will re-patch it for this bridge and "
                    f"save as {patch_revisions.patched_path(self._rom_path, replacing_older=True).name} when you send it or click Continue."
                ),
                text_color="gray",
            )
            self._patched_path = None
        else:
            self._status_label.configure(
                text="ROM is not patched. Will save as <name>_emucoop.nes when you send it or click Continue.",
                text_color="gray",
            )
            self._patched_path = None
        self._continue_btn.configure(state="normal")

    def _continue(self) -> None:
        if self._endpoint_type() == DEVICE_MISTER:
            if not self.controller.mister_config:
                self._status_label.configure(
                    text="Run Test & Deploy before continuing.",
                    text_color="red",
                )
                return
            self.controller.show_screen("relay_setup")
            return

        # Patch and save; sending it to the cart is the "Send patched ROM to EDN8" button's job
        if not self._patch_chosen_rom():
            return
        # Stash the patched path on the controller for later screens
        self.controller.patched_rom_path = self._patched_path
        self.controller.show_screen("relay_setup")

    def _patch_chosen_rom(self) -> bool:
        """Patch the chosen ROM (unless it already is) and save it; False, with the reason shown,
        when it can't be patched."""
        assert self._rom_path is not None
        rom_bytes = self._rom_path.read_bytes()
        try:
            prepared = patch_revisions.prepare_rom(rom_bytes)
        except ips.RomConflict as exc:
            offsets = ", ".join(f"0x{o:06X}" for o, _, _ in exc.mismatches[:6])
            more = f" (+{len(exc.mismatches) - 6} more)" if len(exc.mismatches) > 6 else ""
            self._status_label.configure(
                text=(
                    f"✗ ROM modified at {len(exc.mismatches)} patch site(s) "
                    f"({offsets}{more}). If this is a randomizer seed, "
                    f"please report the flagstring."
                ),
                text_color="red",
            )
            return False
        if not prepared.already_patched:
            out_path = patch_revisions.patched_path(self._rom_path, prepared.replaced_release is not None)
            out_path.write_bytes(prepared.rom)
            self._patched_path = out_path
            again = f" (re-patched from the {prepared.replaced_release} bridge's)" if prepared.replaced_release else ""
            self._status_label.configure(
                text=f"✓ Patched and saved: {out_path.name}{again}", text_color="green"
            )
        return True

    def _update_send_rom_state(self) -> None:
        ready = (self._endpoint_type() == DEVICE_EDN8 and self._rom_path is not None
                 and self._continue_btn.cget("state") == "normal" and self._edlink_path() is not None)
        self._send_rom_btn.configure(state="normal" if ready else "disabled")

    def _send_rom(self) -> None:
        if self._rom_path is None or not self._patch_chosen_rom() or self._patched_path is None:
            return
        path = self._patched_path
        self.controller.patched_rom_path = path
        self._send_rom_btn.configure(state="disabled")
        self._continue_btn.configure(state="disabled")
        self._status_label.configure(text=f"Sending {path.name} to the EverDrive...", text_color="gray")
        self._rom_sent: queue.Queue = queue.Queue()
        threading.Thread(target=self._send_rom_worker, args=(path,), daemon=True).start()
        self._wait_for_rom_sent()

    def _send_rom_worker(self, path: Path) -> None:
        # Off the Tk thread: the result goes back through a queue, never a Tk call
        self._rom_sent.put(edn8_upload.upload_and_launch(path))

    def _wait_for_rom_sent(self) -> None:
        try:
            result = self._rom_sent.get_nowait()
        except queue.Empty:
            self.after(SEND_POLL_MS, self._wait_for_rom_sent)
            return
        self._status_label.configure(text=result.message, text_color=result.color)
        self._continue_btn.configure(state="normal")
        self._update_send_rom_state()

    def on_show(self) -> None:
        endpoint = self._endpoint_type()
        if endpoint != self._active_endpoint:
            self._active_endpoint = endpoint
            self._reset_rom_state()
            if endpoint == DEVICE_MISTER:
                self._settings = UserSettings.load()
                self._mister_host_var.set(self._settings.mister_host)
                self._mister_username_var.set(self._settings.mister_username)
                self._mister_password_var.set(self._settings.mister_password_default)
                self._clear_mister_log()

        if endpoint == DEVICE_MISTER:
            self._show_mister_setup()
        else:
            self._show_edn8_setup()
        self._update_mister_deploy_state()

    def _show_edn8_setup(self) -> None:
        self._title_label.configure(text="ROM Setup")
        self._intro_label.configure(
            text=(
                "Choose your Z1 ROM (vanilla or Z1R seed), then send it to your EDN8.\n"
                "We'll apply the z1rr-coop patch if it's not already patched."
            )
        )
        self._mister_frame.pack_forget()
        if not self._send_rom_btn.winfo_ismapped():
            self._send_rom_btn.pack(pady=8, before=self._status_label)
        self._update_send_rom_state()
        if self._edlink_missing_label is not None and not self._edlink_missing_label.winfo_ismapped():
            self._edlink_missing_label.pack(before=self._status_label)

    def _show_mister_setup(self) -> None:
        self._title_label.configure(text="MiSTer Setup")
        self._intro_label.configure(
            text="Choose your Zelda 1 source ROM, then deploy the MiSTer bridge files."
        )
        self._send_rom_btn.pack_forget()
        if self._edlink_missing_label is not None:
            self._edlink_missing_label.pack_forget()
        if not self._mister_frame.winfo_ismapped():
            self._mister_frame.pack(pady=8, padx=40, fill="x", before=self._status_label)

    def _reset_rom_state(self) -> None:
        self._rom_path = None
        self._patched_path = None
        self._file_label.configure(text="(no file selected)")
        self._status_label.configure(text="", text_color="gray")
        self._continue_btn.configure(state="disabled")
        self._update_send_rom_state()
        self.controller.mister_config = None

    def _endpoint_type(self) -> str:
        endpoint = getattr(self.controller, "endpoint_type", DEVICE_EDN8)
        return endpoint if endpoint == DEVICE_MISTER else DEVICE_EDN8

    def _update_mister_deploy_state(self) -> None:
        if self._endpoint_type() != DEVICE_MISTER:
            return
        ready = (
            not self._deploy_running
            and self._rom_path is not None
            and bool(self._mister_host_var.get().strip())
        )
        can_diagnose = not self._deploy_running and bool(self._mister_host_var.get().strip())
        self._deploy_btn.configure(state="normal" if ready else "disabled")
        self._diagnose_btn.configure(state="normal" if can_diagnose else "disabled")
        self._copy_diagnostics_btn.configure(
            state="normal" if self._last_mister_diagnostics else "disabled"
        )

    def _deploy_to_mister(self) -> None:
        if self._rom_path is None:
            return
        host = self._mister_host_var.get().strip()
        if not host:
            self._status_label.configure(text="MiSTer host/IP is required.", text_color="red")
            return
        self._deploy_running = True
        self._deploy_btn.configure(state="disabled")
        self._continue_btn.configure(state="disabled")
        self.controller.mister_config = None
        self._clear_mister_log()
        self._append_mister_log("Starting MiSTer deploy...")

        thread = threading.Thread(
            target=self._deploy_to_mister_worker,
            args=(
                self._rom_path,
                host,
                self._mister_username_var.get(),
                self._mister_password_var.get(),
            ),
            daemon=True,
        )
        thread.start()

    def _deploy_to_mister_worker(
        self,
        rom_path: Path,
        host: str,
        username: str,
        password: str,
    ) -> None:
        service: MisterDeployService | None = None
        try:
            manifest = self._load_mister_payload_manifest()
            helper = manifest["helper"]
            core = manifest["core"]
            roms = manifest["roms"]

            helper_path = self._mister_payload_path(helper["filename"])
            core_path = self._mister_payload_path(core["filename"])
            if core.get("required", True) and not core_path.is_file():
                raise FileNotFoundError(f"MiSTer core asset missing locally: {core_path}")

            config = MisterSshConfig(
                host=host,
                username=username.strip() or "root",
                password=password or "1",
            )
            service = MisterDeployService(
                config,
                on_event=lambda event: self._post_mister_log(event.message),
            )
            service.connect()
            self._settings.mister_host = host
            self._settings.mister_username = config.username
            self._settings.save()

            assets = [
                DeployAsset(helper_path, helper["remote_path"], executable=True),
                DeployAsset(core_path, core["remote_path"]),
            ]
            service.deploy(assets)
            remote_rom_path = service.stage_rom(rom_path, roms["remote_dir"])
            service.restart_helper(helper["remote_path"], port=int(helper["port"]))
            report = self._build_mister_diagnostics(host, config.username, config.password)
            self._post_mister_diagnostics(report.render_text())
            health = report.helper_health
            if health is None:
                raise RuntimeError("MiSTer helper health was not checked.")
            if not health.helper_reachable:
                raise RuntimeError(format_mister_health_summary(health))

            config_dict = build_mister_rom_setup_config(
                rom_path=rom_path,
                remote_rom_path=remote_rom_path,
                host=host,
                username=config.username,
                password=config.password,
            )
            self.after(0, lambda: self._on_mister_deploy_success(config_dict))
        except Exception as exc:
            message = str(exc)
            self.after(0, lambda message=message: self._on_mister_deploy_error(message))
        finally:
            if service is not None:
                service.close()

    def _diagnose_mister(self) -> None:
        host = self._mister_host_var.get().strip()
        if not host:
            self._status_label.configure(text="MiSTer host/IP is required.", text_color="red")
            return
        self._deploy_running = True
        self._update_mister_deploy_state()
        self._clear_mister_log()
        self._append_mister_log("Running MiSTer diagnostics...")
        self._status_label.configure(text="Running MiSTer diagnostics...", text_color="gray")

        thread = threading.Thread(
            target=self._diagnose_mister_worker,
            args=(
                host,
                self._mister_username_var.get(),
                self._mister_password_var.get(),
            ),
            daemon=True,
        )
        thread.start()

    def _diagnose_mister_worker(self, host: str, username: str, password: str) -> None:
        try:
            report = self._build_mister_diagnostics(
                host,
                username,
                password,
                start_helper_if_needed=True,
            )
            self.after(
                0,
                lambda report_text=report.render_text(): self._on_mister_diagnostics_success(
                    report_text
                ),
            )
        except Exception as exc:
            message = str(exc)
            self.after(0, lambda message=message: self._on_mister_diagnostics_error(message))

    def _build_mister_diagnostics(
        self,
        host: str,
        username: str,
        password: str,
        *,
        start_helper_if_needed: bool = False,
    ) -> MisterDiagnosticReport:
        manifest = self._load_mister_payload_manifest()
        helper = manifest["helper"]
        core = manifest["core"]
        return run_mister_diagnostics(
            host=host,
            username=username,
            password=password,
            helper_local_path=self._mister_payload_path(helper["filename"]),
            helper_remote_path=helper["remote_path"],
            helper_log_path=helper.get("log_path"),
            core_local_path=self._mister_payload_path(core["filename"]),
            core_remote_path=core["remote_path"],
            port=int(helper["port"]),
            mode=tloz_progress,
            start_helper_if_needed=start_helper_if_needed,
            health_probe=probe_mister_helper,
        )

    def _on_mister_diagnostics_success(self, report_text: str) -> None:
        self._deploy_running = False
        self._set_mister_diagnostics(report_text)
        self._status_label.configure(text="MiSTer diagnostics complete.", text_color="green")
        self._update_mister_deploy_state()

    def _on_mister_diagnostics_error(self, message: str) -> None:
        self._deploy_running = False
        self._append_mister_log(f"ERROR: {message}")
        self._status_label.configure(text=message, text_color="red")
        self._update_mister_deploy_state()

    def _on_mister_deploy_success(self, config: dict[str, Any]) -> None:
        self._deploy_running = False
        self.controller.mister_config = config
        self._append_mister_log("MiSTer deploy complete.")
        self._status_label.configure(text="MiSTer deploy complete. Ready.", text_color="green")
        self._continue_btn.configure(state="normal")
        self._update_mister_deploy_state()

    def _on_mister_deploy_error(self, message: str) -> None:
        self._deploy_running = False
        self.controller.mister_config = None
        self._append_mister_log(f"ERROR: {message}")
        self._status_label.configure(text=message, text_color="red")
        self._continue_btn.configure(state="disabled")
        self._update_mister_deploy_state()

    def _post_mister_log(self, message: str) -> None:
        self.after(0, lambda: self._append_mister_log(message))

    def _post_mister_diagnostics(self, report_text: str) -> None:
        self.after(0, lambda: self._set_mister_diagnostics(report_text))

    def _clear_mister_log(self) -> None:
        self._last_mister_diagnostics = ""
        if hasattr(self, "_copy_diagnostics_btn"):
            self._copy_diagnostics_btn.configure(state="disabled")
        self._mister_log.configure(state="normal")
        self._mister_log.delete("1.0", "end")
        self._mister_log.configure(state="disabled")

    def _append_mister_log(self, message: str) -> None:
        self._mister_log.configure(state="normal")
        self._mister_log.insert("end", message + "\n")
        self._mister_log.see("end")
        self._mister_log.configure(state="disabled")

    def _set_mister_diagnostics(self, report_text: str) -> None:
        self._last_mister_diagnostics = report_text
        self._append_mister_log(report_text)
        self._copy_diagnostics_btn.configure(state="normal")

    def _copy_mister_diagnostics(self) -> None:
        if not self._last_mister_diagnostics:
            return
        self.clipboard_clear()
        self.clipboard_append(self._last_mister_diagnostics)
        self._status_label.configure(text="MiSTer diagnostics copied.", text_color="green")

    @staticmethod
    def _load_mister_payload_manifest() -> dict[str, Any]:
        path = files("bridge_core").joinpath("mister_payload", "manifest.json")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _mister_payload_path(filename: str) -> Path:
        return Path(str(files("bridge_core").joinpath("mister_payload", filename)))

    @staticmethod
    def _edlink_path() -> Path | None:
        return edn8_upload.find_edlink()

