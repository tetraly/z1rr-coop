# CC patch architecture (reverse-engineered)

> **Revision 1:** the shipped patch now has a guarded-write command, four freeze slots at $7F40-$7F7F its flag at $7F9E instead of $07FE, and no speed hook in bank 5; see `patch-revision-1.md`. The layout below is the original Crowd Control patch's, before relocation.

> **2026-05-06 update:** All "patch experiments" pursued in this doc (h1 — remove `JSR $B58D`; h2 — register save/restore; the larger "the patch is fundamentally broken for sustained polling" framing) were chasing a host-side bug. The actual root cause was a missing checksum byte in our framing. With correct framing the patch sustains 10 Hz polling indefinitely. See `cc-patch-capabilities.md` "Postmortem" section. The disassembly below is preserved for future reference but the **action items at the end of this doc are no longer needed**.


Captured 2026-05-06 by disassembling `bridge/bridge_core/patches/zelda_cc.ips` against the unpatched `Legend of Zelda, The (USA).nes` ROM. Disassembly tool: `bridge/analyze_cc_patch.py` (minimal 6502 disassembler + IPS-region viewer).

This document records what we know about how the existing CC patch is structured, so it can be modified or replaced for emu-coop's continuous-sync use case.

## Layout of the IPS

22 regions, 1227 bytes of modified/new code. Distribution:

| File offset | Bank | CPU range | Bytes | Purpose |
|---|---|---|---|---|
| 0x006243 | 1 | $A233 | 3 | Hook into bank-1 game code (JSR $A430) |
| 0x006440 | 1 | $A430 | 26 | Item-read indirection helper |
| 0x017376 | 5 | $B366 | 4 | Hook into bank-5 (JSR $B8F0) |
| 0x017900 | 5 | $B8F0 | 18 | Item-write indirection helper |
| 0x01AAFC | 6 | $AAEC | 13 | Misc bank-6 patch |
| 0x01B2A0 | 6 | $B290 | 38 | **NMI inbound: drain USB FIFO into RAM at $7FA0+** |
| 0x01B3A0 | 6 | $B390 | 21 | **Main-loop hook: check ready flag, dispatch** |
| 0x01B4A0 | 6 | $B490 | **517** | Dispatcher prologue + handlers cluster |
| 0x01B700 | 6 | $B6F0 | 77 | Secondary hook (entered from $E65A) |
| 0x01B8A0 | 6 | $B890 | 39 | Action $FF (NullMsg / heartbeat) handler |
| 0x01B920 | 6 | $B910 | 64 | Action $FE (Version) handler |
| 0x01B9A0 | 6 | $B990 | 70 | Read-individual-addrs handler ($00) |
| 0x01BAA0 | 6 | $BA90 | 80 | Array-read handler ($01) |
| 0x01BBA0 | 6 | $BB90 | 72 | Write-pairs handler ($02) |
| 0x01BCA0 | 6 | $BC90 | 78 | Array-write handler ($03) |
| 0x01BDA0 | 6 | $BD90 | 12 | Action jump table (16-bit pointers) |
| 0x01BEA0 | 6 | $BE90 | 49 | **Dispatcher: read action byte, JMP through table** |
| 0x01E66A | 7 | $E65A | 4 | Hook into bank-7 (JSR $B6F0) |
| 0x01ED9A | 7 | $ED8A | 17 | **Main-loop hook injection point + response prelude data at $ED94** |
| 0x01FAF4 | 7 | $FAE4 | 11 | **Displaced original code (JSR $B1E6 / JMP $6CC0)** |
| 0x01FFD0 | 7 | $FFC0 | 8 | **NMI hook entry** |
| 0x02000A | 7 | $FFFA | 6 | **NMI vector rewrite ($E484 → $FFC0)** |

## Control flow

### NMI path (every vblank)

```
[Original NMI vector $E484 redirected to $FFC0]

$FFC0 (NMI hook entry, bank 7):
    LDA #$0E             ; bank value (bank 6 + control bits)
    JSR $FFAC            ; Z1's bank-switch routine
    JMP $B290            ; into bank 6 USB-input drain

$B290 (USB FIFO drain, bank 6):
    LDA $40F1            ; read EDIO USB status register
    BMI ->done           ; if no message ready (bit 7 set), skip
    CMP #$40
    BEQ ->done           ; if status == $40, skip
    LDA $40F0            ; read length byte from FIFO data register
    STA $CF
    LDA #$AB
    STA $07FE            ; set "comm-ready" flag in NES RAM
    LDX #$00
loop:
    CPX $CF
    BEQ ->done
    LDA $40F0            ; read FIFO byte
    STA $7FA0,X          ; store in cart SRAM buffer
    INX
    JMP loop
done:
    JMP $E484            ; chain to original Z1 NMI handler
```

