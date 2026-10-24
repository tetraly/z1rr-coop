"""CLI entry point for the bridge.

Usage:
  python -m bridge_cli run --mode tloz_all --port COM3 --code mycode \
                            [--relay coop.z1rracing.com] [--relay-port 9999]
                            [--force-send]
  python -m bridge_cli patch <input.nes> -o <output.nes>
  python -m bridge_cli read --port COM3 --addr 0x0657 --length 16
  python -m bridge_cli patch-check --port COM3
  python -m bridge_cli mister-run --mode tloz_all --mister-host mister.local \
                                  --code mycode
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import uuid
from importlib import import_module
from importlib.resources import files
from pathlib import Path

import serial

from bridge_core import __version__
from bridge_core import ips, patch_revisions
from bridge_core.cc_client import PATCH_REVISION, CCClient
from bridge_core.cc_endpoint import CCMemoryEndpoint
from bridge_core.edn8_overload import tloz_all_overload_options
from bridge_core.map_write_gate import MapWriteGate
from bridge_core.mister_deploy import DeployAsset, MisterDeployService, MisterSshConfig
from bridge_core.mister_endpoint import ReadOnlyMisterMemoryEndpoint
from bridge_core.mister_health import (
    MisterHelperHealth,
    format_mister_health_summary,
    probe_mister_helper,
)
from bridge_core.mister_helper import MisterHelperMemoryEndpoint, MisterHelperServer
from bridge_core.mister_mailbox import (
    DevMemMailboxMemory,
    FileMailboxMemory,
    MisterMailboxMemoryEndpoint,
    MisterWriteMailbox,
)
from bridge_core.mister_ra import DevMemRAMirrorSource, FileRAMirrorSource
from bridge_core.pipe_client import PipeClient
from bridge_core.readonly_session import run_readonly_session
from bridge_core.status_sink import ConsoleStatusSink
from bridge_core.sync_engine import SyncEngine
from bridge_core.sync_probe import endpoint_error, read_running_probe
from bridge_core.sync_session import run_sync_session
from bridge_core.mister_smartcache import MisterSmartCacheMemoryEndpoint

POLL_HZ = 10
POLL_PERIOD = 1.0 / POLL_HZ
DEFAULT_RELAY = "coop.z1rracing.com"


def cmd_patch(args: argparse.Namespace) -> int:
    src = Path(args.input).read_bytes()
    try:
        prepared = patch_revisions.prepare_rom(src)
    except ips.RomConflict as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "This ROM has been modified at offsets where z1rr-coop needs to "
            "write. If this is a randomizer seed, please report the flagstring "
            "so we can audit.",
            file=sys.stderr,
        )
        return 2
    if prepared.already_patched:
        print(f"Already patched: {args.input}")
        if args.output:
            Path(args.output).write_bytes(src)
        return 0
    out_path = args.output or str(patch_revisions.patched_path(Path(args.input)))
    Path(out_path).write_bytes(prepared.rom)
    again = f" (re-patched from the {prepared.replaced_release} bridge's patch)" if prepared.replaced_release else ""
    print(f"Patched: {args.input} -> {out_path}{again}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    sp = serial.Serial(args.port, baudrate=args.baud, timeout=0)
    cc = CCClient(sp)
    cc.send_read_array(args.addr, args.length)
    result = cc.poll_response(timeout_ms=500)
    if result is None:
        print("timeout", file=sys.stderr)
        return 1
    print(" ".join(f"{b:02X}" for b in result))
    return 0


PATCH_CHECK_BYTE = 0x7F96    # unused byte of the patch's own SRAM page: safe to write


def cmd_patch_check(args: argparse.Namespace) -> int:
    """Hardware check of the extended patch (extend_patch.py), run with the game in play
    (overworld, not paused): the revision, then a guarded write that must happen and one that
    must not, on a byte nothing else uses, which it puts back."""
    sp = serial.Serial(args.port, baudrate=args.baud, timeout=0)
    cc = CCClient(sp)
    version = cc.version()
    if version is None:
        print("FAIL: no answer to the version command (is the game in play, not paused?)")
        return 1
    print(f"version: {version.hex(' ')}")
    if version[0] != PATCH_REVISION:
        print(f"FAIL: patch revision {version[0]}, not {PATCH_REVISION}: patch the ROM with this bridge")
        return 1
    before = cc.read_array(PATCH_CHECK_BYTE, 1)
    if before is None:
        print("FAIL: read timed out")
        return 1
    held, changed = before[0], before[0] ^ 0x5A
    checks = [
        ("guard holds: written", cc.guarded_write([(PATCH_CHECK_BYTE, changed)], [(PATCH_CHECK_BYTE, held)]),
         True, changed),
        ("guard fails: not written", cc.guarded_write([(PATCH_CHECK_BYTE, 0x00)], [(PATCH_CHECK_BYTE, held)]),
         False, changed),
    ]
    failed = 0
    for name, answer, want_answer, want_byte in checks:
        now = cc.read_array(PATCH_CHECK_BYTE, 1)
        ok = answer is want_answer and now is not None and now[0] == want_byte
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}: {name} (answer {answer}, byte {now.hex() if now else 'timeout'})")
    cc.guarded_write([(PATCH_CHECK_BYTE, held)], [])
    return 1 if failed else 0


def cmd_mister_read(args: argparse.Namespace) -> int:
    endpoint = _make_mister_read_endpoint(args.mirror_file)
    try:
        snapshot = endpoint.read_ranges([(args.addr, args.length)])
        if snapshot is None:
            print("mirror busy or inactive", file=sys.stderr)
            return 1
        print(" ".join(f"{snapshot[args.addr + offset]:02X}" for offset in range(args.length)))
        return 0
    finally:
        endpoint.close()


def cmd_mister_helper(args: argparse.Namespace) -> int:
    read_endpoint = _make_mister_read_endpoint(args.mirror_file)
    endpoint = read_endpoint
    if args.enable_writes:
        mailbox_memory = _make_mister_mailbox_memory(args.mailbox_file)
        mailbox = MisterWriteMailbox(
            mailbox_memory,
            ack_timeout_ms=args.write_timeout_ms,
        )
        endpoint = MisterMailboxMemoryEndpoint(read_endpoint, mailbox)
    server = MisterHelperServer((args.host, args.port), endpoint)
    try:
        host, port = server.server_address
        print(f"MiSTer helper listening on {host}:{port}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        endpoint.close()
    return 0


def cmd_mister_run(args: argparse.Namespace) -> int:
    sink = ConsoleStatusSink()
    sink.log(f"Loading mode: {args.mode}")
    mode = import_module(f"bridge_core.modes.{args.mode}")

    sink.log(f"Connecting to MiSTer helper {args.mister_host}:{args.mister_port}")
    endpoint = MisterHelperMemoryEndpoint(
        host=args.mister_host,
        port=args.mister_port,
        timeout=args.mister_timeout,
    )

    sink.log(f"Connecting to relay {args.relay}:{args.relay_port}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((args.relay, args.relay_port))
    sock.setblocking(False)

    peer_id = uuid.uuid4().hex
    pipe = PipeClient(socket=sock, code=args.code, peer_id=peer_id)
    runner = run_sync_session if args.enable_writes else run_readonly_session
    if args.enable_writes:
        sink.log("MiSTer write-capable sync enabled")
    return runner(
        endpoint=endpoint,
        pipe=pipe,
        mode=mode,
        sink=sink,
        poll_hz=args.poll_hz,
    )


def cmd_mister_deploy(args: argparse.Namespace) -> int:
    try:
        manifest = _load_mister_payload_manifest()
        assets = _build_mister_deploy_assets(manifest)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    service = None
    try:
        config = MisterSshConfig(
            host=args.host,
            username=args.username,
            password=args.password,
            port=args.ssh_port,
            timeout_s=args.timeout_s,
        )
        service = MisterDeployService(
            config,
            on_event=lambda event: print(event.message),
        )
        service.connect()
        service.deploy(assets)
        if args.rom:
            remote_rom = service.stage_rom(
                Path(args.rom),
                manifest["roms"]["remote_dir"],
            )
            print(f"ROM staged: {remote_rom}")
        helper = manifest["helper"]
        service.restart_helper(helper["remote_path"], port=int(helper["port"]))
        health = probe_mister_helper(
            host=args.host,
            port=int(helper["port"]),
            timeout=args.timeout_s,
        )
        if not health.helper_reachable:
            print(format_mister_health_summary(health), file=sys.stderr)
            return 1
        print(format_mister_health_summary(health))
        print("MiSTer deploy complete")
        return 0
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if service is not None:
            service.close()


def cmd_mister_health(args: argparse.Namespace) -> int:
    mode = import_module(f"bridge_core.modes.{args.mode}") if args.mode else None
    health = probe_mister_helper(
        host=args.host,
        port=args.port,
        timeout=args.timeout_s,
        mode=mode,
    )
    text = format_mister_health_summary(health)
    if not health.helper_reachable:
        print(text, file=sys.stderr)
        return 1
    print(text)
    return 0 if health.mirror_ok else 2


def _load_mister_payload_manifest() -> dict:
    path = files("bridge_core").joinpath("mister_payload", "manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _build_mister_deploy_assets(manifest: dict) -> list[DeployAsset]:
    helper = manifest["helper"]
    core = manifest["core"]
    helper_path = _mister_payload_path(helper["filename"])
    core_path = _mister_payload_path(core["filename"])
    if core.get("required", True) and not core_path.is_file():
        raise FileNotFoundError(f"MiSTer core asset missing locally: {core_path}")
    return [
        DeployAsset(helper_path, helper["remote_path"], executable=True),
        DeployAsset(core_path, core["remote_path"]),
    ]


def _mister_payload_path(filename: str) -> Path:
    return Path(str(files("bridge_core").joinpath("mister_payload", filename)))


def _make_mister_source(mirror_file: str | None):
    if mirror_file:
        return FileRAMirrorSource(mirror_file)
    return DevMemRAMirrorSource()


def _make_mister_read_endpoint(mirror_file: str | None):
    if mirror_file:
        return ReadOnlyMisterMemoryEndpoint(_make_mister_source(mirror_file))
    return MisterSmartCacheMemoryEndpoint(DevMemMailboxMemory())


def _make_mister_mailbox_memory(mailbox_file: str | None):
    if mailbox_file:
        return FileMailboxMemory(mailbox_file)
    return DevMemMailboxMemory()


def cmd_run(args: argparse.Namespace) -> int:
    sink = ConsoleStatusSink()
    sink.log(f"Loading mode: {args.mode}")
    mode = import_module(f"bridge_core.modes.{args.mode}")

    sink.log(f"Opening serial port {args.port} @ {args.baud}")
    sp = serial.Serial(args.port, baudrate=args.baud, timeout=0)
    cc = CCClient(sp)
    endpoint = CCMemoryEndpoint(cc)

    sink.log(f"Connecting to relay {args.relay}:{args.relay_port}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((args.relay, args.relay_port))
    sock.setblocking(False)

    peer_id = uuid.uuid4().hex
    pipe = PipeClient(socket=sock, code=args.code, peer_id=peer_id)
    pipe._reconnect_enabled = True

    overload_options = tloz_all_overload_options("edn8", args.mode)
    engine = SyncEngine(
        endpoint=endpoint,
        mode=mode,
        map_write_gate=MapWriteGate(),
        resync_send_limit=overload_options.get("resync_send_limit"),
    )
    sink.log("EDN8 map write gate enabled")
    if overload_options:
        sink.log("EDN8 tloz_all overload protection enabled")
    defer_full_poll_while_map_pending = bool(
        overload_options.get("defer_full_poll_while_map_pending", False)
    )
    if args.force_send:
        engine.force_send = True
        sink.log("force_send enabled")

    pipe.on_data = lambda body: _on_data(engine, sink, body)
    pipe.on_abort = lambda reason: sink.message(f"Partner aborted: {reason}")
    def _reconnected():
        sink.message("Partner reconnected — re-syncing state")
        engine.resync()
    pipe.on_partner_reconnected = _reconnected
    pipe.send_join()

    # App-level hello (after pipe ESTABLISHED)
    app_hello_sent = False
    last_state = None
    last_map_backlog_log_at = 0.0
    sink.state("CONNECTING")

    try:
        while pipe.state != "CLOSED":
            loop_start = time.monotonic()

            # When pipe wants to reconnect, open a fresh socket and re-JOIN.
            if pipe.state == "RECONNECTING":
                retry_at = pipe._next_retry_at or 0
                if loop_start >= retry_at:
                    try:
                        new_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        new_sock.connect((args.relay, args.relay_port))
                        new_sock.setblocking(False)
                        pipe.reset_for_reconnect(new_sock)
                        pipe.send_join()
                        sink.log(f"Relay reconnect attempt #{pipe._reconnect_attempt}")
                        sock = new_sock
                    except Exception as e:
                        sink.log(f"Reconnect attempt failed: {e}")
                        pipe._next_retry_at = loop_start + pipe._next_backoff()

            pipe.tick()
            pipe.heartbeat_tick()

            # Surface every state transition
            if pipe.state != last_state:
                sink.state(pipe.state)
                if pipe.state == "RECONNECTING":
                    sink.message("Partner disconnected; reconnecting…")
                    app_hello_sent = False
                last_state = pipe.state

            if pipe.state == "ESTABLISHED" and not app_hello_sent:
                pipe.send_data({"op": "hello", "guid": mode.GUID, "version": __version__})
                app_hello_sent = True

            if pipe.state == "ESTABLISHED":
                try:
                    running_probe = read_running_probe(endpoint, mode, timeout_ms=300)
                except Exception as e:
                    sink.log(f"Endpoint read failed: {endpoint_error(e)}", level="ERROR")
                    running_probe = None
                if running_probe is None:
                    full_snapshot = None
                elif not running_probe:
                    if engine.observe_not_running():
                        sink.log("Game stopped running; pausing sync")
                    full_snapshot = None
                else:
                    for msg in engine.drain_map_write_gate():
                        sink.message(msg)
                    if (
                        defer_full_poll_while_map_pending
                        and engine.map_write_pending_count > 0
                    ):
                        if loop_start - last_map_backlog_log_at >= 1.0:
                            sink.log(
                                f"EDN8 map backlog {engine.map_write_pending_count}; "
                                "throttling full poll"
                            )
                            last_map_backlog_log_at = loop_start
                        _send_resync_chunk(engine, pipe)
                        full_snapshot = None
                    elif engine.resync_send_pending_count > 0:
                        _send_resync_chunk(engine, pipe)
                        full_snapshot = None
                    else:
                        # Poll the mode's READ_RANGES via Action 0x01 (ArrayRead).
                        # Much lighter on the cart's main-loop than scattered reads.
                        try:
                            full_snapshot = endpoint.read_ranges(mode.READ_RANGES, timeout_ms=300)
                        except Exception as e:
                            sink.log(f"Endpoint read failed: {endpoint_error(e)}", level="ERROR")
                            full_snapshot = None
                if full_snapshot is not None:
                    invalid_reason = engine.implausible_snapshot_reason(full_snapshot)
                    if invalid_reason:
                        sink.log(
                            f"Ignoring implausible endpoint snapshot: {invalid_reason}",
                            level="WARNING",
                        )
                        full_snapshot = None
                if full_snapshot is not None:
                    if engine.is_game_running(full_snapshot):
                        engine.observe_running()
                        if not engine.did_cache:
                            to_send = engine.check_first_running(full_snapshot)
                            for addr, value in to_send:
                                pipe.send_data({"addr": addr, "value": value})
                            if engine.resync_send_pending_count:
                                sink.log(
                                    "EDN8 tloz_all resync queued "
                                    f"{engine.resync_send_pending_count} updates"
                                )
                        for msg in engine.drain_sleep_queue():
                            sink.message(msg)
                        for addr, send_value, msg in engine.diff(full_snapshot):
                            pipe.send_data({"addr": addr, "value": send_value})
                            if msg:
                                sink.message(msg)
                    elif engine.observe_not_running():
                        sink.log("Game stopped running; pausing sync")

            elapsed = time.monotonic() - loop_start
            sleep = POLL_PERIOD - elapsed
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        sink.log("Interrupted by user")

    pipe.close()
    sp.close()
    sock.close()
    sink.state("DISCONNECTED")
    return 0 if pipe.state != "FAILED" else 1


def _on_data(engine: SyncEngine, sink: ConsoleStatusSink, body: dict) -> None:
    if body.get("op") == "hello":
        if body.get("guid") != engine.mode.GUID:
            sink.message(f"Partner has incompatible mode: {body.get('guid')}")
            return
        sink.log(f"Partner's app hello OK (guid={body['guid']}, version={body.get('version')})")
        return
    messages = engine.handle_table(body)
    for m in messages:
        sink.message(m)


def _send_resync_chunk(engine: SyncEngine, pipe: PipeClient) -> None:
    for addr, value in engine.drain_resync_send_queue():
        pipe.send_data({"addr": addr, "value": value})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bridge_cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_patch = sub.add_parser("patch", help="Apply CC IPS patch to a ROM")
    p_patch.add_argument("input")
    p_patch.add_argument("-o", "--output")

    p_read = sub.add_parser("read", help="One-shot RAM read for diagnostics")
    p_read.add_argument("--port", required=True)
    p_read.add_argument("--baud", type=int, default=115200)
    p_read.add_argument("--addr", type=lambda s: int(s, 0), required=True)
    p_read.add_argument("--length", type=int, default=1)

    p_patch_check = sub.add_parser(
        "patch-check", help="Check the extended patch's guarded write on the cart (game in play)")
    p_patch_check.add_argument("--port", required=True)
    p_patch_check.add_argument("--baud", type=int, default=115200)

    p_mister_read = sub.add_parser(
        "mister-read",
        help="One-shot NES RAM read from MiSTer's RA mirror",
    )
    p_mister_read.add_argument(
        "--mirror-file",
        help="Read a captured RA mirror file instead of mapping /dev/mem",
    )
    p_mister_read.add_argument("--addr", type=lambda s: int(s, 0), required=True)
    p_mister_read.add_argument("--length", type=int, default=1)

    p_mister_helper = sub.add_parser(
        "mister-helper",
        help="Serve MiSTer RA mirror reads over a tiny JSON-line TCP helper",
    )
    p_mister_helper.add_argument("--host", default="0.0.0.0")
    p_mister_helper.add_argument("--port", type=int, default=55355)
    p_mister_helper.add_argument(
        "--mirror-file",
        help="Serve a captured RA mirror file instead of mapping /dev/mem",
    )
    p_mister_helper.add_argument(
        "--enable-writes",
        action="store_true",
        help="Enable the custom NES core DDRAM write mailbox",
    )
    p_mister_helper.add_argument(
        "--mailbox-file",
        help="Use a file-backed mailbox instead of mapping /dev/mem",
    )
    p_mister_helper.add_argument(
        "--write-timeout-ms",
        type=int,
        default=100,
        help="Milliseconds to wait for the FPGA mailbox ack",
    )

    p_mister_run = sub.add_parser(
        "mister-run",
        help="Run a read-only MiSTer bridge through the relay",
    )
    p_mister_run.add_argument("--mode", required=True, help="Mode module name, e.g. tloz_all")
    p_mister_run.add_argument("--mister-host", required=True)
    p_mister_run.add_argument("--mister-port", type=int, default=55355)
    p_mister_run.add_argument("--mister-timeout", type=float, default=1.0)
    p_mister_run.add_argument("--code", required=True, help="Session code (6+ chars)")
    p_mister_run.add_argument("--relay", default=DEFAULT_RELAY)
    p_mister_run.add_argument("--relay-port", type=int, default=9999)
    p_mister_run.add_argument("--poll-hz", type=int, default=POLL_HZ)
    p_mister_run.add_argument(
        "--enable-writes",
        action="store_true",
        help="Apply incoming relay writes through the MiSTer helper",
    )

    p_mister_deploy = sub.add_parser(
        "mister-deploy",
        help="Deploy the MiSTer helper and Z1RR-coop NES core over SSH",
    )
    p_mister_deploy.add_argument("--host", required=True)
    p_mister_deploy.add_argument("--username", default="root")
    p_mister_deploy.add_argument("--password", default="1")
    p_mister_deploy.add_argument("--ssh-port", type=int, default=22)
    p_mister_deploy.add_argument("--timeout-s", type=float, default=8.0)
    p_mister_deploy.add_argument(
        "--rom",
        help="Optional Zelda 1 source ROM to stage under /media/fat/games/NES/z1rr-coop",
    )

    p_mister_health = sub.add_parser(
        "mister-health",
        help="Check PC connectivity to the MiSTer helper and smart-cache mirror",
    )
    p_mister_health.add_argument("--host", required=True)
    p_mister_health.add_argument("--port", type=int, default=55355)
    p_mister_health.add_argument("--timeout-s", type=float, default=2.0)
    p_mister_health.add_argument(
        "--mode",
        default="tloz_progress",
        help="Mode used to classify the 0x0012 running byte",
    )

    p_run = sub.add_parser("run", help="Run the bridge: connect to relay and sync game state")
    p_run.add_argument("--mode", required=True, help="Mode module name, e.g. tloz_all")
    p_run.add_argument("--port", required=True, help="Serial port (e.g. COM3)")
    p_run.add_argument("--baud", type=int, default=115200)
    p_run.add_argument("--code", required=True, help="Session code (6+ chars)")
    p_run.add_argument("--relay", default=DEFAULT_RELAY)
    p_run.add_argument("--relay-port", type=int, default=9999)
    p_run.add_argument("--force-send", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd == "patch":
        return cmd_patch(args)
    elif args.cmd == "read":
        return cmd_read(args)
    elif args.cmd == "patch-check":
        return cmd_patch_check(args)
    elif args.cmd == "mister-read":
        return cmd_mister_read(args)
    elif args.cmd == "mister-helper":
        return cmd_mister_helper(args)
    elif args.cmd == "mister-run":
        return cmd_mister_run(args)
    elif args.cmd == "mister-deploy":
        return cmd_mister_deploy(args)
    elif args.cmd == "mister-health":
        return cmd_mister_health(args)
    elif args.cmd == "run":
        return cmd_run(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
