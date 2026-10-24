from bridge_core.cc_client import CCClient
from bridge_core.cc_endpoint import CCMemoryEndpoint

from .mock_cc_server import MockCCServer


class FakeCCClient:
    def __init__(self) -> None:
        self.read_ranges_calls = []
        self.read_addrs_calls = []
        self.write_pairs_calls = []
        self.closed = False

    def read_ranges(self, ranges, timeout_ms=300):
        self.read_ranges_calls.append((ranges, timeout_ms))
        return {0x0012: 0x05}

    def send_read_addrs(self, addrs):
        self.read_addrs_calls.append(addrs)

    def poll_response(self, timeout_ms=200):
        return bytes([0x01])

    def send_write_pairs(self, pairs):
        self.write_pairs_calls.append(pairs)

    def close(self):
        self.closed = True


def test_cc_endpoint_delegates_read_ranges():
    client = FakeCCClient()
    endpoint = CCMemoryEndpoint(client)

    assert endpoint.read_ranges([(0x0012, 1)], timeout_ms=123) == {0x0012: 0x05}
    assert client.read_ranges_calls == [([(0x0012, 1)], 123)]


def test_cc_endpoint_reads_single_byte():
    client = FakeCCClient()
    endpoint = CCMemoryEndpoint(client)

    assert endpoint.read_byte(0x0657, timeout_ms=456) == 0x01
    assert client.read_addrs_calls == [[0x0657]]


def test_cc_endpoint_write_pairs_updates_cart_ram():
    server = MockCCServer()
    endpoint = CCMemoryEndpoint(CCClient(server.serial))

    assert endpoint.write_pairs([(0x0657, 0x01)]) is True
    server.step()

    assert server.ram[0x0657] == 0x01


def test_cc_endpoint_closes_client():
    client = FakeCCClient()
    endpoint = CCMemoryEndpoint(client)

    endpoint.close()

    assert client.closed is True


class AnsweringSerial:
    """The mock cart's serial port, answering each frame as soon as the bridge writes it."""

    def __init__(self, server: MockCCServer) -> None:
        self.server = server

    def write(self, data: bytes) -> int:
        written = self.server.serial.write(data)
        self.server.step()
        return written

    def read(self, n: int = 1) -> bytes:
        return self.server.serial.read(n)

    @property
    def in_waiting(self) -> int:
        return self.server.serial.in_waiting

    def close(self) -> None:
        self.server.serial.close()


def _endpoint_on_cart(patch_revision: int) -> tuple[CCMemoryEndpoint, MockCCServer]:
    server = MockCCServer(patch_revision=patch_revision)
    server.set_ram(0x07FD, b"\x11\x22")
    return CCMemoryEndpoint(CCClient(AnsweringSerial(server))), server


def test_a_revision_1_cart_reports_07fe_as_game_data():
    endpoint, _server = _endpoint_on_cart(patch_revision=1)
    assert endpoint.read_ranges([(0x07FD, 2)], timeout_ms=50) == {0x07FD: 0x11, 0x07FE: 0x22}
    assert endpoint.patch_revision == 1


def test_a_beta_8_cart_leaves_out_its_flag_byte():
    """Beta 8's patch flag sits at $07FE: tloz_all must not OR it into the partner's room $7F."""
    endpoint, _server = _endpoint_on_cart(patch_revision=0)
    assert endpoint.read_ranges([(0x07FD, 2)], timeout_ms=50) == {0x07FD: 0x11}
    assert endpoint.patch_revision == 0


def test_reads_without_07fe_never_ask_the_version():
    client = FakeCCClient()
    endpoint = CCMemoryEndpoint(client)
    endpoint.read_ranges([(0x0012, 1)])
    assert endpoint.patch_revision is None
