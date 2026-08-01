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

from usage_state import TIME_SYNC_UUID, USAGE_STATE_UUID, UsageState

DEVICE_NAME = "esp32-claude"
SCAN_TIMEOUT_S = 10.0
RECONNECT_DELAY_S = 5.0

# 26-byte UsageState needs at least this many ATT payload bytes (26 + 3 header) in
# one packet. Below this, delivery relies on the stack's automatic queued/long
# writes — see docs/handover.md section 4.
MIN_MTU_FOR_SINGLE_PACKET = 29


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
        print(f'[ble] scanning for "{DEVICE_NAME}"...')
        device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=SCAN_TIMEOUT_S)
        if device is None:
            print(f'[ble] "{DEVICE_NAME}" not found, retrying in {RECONNECT_DELAY_S}s')
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
            while client.is_connected and not disconnected.is_set():
                state = get_state()
                await _push_state(client, state)
                print(f"[ble] pushed: {state}")
                try:
                    await asyncio.wait_for(disconnected.wait(), timeout=poll_interval_s)
                except asyncio.TimeoutError:
                    pass  # normal: no disconnect within the poll interval
        except Exception as e:
            print(f"[ble] error while connected: {e}")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

        print(f"[ble] disconnected, reconnecting in {RECONNECT_DELAY_S}s")
        await asyncio.sleep(RECONNECT_DELAY_S)
