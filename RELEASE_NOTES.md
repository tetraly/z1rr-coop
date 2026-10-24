# Unreleased: EDN8 patch revision 1

- **New EDN8 patch (revision 1).** Adds a guarded-write command (for Archipelago), shrinks the freeze slots to four at $7F40-$7F7F, moves the patch's flag off $07FE (which erased room $7F's saved flags in levels 7-9), and drops the unused Crowd Control speed hook, so ROMs with Pegasus Boots or Red Bubble Curse: Slow Speed are accepted. Co-op's sync is unchanged. Details: `bridge/docs/patch-revision-1.md`.
- **ROMs patched by beta 8** are re-patched from their original when chosen; the beta 8 bridge refuses ROMs patched by this one.
- **Beta 8 carts paired with newer ones:** the bridge no longer syncs a beta 8 cart's own $07FE (its patch flag), and ignores that flag's $AB when a beta 8 EDN8 partner sends it; it put a stray $AB into room $7F of levels 7-9.
- **`bridge_cli patch-check`** checks a cart's patch revision and guarded write.

# z1rr-coop v2.0 beta 8 - release notes

Eighth community-testing build of the z1rr-coop bridge. Beta8 focuses on hardware-session stability and diagnostics after live EDN8 and MiSTer testing: better MiSTer setup checks, clearer live diagnostics, EDN8 `tloz_all` overload protection, newer EDN8 firmware COM detection, and bridge message parity with FCEUX.

This is a pre-release. Keep bridge diagnostics handy while validating hardware sessions, especially MiSTer helper/core setup and EDN8 `tloz_all`.

## Which file do I download?

| You play on... | Download | Size |
|---|---|---|
| **Hardware bridge: EDN8 or MiSTer** | `z1rr-coop-2.0-beta8-hardware.exe` | ~19 MB |
| **32-bit FCEUX** | `z1rr-coop-2.0-beta8-fceux-win32.zip` | ~540 KB |
| **64-bit FCEUX** | `z1rr-coop-2.0-beta8-fceux-win64.zip` | ~1.3 MB |

Any two endpoints can be paired together: FCEUX, EDN8, or MiSTer. FCEUX <-> FCEUX, FCEUX <-> EDN8, FCEUX <-> MiSTer, EDN8 <-> MiSTer, EDN8 <-> EDN8, and MiSTer <-> MiSTer all use the same shared cloud relay and pair by an agreed-on session code.

## What's new in v2.0 beta 8

- **Bridge Messages tab now matches FCEUX partner item messaging.** Incoming partner pickups display `Partner got ...` in the hardware bridge Messages tab for normal items and progress bitmaps, including Raft, level maps/compasses, and triforce pieces.
- **Z1RR-coop Bridge app branding.** The Windows app title, bridge package metadata, helper startup text, and bridge docs now use Z1RR-coop naming instead of the older upstream label.
- **EDN8 patched ROM title branding.** Newly patched EDN8 ROMs now show `Z1RRCOOP` on the Zelda 1 title screen.
- **Incoming sync diagnostics.** The Diagnostics tab now reports `Last incoming result`, showing whether the latest partner update was applied, no-op'd, queued while the game was not running, or hit a read/write error.
- **MiSTer Diagnose flow.** MiSTer setup now includes Diagnose MiSTer and Copy Diagnostics actions so testers can gather SSH, helper, core, helper-port, PC-to-helper, mirror-read, frame, and running-state details without using CLI commands.
- **MiSTer helper health probe.** Test & Deploy now checks required MiSTer tools, confirms helper/core freshness, starts the helper when needed, verifies the helper port, and probes RAM mirror health.
- **MiSTer live RAM sample diagnostics.** Live sessions show running-probe counts, last running byte, endpoint frame, and selected RAM sample bytes to make RAM mirror/core issues visible.
- **Newer EDN8 firmware USB detection.** The hardware bridge prioritizes both the older STM32 EDN8 USB VID and the newer `38DF:0017` EverDrive USB device ID used by firmware `v25.1109`.
- **EDN8 `tloz_all` overload protection.** Reconnect re-sync sends queued EDN8 `tloz_all` state gradually, and incoming map-write backlogs temporarily defer full polling so reads and writes are not stacked on the cart.
- **More reliable hardware build script.** The hardware builder now runs PyInstaller through `python -m PyInstaller` to avoid stale exe copies when uv cannot canonicalize the PyInstaller console-script shim.