**Per-frame NMI cost when no command pending:** ~12 cycles (3 instructions + JMP + chain).
**Per-frame NMI cost when command arriving:** 12 + (N * ~12) cycles for N-byte message, plus chain. For typical 8-byte read request: ~120 cycles (~67 μs at 1.79 MHz) — well within vblank's 2273-cycle budget.

The NMI hook does **not** appear to be the bottleneck.

### Main-loop path (every game-loop iteration)

The patch injects itself into Z1's main loop by overwriting bytes at $ED8E (originally `JSR $B1E6 ; JMP $6CC0`, which called bank-5 game logic and continued the loop). The new sequence:

```
$ED8B: JSR $FFAC          ; bank switch (was already there)
$ED8E: JSR $B390          ; CC main-loop hook (was JSR $B1E6)
$ED91: JMP $FAE4          ; continue to displaced original (was JMP $6CC0)

$B390 (main-loop hook, bank 6):
    LDA $07FE             ; check comm-ready flag
    CMP #$AB
    BNE ->skip            ; no command → skip dispatcher
    JSR $BE90             ; **dispatcher** (only when command present)
    LDA #$00
    STA $07FE             ; clear flag
skip:
    JSR $B58D             ; **always called** (helper inside the 517-byte region)
    JMP $FAE4

$FAE4 (displaced original code):
    LDA #$05
    JSR $FFAC             ; bank-switch back to bank 5
    JSR $B1E6             ; original game logic call
    JMP $6CC0             ; original loop continuation
```

