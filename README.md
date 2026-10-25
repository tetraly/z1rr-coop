# z1rr-coop

z1rr-coop is a Zelda 1 focused expansion of Andi McClure's emu-coop. It lets two players synchronize game state over the internet so single-player games can be played cooperatively across emulators and hardware.

For the v2.0 release line, the core goal is endpoint interoperability: FCEUX, EverDrive Pro N8, and MiSTer can all participate in the same relay/session system. A player on MiSTer can pair with a player on FCEUX, an EDN8 player can pair with a MiSTer player, two hardware players can pair with each other, and FCEUX-to-FCEUX still works.

Mode files are programs, like `.exe` files. Do not install a mode file unless it came from someone you know and trust.

For official support for z1rr-coop, please visit the Z1R Racing Discord: <https://discord.gg/MX6EB26HYB>

## Supported Endpoints

| Endpoint | How it connects | Notes |
|---|---|---|
| FCEUX | Lua scripts from the emulator package | Uses `coop.lua` inside FCEUX. |
| EverDrive Pro N8 / real NES | PC hardware bridge | The bridge patches/uploads the ROM and talks to the cart over USB. |
| MiSTer | PC hardware bridge plus custom NES core | The bridge deploys `NES_z1rr-coop.rbf`, starts `mister-helper.py`, and stages ROMs over SSH/SCP. |

Any two endpoints can pair together as long as both clients use a compatible release and the same mode:

- FCEUX <-> FCEUX
- FCEUX <-> EDN8
- FCEUX <-> MiSTer
- EDN8 <-> MiSTer
- EDN8 <-> EDN8
- MiSTer <-> MiSTer

The bridge currently supports the Zelda 1 modes `tloz_basic`, `tloz_progress`, and `tloz_all`.

## Connectivity

Older emu-coop releases used IRC as the network backbone. z1rr-coop replaces that with client-agnostic transports:

- **Relay**: both players connect outbound to a small Python relay daemon. This is the normal internet play path and requires no port forwarding.
- **Direct**: peer-to-peer TCP. This is useful for LAN play or private networks such as Tailscale, ZeroTier, or Hamachi.

The relay protocol is endpoint-neutral. FCEUX Lua clients and hardware bridge clients use the same session-code workflow, so the relay does not care whether a peer is FCEUX, EDN8, MiSTer, or a future client.

Packaged clients default to the public relay at `coop.z1rracing.com:9999`.

Self-hosters can run their own relay; see [relay/README.md](relay/README.md).

## PC Hardware Bridge

The hardware bridge is the Windows app that makes real hardware feel like another Z1RR-coop endpoint.

For **EverDrive Pro N8**, the bridge:

- Validates and patches the user's Zelda 1 ROM.
- Uploads the patched ROM to the EDN8 over USB.
- Reads and writes game memory through the EDN8 USB protocol.
- Joins the shared relay session as a normal peer.

For **MiSTer**, the bridge:

- Connects to MiSTer over SSH using the user-provided host/IP.
- Deploys the custom `NES_z1rr-coop.rbf` NES core without overwriting the stock `NES.rbf`.
- Deploys and starts `mister-helper.py`.
- Stages source ROMs under `/media/fat/games/NES/z1rr-coop/`.
- Reads and writes NES memory through the MiSTer helper/core path.

For bridge details, see [bridge/README.md](bridge/README.md).

## Downloads

Current beta8 release:

- Hardware bridge for EDN8 and MiSTer: `dist/hardware/z1rr-coop-2.0-beta8-hardware.exe`
- Emulator package for 32-bit FCEUX: `dist/emu/z1rr-coop-2.0-beta8-fceux-win32.zip`
- Emulator package for 64-bit FCEUX: `dist/emu/z1rr-coop-2.0-beta8-fceux-win64.zip`

Release page:

<https://github.com/BogieSmalls/z1rr-coop/releases/tag/v2.0.0-beta.8>

## Quick Start

### FCEUX

1. Download and extract the FCEUX zip that matches your FCEUX bitness: `fceux-win32.zip` for 32-bit FCEUX or `fceux-win64.zip` for 64-bit FCEUX.
2. Launch FCEUX and load your Zelda 1 ROM.
3. From the FCEUX Lua menu, load `coop.lua`.
4. Choose Relay or Direct, enter the agreed session code, and select the same Zelda 1 mode as your partner.

FCEUX native Lua modules must match the emulator process bitness. Running the 32-bit package under 64-bit FCEUX, or the 64-bit package under 32-bit FCEUX, can fail with a native DLL load error such as `iuplua.dll: %1 is not a valid Win32 application`.

Some 64-bit FCEUX builds run Lua on a thread where IUP cannot open the connection dialog. If the log says `Must call iup.Open in main thread`, edit `coop_config.lua` in the extracted FCEUX package, set `enabled = true`, enter the shared relay session code, confirm the `mode` value, save, and reload `coop.lua`. The packaged fallback config preselects Zelda 1 "sync most things"; change it there if you are testing a different mode.

