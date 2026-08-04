"""BLE central: connects to the esp32-claude peripheral, syncs its clock, and
pushes a fresh UsageState every poll interval. See docs/handover.md section 5.

Windows-tested (bleak's WinRT backend) per docs/handover.md's open decisions.
"""

from __future__ import annotations

import asyncio
import struct
import time
from typing import Callable

from bleak import BleakClient, BleakScanner

import config
from usage_state import STRUCT_SIZE, TIME_SYNC_UUID, USAGE_STATE_UUID, UsageState

DEVICE_NAME = "esp32-claude"
SCAN_TIMEOUT_S = 10.0
RECONNECT_DELAY_S = 5.0

# How often to re-read state and look for a change. Much shorter than the
# push/heartbeat interval because the quota percentages — the numbers actually
# worth watching — come from a cheap local file read; the expensive ccusage
# subprocesses are TTL-cached inside ccusage_reader, so polling this often
# costs almost nothing.
CHECK_INTERVAL_S = 15.0

# UsageState needs STRUCT_SIZE + 3 bytes of ATT header to land in one packet.
# Derived rather than hardcoded so it tracks the struct automatically — this
# was 29 when the struct was 26 bytes and would have silently gone stale.
# Below this, delivery relies on the stack's queued/long writes (handover.md #4).
MIN_MTU_FOR_SINGLE_PACKET = STRUCT_SIZE + 3


async def _sync_time(client: BleakClient) -> None:
    epoch = int(time.time())
    await client.write_gatt_char(TIME_SYNC_UUID, struct.pack("<I", epoch), response=True)
    print(f"[ble] time synced: epoch={epoch}")


async def _push_state(client: BleakClient, state: UsageState) -> None:
    await client.write_gatt_char(USAGE_STATE_UUID, state.pack(), response=True)


async def run_forever(poll_interval_s: float, get_state: Callable[[], UsageState]) -> None:
    """Connects, syncs time, and pushes `get_state()` every `poll_interval_s`
    seconds until the process is killed. Reconnects on any disconnect or error —
    the laptop sleeping/waking or the device rebooting are normal cases, not
    fatal ones (docs/handover.md section 5)."""
    while True:
        expected = (config.DEVICE_ADDRESS or "").strip()
        if expected:
            print(f"[ble] scanning for {expected}...")
            device = await BleakScanner.find_device_by_address(expected, timeout=SCAN_TIMEOUT_S)
            missing = f"[ble] {expected} not found"
        else:
            print(f'[ble] scanning for "{DEVICE_NAME}"...')
            device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=SCAN_TIMEOUT_S)
            missing = f'[ble] "{DEVICE_NAME}" not found'

        if device is None:
            print(f"{missing}, retrying in {RECONNECT_DELAY_S}s")
            await asyncio.sleep(RECONNECT_DELAY_S)
            continue

        # Belt and braces: find_device_by_address already filters, but the
        # address is the thing being trusted, so it gets checked rather than
        # assumed. Case-insensitive - backends differ on how they format it.
        if expected and (device.address or "").lower() != expected.lower():
            print(f"[ble] REFUSING {device.address}: expected {expected}")
            await asyncio.sleep(RECONNECT_DELAY_S)
            continue

        disconnected = asyncio.Event()
        client = BleakClient(device, disconnected_callback=lambda _c: disconnected.set())

        try:
            await client.connect()
        except Exception as e:
            print(f"[ble] connect failed: {e}, retrying in {RECONNECT_DELAY_S}s")
            await asyncio.sleep(RECONNECT_DELAY_S)
            continue

        # docs/handover.md #4: never assume the requested MTU was granted — verify it.
        print(f"[ble] connected to {device.address}, MTU={client.mtu_size}")
        if client.mtu_size < MIN_MTU_FOR_SINGLE_PACKET:
            print(
                f"[ble] WARNING: MTU {client.mtu_size} can't fit UsageState in one packet "
                f"(needs {MIN_MTU_FOR_SINGLE_PACKET}); relying on queued writes — verify this "
                f"actually arrives intact on real hardware (build order step 4)"
            )

        try:
            await _sync_time(client)
            last_key = None
            last_push = 0.0
            while client.is_connected and not disconnected.is_set():
                # Off-thread even though ccusage_reader now refreshes in the
                # background: this still globs the transcript directory and
                # reads two JSON files, and anything synchronous here stalls
                # the BLE stack's own keepalive and disconnect handling too.
                state = await asyncio.to_thread(get_state)

                # Push when anything the display shows has actually changed, or
                # as a periodic heartbeat so the device's staleness indicator
                # doesn't drift toward "stale" while nothing is happening.
                # `ts` is excluded from the comparison because it changes every
                # call by definition and would make every poll look like news.
                key = state.pack()[5:]
                now = time.monotonic()
                if key != last_key or (now - last_push) >= poll_interval_s:
                    await _push_state(client, state)
                    last_key = key
                    last_push = now
                    print(f"[ble] pushed: {state}")

                try:
                    await asyncio.wait_for(disconnected.wait(), timeout=CHECK_INTERVAL_S)
                except asyncio.TimeoutError:
                    pass  # normal: no disconnect within the check interval
        except Exception as e:
            print(f"[ble] error while connected: {e}")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

        print(f"[ble] disconnected, reconnecting in {RECONNECT_DELAY_S}s")
        await asyncio.sleep(RECONNECT_DELAY_S)
