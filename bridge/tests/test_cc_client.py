import pytest

from bridge_core.cc_client import MAX_FRAME_LENGTH, CCClient

from .mock_cc_server import MockCCServer

# action $03: MID, action, length, address lo, hi, the bytes, checksum
MAX_WRITE_ARRAY_BYTES = MAX_FRAME_LENGTH - 6


def make_client_with_mock(ram_size: int = 0x0800) -> tuple[CCClient, MockCCServer]:
    server = MockCCServer(ram_size=ram_size)
    client = CCClient(serial_port=server.serial)
    return client, server


def test_read_array_returns_correct_bytes():
    client, server = make_client_with_mock()
    server.set_ram(0x0010, b"\xab\xcd\xef")
    client.send_read_array(0x0010, 3)
    server.step()
    result = client.poll_response(timeout_ms=100)
    assert result == b"\xab\xcd\xef"


def test_read_individual_addresses():
    client, server = make_client_with_mock()
    server.set_ram(0x0010, 0xAB)
    server.set_ram(0x0050, 0xCD)
    server.set_ram(0x0100, 0xEF)
    client.send_read_addrs([0x0010, 0x0050, 0x0100])
    server.step()
    result = client.poll_response(timeout_ms=100)
    assert result == b"\xab\xcd\xef"


def test_write_pairs_updates_ram():
    client, server = make_client_with_mock()
    client.send_write_pairs([(0x0010, 0xAB), (0x0050, 0xCD)])
    server.step()
    assert server.ram[0x0010] == 0xAB
    assert server.ram[0x0050] == 0xCD


def test_write_array_updates_ram():
    client, server = make_client_with_mock()
    client.send_write_array(0x0010, b"\xab\xcd\xef")
    server.step()
    assert server.ram[0x0010:0x0013] == b"\xab\xcd\xef"


def test_msg_id_wraps_after_255():
    client, server = make_client_with_mock()
    for _ in range(257):
        client.send_read_array(0x0000, 4)
        server.step()
        client.poll_response(timeout_ms=100)
    # Should not raise; client should keep msg_id in 1-255 range (skipping 0)
    assert 1 <= client._msg_id <= 255


def test_poll_response_returns_none_on_timeout():
    client, _server = make_client_with_mock()
    # Don't issue a request; nothing should be in the RX buffer
    result = client.poll_response(timeout_ms=10)
    assert result is None


def test_frame_includes_checksum_byte():
    """Regression guard for the missing-checksum bug that caused the cart to
    pull one byte from the next frame in FIFO and crash after ~97 transactions.
    A correctly framed Read8 (action 0x00, count=1, addr $0042) is:
        [L=6, mid=1, action=0, count=1, addr_lo=0x42, addr_hi=0x00, checksum=0x44]
    where checksum = (mid + action + count + addr_lo + addr_hi) mod 256.
    """
    from bridge_core.cc_client import _make_cc_frame
    payload = bytes((1, 0x42, 0x00))  # count=1, addr=$0042
    frame = _make_cc_frame(mid=1, action=0x00, payload=payload)
    # L = body length WITH checksum = 2 (mid+action) + 3 (payload) + 1 (checksum) = 6
    assert frame[0] == 6
    assert frame[1] == 1     # mid
    assert frame[2] == 0x00  # action
    assert frame[3] == 1     # count
    assert frame[4] == 0x42  # addr_lo
    assert frame[5] == 0x00  # addr_hi
    expected_checksum = (1 + 0x00 + 1 + 0x42 + 0x00) & 0xFF
    assert frame[6] == expected_checksum
    assert len(frame) == 7


def test_guarded_write_writes_when_the_guards_hold():
    client, server = make_client_with_mock()
    server.set_ram(0x067B, 3)
    client.send_guarded_write([(0x067B, 4), (0x0657, 1)], [(0x067B, 3), (0x0657, 0)])
    server.step()
    assert client.poll_response(timeout_ms=100) == b"\x01"
    assert (server.ram[0x067B], server.ram[0x0657]) == (4, 1)


def test_guarded_write_reports_a_failed_guard():
    client, server = make_client_with_mock()
    server.set_ram(0x0657, 1)
    client.send_guarded_write([(0x067B, 4)], [(0x0657, 0)])
    server.step()
    assert client.poll_response(timeout_ms=100) == b"\x00"
    assert server.ram[0x067B] == 0


def test_guarded_write_without_an_answer_is_unknown():
    client, _server = make_client_with_mock()
    assert client.guarded_write([(0x067B, 4)], [], timeout_ms=10) is None


def test_guarded_write_refuses_a_frame_that_reaches_7fff():
    client, _server = make_client_with_mock()
    client.send_guarded_write([(0x0600 + i, 0) for i in range(3)], [(0x0700 + i, 0) for i in range(27)])
    with pytest.raises(ValueError):
        client.send_guarded_write([(0x0600 + i, 0) for i in range(3)], [(0x0700 + i, 0) for i in range(28)])


def test_no_frame_longer_than_the_cart_takes_is_sent():
    from bridge_core.cc_client import MAX_READ_ADDRS
    client, server = make_client_with_mock()
    client.send_write_array(0x0010, bytes(MAX_WRITE_ARRAY_BYTES))
    with pytest.raises(ValueError):
        client.send_write_array(0x0010, bytes(MAX_WRITE_ARRAY_BYTES + 1))
    with pytest.raises(ValueError):
        client.send_read_addrs(list(range(MAX_READ_ADDRS + 1)))


def test_read_addrs_splits_into_frames_the_cart_takes():
    from bridge_core.cc_client import MAX_READ_ADDRS
    client, server = make_client_with_mock()
    sent = []
    send = client.send_read_addrs
    client.send_read_addrs = lambda addrs: (sent.append(len(addrs)), send(addrs), server.step())
    server.set_ram(0x0100, bytes(range(100)))
    assert client.read_addrs(list(range(0x0100, 0x0164)), timeout_ms=50) == bytes(range(100))
    assert sent == [MAX_READ_ADDRS, MAX_READ_ADDRS, 100 - 2 * MAX_READ_ADDRS]


def test_reply_data_that_looks_like_a_status_pair_is_kept():
    """Data can hold 00 A5 or 04 A5, the firmware's status pairs: the reply is found by its header
    and its values taken as received, not with those pairs cut out."""
    from bridge_core.cc_client import REPLY_PRELUDE
    from .mock_serial import MockSerial
    sp = MockSerial()
    client = CCClient(sp)
    client.send_read_array(0x0657, 4)
    sp.take_tx()
    values = bytes((0x00, 0xA5, 0x04, 0xA5))
    sp.feed_rx(bytes((0x00, 0xA5)) + REPLY_PRELUDE + bytes((len(values) + 3, 0x00, 0x20, 1, 0x01)) + values)
    assert client.poll_response(timeout_ms=20) == values
