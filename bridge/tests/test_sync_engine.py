"""Behavioral parity tests for record_changed() — verified against driver.lua."""
import pytest

from bridge_core.sync_engine import record_changed


def test_high_kind_send_when_increased():
    record = {"kind": "high", "size": 1}
    allow, value = record_changed(record, value=1, previous_value=0, receiving=False)
    assert allow is True
    assert value == 1


def test_high_kind_no_send_when_decreased():
    record = {"kind": "high", "size": 1}
    allow, _value = record_changed(record, value=0, previous_value=1, receiving=False)
    assert allow is False


def test_high_kind_no_send_when_same():
    record = {"kind": "high", "size": 1}
    allow, _value = record_changed(record, value=1, previous_value=1, receiving=False)
    assert allow is False


def test_delta_kind_send_when_changed():
    record = {"kind": "delta", "size": 1}
    allow, value = record_changed(record, value=8, previous_value=0, receiving=False)
    assert allow is True
    assert value == 8  # delta = 8 - 0


def test_delta_kind_can_ignore_zero_boundary_startup_transitions():
    record = {"kind": "delta", "size": 1, "ignoreZeroBoundary": True}

    assert record_changed(record, value=0, previous_value=8, receiving=False) == (False, 0)
    assert record_changed(record, value=8, previous_value=0, receiving=False) == (False, 8)
    assert record_changed(record, value=16, previous_value=8, receiving=False) == (True, 8)
    assert record_changed(record, value=8, previous_value=16, receiving=False) == (True, -8)


def test_delta_kind_receive_applies_delta():
    record = {"kind": "delta", "size": 1}
    allow, value = record_changed(record, value=8, previous_value=4, receiving=True)
    assert allow is True
    assert value == 12  # 4 + 8


def test_delta_kind_clamp_min():
    record = {"kind": "delta", "size": 1, "deltaMin": 1}
    allow, value = record_changed(record, value=5, previous_value=0, receiving=True)
    # without clamp, 0+5=5; clamp doesn't trigger
    assert allow is True
    assert value == 5


def test_delta_kind_no_send_zero_delta_on_receive():
    record = {"kind": "delta", "size": 1}
    allow, _value = record_changed(record, value=0, previous_value=4, receiving=True)
    # delta of 0 means no change; allow=False
    assert allow is False


def test_bitor_kind_changes_bits():
    record = {"kind": "bitOr", "size": 1}
    allow, value = record_changed(record, value=0b1010, previous_value=0b0001, receiving=True)
    assert allow is True
    assert value == 0b1011  # OR


def test_masked_bitor_send_uses_masked_value():
    record = {"kind": "bitOr", "size": 1, "mask": 0x90}

    allow, value = record_changed(record, value=0x94, previous_value=0x00, receiving=False)

    assert allow is True
    assert value == 0x90


def test_function_kind_invokes_callback():
    captured = []

    def custom_kind(val, prev, recv):
        captured.append((val, prev, recv))
        return True, val * 2

    record = {"kind": custom_kind}
    allow, value = record_changed(record, value=5, previous_value=2, receiving=True)
    assert allow is True
    assert value == 10
    assert captured == [(5, 2, True)]


from bridge_core.memory_endpoint import DictMemoryEndpoint
from bridge_core.map_write_gate import MapWriteGate
from bridge_core.modes import tloz_all
from bridge_core.sync_engine import SyncEngine


def make_engine(
    memory=None,
    map_write_gate=None,
    resync_send_limit=None,
) -> tuple[SyncEngine, DictMemoryEndpoint]:
    endpoint = DictMemoryEndpoint(memory or {})
    engine = SyncEngine(
        endpoint=endpoint,
        mode=tloz_all,
        map_write_gate=map_write_gate,
        resync_send_limit=resync_send_limit,
    )
    return engine, endpoint


def test_sync_engine_starts_idle():
    engine, _endpoint = make_engine()
    assert engine.did_cache is False
    assert engine.is_game_running({0x12: 0x0}) is False


def test_sync_engine_detects_running():
    engine, _endpoint = make_engine()
    assert engine.is_game_running({0x12: 0x5}) is True


def test_sync_engine_caches_on_first_running_tick():
    engine, _endpoint = make_engine({0x0657: 0, 0x12: 0x5})
    engine.check_first_running({0x0657: 0, 0x12: 0x5})
    assert engine.did_cache is True
    assert engine.cache[0x0657] == 0


def test_sync_engine_handle_table_writes_to_ram():
    engine, endpoint = make_engine({0x0012: 0x05, 0x0657: 0})
    engine.cache[0x0657] = 0
    engine.handle_table({"addr": 0x0657, "value": 1})
    assert endpoint.read_byte(0x0657) == 1