## What's new in v2.0 beta 7

- **Newer EDN8 firmware USB detection.** The hardware bridge now prioritizes both the older STM32 EDN8 USB VID and the newer `38DF:0017` EverDrive USB device ID, so the COM dropdown is less likely to default to an unrelated CH340 serial adapter after firmware `v25.1109`.
- **EDN8 `tloz_all` reconnect safety.** Reconnect re-sync now sends queued EDN8 `tloz_all` state gradually instead of blasting every non-zero map/progress byte in one tick.
- **EDN8 map backlog protection.** While an incoming `tloz_all` map-write backlog is draining, the EDN8 bridge temporarily skips full `tloz_all` polling so read and write pressure are not stacked on the cart.
- **EDN8 diagnostics tab.** The live session screen now has a Diagnostics tab with copyable EDN8 counters for polls, endpoint failures, incoming/outgoing updates, map backlog, resync backlog, and last endpoint error.

## What's new in v2.0 beta 6

- **Stable relay hostname.** FCEUX fallback config, the FCEUX connection dialog, the hardware bridge GUI, and bridge CLI defaults now use `coop.z1rracing.com:9999` instead of a raw OCI IP address.
- **Relay DNS target updated.** `coop.z1rracing.com` currently points at reserved IPv4 `157.151.194.113`; packaged clients should keep using the hostname.

## What's new in v2.0 beta 5

- **EDN8 `tloz_all` write safety.** Hardware-side map writes are now gated and rate-limited so a large incoming map burst does not hammer the EDN8 USB path.
- **Stronger snapshot validation.** The hardware bridge rejects implausible local snapshots, implausible incoming partner values, and masked map values with impossible bits before they can be broadcast or written.
- **Better EDN8 transition handling.** Running-state transients during Zelda 1 screen transitions no longer immediately flush map diffs or stop sync work.
- **Startup noise cleanup.** Initial bomb-capacity deltas are suppressed so starting a seed does not announce misleading "got rid of bombs" / "bomb upgrade" messages.
- **Separate FCEUX packages.** 32-bit and 64-bit FCEUX now have separate zips with matching native IUP/IUPLua/LuaSocket modules.
- **64-bit FCEUX config fallback.** If a 64-bit FCEUX build cannot open the IUP dialog from Lua, `coop_config.lua` can provide relay/session settings directly. The fallback config ships disabled, with the Zelda 1 "sync most things" mode line ready to edit.
- **Hardware version reporting.** Hardware app hellos now report the release version instead of the internal package version.
- **MiSTer setup polish.** The hardware setup window opens taller and the MiSTer flow is scrollable so the Continue button remains reachable.
- **Hardware app icon.** The Windows hardware bridge now uses the Gannon "shyboi" icon.
- **Relay diagnostics.** Relay payload tracing remains available for beta validation and makes sender/receiver behavior visible in `journalctl`.

## What's new in v2.0 beta 4

- **MiSTer hardware bridge support.** The bridge added a device choice screen, MiSTer SSH setup, ROM staging under `/media/fat/games/NES/z1rr-coop/`, and helper/core deployment.
- **Two-way MiSTer sync.** The MiSTer path uses the bundled `NES_z1rr-coop.rbf` core plus `mister-helper.py` to read/write game memory through the same sync engine and relay protocol used by FCEUX and EDN8.
- **Generic release layout.** Emulator builds now land in `dist/emu/` and the bridge app lands in `dist/hardware/`.

## What's new in v2.0 beta 3

- **PRG1 (Rev A) ROM support.** Both PRG0 and PRG1 vanilla Z1 ROMs are supported by the same patch. The title-screen rename is placed in blank padding shared by both revisions.
- **Patch-time validator.** The bridge checks every ROM region the patch needs and refuses to apply if the input has been modified there. Vanilla and clean Z1R seeds apply as before.

## What's new in v2.0 beta 2

- **ROM patch is compatible with more Z1R seeds.** The patch moved to ROM regions that are clean across every Z1R flagset audited.
- **Build/release tooling unified.** `build-all.ps1` builds every endpoint, with artifacts landing in per-endpoint folders under `dist/`.

## Full feature set

### Hardware bridge

A standalone Windows app that lets hardware endpoints play Z1RR-coop through the shared relay. EDN8 and MiSTer are supported in beta8, and either can pair with FCEUX or another hardware bridge endpoint. Works with vanilla Z1, Z1R seeds, or any Z1-derived ROM.

