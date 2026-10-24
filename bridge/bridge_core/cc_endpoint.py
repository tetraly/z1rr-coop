"""MemoryEndpoint adapter for the EDN8 Crowd Control USB client."""
from __future__ import annotations

from bridge_core.cc_client import PATCH_REVISION, CCClient
from bridge_core.sync_engine import REVISION_0_FLAG

# The 2.0 beta 8 patch (revision 0) keeps its "message waiting" flag at $07FE, which is also room
# $7F's saved flags in the levels 7-9 block: every read of it during a command returns the flag
# ($AB), and every write is wiped when the command ends. So on a revision-0 cart $07FE is left out
# of the snapshot (tloz_all would OR that $AB into the partner's room $7F) until the cart says it
# is revision 1 or later (extend_patch.py), whose flag is at $7F9E.
VERSION_TIMEOUT_MS = 300


class CCMemoryEndpoint:
    def __init__(self, client: CCClient) -> None:
        self._client = client
        self._revision: int | None = None

    @property
    def patch_revision(self) -> int | None:
        """The cart's patch revision, once it has answered the version command (it answers only
        during play), else None."""
        return self._revision

    def _flag_byte_is_game_data(self) -> bool:
        if self._revision is None:
            version = self._client.version(timeout_ms=VERSION_TIMEOUT_MS)
            if version:
                self._revision = version[0]
        return self._revision is not None and self._revision >= PATCH_REVISION

    def read_ranges(
        self,
        ranges: list[tuple[int, int]],
        timeout_ms: int = 300,
    ) -> dict[int, int] | None:
        snapshot = self._client.read_ranges(ranges, timeout_ms=timeout_ms)
        if snapshot is not None and REVISION_0_FLAG in snapshot and not self._flag_byte_is_game_data():
            del snapshot[REVISION_0_FLAG]
        return snapshot

    def read_byte(self, addr: int, timeout_ms: int = 200) -> int | None:
        self._client.send_read_addrs([addr])
        result = self._client.poll_response(timeout_ms=timeout_ms)
        if result and len(result) >= 1:
            return result[0]
        return None

    def write_pairs(self, pairs: list[tuple[int, int]]) -> bool:
        self._client.send_write_pairs(pairs)
        return True

    def close(self) -> None:
        self._client.close()