**Per-frame overhead when no command pending:** ~5 cycles for the flag check + always-on `JSR $B58D` (whose body we haven't fully disassembled — probably ≤30 cycles). Negligible.

**Per-frame overhead when command present:** dispatcher at $BE90 reads the action byte from the input buffer, indexes into the jump table at $BD90, and JMPs through `($0000)` to a handler. Handler walks the input buffer reading args, writes response bytes to FIFO at $40F0. For a 1-byte read response: ~50-100 cycles in the handler + ~100 cycles in surrounding overhead. Still <500 cycles total — doesn't approach a full-frame budget.

### Dispatcher

```
$BE90 (action dispatcher):
    LDX #$00
    LDA $7FA0,X           ; first byte = msgID
    STA $03
    INX
    LDA $7FA0,X           ; second byte = action
    STA $04
    INX
    CMP #$FF              ; FF = NullMsg
    BNE ?
    JMP $B890
    CMP #$FE              ; FE = Version
    BNE ?
    JMP $B910
    CMP #$06              ; clamp action to 0..5
    BCC ok
    LDA #$05
ok:
    ASL A                 ; ×2 for 16-bit table index
    TAY
    LDA $BD90,Y           ; load handler low byte
    STA $00
    LDA $BD91,Y           ; load handler high byte
    STA $01
    JMP ($0000)           ; indirect JMP to handler
```

Jump table at $BD90 has entries for actions 0x00-0x05. Per `_CommFormat.txt`:
- $00: Reads (handler at $B990)
- $01: ArrayReads ($BA90)
- $02: Writes ($BB90)
- $03: ArrayWrites ($BC90)
- $04: Freeze (handler unknown — probably in the 517-byte region)
- $05: CommReturn / null (handler unknown)

### Secondary hook ($E65A → $B6F0)

The patch injects `JSR $B6F0` at CPU $E65A in bank 7, displacing some original Z1 code. We haven't fully disassembled what $B6F0 does (77 bytes). It's separate from the main-loop hook and likely supports the Freeze (action 0x04) feature, which conditionally writes memory based on watched values.

## Where the actual frame-cost comes from

Reading the per-instruction overhead, **the patch's per-frame cost is negligible whether or not a command is being processed**. It does not look like the dispatcher or any handler we've inspected ever monopolizes a full frame's CPU budget on its own.

But our audits empirically showed:

1. Each USB read response takes ~16ms = exactly one NES frame.
2. ~100 sustained reads crash the game.

The 16ms latency is **wait time, not work time** — host sends a request, waits for the next NMI to drain it, waits for the next main-loop iteration to dispatch and respond. That's 1-2 frames of wall-clock time, not 1-2 frames of CPU starvation.

So the real question — which our analysis hasn't yet answered — is **what cumulative side effect of repeated CC servicing crashes the game?** Hypotheses worth testing in priority order:

1. **`JSR $B58D` (always called from main-loop hook).** This 30-byte block somewhere in the big 517-byte region runs every single frame whether or not we have USB activity. If it has any timing-sensitive interaction with PPU/sprite state, it would gradually corrupt the game even with no commands. **Test:** patch out the `JSR $B58D` call and see whether the game survives sustained polling. If yes — the always-called helper is the culprit.

2. **Bank-switch leaking state.** The NMI hook does `LDA #$0E ; JSR $FFAC` to swap in bank 6. The chain back to original NMI ($E484) assumes some specific bank state. If $FFAC doesn't restore the bank perfectly when the original handler runs, bank-mapped resources could go stale. **Test:** check whether $FFAC is symmetric (saves and restores prior bank) or one-way (just sets new bank). If one-way, that's a bug under heavy use.

3. **RAM at $07FE collision.** The patch uses NES work-RAM byte $07FE as the comm-ready flag. Z1 may use that byte for game state. If so, the patch occasionally clobbering it with $00 / $AB confuses the game. **Test:** check whether unmodified Z1 ever writes $07FE.

4. **Buffer at $7FA0+ overlap with cart SRAM the game uses.** $7FA0-$7FFF is in cart SRAM ($6000-$7FFF). If Z1 stores save-game data or temp state there, the patch is overwriting it on every USB message. **Test:** check Z1's SRAM map to see what lives at $7FA0+.

5. **Stack growth.** Each NMI pushes 3 bytes; the patch adds JSRs that push 4 more. If anything fails to RTI/RTS cleanly, the stack could climb over time. **Test:** add stack pointer logging to monitor across sustained polling.

## Implications for option #2 (author a co-op-friendly patch)

**Yes, we have enough information to attempt this.** What we'd need to add to the workflow:

- **6502 assembler:** ca65 (part of cc65) is the standard. Pure-Python options: `asm6` via PyPI, or hand-assembled bytes for small mods.
- **IPS generator:** a few dozen lines of Python (`bridge_core/ips.py` already has the parse side; the inverse — given pre/post ROM, emit IPS — is straightforward).
- **Iteration loop:** modify ASM → assemble → diff with original → emit IPS → user copies patched ROM to EDN8 → user runs sustained-polling test → report results.

**Recommended first experiment:** isolate the `JSR $B58D` hypothesis. The minimal change is to replace those 3 bytes (`20 8D B5`) with `EA EA EA` (three NOPs) and re-test the audit. If the game survives sustained polling without that call, we know the always-called helper is the issue and can investigate what $B58D actually does. That's a 5-minute change and the cleanest discriminator.

If $B58D is **not** the issue, the next experiment is to throttle the dispatcher: maintain a frame counter and only let `JSR $BE90` fire every Nth frame. Effectively rate-limits the per-game-frame USB processing budget. Concrete change: replace `B397: JSR $BE90` with a small block that decrements a counter and only calls $BE90 when zero.

Both experiments require an assembler/IPS-builder pipeline, which is the next infrastructure to build before doing any patch surgery.

## Next steps if pursuing this

1. **Build the patch toolchain:**
   - Add a Python module `bridge_core/ips_build.py` with `make_ips(orig_bytes, patched_bytes) -> bytes`.
   - Optional: add a thin wrapper around an external assembler, or hand-assemble small mods inline (the 5-byte changes proposed above don't need a full assembler).
   - Add a small CLI: `bridge_cli patch-experiment --base zelda.nes --apply zelda_cc.ips --modify <spec> -o new_cc.ips`.

2. **Hypothesis-test loop:**
   - Hypothesis 1: NOP out `JSR $B58D` (3 bytes at $B397). Build modified IPS. User flashes/runs. User runs sustained-polling audit.
   - If hypothesis fails, move to hypothesis 2 (throttle dispatcher), then 3, 4, 5 in order.

3. **Fully disassemble the 517-byte region at $B490.** It contains $B58D (the always-called helper) plus the dispatcher prologue plus possibly the action $04 Freeze handler. Understanding what's in this block is required for any non-trivial modification.

4. **Authoring path:** once we know the bottleneck, the actual fix is likely a small (1-50 byte) modification, not a wholesale rewrite. Keep the existing patch's work-RAM layout and FIFO conventions; just make the per-frame cost cooperative.

## Tools

- `bridge/analyze_cc_patch.py` — IPS region viewer + minimal 6502 disassembler. Run as `uv run python analyze_cc_patch.py <orig.nes>`. Pass `--region 0xNNNNNN` to focus on one region. Pass `--no-disasm` for raw byte dumps.

## References

- `bridge/docs/cc-patch-capabilities.md` — empirical audit results that motivated this analysis
- [WarpWorld/NES-Hardware-Example-Punchout](https://github.com/WarpWorld/NES-Hardware-Example-Punchout) — full 6502 ASM source for an analogous CC patch, useful as cross-reference
- `_CommFormat.txt` from the WarpWorld repo — N8 wire-protocol spec
