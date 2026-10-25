# EDN8 patch revision 1: guarded write, four freeze slots, flag off $07FE, no speed hook, a safe drain

`bridge/extend_patch.py` builds `bridge_core/patches/zelda_z1rr_coop.ips` (and its
`.expected.json`) from 2.0 beta 8's relocated patch, which is kept beside it as
`zelda_z1rr_coop_beta8.ips` (with its manifest); `tests/test_extended_patch.py` checks the shipped
files rebuild byte for byte. Co-op's commands and framing are unchanged; everything below is
additive, fixes a byte the patch shared with the game, or fixes the drain (the crash found on
hardware: "The NMI drain" below). The
version command's first byte, 0 before, is now 1 (`cc_client.PATCH_REVISION`).

## The changes

| What | Before (beta 8) | Revision 1 | Why |
|---|---|---|---|
| "Message waiting" flag | CPU RAM $07FE | SRAM $7F9E | $07FE is room $7F's saved flags in the levels 7-9 block (`WorldFlags` $077F + $7F). Every command set it to $AB and then $00, erasing that room's visited, key-door and item-taken bits; a save kept the loss. `tloz_all` even syncs it as a room byte. |
| Main-loop hook order | dispatch, then the once-only setup and freeze scan ($B5A7) | setup and freeze scan, then dispatch | SRAM keeps the new flag through a power-off and a reset; the setup clears it, and now runs before the dispatcher. Same 21 bytes. See "Power-on and reset" below for what this does and doesn't cover. |
| Freeze slots | 8, $7F00-$7F7F | 4, $7F40-$7F7F | $7F00-$7F3F is left to the ROM (ZORA's statistics screen keeps its counters there). Before, the power-on setup cleared $7F00/$7F10/$7F20/$7F30, and the per-frame scan treated any of those bytes reaching $AB as an armed slot and wrote through the bytes after it. Nothing in the bridge sends the freeze command; a fifth freeze is refused cleanly (answer 0). |
| Action $05 | unused (the dispatcher sent actions >= 5 to an `RTS`) | guarded write, $B883-$B8DA in bank 4 | Below. Actions >= 6 still do nothing. |
| NMI drain | bank 4 $B46F: one status check, then the frame's length and that many bytes; no checksum check | bank 4 $BA40 ($B46F jumps there): the status before every byte, at most 8 bytes per NMI, the frame carried over to the next NMI, lengths outside 3-95 refused; the hook checks the checksum ($BAC0) before dispatching | A frame still arriving was misread and could copy past $7FFF into the MMC1 registers: garbled HUD and palettes, then a crash. Below. |
| Speed hook | `InitLinkSpeed`'s first two instructions (bank 5 $B366) call a helper at bank 5 $B8F0 that takes Link's speed from $7F93 while $7F92 is $AB (a Crowd Control effect) | vanilla bytes at both | Nothing in z1rr-coop sets $7F92. A randomizer's speed features hook those same instructions (ZORA's Pegasus Boots and Red Bubble Curse: Slow Speed), so with the hook a ZORA ROM using them failed the bridge's manifest check. Bank 5 is now untouched. |

## The NMI drain (the crash found on hardware)

**What players saw** (2026-10-24, EverDrive N8 Pro on OS 2.15, through a Game Genie; beta 8 and
revision 1 alike): with a partner connected, or with nothing but
`audit_bridge_polling.py --pattern running --hz 10` (1-byte reads), swinging the sword or firing
beams soon garbled the HUD (a stray "0 x" at the top left, a tan minimap), sometimes shook the
screen, and then reset or crashed the game. Single player, and the bridge with no partner, were
clean. A timeout came with the glitch: one frame got no answer.

**Why.** Beta 8's drain (bank 4 $B46F, run first thing in every NMI) checked the FIFO's status
once, read the frame's length L, and then read L bytes into $7FA0 without looking at the status
again. A frame arrives over USB a few bytes at a time; when the NMI caught one part-way, the drain
read garbage for the bytes not yet there, the frame got no answer (the timeout), and its remaining
bytes stayed in the FIFO for the next NMI to take as a new length, up to 255. From $7FA0, any
length over 96 copies past $7FFF (the game's `SaveRamEnd` marker) into $8000 and up, which on
Zelda's MMC1 are mapper register writes: mirroring and CHR and PRG banks. Even a shorter wrong
length made the drain copy that many bytes at the start of vblank, crowding out the game's own
screen updates (the shake), and the dispatcher ran whatever command the garbage spelled, since
nothing checked the checksum. A sword swing is a busy frame, which is when the game has least
vblank to spare. krikzz's own FIFO reader checks the status before every byte (EDN8-PRO
`edio/everdrive_asm.s`, `bmi` on `REG_FIFO_STAT` bit 7, `FIFO_CPU_RXF`).

