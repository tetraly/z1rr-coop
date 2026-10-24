"""In-memory simulation of an EDN8 + CC-patched cart, for unit tests.

Wraps a MockSerial. When the bridge writes a CC frame to the mock serial,
this simulator interprets it (via .step()) and pushes the appropriate response
into the mock serial's RX buffer.
"""
from __future__ import annotations

from .mock_serial import MockSerial


# CC protocol constants (mirror the real cart firmware)
ADDR_FIFO = 0x01810000
CMD_MEM_WR = 0x1A
P = 0x2B


class MockCCServer:
    """Simulates the cart-side of the CC protocol against a MockSerial."""

    def __init__(self, ram_size: int = 0x0800) -> None:
        self.serial = MockSerial()
        self.ram = bytearray(ram_size)
        self._tx_carryover = bytearray()  # partial frames from bridge that haven't completed yet

    def set_ram(self, addr: int, value: int | bytes) -> None:
        if isinstance(value, int):
            self.ram[addr] = value
        else:
            self.ram[addr:addr + len(value)] = value

    def step(self) -> None:
        """Process all pending bridge -> cart frames in the mock serial's TX buffer.

        For each received frame, push the appropriate response into the cart -> bridge
        RX buffer. Tests call this after issuing a bridge command, before reading the
        response.
        """
        tx = bytes(self._tx_carryover) + self.serial.take_tx()
        self._tx_carryover.clear()
        idx = 0
        while idx < len(tx):
            # Parse the USB MEM_WR header: P, P^FF, CMD, CMD^FF (4 bytes)
            if idx + 4 > len(tx):
                self._tx_carryover += tx[idx:]
                return
            if tx[idx] != P or tx[idx + 1] != (P ^ 0xFF):
                # Skip until next plausible header
                idx += 1
                continue
            cmd = tx[idx + 2]
            if tx[idx + 3] != (cmd ^ 0xFF):
                idx += 1
                continue
            if cmd != CMD_MEM_WR:
                idx += 4
                continue
            # MEM_WR: 4-byte addr, 4-byte len, 1-byte exec, payload
            if idx + 4 + 4 + 4 + 1 > len(tx):
                self._tx_carryover += tx[idx:]
                return
            payload_len = int.from_bytes(tx[idx + 8:idx + 12], "little")
            total = 4 + 4 + 4 + 1 + payload_len
            if idx + total > len(tx):
                self._tx_carryover += tx[idx:]
                return
            payload = tx[idx + 13:idx + 13 + payload_len]
            self._handle_mem_wr(payload)
            idx += total

    def _handle_mem_wr(self, payload: bytes) -> None:
        """A MEM_WR to ADDR_FIFO carries an inner CC frame:
            [L, MID, ACTION, ...payload, CHECKSUM]
        where L = body length INCLUDING checksum, total frame length = 1 + L.
        Checksum = sum(MID + ACTION + payload) mod 256."""
        if not payload:
            return
        L = payload[0]
        # Total frame length is 1 (L byte) + L (body+checksum)
        if L + 1 != len(payload):
            return
        if len(payload) < 4:  # need at least L + mid + action + checksum
            return
        mid = payload[1]
        action = payload[2]
        body = payload[3:-1]  # everything between action and checksum
        checksum = payload[-1]
        # Validate checksum: sum of MID + ACTION + payload mod 256
        expected = (mid + action + sum(body)) & 0xFF
        if checksum != expected:
            return
        if action == 0x00:
            # Read individual addresses: count + 2-byte addr each
            count = body[0]
            addrs = [int.from_bytes(body[1 + 2 * i:3 + 2 * i], "little") for i in range(count)]
            values = bytes(self.ram[a] if a < len(self.ram) else 0 for a in addrs)
            self._respond(mid, 0x00, values)
        elif action == 0x01:
            # Read array: 1-byte count + 2-byte base
            count = body[0]
            base = int.from_bytes(body[1:3], "little")
            values = bytes(self.ram[base:base + count])
            self._respond(mid, 0x01, values)
        elif action == 0x02:
            # Write pairs: count + (2-byte addr + 1-byte val) each
            count = body[0]
            for i in range(count):
                a = int.from_bytes(body[1 + 3 * i:3 + 3 * i], "little")
                v = body[3 + 3 * i]
                if a < len(self.ram):
                    self.ram[a] = v
            self._respond(mid, 0x02, b"\x01")
        elif action == 0x03:
            # Write array: 1-byte count + 2-byte base + count bytes
            count = body[0]
            base = int.from_bytes(body[1:3], "little")
            data = body[3:3 + count]
            self.ram[base:base + len(data)] = data
            self._respond(mid, 0x03, b"\x01")
        elif action == 0x05:
            # Guarded write: G + G (2-byte addr + val), then W + W (2-byte addr + val)
            def triples(at: int) -> tuple[list[tuple[int, int]], int]:
                count = body[at]
                pairs = [(int.from_bytes(body[at + 1 + 3 * i:at + 3 + 3 * i], "little"), body[at + 3 + 3 * i])
                         for i in range(count)]
                return pairs, at + 1 + 3 * count
            guards, at = triples(0)
            writes, _ = triples(at)
            held = all(a < len(self.ram) and self.ram[a] == v for a, v in guards)
            if held:
                for a, v in writes:
                    if a < len(self.ram):
                        self.ram[a] = v
            self._respond(mid, 0x05, b"\x01" if held else b"\x00")

    def _respond(self, mid: int, action: int, values: bytes) -> None:
        """Push a CC response frame back to the bridge: 0x20, mid, action, values."""
        self.serial.feed_rx(bytes([0x20, mid, action]) + values)