### EverDrive Pro N8

1. Download and run the hardware bridge.
2. Choose `EverDrive Pro N8`.
3. Select your Zelda 1 ROM.
4. Click `Send patched ROM to EDN8`: the bridge patches the ROM, copies it to the cart and starts it (click it again any time to restart the game), then click `Continue`.
5. Choose the same relay/session code and mode as your partner.

### MiSTer

1. Download and run the hardware bridge.
2. Choose `MiSTer`.
3. Enter the MiSTer host/IP, username, and password.
4. Let the bridge deploy the custom core, helper, and ROM.
5. Launch the deployed `NES_z1rr-coop` core and staged ROM, then connect with the same relay/session code and mode as your partner.

## Project Layout

- `modes/`: Zelda 1 Lua mode files used by the FCEUX-side client.
- `bridge/`: Python hardware bridge, GUI, CLI, EDN8 support, MiSTer support, and bridge tests.
- `relay/`: relay daemon for internet sessions.
- `mister/`: vendored MiSTer NES core project and build notes for `NES_z1rr-coop.rbf`.
- `dist/emu/`: FCEUX release packages.
- `dist/hardware/`: hardware bridge release packages.

## Development

Build all distributables:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-all.ps1
```

Run bridge tests:

```powershell
cd bridge
uv run python -m pytest tests/ -v
```

Build the MiSTer core payload:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-mister-core.ps1
```

## Roadmap

Planned work after the v2.0 stabilization push:

- Add more gameplay modes, including DIBS-style competitive Zelda 1 modes once the bridge-side sync engine supports their extra write semantics cleanly.
- Add additional emulator endpoints, with BizHawk and Mesen as likely next targets.
- Continue reducing setup friction for hardware players.

## Author / License

These files were written by <<andi.m.mcclure@gmail.com>>. The "tloz_" modes (Zelda 1) were written by megmacAttack.

Big thanks to:
* The LTTP Randomizer team, esp. Mike Trethewey, Zarby89 and Karkat, for information
* Alex Zandra, Maya Shinohara, and Andypro1 from github for help testing

Unless otherwise noted, the license is:

	Copyright (C) 2017 Andi McClure

	Permission is hereby granted, free of charge, to any person obtaining a copy
	of this software and associated documentation files (the "Software"), to deal
	in the Software without restriction, including without limitation the rights
	to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
	copies of the Software, and to permit persons to whom the Software is
	furnished to do so, subject to the following conditions:

	The above copyright notice and this permission notice shall be included in
	all copies or substantial portions of the Software.

	THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF
	ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED
	TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
	PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT
	SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR
	ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
	ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
	OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
	OR OTHER DEALINGS IN THE SOFTWARE.

Included in this directory is Penlight. Here is its license:

	Copyright (C) 2009-2016 Steve Donovan, David Manura.

	Permission is hereby granted, free of charge, to any person obtaining a copy
	of this software and associated documentation files (the "Software"), to deal
	in the Software without restriction, including without limitation the rights
	to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
	copies of the Software, and to permit persons to whom the Software is
	furnished to do so, subject to the following conditions:

	The above copyright notice and this permission notice shall be included in
	all copies or substantial portions of the Software.

	THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF
	ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED
	TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
	PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT
	SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR
	ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
	ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
	OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
	OR OTHER DEALINGS IN THE SOFTWARE.

Included in this directory is Luasocket. Here is its license:

	LuaSocket 3.0 license
	Copyright © 2004-2013 Diego Nehab

	Permission is hereby granted, free of charge, to any person obtaining a
	copy of this software and associated documentation files (the "Software"),
	to deal in the Software without restriction, including without limitation
	the rights to use, copy, modify, merge, publish, distribute, sublicense,
	and/or sell copies of the Software, and to permit persons to whom the
	Software is furnished to do so, subject to the following conditions:

	The above copyright notice and this permission notice shall be included in
	all copies or substantial portions of the Software.

	THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
	IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
	FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
	AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
	LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
	FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
	DEALINGS IN THE SOFTWARE.

Included in this directory is IUP. Here is its license:

	Copyright (c) 1994-2017 Tecgraf/PUC-Rio.

	Permission is hereby granted, free of charge, to any person obtaining a
	copy of this software and associated documentation files (the "Software"),
	to deal in the Software without restriction, including without limitation
	the rights to use, copy, modify, merge, publish, distribute, sublicense,
	and/or sell copies of the Software, and to permit persons to whom the
	Software is furnished to do so, subject to the following conditions:

	The above copyright notice and this permission notice shall be included in
	all copies or substantial portions of the Software.

	THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
	IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
	FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
	AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
	LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
	FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
	DEALINGS IN THE SOFTWARE.
