"""Extend the z1rr-coop patch (2.0 beta 8's relocated IPS) for Archipelago on real hardware.

Three changes, all inside the patch's own code in bank 4, and none to how co-op works:

1. **The "message waiting" flag moves off $07FE** to $7F9E, a byte of the patch's own SRAM page
   that nothing else uses. $07FE is also the saved flag byte of room $7F in the levels 7-9 block
   (WorldFlags $077F + $7F): every command used to erase that room's visited, key-door and
   item-taken bits, and a save kept the loss.
   SRAM keeps its value through a power-off, so the main-loop hook now runs the patch's once-only
   setup (the $07FF latch) before the dispatcher, and that setup clears the flag: a command left
   waiting when the console was switched off is dropped, not run on the first frame of play.
2. **The freeze slots shrink from eight to four**, from $7F00-$7F7F to $7F40-$7F7F, so that
   $7F00-$7F3F is free for a randomizer's battery-backed data (ZORA's statistics screen). Neither
   the bridge nor any co-op mode sends the freeze command; a fifth freeze is refused, as a ninth
   was before. The setup clears the four slots' first bytes as it cleared the eight.
3. **A new command, $05: guarded write.** Payload: G, then G guards (address lo, hi, value), then
   W, then W writes (address lo, hi, value). In one game frame it compares every guard's address
   with its value and, only if all match, makes every write; it answers one byte, 1 (written) or
   0 (a guard failed, nothing written). Archipelago's client gives items this way, so a pickup
   between its read and its write can't be overwritten or an item given twice. Guards read their
   address, so they belong on RAM, never on a register with read side effects.

The version command's answer changes from 00 00 10 10 08 to 01 00 10 10 08: the first byte, 0 in
the Crowd Control original, is this patch's revision.

The script applies the current IPS to vanilla PRG0, checks every byte it changes still holds the
beta 8 value (so it refuses to run twice, or on another patch), and rewrites the IPS and its
expected-vanilla manifest in place.

Usage:
    python extend_patch.py <vanilla PRG0 .nes> [--prg1 <vanilla PRG1 .nes>]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from bridge_core.ips import apply, parse
from bridge_core.ips_build import make_ips

PATCH_PATH = Path(__file__).parent / "bridge_core" / "patches" / "zelda_z1rr_coop.ips"
VANILLA_SHA1S = {
    "prg0": "dab79c84934f9aa5db4e7dad390e5d0c12443fa2",
    "prg1": "3701381a82fc7d52b2dd3e8892047b30a114ab43",
}

INES_HEADER = 16
BANK_SIZE = 0x4000
SWAP_WINDOW = 0x8000
BANK_4 = 4

PATCH_REVISION = 0x01

# RAM the patch uses
OLD_FLAG = 0x07FE
NEW_FLAG = 0x7F9E
MESSAGE_BUFFER = 0x7FA0              # MID, action, payload, checksum, as the NMI drain stores them
FREEZE_SLOTS_START = 0x40            # offset into $7F00; was $00 (eight slots)
ZP_POINTER = 0x00                    # $00-$01, the patch's indirect pointer
ZP_COUNT = 0x02
ZP_ACTION = 0x04                     # the dispatcher keeps the MID in $03 and the action in $04
ZP_RESULT = 0x05                     # the value byte the freeze reply sends

GUARDED_WRITE_ACTION = 0x05
GUARDED_WRITE_ORIGIN = 0xB883        # bank 4, right after the beta 8 patch's last byte
GUARDED_WRITE_LIMIT = 0xB900         # first byte ZORA uses after it (docs/rom-map.md, fp-prog-02)
FREEZE_REPLY = 0xB52C                # the freeze handler's reply: prelude, size 4, MID, action, $05
JUMP_TABLE_SLOT_5 = 0xB84C           # the dispatcher's sixth pointer (it sends actions >= 5 there)


def bank4_offset(cpu: int) -> int:
    return INES_HEADER + BANK_4 * BANK_SIZE + (cpu - SWAP_WINDOW)


def le16(value: int) -> bytes:
    return bytes((value & 0xFF, value >> 8))


@dataclass(frozen=True)
class Edit:
    """Bytes at a bank-4 CPU address: what the beta 8 patch holds there, and what they become."""
    cpu: int
    old: bytes
    new: bytes
    why: str


NOP3 = bytes((0xEA, 0xEA, 0xEA))

EDITS: tuple[Edit, ...] = (
    # 1. the flag: the NMI drain's STA, then the main-loop hook, rewritten in place (same 21 bytes)
    Edit(0xB47F, b"\x8d" + le16(OLD_FLAG), b"\x8d" + le16(NEW_FLAG), "NMI drain: STA flag"),
    Edit(0xB495,
         bytes.fromhex("adfe07c9abd008204eb8a9008dfe0720a7b54ce4fa"),
         b"\x20" + le16(0xB5A7)                 # JSR setup + freeze scan (was after the dispatch)
         + b"\xad" + le16(NEW_FLAG)             # LDA flag
         + bytes.fromhex("c9abd008204eb8a900")   # CMP #$AB / BNE +8 / JSR dispatch / LDA #0
         + b"\x8d" + le16(NEW_FLAG)             # STA flag
         + b"\x4c" + le16(0xFAE4),              # JMP to the displaced original code
         "main-loop hook: setup first, flag at $7F9E"),
    # 2. the freeze slots: the three scans start at $7F40
    Edit(0xB558, b"\xa2\x00", bytes((0xA2, FREEZE_SLOTS_START)), "freeze: find the slot to update"),
    Edit(0xB57C, b"\xa2\x00", bytes((0xA2, FREEZE_SLOTS_START)), "freeze: find a free slot"),
    Edit(0xB5CD, b"\xa2\x00", bytes((0xA2, FREEZE_SLOTS_START)), "per-frame freeze scan"),
    # the setup's clears: $7F00 becomes the flag; $7F10-$7F30 are no longer slots
    Edit(0xB5B5, b"\x8d\x00\x7f", b"\x8d" + le16(NEW_FLAG), "setup: clear the flag"),
    Edit(0xB5B8, b"\x8d\x10\x7f", NOP3, "setup: $7F10 is not a slot"),
    Edit(0xB5BB, b"\x8d\x20\x7f", NOP3, "setup: $7F20 is not a slot"),
    Edit(0xB5BE, b"\x8d\x30\x7f", NOP3, "setup: $7F30 is not a slot"),
    # 3. the guarded write: the jump table's sixth pointer, then the handler in free space
    Edit(JUMP_TABLE_SLOT_5, le16(0xB841), le16(GUARDED_WRITE_ORIGIN), "dispatch: action $05"),
    # the version answer's first byte
    Edit(0xB6FC, b"\xa9\x00", bytes((0xA9, PATCH_REVISION)), "version: patch revision"),
)


# --- a small assembler: just the instructions the guarded write uses -------------------------------

OPCODES = {
    ("LDA", "imm"): 0xA9, ("LDA", "zp"): 0xA5, ("LDA", "absx"): 0xBD, ("LDA", "indy"): 0xB1,
    ("STA", "zp"): 0x85, ("STA", "indy"): 0x91,
    ("LDY", "imm"): 0xA0, ("CMP", "imm"): 0xC9, ("CMP", "absx"): 0xDD,
    ("INX", "imp"): 0xE8, ("INC", "zp"): 0xE6, ("DEC", "zp"): 0xC6,
    ("BEQ", "rel"): 0xF0, ("BNE", "rel"): 0xD0,
    ("JMP", "abs"): 0x4C, ("RTS", "imp"): 0x60,
}
OPERAND_SIZE = {"imp": 0, "imm": 1, "zp": 1, "indy": 1, "rel": 1, "abs": 2, "absx": 2}


def assemble(origin: int, program: list[str | tuple]) -> bytes:
    """Two passes: label addresses, then bytes. A program item is a label (str) or
    (mnemonic, mode[, operand]), the operand a number or a label."""
    labels: dict[str, int] = {}
    pc = origin
    for item in program:
        if isinstance(item, str):
            labels[item] = pc
        else:
            pc += 1 + OPERAND_SIZE[item[1]]
    out = bytearray()
    for item in program:
        if isinstance(item, str):
            continue
        mnemonic, mode, *rest = item
        out.append(OPCODES[(mnemonic, mode)])
        operand = labels[rest[0]] if rest and isinstance(rest[0], str) else (rest[0] if rest else None)
        if mode == "rel":
            displacement = operand - (origin + len(out) + 1)
            if not -128 <= displacement <= 127:
                raise ValueError(f"branch to {rest[0]} out of range")
            out.append(displacement & 0xFF)
        elif OPERAND_SIZE[mode] == 1:
            out.append(operand)
        elif OPERAND_SIZE[mode] == 2:
            out += le16(operand)
    return bytes(out)


def _triple_from_buffer() -> list[tuple]:
    """$00-$01 = the address at buffer+X, A = the value after it."""
    return [("LDA", "absx", MESSAGE_BUFFER), ("STA", "zp", ZP_POINTER),
            ("LDA", "absx", MESSAGE_BUFFER + 1), ("STA", "zp", ZP_POINTER + 1),
            ("LDY", "imm", 0)]


ADVANCE = [("INX", "imp")] * 3 + [("DEC", "zp", ZP_COUNT)]

# Entered from the dispatcher with X = 2 (the payload's first byte); MID in $03, action in $04.
GUARDED_WRITE: list[str | tuple] = [
    ("LDA", "zp", ZP_ACTION), ("CMP", "imm", GUARDED_WRITE_ACTION),
    ("BNE", "rel", "ignore"),            # the dispatcher sends every action >= 5 here
    ("LDA", "imm", 0), ("STA", "zp", ZP_RESULT),
    ("LDA", "absx", MESSAGE_BUFFER), ("STA", "zp", ZP_COUNT), ("INX", "imp"),
    "guard",
    ("LDA", "zp", ZP_COUNT), ("BEQ", "rel", "writes"),
    *_triple_from_buffer(),
    ("LDA", "indy", ZP_POINTER), ("CMP", "absx", MESSAGE_BUFFER + 2),
    ("BNE", "rel", "reply"),             # a guard failed: nothing written, answer 0
    *ADVANCE, ("JMP", "abs", "guard"),
    "writes",
    ("LDA", "absx", MESSAGE_BUFFER), ("STA", "zp", ZP_COUNT), ("INX", "imp"),
    "write",
    ("LDA", "zp", ZP_COUNT), ("BEQ", "rel", "written"),
    *_triple_from_buffer(),
    ("LDA", "absx", MESSAGE_BUFFER + 2), ("STA", "indy", ZP_POINTER),
    *ADVANCE, ("JMP", "abs", "write"),
    "written",
    ("INC", "zp", ZP_RESULT),
    "reply",
    ("JMP", "abs", FREEZE_REPLY),
    "ignore",
    ("RTS", "imp"),
]


def guarded_write_code() -> bytes:
    code = assemble(GUARDED_WRITE_ORIGIN, GUARDED_WRITE)
    assert GUARDED_WRITE_ORIGIN + len(code) <= GUARDED_WRITE_LIMIT, "guarded write overruns ZORA's $B900"
    return code


def extend(vanilla: bytes, beta8_ips: bytes) -> bytes:
    """The vanilla ROM with the extended patch applied. Raises ValueError when the IPS is not
    beta 8's (any edited byte differs from beta 8's value), naming the first such site."""
    rom = bytearray(apply(vanilla, beta8_ips))
    for edit in EDITS:
        at = bank4_offset(edit.cpu)
        held = bytes(rom[at:at + len(edit.old)])
        if held != edit.old:
            already = held == edit.new
            raise ValueError(f"${edit.cpu:04X} ({edit.why}) holds {held.hex()}, not beta 8's {edit.old.hex()}"
                             + (": the patch is already extended" if already else ""))
        rom[at:at + len(edit.new)] = edit.new
    code = guarded_write_code()
    at = bank4_offset(GUARDED_WRITE_ORIGIN)
    if any(byte != 0xFF for byte in rom[at:at + len(code)]):
        raise ValueError(f"${GUARDED_WRITE_ORIGIN:04X} is not free in this ROM")
    rom[at:at + len(code)] = code
    return bytes(rom)


def manifest(vanilla: bytes, ips: bytes, ips_name: str) -> dict:
    """The expected-vanilla sidecar, as relocate_patch.py writes it."""
    return {
        "patch_name": "z1rr-coop",
        "ips": ips_name,
        "vanilla_sha1": VANILLA_SHA1S,
        "expected": {f"0x{offset:06X}": vanilla[offset:offset + len(payload)].hex()
                     for offset, payload in parse(io.BytesIO(ips))},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vanilla", help="vanilla PRG0 Legend of Zelda ROM")
    parser.add_argument("--prg1", help="vanilla PRG1 ROM, to check every patch site matches PRG0")
    args = parser.parse_args()
    vanilla = Path(args.vanilla).read_bytes()
    if hashlib.sha1(vanilla).hexdigest() != VANILLA_SHA1S["prg0"]:
        sys.exit("the ROM is not vanilla PRG0")
    try:
        extended = extend(vanilla, PATCH_PATH.read_bytes())
    except ValueError as error:
        sys.exit(str(error))
    ips = make_ips(vanilla, extended)
    sidecar = manifest(vanilla, ips, PATCH_PATH.name)
    if args.prg1:
        prg1 = Path(args.prg1).read_bytes()
        if hashlib.sha1(prg1).hexdigest() != VANILLA_SHA1S["prg1"]:
            sys.exit("the --prg1 ROM is not vanilla PRG1")
        differ = [site for site, want in sidecar["expected"].items()
                  if prg1[int(site, 16):int(site, 16) + len(want) // 2].hex() != want]
        if differ:
            sys.exit(f"patch sites differ between PRG0 and PRG1: {differ}")
        print("# every patch site holds the same bytes in PRG0 and PRG1")
    PATCH_PATH.write_bytes(ips)
    PATCH_PATH.with_suffix(".expected.json").write_text(json.dumps(sidecar, indent=2) + "\n")
    print(f"# {PATCH_PATH.name}: {len(ips)} bytes; guarded write at ${GUARDED_WRITE_ORIGIN:04X}, "
          f"{len(guarded_write_code())} bytes; {len(sidecar['expected'])} manifest sites")


if __name__ == "__main__":
    main()