def test_sync_engine_handle_table_rejects_implausible_partner_item_value():
    engine, endpoint = make_engine({0x0012: 0x05, 0x065B: 0})
    engine.cache[0x065B] = 0

    messages = engine.handle_table({"addr": 0x065B, "value": 0xFF})

    assert endpoint.read_byte(0x065B) == 0
    assert messages == [
        "Ignoring implausible partner change: 0x065B Blue Candle/Red Candle value 255 exceeds max 2"
    ]


def test_sync_engine_gates_and_coalesces_incoming_map_writes():
    gate = MapWriteGate(drain_interval_s=0)
    engine, endpoint = make_engine({0x0012: 0x05, 0x067F: 0}, map_write_gate=gate)

    assert engine.handle_table({"addr": 0x067F, "value": 0x10}, now=0) == []
    assert engine.handle_table({"addr": 0x067F, "value": 0x90}, now=0) == []

    assert endpoint.read_byte(0x067F) == 0
    assert gate.pending_count == 1

    assert engine.drain_map_write_gate(now=0) == []

    assert endpoint.read_byte(0x067F) == 0x90
    assert gate.pending_count == 0


def test_sync_engine_map_write_gate_does_not_delay_inventory_writes():
    gate = MapWriteGate(drain_interval_s=100)
    engine, endpoint = make_engine(
        {0x0012: 0x05, 0x0657: 0, 0x067F: 0},
        map_write_gate=gate,
    )

    messages = engine.handle_table({"addr": 0x0657, "value": 1}, now=0)

    assert endpoint.read_byte(0x0657) == 1
    assert messages == ["Partner got Wood Sword"]
    assert gate.pending_count == 0


def test_sync_engine_map_write_gate_drains_one_write_per_interval():
    gate = MapWriteGate(drain_interval_s=1.0)
    engine, endpoint = make_engine(
        {0x0012: 0x05, 0x067F: 0, 0x0680: 0},
        map_write_gate=gate,
    )
    engine.handle_table({"addr": 0x067F, "value": 0x10}, now=0)
    engine.handle_table({"addr": 0x0680, "value": 0x80}, now=0)

    engine.drain_map_write_gate(now=0)
    engine.drain_map_write_gate(now=0.5)

    assert endpoint.read_byte(0x067F) == 0x10
    assert endpoint.read_byte(0x0680) == 0
    assert gate.pending_count == 1

    engine.drain_map_write_gate(now=1.0)

    assert endpoint.read_byte(0x0680) == 0x80
    assert gate.pending_count == 0


def test_sync_engine_map_write_gate_waits_until_game_is_running():
    gate = MapWriteGate(drain_interval_s=0)
    engine, endpoint = make_engine({0x0012: 0x00, 0x067F: 0}, map_write_gate=gate)
    engine.handle_table({"addr": 0x067F, "value": 0x10}, now=0)

    engine.drain_map_write_gate(now=0)

    assert endpoint.read_byte(0x067F) == 0
    assert gate.pending_count == 1

    endpoint.memory[0x0012] = 0x05
    engine.drain_map_write_gate(now=0)

    assert endpoint.read_byte(0x067F) == 0x10
    assert gate.pending_count == 0


def test_sync_engine_handle_table_emits_message():
    engine, _endpoint = make_engine({0x0012: 0x05, 0x0657: 0})
    engine.cache[0x0657] = 0
    msgs = engine.handle_table({"addr": 0x0657, "value": 1})
    assert any("Wood Sword" in m for m in msgs)


def test_sync_engine_handle_table_emits_single_name_message():
    engine, _endpoint = make_engine({0x0012: 0x05, 0x0660: 0})

    msgs = engine.handle_table({"addr": 0x0660, "value": 1})

    assert msgs == ["Partner got Raft"]


def test_sync_engine_handle_table_emits_bitmap_messages():
    engine, endpoint = make_engine({0x0012: 0x05, 0x0671: 0})

    msgs = engine.handle_table({"addr": 0x0671, "value": 0x05})

    assert endpoint.read_byte(0x0671) == 0x05
    assert msgs == [
        "Partner got First Triforce Piece",
        "Partner got Third Triforce Piece",
    ]


def test_sync_engine_drain_sleep_queue_returns_incoming_results():
    engine, endpoint = make_engine({0x0012: 0x00, 0x0660: 0})

    assert engine.handle_table({"addr": 0x0660, "value": 1}) == []
    endpoint.memory[0x0012] = 0x05
    results = engine.drain_sleep_queue_results()

    assert len(results) == 1
    assert results[0].applied
    assert results[0].addr == 0x0660
    assert results[0].previous_value == 0
    assert results[0].written_value == 1
    assert results[0].messages == ["Partner got Raft"]


