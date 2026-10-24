"""The extended patch (extend_patch.py) run on a 6502 (py65): the shipped IPS's bank 4 and bank 7
code, with the EverDrive's USB FIFO ($40F0 data, $40F1 status) simulated.

Each command goes in as the cart would see it: the NMI drain ($B46F) reads the frame from the FIFO
into $7FA0 and sets the flag; the main-loop hook ($B495) dispatches it and answers through the FIFO.
The game's NMI ($E484) and the code the hook continues to ($FAE4) are replaced by RTS.

Needs a vanilla PRG0 ROM: Z1RR_VANILLA_PRG0, or the path test_ips.py uses."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

py65_mpu = pytest.importorskip("py65.devices.mpu6502")
py65_memory = pytest.importorskip("py65.memory")

from bridge_core.cc_client import _make_cc_frame
from bridge_core.ips import apply
from extend_patch import NEW_FLAG, OLD_FLAG, PATCH_PATH, PATCH_REVISION, VANILLA_SHA1S

VANILLA_PRG0 = Path(os.environ.get("Z1RR_VANILLA_PRG0",
                                   r"N:\Games\ROMs\NES\Legend of Zelda, The (U) (PRG0) [!].nes"))
INES_HEADER, BANK_SIZE = 16, 0x4000
FIFO_DATA, FIFO_STATUS = 0x40F0, 0x40F1
FIFO_READY, FIFO_EMPTY = 0x00, 0x80          # the drain skips a status with bit 7 set
NMI_DRAIN, MAIN_LOOP_HOOK = 0xB46F, 0xB495
GAME_NMI, HOOK_CONTINUES = 0xE484, 0xFAE4
TRAMPOLINE, RETURNED = 0x5000, 0x5003         # JSR target / sentinel, in unmapped space
RTS, JSR = 0x60, 0x20
INIT_LATCH, INITIALIZED = 0x07FF, 0xAB
FLAG_SET = 0xAB
REPLY_PRELUDE = bytes((0x2B, 0xD4, 0x22, 0xDD))
STEP_LIMIT = 200_000
FREEZE_SLOTS = (0x7F40, 0x7F50, 0x7F60, 0x7F70)
STATS_BLOCK = range(0x7F00, 0x7F40)


def _vanilla() -> bytes:
    if not VANILLA_PRG0.exists():
        pytest.skip("vanilla PRG0 not available (set Z1RR_VANILLA_PRG0)")
    import hashlib
    rom = VANILLA_PRG0.read_bytes()
    assert hashlib.sha1(rom).hexdigest() == VANILLA_SHA1S["prg0"]
    return rom


class Cart:
    """The patched ROM's bank 4 at $8000 and bank 7 at $C000, 64 KB of RAM-like memory, and the
    USB FIFO: frames queued for the drain, bytes the cart sends collected in `sent`."""

    def __init__(self) -> None:
        rom = apply(_vanilla(), PATCH_PATH.read_bytes())
        self.memory = py65_memory.ObservableMemory()
        for bank, origin in ((4, 0x8000), (7, 0xC000)):
            start = INES_HEADER + bank * BANK_SIZE
            for i, byte in enumerate(rom[start:start + BANK_SIZE]):
                self.memory[origin + i] = byte
        self.memory[GAME_NMI] = RTS
        self.memory[HOOK_CONTINUES] = RTS
        self.memory[INIT_LATCH] = INITIALIZED     # past the power-on setup unless a test says not
        self.incoming = bytearray()
        self.sent = bytearray()
        self.memory.subscribe_to_read([FIFO_STATUS], lambda _a: FIFO_READY if self.incoming else FIFO_EMPTY)
        self.memory.subscribe_to_read([FIFO_DATA], lambda _a: self.incoming.pop(0))
        self.memory.subscribe_to_write([FIFO_DATA], lambda _a, value: self.sent.append(value))
        self.cpu = py65_mpu.MPU(memory=self.memory)

    def __getitem__(self, address: int) -> int:
        return self.memory[address]

    def __setitem__(self, address: int, value: int) -> None:
        self.memory[address] = value

    def call(self, routine: int) -> None:
        self.memory[TRAMPOLINE:TRAMPOLINE + 3] = [JSR, routine & 0xFF, routine >> 8]
        self.cpu.pc = TRAMPOLINE
        for _ in range(STEP_LIMIT):
            if self.cpu.pc == RETURNED:
                return
            self.cpu.step()
        raise AssertionError(f"${routine:04X} did not return")

    def frame(self) -> None:
        """One NMI: the drain, then (in play) the main-loop hook."""
        self.call(NMI_DRAIN)
        self.call(MAIN_LOOP_HOOK)

    def command(self, mid: int, action: int, payload: bytes) -> bytes | None:
        """Send one frame, run one game frame; the values of its answer, or None without one."""
        self.incoming += _make_cc_frame(mid, action, payload)
        self.sent.clear()
        self.frame()
        if not self.sent:
            return None
        assert bytes(self.sent[:4]) == REPLY_PRELUDE
        size = self.sent[4]
        assert bytes(self.sent[5:9]) == bytes((0x00, 0x20, mid, action))
        return bytes(self.sent[9:9 + size - 3])


def triples(pairs: list[tuple[int, int]]) -> bytes:
    return bytes((len(pairs),)) + b"".join(bytes((a & 0xFF, a >> 8, v)) for a, v in pairs)


def guarded(writes: list[tuple[int, int]], guards: list[tuple[int, int]]) -> bytes:
    return triples(guards) + triples(writes)


def freeze_payload(address: int, value: int) -> bytes:
    """Action $04's 11 bytes in the order the handler reads them: $7F84, $7F89, address lo, hi,
    $7F82 (value), $7F83, $7F85-$7F88, and $7F8A (nonzero: arm)."""
    return bytes((0x01, 0x00, address & 0xFF, address >> 8, value, 0xFF, 0x01, 0x00, 0x00, 0x00, 0x01))


# --- the flag ------------------------------------------------------------------------------------

def test_the_flag_is_at_7f9e_and_07fe_is_left_alone():
    cart = Cart()
    cart[OLD_FLAG] = 0x5A                        # room $7F's saved flags in the levels 7-9 block
    cart.incoming += _make_cc_frame(1, 0x01, bytes((1, 0x00, 0x06)))
    cart.call(NMI_DRAIN)
    assert (cart[NEW_FLAG], cart[OLD_FLAG]) == (FLAG_SET, 0x5A)
    cart.call(MAIN_LOOP_HOOK)
    assert (cart[NEW_FLAG], cart[OLD_FLAG]) == (0x00, 0x5A)
    assert cart.sent


def test_a_command_left_waiting_at_power_off_is_dropped():
    cart = Cart()
    cart[INIT_LATCH] = 0x00                      # just switched on
    cart[NEW_FLAG] = FLAG_SET                    # SRAM kept a waiting write pair
    frame = _make_cc_frame(9, 0x02, triples([(0x0657, 0x77)]))
    for i, byte in enumerate(frame[1:]):
        cart[0x7FA0 + i] = byte
    cart.call(MAIN_LOOP_HOOK)
    assert (cart[0x0657], cart[NEW_FLAG], cart[INIT_LATCH]) == (0x00, 0x00, INITIALIZED)
    assert not cart.sent


# --- co-op's commands still work -------------------------------------------------------------------

def test_read_array_and_write_pairs():
    cart = Cart()
    cart[0x0657], cart[0x0658] = 0x12, 0x34
    assert cart.command(1, 0x01, bytes((2, 0x57, 0x06))) == b"\x12\x34"
    assert cart.command(2, 0x02, triples([(0x0659, 0x56)])) == b""
    assert cart[0x0659] == 0x56


def test_the_version_names_the_revision():
    assert Cart().command(3, 0xFE, b"")[:1] == bytes((PATCH_REVISION,))


# --- the guarded write ---------------------------------------------------------------------------

def test_writes_when_every_guard_holds():
    cart = Cart()
    cart[0x067B], cart[0x0657] = 3, 0x00
    assert cart.command(4, 0x05, guarded([(0x067B, 4), (0x0657, 1)], [(0x067B, 3), (0x0657, 0)])) == b"\x01"
    assert (cart[0x067B], cart[0x0657]) == (4, 1)


def test_writes_nothing_when_one_guard_fails():
    cart = Cart()
    cart[0x067B], cart[0x0657] = 3, 0x01        # the item was picked up since the read
    assert cart.command(5, 0x05, guarded([(0x067B, 4), (0x0657, 1)], [(0x067B, 3), (0x0657, 0)])) == b"\x00"
    assert (cart[0x067B], cart[0x0657]) == (3, 1)


def test_no_guards_writes_and_largest_frame_fits():
    cart = Cart()
    assert cart.command(6, 0x05, guarded([(0x0600 + i, i) for i in range(3)], [])) == b"\x01"
    assert [cart[0x0600 + i] for i in range(3)] == [0, 1, 2]
    guards = [(0x0700 + i, 0) for i in range(27)]
    assert cart.command(7, 0x05, guarded([(0x0620 + i, 0x40 + i) for i in range(3)], guards)) == b"\x01"
    assert [cart[0x0620 + i] for i in range(3)] == [0x40, 0x41, 0x42]
    assert cart[0x7FFF] != 0x05                  # untouched by a 30-pair frame (its checksum ends at $7FFE)


def test_actions_above_5_are_still_ignored():
    cart = Cart()
    assert cart.command(8, 0x06, guarded([(0x0600, 9)], [])) is None
    assert cart[0x0600] == 0


# --- the freeze slots ------------------------------------------------------------------------------

def test_the_stats_block_is_never_touched():
    cart = Cart()
    cart[INIT_LATCH] = 0x00                      # power-on setup runs
    for address in STATS_BLOCK:
        cart[address] = 0xAB                     # every slot-like byte "armed"
    cart[0x0600] = 0x11
    for _ in range(3):
        cart.frame()
    assert all(cart[address] == 0xAB for address in STATS_BLOCK)
    assert cart[0x0600] == 0x11
    assert all(cart[slot] == 0 for slot in FREEZE_SLOTS)


def test_four_freezes_arm_and_a_fifth_is_refused():
    cart = Cart()
    for n, slot in enumerate(FREEZE_SLOTS):
        assert cart.command(10 + n, 0x04, freeze_payload(0x0610 + n, 0x20 + n)) == b"\x01"
        assert cart[slot] == 0xAB
    assert cart.command(20, 0x04, freeze_payload(0x0618, 0x30)) == b"\x00"
    assert all(cart[address] == 0 for address in STATS_BLOCK)


# --- the bridge's client against the cart's code ----------------------------------------------------

class CartSerial:
    """A serial port wired to Cart: each USB MEM_WR to the FIFO is one frame for the drain, run as
    one game frame; what the cart sends comes back as received bytes."""

    def __init__(self, cart: Cart) -> None:
        self.cart = cart
        self.pending = bytearray()
        self.received = bytearray()

    def write(self, data: bytes) -> int:
        self.pending += data
        header = 4 + 4 + 4 + 1                    # P, ~P, CMD, ~CMD; address; length; exec
        while len(self.pending) >= header:
            length = int.from_bytes(self.pending[8:12], "little")
            if len(self.pending) < header + length:
                break
            frame = bytes(self.pending[header:header + length])
            del self.pending[:header + length]
            self.cart.incoming += frame
            self.cart.sent.clear()
            self.cart.frame()
            self.received += self.cart.sent
        return len(data)

    def read(self, n: int = 1) -> bytes:
        out = bytes(self.received[:n])
        del self.received[:n]
        return out

    @property
    def in_waiting(self) -> int:
        return len(self.received)

    def close(self) -> None:
        pass


def test_the_client_gives_through_the_cart():
    from bridge_core.cc_client import CCClient
    cart = Cart()
    client = CCClient(CartSerial(cart))
    cart[0x067B] = 2
    assert client.guarded_write([(0x067B, 3)], [(0x067B, 2)], timeout_ms=50) is True
    assert client.guarded_write([(0x067B, 4)], [(0x067B, 2)], timeout_ms=50) is False
    assert client.read_array(0x067B, 1, timeout_ms=50) == b"\x03"
    assert client.version(timeout_ms=50)[0] == PATCH_REVISION


def test_patch_check_passes_on_the_extended_cart(monkeypatch, capsys):
    import bridge_cli.__main__ as cli
    cart = Cart()
    cart[cli.PATCH_CHECK_BYTE] = 0x33
    monkeypatch.setattr(cli.serial, "Serial", lambda *a, **k: CartSerial(cart))
    assert cli.main(["patch-check", "--port", "COM9"]) == 0
    assert capsys.readouterr().out.count("PASS") == 2
    assert cart[cli.PATCH_CHECK_BYTE] == 0x33


# --- the speed hook --------------------------------------------------------------------------------

def test_bank_5_is_vanilla():
    """No speed hook: InitLinkSpeed ($B366) and the old helper ($B8F0) hold vanilla's bytes, so a
    randomizer's own speed hooks there (ZORA's Pegasus Boots, Slow Speed) are left alone."""
    vanilla = _vanilla()
    patched = apply(vanilla, PATCH_PATH.read_bytes())
    bank_5 = slice(INES_HEADER + 5 * BANK_SIZE, INES_HEADER + 6 * BANK_SIZE)
    assert patched[bank_5] == vanilla[bank_5]