**Revision 1's drain** (bank 4 $BA40-$BABF; $B46F is now a `JMP` to it, the NMI hook in bank 7
unchanged):
- reads a byte only while the status's bit 7 is clear, and keeps the frame it has started in SRAM
  ($7F97 length, $7F98 bytes read), so the next NMI finishes it;
- refuses a length under 3 or over 95: the frame then ends at $7FFE at most, and the byte is
  dropped;
- drops a frame that got no byte for a whole NMI (the FIFO empty as the NMI starts), and state a
  power-off left in SRAM that isn't a frame in progress;
- reads at most 8 bytes per NMI (one co-op frame is 7), at 28 cycles a byte, so it takes about as
  much of vblank as beta 8's did for one frame, and never more; the largest frame (95 bytes, a
  guarded write) takes 12 NMIs;
- sets the flag only when the whole frame is in, with its length in $7F99; the main-loop hook calls
  the checksum check (bank 4 $BAC0) in place of the dispatcher, which runs the frame only if its
  last byte is the sum of the others mod 256 (`cc_client._make_cc_frame`), and otherwise drops it
  without an answer.

The host now refuses to build a frame longer than 95 bytes (`cc_client.MAX_FRAME_LENGTH`), and
`read_addrs` splits at 45 addresses (`MAX_READ_ADDRS`; it used 100, a 204-byte frame, which beta 8's
drain also copied past $7FFF). Co-op only ever sends 7-byte frames.

## Action $05: guarded write

Payload: `G`, then G guards `(addr lo, addr hi, value)`, then `W`, then W writes
`(addr lo, addr hi, value)`. In one game frame the cart compares every guard and, only if all
match, makes every write. It answers with one value byte through the freeze command's reply:
`1` written, `0` a guard failed and nothing was written.

- **Atomic with respect to the game.** The dispatcher runs inside Zelda's NMI (the hook sits in
  `UpdateHeartsAndRupees`), and Zelda keeps NMIs off while its handler runs, so no game code runs
  between the compare and the write.
- **Guards read their address**, so put them on RAM only, never on a register with read side
  effects ($2002, $4016, ...).
- **Size.** The NMI drain copies MID..checksum to $7FA0 and up; $7FFF is the game's own
  `SaveRamEnd` marker ($A5), and if it is lost, the game clears $6530-$7FFF at the next power-on.
  `CCClient.send_guarded_write` refuses a frame that would reach it: at most 30 guards and writes
  together. Archipelago's ZORA client needs at most 12 guards and 3 writes.
- **When it runs.** Like every command, only on frames where `UpdateHeartsAndRupees` runs (play,
  paused included), not on the title screen, the menus or during transitions; a command sent then waits, and the next
  frame's message replaces it.

Host side: `CCClient.guarded_write(writes, guards)` returns True, False, or None on a timeout
(unknown: read again before retrying).

## Power-on and reset

The flag now lives in battery-backed SRAM, which neither a reset nor a power-off clears; beta 8's
flag was in CPU RAM that the game's `ClearRam` zeroes. The patch's once-only setup clears it, but
the setup runs only while its latch, CPU RAM $07FF, isn't $AB, and `ClearRam` stops at $07FE:

- **Power-on:** CPU RAM comes up with arbitrary values, so the setup runs and drops a command
  left waiting when the console was switched off, unless $07FF happens to power up as $AB, as it
  could already under beta 8 (about 1 in 256; console-dependent).
- **Reset:** $07FF survives, so the setup doesn't run again: a command waiting when Reset was
  pressed (sent outside play, where commands wait) runs on the first frame of play afterwards.
  Beta 8 dropped it. In co-op this is almost always a read, whose late answer the bridge ignores
  (its MID is stale); the bridge sends writes only while it sees the game running. A guarded write
  re-checks its guards when it runs.
- **Relaunching from the EverDrive menu** (or the bridge's auto-launch) may behave like a reset:
  whether it keeps $07FF, and whether the cart's saved SRAM brings back $7F9E = $AB with a stale
  message, is for the hardware check. The same rule applies: a stale command, almost always a read,
  runs once on the first frame of play.

## Earlier patches and mixed carts

- **ROMs patched by beta 8** are recognized (`bridge_core/patch_revisions.py`): the bridge writes
  back the original bytes from beta 8's manifest, then applies this patch, and saves the result as
  `<name>_emucoop.nes` (an existing `<name>_emucoop.nes` is replaced, not suffixed again). The beta 8
  bridge refuses a ROM patched by this one.
- **A beta 8 cart's $07FE**, its flag byte, reads $AB during every command, so `tloz_all` would OR
  that into the partner's room $7F of levels 7-9 (as it does today with any beta 8 EDN8). The EDN8
  endpoint (`bridge_core/cc_endpoint.py`) asks the cart's revision with the version command once
  the game is in play and leaves $07FE out of the snapshot until the cart says revision 1 or later.
  A beta 8 bridge still sends its own cart's $AB there when its partner reconnects (its resync
  resends every non-zero byte), so the sync engine ignores an incoming $07FE of exactly $AB
  (`sync_engine.REVISION_0_FLAG`); any other value is synced as before.

