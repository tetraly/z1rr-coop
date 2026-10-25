"""CC USB serial protocol client.

Wraps a serial.Serial-like object and exposes methods to send CC frames
(read/write/freeze) and parse responses.

CC frame format (host -> cart, wrapped in a USB MEM_WR to ADDR_FIFO):
    L (1 byte)       = body length INCLUDING the trailing checksum byte
    MID (1 byte)     = monotonic message id, 1..255
    ACTION (1 byte)  = 0x00 read addrs, 0x01 read array, 0x02 write pairs,
                       0x03 write array, 0x04 freeze, 0x05 guarded write
                       (extend_patch.py), 0xFE version
    payload...
    CHECKSUM (1 byte) = sum(MID + ACTION + payload) mod 256

The cart's NMI hook reads the L byte then loops L times to consume the body.
If we send L = len(body without checksum) the cart's loop falls one byte short
of the frame and pulls the leading byte of the next frame, snowballing into a
crash after ~97 transactions. This is the protocol's actual contract, recovered
by reverse-engineering Crowd Control's official EverDriveN8ProConnector.

CC response (cart -> host):
    [0x2B, 0xD4, 0x22, 0xDD] (4-byte EDIO write prelude)
    + size byte (= N + 3)
    + 0x00, 0x20
    + MID, ACTION
    + values (N bytes)
We only scan for the [0x20, MID, ACTION, ...] tail in poll_response.
"""
from __future__ import annotations

import time
from typing import Protocol


# USB-level constants
ADDR_FIFO = 0x01810000
CMD_MEM_WR = 0x1A
P = 0x2B

ACTION_GUARDED_WRITE = 0x05
ACTION_VERSION = 0xFE
VERSION_LENGTH = 5
PATCH_REVISION = 0x01        # the version's first byte once extend_patch.py's changes are in
REPLY_PRELUDE = bytes((P, P ^ 0xFF, 0x22, 0x22 ^ 0xFF))     # the cart's EDIO write, before each reply
# MID..checksum land at $7FA0-$7FFE; $7FFF is the game's SaveRamEnd marker ($A5). The cart drops a
# frame with a longer L (extend_patch.py's drain; beta 8's copied it on past $7FFF), so none is sent.
MAX_FRAME_LENGTH = 0x7FFF - 0x7FA0
MAX_GUARDED_BODY = MAX_FRAME_LENGTH
# action $00: MID, action, count, two bytes per address, checksum
MAX_READ_ADDRS = (MAX_FRAME_LENGTH - 4) // 2


class SerialLike(Protocol):
    def write(self, data: bytes) -> int: ...
    def read(self, n: int = 1) -> bytes: ...
    @property
    def in_waiting(self) -> int: ...
    def close(self) -> None: ...


def _make_usb_header(cmd: int) -> bytes:
    return bytes([P, P ^ 0xFF, cmd & 0xFF, (cmd ^ 0xFF) & 0xFF])


def _u32le(v: int) -> bytes:
    return bytes([(v >> 0) & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF])


def _make_cc_frame(mid: int, action: int, payload: bytes) -> bytes:
    body = bytes((mid & 0xFF, action & 0xFF)) + payload
    checksum = sum(body) & 0xFF
    body_with_checksum = body + bytes((checksum,))
    L = len(body_with_checksum)
    if L > MAX_FRAME_LENGTH:
        raise ValueError(f"CC frame too large: {L} bytes (the cart takes {MAX_FRAME_LENGTH} at most)")
    return bytes((L,)) + body_with_checksum


def _strip_status_pairs(b: bytes) -> bytes:
    """Cart firmware periodically sends 0x00 0xA5 or 0x04 0xA5 status pairs;
    strip them from any received byte stream."""
    out = bytearray()
    i = 0
    while i < len(b):
        if i + 1 < len(b) and b[i] in (0x00, 0x04) and b[i + 1] == 0xA5:
            i += 2
            continue
        out.append(b[i])
        i += 1
    return bytes(out)


