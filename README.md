# esp32-claude

Desk display for Claude Code token usage. A Windows Python script reads
[ccusage](https://github.com/ryoppippi/ccusage) and pushes it to a round
ESP32 display over BLE.

Unofficial project. Not affiliated with or endorsed by Anthropic.

Full design spec: [docs/handover.md](docs/handover.md).

## Hardware

Generic ESP32 dev board + round GC9A01 240x240 SPI display with resistive
touch. Pin mapping lives in `firmware/lib/TFT_eSPI_Setup/User_Setup.h`
(persists there regardless of which ESP32 board variant `firmware/platformio.ini`
targets).

## Status

Flashed and verified end-to-end on real hardware: boots, advertises over
BLE, host connects at full requested MTU (64 bytes), time syncs, and pushes
real ccusage data successfully (see `docs/BUILD_PROGRESS.md` for the full
log). **Not yet confirmed:** what the round display actually looks like —
needs a human looking at the physical screen.

Shows, on the round display: an arc gauge around the rim for `block_pct`,
today's token count as the big centre numeral, this week's token total as a
small caption above it, the current block's reset time in Singapore time
(fixed UTC+8, no DST) below the numeral, and a connection-status dot
(green/yellow/red for fresh/stale/disconnected) with an age caption.

## Firmware (`firmware/`)

PlatformIO project, `esp32dev` env, Arduino framework, NimBLE (not Bluedroid —
saves ~100KB flash on a board with no PSRAM).

```
cd firmware
pio run                 # build
pio run -t upload       # flash (board connected over USB, CH340/CP2102 bridge)
pio device monitor      # serial @ 115200
```

Advertises as `esp32-claude` with one GATT service exposing two characteristics:

| Characteristic | Properties | Payload |
|---|---|---|
| Usage State | Read, Write | `UsageState` struct (34 bytes, version 2) |
| Time Sync | Write | `uint32_t` epoch seconds |

**Deviation from the original spec:** `docs/handover.md` lists Usage State as
"Read, Notify". Notify only flows server→client, but the data source here is
the host — the host must *write* the struct to the device. Implemented as
Read+Write instead; Read stays for manual inspection with nRF Connect during
bring-up.

## Host (`host/`)

```
cd host
pip install -r requirements.txt
python esp32-claude.py
```

Reads `ccusage daily`, `ccusage weekly`, and `ccusage blocks` (all
`--json --offline`) every 5 minutes, packs the result into the same
`UsageState` struct the firmware expects, and pushes it over BLE. Reconnects
automatically if the device or the laptop's Bluetooth radio drops (lid
close/sleep is normal, not an error).

Pinned against `ccusage 20.0.19` — schemas have already drifted once between
versions during this project (see `host/ccusage_reader.py` docstring); re-verify
with `jq 'keys'` on live output before bumping it.

### Calibrating `block_pct`

ccusage reports tokens used in the current 5-hour block but not your plan's
limit — nothing exposes that. `block_pct` reads 0 until you calibrate it:

1. Run the host script and use Claude Code normally until you actually hit a
   rate limit.
2. Note the `block_tokens` value the script printed right before that happened.
3. Set `BLOCK_TOKEN_CEILING` in `host/config.py` to that number.

Until then the arc gauge stays empty rather than showing a made-up percentage.
`*_cents` fields are what those tokens would cost at API rates, not money
actually spent on a subscription — label any UI accordingly.

### Autostart (Windows)

```
powershell -ExecutionPolicy Bypass -File host\install_autostart.ps1
```

Registers a per-user Scheduled Task (`esp32-claude-host`) that runs the host
script at logon. Remove with
`Unregister-ScheduledTask -TaskName esp32-claude-host -Confirm:$false`.

## Build order

See `docs/handover.md` section 8 for the full sequence and rationale.
`docs/BUILD_PROGRESS.md` tracks current status against it.