## Known, unchanged from beta 8

- **Hearts refill twice as fast on an EDN8.** The main-loop hook is reached by `JSR` from
  `UpdateHeartsAndRupees` ($ED8E) but ends with `JMP $FAE4`, the displaced original code, which
  then returns to $ED91, whose `JMP $FAE4` runs it again: `World_FillHearts` and
  `World_ChangeRupees` run twice a frame. Measured in ZORA's emulator: `HeartPartial` gains 48 in
  4 frames of a refill with the patch (beta 8 and revision 1 alike), 24 without. Rupees count at
  the normal rate, because `World_ChangeRupees` returns when the frame's tile buffer is taken.
  Ending the hook with `RTS` instead would fix it; that changes EDN8 play, so it is left to the
  maintainer.

## Tests

- `tests/test_extended_patch.py` runs the shipped IPS's bank 4 and bank 7 code on a 6502 (py65,
  in the `test` extra) with the USB FIFO simulated: the flag, the power-off case, co-op's read and
  write commands, the version, the guarded write (holds, fails, no guards, the largest frame,
  actions above 5), the statistics block left alone with every byte $AB, four freezes and a refused
  fifth, bank 5 left exactly as vanilla, and `CCClient` and `bridge_cli patch-check` against the
  cart's own code; also the shipped IPS and manifest rebuilding from beta 8's, and a beta 8 ROM
  re-patched to exactly the vanilla-patched ROM. Set `Z1RR_VANILLA_PRG0` to a vanilla PRG0 ROM. Against beta 8's IPS, every new test fails and co-op's
  pass.
- The drain's own tests feed the FIFO the way USB does: a frame finished in the next NMI, one that
  stops arriving (dropped, the next one works), a length of $C8 followed by other frames' bytes
  ($7FFF and $8000-$80FF untouched), a wrong checksum (nothing runs), the largest frame (12 NMIs,
  never more than 8 reads in one), and SRAM state a power-off left. The simulated FIFO fails a test
  that reads its data with nothing there. Against beta 8's IPS the first five fail, four of them
  on that read.
- `tests/test_cc_endpoint.py`: $07FE kept on a revision-1 cart, left out on a beta 8 one.
- The manifest validates PRG0 and PRG1 (`extend_patch.py --prg1` checks every site).
- What the py65 harness can't show: it maps only banks 4 and 7 and stubs the game's NMI and the
  hook's continuation, so bank switching, NMI timing, the game's own use of zero page $00-$05,
  reset and power-on, and EverDrive SRAM saving are for the hardware check.

## Hardware check (EverDrive N8 Pro, Windows)

1. Patch a vanilla ROM (or one beta 8 patched) with this bridge (`python -m bridge_cli patch zelda.nes -o zelda_r1.nes`,
   or the GUI's EDN8 flow) and start a game; stay in play (pausing is fine).
2. `python -m bridge_cli patch-check --port COMx`: it prints the version (first byte `01`) and two
   PASS lines, a guarded write that happens and one that doesn't, on $7F96 (an unused byte of the
   patch's page), which it then restores.
3. Co-op regression: an EDN8 <-> FCEUX `tloz_all` session for at least 15 minutes, through a
   level, a save, a reset and Continue, as for any bridge release.
4. Relaunch the game from the EverDrive menu with the bridge running, and check the first poll
   after it behaves (at most one stale read answered or timed out).
5. The drain: with a game in play and no partner, run
   `uv run python audit_bridge_polling.py --port COMx --pattern running --hz 10 --duration 600 --abort-after-timeouts 1000 --verbose`
   and swing the sword (Up+A too) and fire beams for 10 minutes. Beta 8 garbled the HUD within a few
   swings; revision 1 should show no glitch or shake (a rare timeout away from screen changes is a
   frame dropped, not a fault). Then the same with a partner connected.
6. If the ROM is a ZORA seed: play into levels 7-9 and check room $7F keeps its item taken after a
   save (the $07FE fix), and that the statistics screen's times survive a power cycle (the slots).
   With Pegasus Boots on, the bridge now accepts the ROM and the boots run (the speed hook).

## For randomizers

A randomizer that keeps this patch's bytes clear now also needs bank 4 $B883-$B8DA (the guarded
write), bank 4 $BA40-$BAFF (the drain and the checksum check; $BAD9-$BAFF kept free for it) and
SRAM $7F97-$7F99 and $7F9E, and no longer needs CPU RAM $07FE, SRAM $7F00-$7F3F, or bank 5's
$B366-$B369 and $B8F0-$B901 (while beta 8's bridge is in use, keep those as well). Z1R's flagsets
were audited against beta 8's ranges only (`relocate_patch.py`); about 45 local Z1R and ZORA seeds
showed no conflict at $B883-$B8DA, but the full flagset matrix has not been checked. ZORA keeps
the list in its `docs/rom-map.md` (ZORA's repository).