class CCClient:
    """Wraps a serial port and exposes the CC USB protocol primitives."""

    def __init__(self, serial_port: SerialLike) -> None:
        self._sp = serial_port
        self._msg_id = 0
        self._rx_buf = bytearray()
        self._expected_mid = 0
        self._expected_action = 0
        self._expected_values = 0

    def _next_mid(self) -> int:
        self._msg_id = (self._msg_id + 1) & 0xFF
        if self._msg_id == 0:
            self._msg_id = 1
        return self._msg_id

    def _usb_mem_wr(self, addr: int, data: bytes) -> None:
        self._sp.write(_make_usb_header(CMD_MEM_WR))
        self._sp.write(_u32le(addr))
        self._sp.write(_u32le(len(data)))
        self._sp.write(b"\x00")
        self._sp.write(data)

    def send_read_addrs(self, addrs: list[int]) -> None:
        mid = self._next_mid()
        payload = bytes((len(addrs),))
        for a in addrs:
            payload += bytes((a & 0xFF, (a >> 8) & 0xFF))
        self._usb_mem_wr(ADDR_FIFO, _make_cc_frame(mid, 0x00, payload))
        self._expected_mid = mid
        self._expected_action = 0x00
        self._expected_values = len(addrs)

    def send_read_array(self, base: int, length: int) -> None:
        mid = self._next_mid()
        payload = bytes((length & 0xFF, base & 0xFF, (base >> 8) & 0xFF))
        self._usb_mem_wr(ADDR_FIFO, _make_cc_frame(mid, 0x01, payload))
        self._expected_mid = mid
        self._expected_action = 0x01
        self._expected_values = length

    def send_write_pairs(self, pairs: list[tuple[int, int]]) -> None:
        mid = self._next_mid()
        payload = bytes((len(pairs),))
        for addr, val in pairs:
            payload += bytes((addr & 0xFF, (addr >> 8) & 0xFF, val & 0xFF))
        self._usb_mem_wr(ADDR_FIFO, _make_cc_frame(mid, 0x02, payload))

    def send_write_array(self, base: int, data: bytes) -> None:
        mid = self._next_mid()
        payload = bytes((len(data) & 0xFF, base & 0xFF, (base >> 8) & 0xFF)) + data
        self._usb_mem_wr(ADDR_FIFO, _make_cc_frame(mid, 0x03, payload))

    def send_guarded_write(self, writes: list[tuple[int, int]], guards: list[tuple[int, int]]) -> None:
        """Action 0x05 (extended patch, revision 1): in one game frame, make every (addr, value)
        write only if every (addr, value) guard still holds. Guards read their address, so they
        belong on RAM. The cart stores MID..checksum at $7FA0; a frame body over
        MAX_GUARDED_BODY bytes would reach $7FFF, the game's save-RAM marker, so it is refused."""
        payload = bytes((len(guards),))
        for addr, val in guards:
            payload += bytes((addr & 0xFF, (addr >> 8) & 0xFF, val & 0xFF))
        payload += bytes((len(writes),))
        for addr, val in writes:
            payload += bytes((addr & 0xFF, (addr >> 8) & 0xFF, val & 0xFF))
        if len(payload) + 3 > MAX_GUARDED_BODY:
            raise ValueError(f"guarded write too large: {len(guards)} guards + {len(writes)} writes "
                             f"(at most {(MAX_GUARDED_BODY - 5) // 3} together)")
        mid = self._next_mid()
        self._usb_mem_wr(ADDR_FIFO, _make_cc_frame(mid, ACTION_GUARDED_WRITE, payload))
        self._expected_mid = mid
        self._expected_action = ACTION_GUARDED_WRITE
        self._expected_values = 1

    def guarded_write(self, writes: list[tuple[int, int]], guards: list[tuple[int, int]],
                      timeout_ms: int = 500) -> bool | None:
        """True if the cart made the writes, False if a guard failed (nothing written), None on
        a timeout (unknown: read again before retrying)."""
        self.send_guarded_write(writes, guards)
        answer = self.poll_response(timeout_ms=timeout_ms)
        if answer is None or len(answer) != 1:
            return None
        return answer[0] == 1

    def version(self, timeout_ms: int = 500) -> bytes | None:
        """Action 0xFE's five bytes; the first is the patch revision (0 for beta 8 and before,
        PATCH_REVISION for extend_patch.py's), or None on a timeout."""
        mid = self._next_mid()
        self._usb_mem_wr(ADDR_FIFO, _make_cc_frame(mid, ACTION_VERSION, b""))
        self._expected_mid = mid
        self._expected_action = ACTION_VERSION
        self._expected_values = VERSION_LENGTH
        return self.poll_response(timeout_ms=timeout_ms)

    def read_addrs(self, addrs: list[int], timeout_ms: int = 500,
                   chunk_size: int = MAX_READ_ADDRS) -> bytes | None:
        """Read N individual addresses; chunks into multiple frames if needed
        (a frame fits at most MAX_READ_ADDRS addresses). Returns concatenated bytes in the
        original `addrs` order, or None if any chunk times out."""
        if not addrs:
            return b""
        out = bytearray()
        for chunk_start in range(0, len(addrs), chunk_size):
            chunk = addrs[chunk_start:chunk_start + chunk_size]
            self.send_read_addrs(chunk)
            chunk_vals = self.poll_response(timeout_ms=timeout_ms)
            if chunk_vals is None or len(chunk_vals) != len(chunk):
                return None
            out += chunk_vals
        return bytes(out)

    def read_array(self, base: int, length: int, timeout_ms: int = 500) -> bytes | None:
        """Read a contiguous range; convenience for send_read_array + poll."""
        self.send_read_array(base, length)
        return self.poll_response(timeout_ms=timeout_ms)

    def read_ranges(self, ranges: list[tuple[int, int]], timeout_ms: int = 300) -> dict[int, int] | None:
        """Read a set of contiguous (base, length) ranges via Action 0x01.
        Returns a {addr: byte_value} dict spanning every byte in every range,
        or None if any range read times out."""
        snapshot: dict[int, int] = {}
        for base, length in ranges:
            data = self.read_array(base, length, timeout_ms=timeout_ms)
            if data is None or len(data) != length:
                return None
            for i, byte in enumerate(data):
                snapshot[base + i] = byte
        return snapshot

    def _framed_reply(self) -> bytes | None:
        """The values of the expected reply, found by its whole header (prelude, size, $00 $20, MID,
        action) in the bytes as received, and taken as they are: data can hold the same bytes as a
        status pair (00 A5, 04 A5), which the scan below would cut out of it."""
        header = REPLY_PRELUDE + bytes((self._expected_values + 3, 0x00, 0x20, self._expected_mid & 0xFF))
        at = self._rx_buf.find(header)
        while at >= 0:
            action_at = at + len(header)
            if action_at >= len(self._rx_buf):
                return None
            if self._rx_buf[action_at] in (self._expected_action & 0xFF, 0x00):
                end = action_at + 1 + self._expected_values
                if end > len(self._rx_buf):
                    return None
                result = bytes(self._rx_buf[action_at + 1:end])
                self._rx_buf.clear()
                return result
            at = self._rx_buf.find(header, at + 1)
        return None

    def poll_response(self, timeout_ms: int = 100) -> bytes | None:
        """Wait up to timeout_ms for a response matching the last send_read_*: by its whole header
        first, else by scanning for [0x20, MID, action] with status pairs removed."""
        deadline = time.perf_counter() + timeout_ms / 1000.0
        while time.perf_counter() < deadline:
            n = self._sp.in_waiting
            if n:
                self._rx_buf += self._sp.read(n)
            framed = self._framed_reply()
            if framed is not None:
                return framed
            filt = _strip_status_pairs(bytes(self._rx_buf))
            for i in range(len(filt) - 3):
                if (
                    filt[i] == 0x20
                    and filt[i + 1] == (self._expected_mid & 0xFF)
                    and filt[i + 2] in (self._expected_action & 0xFF, 0x00)
                ):
                    start = i + 3
                    end = start + self._expected_values
                    if end <= len(filt):
                        result = bytes(filt[start:end])
                        self._rx_buf.clear()
                        return result
            time.sleep(0.001)
        return None

    def close(self) -> None:
        self._sp.close()
