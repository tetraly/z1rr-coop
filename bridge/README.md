# Z1RR-coop Bridge

Connect a real NES endpoint to Z1RR-coop sessions through EverDrive Pro N8 or MiSTer.

## What this is

A Windows app (with macOS support best-effort) that:
- Patches and uploads a Z1 ROM for EDN8
- Deploys the MiSTer helper/core payload and stages an unpatched source ROM for MiSTer
- Talks to the selected device to read/write game memory
- Connects to the Z1RR-coop relay on the internet
- Pairs with FCEUX, EDN8, or MiSTer endpoints through the same relay/session code
- Syncs items, dungeon progress, and other game state in real time

Any two supported endpoints can be paired together through the relay as long as
both clients use the same mode: FCEUX <-> FCEUX, FCEUX <-> EDN8, FCEUX <->
MiSTer, EDN8 <-> MiSTer, EDN8 <-> EDN8, or MiSTer <-> MiSTer.

## Quick start

1. **Download `z1rr-coop-2.0-beta8-hardware.exe`** (single file, ~19 MB).
2. **Run the .exe** and choose `EverDrive Pro N8` or `MiSTer`.
3. For **EDN8**, plug in your cart and follow the patch/upload flow:
   - Pick your Z1 ROM (vanilla or Z1R seed; both work)
   - Auto-detect your EDN8's COM port
   - Choose a session code (any 6+ char string you and your partner agree on)
   - Connect to the relay
4. For **MiSTer**, enter your MiSTer host/IP, keep the default `root` / `1` credentials unless changed, and deploy:
   - `mister-helper.py` to `/media/fat/Scripts/z1rr-coop/`
   - the custom core as `/media/fat/_Console/NES_z1rr-coop.rbf`
   - your source ROM under `/media/fat/games/NES/z1rr-coop/`
5. **Play co-op.**

MiSTer support uses the bundled custom odelot fork core artifact,
`bridge_core/mister_payload/NES_z1rr-coop.rbf`. Source builds can regenerate it
with `build-mister-core.ps1`; the GUI and `mister-deploy` command stop with a
clear missing-core error if it is not present.

## Modes

The bridge currently supports the Zelda 1 modes `tloz_basic`, `tloz_progress`,
and `tloz_all`. Both peers must select the same mode. Future modes will be added
by porting from the FCEUX-side `modes/*.lua` files.

## CLI usage (advanced)

If you don't want the GUI:

```powershell
# Patch a ROM
python -m bridge_cli patch zelda.nes -o zelda_CC.nes

# Run a session
python -m bridge_cli run --mode tloz_all --port COM3 --code mycode

# Check a cart's patch (revision, guarded write); the game in play, not paused
python -m bridge_cli patch-check --port COM3

# Deploy MiSTer helper/core payload
python -m bridge_cli mister-deploy --host 192.168.0.130

# Run a MiSTer session after helper deployment
python -m bridge_cli mister-run --mode tloz_all --mister-host 192.168.0.130 --code mycode --enable-writes
```

## Troubleshooting

### "Could not open COM port"

EDN8 isn't plugged in, or another program is holding the port. Close any other
program using the cart and click Connect again.

### "MiSTer core asset missing locally"

The bridge found the helper payload but not `NES_z1rr-coop.rbf`. Build the custom
odelot NES core fork from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-mister-core.ps1
```

The script copies the output to
`bridge/bridge_core/mister_payload/NES_z1rr-coop.rbf`; deploy again after it
finishes.

### "Partner has incompatible mode (guid mismatch)"

You and your partner picked different modes. Both peers must use the same mode
(e.g., both `tloz_all`).

### "Connection lost (heartbeat timeout)"

Network drop. Bridge auto-reconnects with exponential backoff. Should recover
within ~30 seconds when the network is back.

### My inventory shows a phantom Heart Container or bomb upgrade

This is a known upstream emu-coop quirk (not specific to the bridge). When you and your
partner connect on the title screen and then start a new game, the cache snapshot
can race ahead of the game's initial inventory writes. Workaround: load a save
state of an already-running game on both peers before connecting.

## Development

See the architectural design at
`docs/superpowers/specs/2026-05-06-edn8-bridge-design.md`.

To set up for development:

```powershell
cd bridge
uv sync --extra test --extra dist
uv run python -m pytest tests/ -v
uv run python -m bridge_gui  # run the GUI
uv run python -m bridge_cli --help  # CLI options
```

To build a Windows distributable:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-hardware.ps1
# Output: ..\dist\hardware\z1rr-coop-<version>-hardware.exe
```

### EDN8 polling diagnostics

To isolate lower-level EDN8 read issues without FCEUX or the relay in the loop,
run the bridge polling audit against a running CC-patched ROM:

```powershell
cd bridge
uv run python audit_bridge_polling.py --port COM5 --pattern tloz_all --duration 300 --stop-on-anomaly
uv run python audit_bridge_polling.py --port COM5 --pattern inventory --hz 10 --duration 120 --stop-on-anomaly
uv run python audit_bridge_polling.py --port COM5 --pattern inventory --method array --verbose --duration 5
uv run python audit_bridge_polling.py --port COM5 --pattern inventory --method addrs --verbose --duration 5
uv run python audit_bridge_polling.py --port COM5 --pattern custom --addr 0x065B --length 1 --method array --verbose --duration 5
uv run python audit_bridge_polling.py --port COM5 --pattern custom --addr 0x065B --length 1 --method addrs --verbose --duration 5
uv run python audit_bridge_polling.py --port COM5 --pattern map --hz 2 --duration 120 --stop-on-anomaly
uv run python audit_bridge_polling.py --port COM5 --pattern running --hz 10 --duration 120
```

The audit uses the current checksum-correct `CCClient` framing and prints raw
range bytes when it sees impossible item values or mostly-`FF` range reads.
Use `--method array` to exercise CC Action `0x01` contiguous reads, and
`--method addrs` to exercise CC Action `0x00` individual-address reads over
the same ranges. The `addrs` method is intended for inventory or single-byte
diagnostics; by default the script refuses large `addrs` sweeps such as
`tloz_all` or `map` because those fan out into multiple CC transactions per
tick and can overload the cart.

In normal EDN8 `tloz_all` sessions, reconnect re-sync and incoming map writes
are throttled separately: reconnect state is sent in small chunks, and full
polling pauses briefly while a queued map-write backlog drains.

The hardware session screen also includes a **Diagnostics** tab. EDN8 players
can use **Copy Diagnostics** to share live poll counts, endpoint failures,
incoming/outgoing update counts, map/resync backlog, and last endpoint error.