def test_sync_engine_resync_clears_cache():
    engine, _endpoint = make_engine()
    engine.cache[0x0657] = 1
    engine.did_cache = True
    engine.resync()
    assert engine.did_cache is False
    assert engine.force_send is True


def test_sync_engine_force_send_skips_delta_records():
    engine, _endpoint = make_engine()
    engine.force_send = True

    to_send = engine.check_first_running({0x0012: 0x05, 0x0657: 1, 0x067C: 8})

    assert (0x0657, 1) in to_send
    assert not any(addr == 0x067C for addr, _value in to_send)


def test_sync_engine_throttles_force_send_when_limit_is_set():
    engine, _endpoint = make_engine(resync_send_limit=2)
    engine.resync()

    first = engine.check_first_running(
        {
            0x0012: 0x05,
            0x0657: 1,
            0x065A: 1,
            0x065C: 1,
            0x067C: 8,
        }
    )
    second = engine.drain_resync_send_queue()
    third = engine.drain_resync_send_queue()

    assert first == [(0x0657, 1), (0x065A, 1)]
    assert second == [(0x065C, 1)]
    assert third == []
    assert not any(addr == 0x067C for addr, _value in first + second + third)


def test_sync_engine_suppressed_zero_boundary_delta_updates_cache():
    engine, _endpoint = make_engine()
    engine.cache[0x067C] = 8

    assert engine.diff({0x0012: 0x05, 0x067C: 0}) == []
    assert engine.cache[0x067C] == 0

    assert engine.diff({0x0012: 0x05, 0x067C: 8}) == []
    assert engine.cache[0x067C] == 8

    assert engine.diff({0x0012: 0x05, 0x067C: 16}) == [
        (0x067C, 8, None)
    ]


def test_sync_engine_rejects_impossible_high_item_snapshot():
    engine, _endpoint = make_engine()

    reason = engine.implausible_snapshot_reason({0x0012: 0x05, 0x065A: 0xFF})

    assert reason == "0x065A Bow value 255 exceeds max 1"


def test_sync_engine_reports_impossible_single_high_item_change():
    engine, _endpoint = make_engine()

    reason = engine.implausible_change_reason(0x065B, 0xFF)

    assert reason == "0x065B Blue Candle/Red Candle value 255 exceeds max 2"


def test_sync_engine_allows_masked_overworld_map_noise_change():
    engine, _endpoint = make_engine()

    reason = engine.implausible_change_reason(0x067F, 0x94)

    assert reason is None


def test_sync_engine_allows_masked_overworld_map_noise_snapshot():
    engine, _endpoint = make_engine()

    reason = engine.implausible_snapshot_reason({0x0012: 0x05, 0x067F: 0x94})

    assert reason is None


def test_sync_engine_masked_overworld_noise_does_not_block_progress_diff():
    engine, _endpoint = make_engine()
    engine.cache[0x0668] = 0
    engine.cache[0x06E5] = 0

    changes = engine.diff({0x0012: 0x05, 0x0668: 0x80, 0x06E5: 0x01})

    assert (0x0668, 0x80, None) in changes
    assert not any(addr == 0x06E5 for addr, _value, _msg in changes)


def test_sync_engine_debounces_transient_not_running_before_clearing_cache():
    engine, _endpoint = make_engine()
    engine.running_pause_threshold = 3
    engine.did_cache = True

    assert engine.observe_not_running() is False
    assert engine.observe_not_running() is False
    assert engine.did_cache is True

    assert engine.observe_not_running() is True
    assert engine.did_cache is False


def test_sync_engine_running_state_resets_not_running_debounce():
    engine, _endpoint = make_engine()
    engine.running_pause_threshold = 3
    engine.did_cache = True

    assert engine.observe_not_running() is False
    engine.observe_running()
    assert engine.observe_not_running() is False
    assert engine.observe_not_running() is False
    assert engine.did_cache is True


def test_sync_engine_ignores_a_beta_8_partners_patch_flag():
    """A beta 8 EDN8 partner resends its $07FE (its patch flag, always $AB) after a reconnect; it
    must not mark room $7F of levels 7-9 here. Any other value there is synced as before."""
    engine, endpoint = make_engine({0x0012: 0x05, 0x07FE: 0x00})
    engine.cache[0x07FE] = 0

    assert engine.handle_table_result({"addr": 0x07FE, "value": 0xAB}).status == "partner_patch_flag"
    assert endpoint.read_byte(0x07FE) == 0

    engine.handle_table({"addr": 0x07FE, "value": 0x80})
    assert endpoint.read_byte(0x07FE) == 0x80