- Choose EDN8 or MiSTer from the first screen.
- EDN8 flow patches the ROM and uploads it to `sd:\z1rr-coop\`.
- MiSTer flow deploys `NES_z1rr-coop.rbf` and `mister-helper.py` over SSH/SCP, then stages ROMs under `/media/fat/games/NES/z1rr-coop/`.
- Talks to the same OCI relay as FCEUX and pairs by session code, so endpoint combinations are interchangeable as long as both clients choose the same mode.
- Auto-reconnects on network drops on both sides.

### Three Z1 modes ported to the bridge

| Mode | Syncs |
|---|---|
| `tloz_basic` | Inventory only: sword, bow, candle, rings, etc. |
| `tloz_progress` | Inventory plus level compasses/maps and triforce pieces |
| `tloz_all` | Inventory, progress, full overworld map, and full dungeon map |

Pick the mode that matches the workload you want; both peers must use the same mode.

## Held back to v2.1

- **Bogie's DIBS! competitive modes** (`tloz_dibs_easy`, `tloz_dibs_medium`, `tloz_dibs_medium_entrances_on`). DIBS! needs a few sync-engine extensions: handler-driven writes to addresses other than the synced one, and structured non-scalar wire values.

## Quick start

### Hardware bridge players

1. Download `z1rr-coop-2.0-beta8-hardware.exe`.
2. For EDN8, plug your EDN8 into your PC over USB and into your NES, then power on the NES. For MiSTer, make sure SSH is enabled and the MiSTer is reachable on your network.
3. Run the .exe. The flow walks you through hardware choice, ROM setup, relay connection, and pairing.
4. For EDN8, launch the patched ROM from the `z1rr-coop` folder on the cart menu. For MiSTer, launch the deployed `NES_z1rr-coop` core and staged ROM.
5. Agree on a session code with your partner, enter it in both clients, and play.

### FCEUX players

1. Download the FCEUX zip that matches your emulator bitness: `z1rr-coop-2.0-beta8-fceux-win32.zip` for 32-bit FCEUX, or `z1rr-coop-2.0-beta8-fceux-win64.zip` for 64-bit FCEUX.
2. Extract the zip anywhere.
3. Launch FCEUX and load your Z1 ROM.
4. From the FCEUX Lua menu, load `coop.lua` from the extracted folder.
5. In the connection dialog: Transport=Relay, Host=`coop.z1rracing.com`, Port=`9999`, Session code=your shared code, Mode=`tloz_basic` / `tloz_progress` / `tloz_all`.
6. Click OK and play.

If your FCEUX build cannot open the dialog and reports `Must call iup.Open in main thread`, edit `coop_config.lua` next to `coop.lua`, set `enabled = true`, enter the shared session code, confirm the `mode` line, save, and reload `coop.lua`.

Beta8 intentionally ships only the Zelda 1 modes listed above. Older emulator-only modes are no longer included in the packaged client.

## Known issues

- If both peers drop simultaneously and the relay's slot times out, reconnects may report `Partner aborted: no partner` until you change the session code or wait for cleanup.
- EDN8 USB servicing can briefly stall during some Z1 transitions; bridge log timeouts during those windows usually self-recover.
- EDN8 `tloz_all` is still the highest-load hardware mode. If you see repeated "Game stopped running" or implausible snapshot warnings, capture the hardware log and relay payload lines.
- Some FCEUX builds cannot show IUP dialogs from Lua. Use the matching win32/win64 package first, then use the `coop_config.lua` fallback if the dialog still cannot open.
- The patched ROM uploaded to your EDN8 is your own copyrighted property. Do not redistribute it. Patch your own seed; share the IPS, not the ROM.

## Reporting issues

GitHub issues: <https://github.com/BogieSmalls/z1rr-coop/issues>

When reporting, include which artifact you used, what mode you were running, what the bridge log and FCEUX console showed, and what you were doing when the issue happened.

## Credits

- **Andi McClure** - original emu-coop framework
- **megmacAttack** - the `tloz_*` Z1 mode files
- **Bogie** - z1rr-coop EDN8/MiSTer bridge, relay deployment, DIBS! competitive modes
- **Warp World** - Crowd Control project, related upstream work in the NES hardware space
- **odelot** - NES_MiSTer fork that opened the path for MiSTer support
