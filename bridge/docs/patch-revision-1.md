# EDN8 patch revision 1: guarded write, four freeze slots, flag off $07FE

`bridge/extend_patch.py` rebuilds `bridge_core/patches/zelda_z1rr_coop.ips` (and its
`.expected.json`) from 2.0 beta 8's relocated patch. Co-op's commands, framing and timing are
unchanged; everything below is additive or fixes a byte the patch shared with the game. The
version command's first byte, 0 before, is now 1 (`cc_client.PATCH_REVISION`).

## The changes

| What | Before (beta 8) | Revision 1 | Why |
|---|---|---|---|
| "Message waiting" flag | CPU RAM $07FE | SRAM $7F9E | $07FE is room $7F's saved flags in the levels 7-9 block (`WorldFlags` $077F + $7F). Every command set it to $AB and then $00, erasing that room's visited, key-door and item-taken bits; a save kept the loss. `tloz_all` even syncs it as a room byte. |
| Main-loop hook order | dispatch, then the once-only setup and freeze scan ($B5A7) | setup and freeze scan, then dispatch | SRAM survives a power-off; the setup now clears the flag first, so a command left waiting at power-off is dropped rather than run on the first frame of play. Same 21 bytes. |
| Freeze slots | 8, $7F00-$7F7F | 4, $7F40-$7F7F | $7F00-$7F3F is left to the ROM (ZORA's statistics screen keeps its counters there). Before, the power-on setup cleared $7F00/$7F10/$7F20/$7F30, and the per-frame scan treated any of those bytes reaching $AB as an armed slot and wrote through the bytes after it. Nothing in the bridge sends the freeze command; a fifth freeze is refused cleanly (answer 0). |
| Action $05 | unused (the dispatcher sent actions >= 5 to an `RTS`) | guarded write, $B883-$B8DA in bank 4 | Below. Actions >= 6 still do nothing. |

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
- **When it runs.** Like every command, only on frames where `UpdateHeartsAndRupees` runs
  (play), not on the title screen or during transitions; a command sent then waits, and the next
  frame's message replaces it.

Host side: `CCClient.guarded_write(writes, guards)` returns True, False, or None on a timeout
(unknown: read again before retrying).

## Tests

- `tests/test_extended_patch.py` runs the shipped IPS's bank 4 and bank 7 code on a 6502 (py65,
  in the `test` extra) with the USB FIFO simulated: the flag, the power-off case, co-op's read and
  write commands, the version, the guarded write (holds, fails, no guards, the largest frame,
  actions above 5), the statistics block left alone with every byte $AB, four freezes and a refused
  fifth, and `CCClient` and `bridge_cli patch-check` against the cart's own code. Set
  `Z1RR_VANILLA_PRG0` to a vanilla PRG0 ROM. Against beta 8's IPS, every new test fails and co-op's
  pass.
- The manifest validates PRG0 and PRG1 (`extend_patch.py --prg1` checks every site).

## Hardware check (EverDrive N8 Pro, Windows)

1. Patch a vanilla ROM with this bridge (`python -m bridge_cli patch zelda.nes -o zelda_r1.nes`,
   or the GUI's EDN8 flow) and start a game; stay on the overworld, not paused.
2. `python -m bridge_cli patch-check --port COMx`: it prints the version (first byte `01`) and two
   PASS lines, a guarded write that happens and one that doesn't, on $7F96 (an unused byte of the
   patch's page), which it then restores.
3. Co-op regression: an EDN8 <-> FCEUX `tloz_all` session for at least 15 minutes, through a
   level, a save, a reset and Continue, as for any bridge release.
4. If the ROM is a ZORA seed: play into levels 7-9 and check room $7F keeps its item taken after a
   save (the $07FE fix), and that the statistics screen's times survive a power cycle (the slots).

## For randomizers

ZORA's reserved ranges (`docs/rom-map.md` "Reserved for z1rr-coop") gain bank 4 $B883-$B8DA and
SRAM $7F9E, and lose CPU RAM $07FE and SRAM $7F00-$7F3F.
