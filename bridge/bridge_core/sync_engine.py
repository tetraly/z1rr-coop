"""Sync engine — Python port of driver.lua.

record_changed() is line-by-line equivalent to the Lua function. Other parts
(SyncEngine class with poll/cache/handleTable) come in a later task.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bridge_core.map_write_gate import MapWriteGate


def record_changed(
    record: dict[str, Any],
    value: int,
    previous_value: int,
    receiving: bool,
) -> tuple[bool, int]:
    """Port of driver.lua's recordChanged().

    Returns (allow, value): whether to send/apply this change, and the resolved
    value to send/apply (which may differ from the input).
    """
    if record.get("kind") == "trigger":
        return False, value

    unaltered = value
    allow = True

    # Compute mask
    size = record.get("size", 1)
    if size == 2:
        mask = 0xFFFF
    elif size == 4:
        mask = 0xFFFFFFFF
    else:
        mask = 0xFF

    inverse_mask = 0
    masked_value = value

    if "mask" in record:
        mask = record["mask"]
        inverse_mask = (~mask) & 0xFFFFFFFF
        masked_value = (mask & value) | (inverse_mask & previous_value)

    kind = record.get("kind")

    if callable(kind):
        result = kind(value, previous_value, receiving)
        if isinstance(result, tuple):
            allow, value = result
        else:
            allow = bool(result)
        if value is None:
            value = unaltered
    elif kind == "high":
        allow = (mask & value) > (mask & previous_value)
        value = masked_value
    elif kind == "bitOr":
        allow = masked_value != previous_value
        if receiving:
            value = masked_value | previous_value
        else:
            value = masked_value
    elif kind == "bitAnd":
        allow = masked_value != previous_value
        if receiving:
            value = masked_value & previous_value
        else:
            value = masked_value
    elif kind == "delta":
        if not receiving:
            if record.get("ignoreZeroBoundary") and (
                (mask & value) == 0 or (mask & previous_value) == 0
            ):
                return False, unaltered
            allow = masked_value != previous_value
            value = (mask & value) - (mask & previous_value)
        else:
            allow = value != 0
            masked_sum = previous_value + value
            if "deltaMin" in record and masked_sum < record["deltaMin"]:
                masked_sum = record["deltaMin"]
            if "deltaMax" in record and masked_sum > record["deltaMax"]:
                masked_sum = record["deltaMax"]
            value = (inverse_mask & previous_value) | (mask & masked_sum)
    else:
        allow = masked_value != previous_value
        value = masked_value

    if allow and "cond" in record:
        # cond is a callable: cond(value, size) -> bool
        cond_fn = record["cond"]
        if callable(cond_fn):
            allow = cond_fn(masked_value, size)

    return allow, value


from bridge_core.memory_endpoint import MemoryEndpoint
from bridge_core.sync_probe import endpoint_error, read_running_byte

# The 2.0 beta 8 EDN8 patch's "message waiting" flag: $07FE (room $7F's saved flags in the levels 7-9
# block) reads $AB during every command on such a cart (cc_endpoint.py).
REVISION_0_FLAG, REVISION_0_FLAG_SET = 0x07FE, 0xAB


@dataclass
class IncomingApplyResult:
    addr: int | None = None
    incoming_value: int | None = None
    status: str = "ignored"
    previous_value: int | None = None
    written_value: int | None = None
    messages: list[str] = field(default_factory=list)

    @property
    def applied(self) -> bool:
        return self.status == "applied"


class SyncEngine:
    """Polling-based equivalent of GameDriver in driver.lua.

    Holds a cache of last-known RAM values for the mode's sync set, diffs each
    poll snapshot against it, runs record_changed, and either emits outgoing
    data frames (via the caller) or applies incoming ones (via cc_client).
    """

    def __init__(
        self,
        endpoint: MemoryEndpoint,
        mode: Any,
        map_write_gate: MapWriteGate | None = None,
        running_pause_threshold: int = 30,
        resync_send_limit: int | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.mode = mode
        self.map_write_gate = map_write_gate
        self.running_pause_threshold = running_pause_threshold
        self.resync_send_limit = resync_send_limit
        self._not_running_ticks = 0
        self._resync_send_queue: list[tuple[int, int]] = []
        self.cache: dict[int, int] = {}
        self.did_cache = False
        self.force_send = False
        self.sleep_queue: list[dict] = []

    def is_game_running(self, snapshot: dict[int, int]) -> bool:
        return self.mode.is_running(snapshot)

    def implausible_snapshot_reason(self, snapshot: dict[int, int]) -> str | None:
        for addr, record in self.mode.SYNC.items():
            if addr not in snapshot:
                continue
            reason = self.implausible_change_reason(addr, snapshot[addr])
            if reason:
                return reason
        return None

    def implausible_change_reason(self, addr: int, value: int) -> str | None:
        record = self.mode.SYNC.get(addr)
        if record is None:
            return None
        max_value = self._max_plausible_value(record)
        if max_value is None:
            return None
        if value < 0 or value > max_value:
            label = self._record_label(record)
            return f"0x{addr:04X} {label} value {value} exceeds max {max_value}"
        return None

    def check_first_running(self, snapshot: dict[int, int]) -> list[tuple[int, int]]:
        """Populate cache on the first running tick. If force_send is True,
        return a list of (addr, value) pairs to broadcast."""
        if self.implausible_snapshot_reason(snapshot):
            return []
        if self.did_cache:
            return []
        to_send: list[tuple[int, int]] = []
        for addr in self.mode.SYNC:
            value = snapshot.get(addr, 0)
            if addr not in self.cache:
                self.cache[addr] = value
            if (
                self.force_send
                and value != 0
                and self.mode.SYNC[addr].get("kind") != "delta"
            ):
                to_send.append((addr, value))
        self.did_cache = True
        if self.force_send and self.resync_send_limit is not None:
            self._resync_send_queue.extend(to_send)
            return self.drain_resync_send_queue()
        if self.force_send:
            self.force_send = False
        return to_send

    def diff(self, snapshot: dict[int, int]) -> list[tuple[int, int, str | None]]:
        """For each watched address that changed since last poll, run
        record_changed (sending side). Returns list of (addr, send_value, message)
        for changes that should be transmitted."""
        if self.implausible_snapshot_reason(snapshot):
            return []
        out: list[tuple[int, int, str | None]] = []
        for addr, record in self.mode.SYNC.items():
            cur = snapshot.get(addr, 0)
            prev = self.cache.get(addr, cur)
            if cur == prev:
                continue
            allow, send_value = record_changed(record, cur, prev, receiving=False)
            if allow:
                self.cache[addr] = cur
                msg = self._build_send_message(record, cur, prev)
                out.append((addr, send_value, msg))
            elif self._should_cache_suppressed_delta(record, cur, prev):
                self.cache[addr] = cur
        return out

    def handle_table(
        self,
        t: dict,
        *,
        queue_if_not_running: bool = True,
        now: float | None = None,
        _bypass_map_write_gate: bool = False,
    ) -> list[str]:
        """Apply a partner's data frame to RAM. Returns any user-visible messages."""
        return self.handle_table_result(
            t,
            queue_if_not_running=queue_if_not_running,
            now=now,
            _bypass_map_write_gate=_bypass_map_write_gate,
        ).messages

    def handle_table_result(
        self,
        t: dict,
        *,
        queue_if_not_running: bool = True,
        now: float | None = None,
        _bypass_map_write_gate: bool = False,
    ) -> IncomingApplyResult:
        """Apply a partner's data frame and return messages plus handling detail."""
        addr = t.get("addr")
        if addr is None:
            return IncomingApplyResult(status="missing_addr")
        value = t.get("value")
        if value is None:
            return IncomingApplyResult(addr=int(addr), status="missing_value")
        addr = int(addr)
        incoming_value = int(value)
        result = IncomingApplyResult(
            addr=addr,
            incoming_value=incoming_value,
            status="received",
        )
        record = self.mode.SYNC.get(addr)
        if record is None:
            result.status = "unknown_addr"
            result.messages = [f"Partner changed unknown address 0x{addr:04X}"]
            return result
        invalid_reason = self.implausible_change_reason(addr, incoming_value)
        if invalid_reason:
            result.status = "implausible"
            result.messages = [f"Ignoring implausible partner change: {invalid_reason}"]
            return result
        if addr == REVISION_0_FLAG and incoming_value == REVISION_0_FLAG_SET:
            # A beta 8 EDN8 partner's own patch flag, read as room $7F's saved flags (it resends it
            # after a reconnect): ORing it in would mark that room in this player's save.
            result.status = "partner_patch_flag"
            return result
        if (
            not _bypass_map_write_gate
            and self.map_write_gate is not None
            and self._should_gate_map_write(addr, record)
        ):
            self.map_write_gate.enqueue(addr, incoming_value, now=now)
            result.status = "deferred_map_write"
            return result
        if queue_if_not_running:
            try:
                running = read_running_byte(self.endpoint, self.mode)
            except Exception:
                running = None
            if not running:
                self.sleep_queue.append(dict(t))
                result.status = "queued_not_running"
                return result
        try:
            previous_value = self.endpoint.read_byte(addr)
        except Exception as exc:
            result.status = "read_error"
            result.messages = [f"Could not read address 0x{addr:04X}: {endpoint_error(exc)}"]
            return result
        if previous_value is None:
            result.status = "read_error"
            result.messages = [f"Could not read address 0x{addr:04X}"]
            return result
        result.previous_value = int(previous_value) & 0xFF
        allow, value = record_changed(record, incoming_value, previous_value, receiving=True)
        result.written_value = int(value) & 0xFF
        if allow:
            try:
                wrote = self.endpoint.write_pairs([(addr, value & 0xFF)])
            except NotImplementedError:
                wrote = False
            except Exception as exc:
                result.status = "write_error"
                result.messages = [f"Could not write address 0x{addr:04X}: {endpoint_error(exc)}"]
                return result
            if not wrote:
                result.status = "write_error"
                result.messages = [f"Could not write address 0x{addr:04X}"]
                return result
            result.status = "applied"
            self.cache[addr] = value & 0xFF
            result.messages = self._build_receive_messages(record, value, previous_value)
        else:
            result.status = "no_change"
        return result

    def observe_running(self) -> None:
        self._not_running_ticks = 0

    def observe_not_running(self) -> bool:
        """Return True only when a sustained non-running state should pause sync."""
        self._not_running_ticks += 1
        if self._not_running_ticks < self.running_pause_threshold:
            return False
        if self.did_cache:
            self.did_cache = False
            return True
        return False

    def drain_map_write_gate(self, *, now: float | None = None) -> list[str]:
        if self.map_write_gate is None or not self.map_write_gate.is_due(now=now):
            return []
        try:
            running = read_running_byte(self.endpoint, self.mode)
        except Exception:
            return []
        if not running:
            return []
        item = self.map_write_gate.pop_due(now=now)
        if item is None:
            return []
        addr, value = item
        return self.handle_table(
            {"addr": addr, "value": value},
            queue_if_not_running=False,
            now=now,
            _bypass_map_write_gate=True,
        )

    @property
    def map_write_pending_count(self) -> int:
        if self.map_write_gate is None:
            return 0
        return self.map_write_gate.pending_count

    @property
    def resync_send_pending_count(self) -> int:
        return len(self._resync_send_queue)

    def drain_resync_send_queue(self) -> list[tuple[int, int]]:
        if not self._resync_send_queue:
            self.force_send = False
            return []
        limit = self.resync_send_limit
        if limit is None or limit <= 0:
            limit = len(self._resync_send_queue)
        chunk = self._resync_send_queue[:limit]
        del self._resync_send_queue[:limit]
        if not self._resync_send_queue:
            self.force_send = False
        return chunk

    def drain_sleep_queue(self) -> list[str]:
        messages: list[str] = []
        for result in self.drain_sleep_queue_results():
            messages.extend(result.messages)
        return messages

    def drain_sleep_queue_results(self) -> list[IncomingApplyResult]:
        queued = self.sleep_queue
        self.sleep_queue = []
        results: list[IncomingApplyResult] = []
        for item in queued:
            results.append(self.handle_table_result(item, queue_if_not_running=False))
        return results

    def resync(self) -> None:
        """Clear cache and arm force_send so the next running tick re-broadcasts state."""
        self.cache.clear()
        self._resync_send_queue.clear()
        self.did_cache = False
        self.force_send = True
        self._not_running_ticks = 0

    def _build_send_message(self, record: dict, cur: int, prev: int) -> str | None:
        """Local-side analog of handle_table's message construction (for the
        sending peer's own UI: 'You picked up Wood Sword')."""
        if "name" in record and cur > prev:
            return f"You got {record['name']}"
        if "name_map" in record and cur > prev:
            idx = cur - 1
            if 0 <= idx < len(record["name_map"]):
                return f"You got {record['name_map'][idx]}"
        return None

    @staticmethod
    def _build_receive_messages(
        record: dict,
        value: int,
        previous_value: int,
    ) -> list[str]:
        if "receive_trigger" in record:
            msg = record["receive_trigger"](value, previous_value)
            return [msg] if msg else []
        if "message" in record:
            msg = record["message"](value, previous_value)
            return [msg] if msg else []

        verb = record.get("verb", "got")
        if "name" in record and value != previous_value:
            return [f"Partner {verb} {record['name']}"]
        if "name_map" in record and value > 0 and value != previous_value:
            idx = value - 1
            if 0 <= idx < len(record["name_map"]):
                return [f"Partner {verb} {record['name_map'][idx]}"]
            return []
        if "name_bitmap" in record:
            messages = []
            for bit, name in enumerate(record["name_bitmap"]):
                mask = 1 << bit
                if value & mask and not previous_value & mask:
                    messages.append(f"Partner {verb} {name}")
            return messages
        return []

    @staticmethod
    def _max_plausible_value(record: dict) -> int | None:
        if record.get("kind") != "high":
            return None
        if "name_map" in record:
            return len(record["name_map"])
        if "name" in record:
            return 1
        return None

    @staticmethod
    def _record_size_mask(record: dict) -> int:
        size = record.get("size", 1)
        if size == 2:
            return 0xFFFF
        if size == 4:
            return 0xFFFFFFFF
        return 0xFF

    @staticmethod
    def _record_label(record: dict) -> str:
        if "name" in record:
            return record["name"]
        if "name_map" in record:
            return "/".join(record["name_map"])
        return str(record.get("kind", "record"))

    @staticmethod
    def _should_gate_map_write(addr: int, record: dict) -> bool:
        return 0x067F <= addr <= 0x07FE and record.get("kind") == "bitOr"

    @staticmethod
    def _should_cache_suppressed_delta(
        record: dict[str, Any],
        value: int,
        previous_value: int,
    ) -> bool:
        if record.get("kind") != "delta" or not record.get("ignoreZeroBoundary"):
            return False
        mask = SyncEngine._record_size_mask(record)
        if "mask" in record:
            mask = record["mask"]
        return (mask & value) == 0 or (mask & previous_value) == 0
