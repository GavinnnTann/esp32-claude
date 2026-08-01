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

Firmware and host code are written and build/import cleanly, but **the
display has not yet been flashed and visually verified on real hardware** —
that's the next step (see "Build order" below). Treat anything on-screen as
unverified until then.

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
| Usage State | Read, Write | `UsageState` struct (26 bytes) |
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

Reads `ccusage daily --json --offline` and `ccusage blocks --json --offline`
every 5 minutes, packs the result into the same `UsageState` struct the
firmware expects, and pushes it over BLE. Reconnects automatically if the
device or the laptop's Bluetooth radio drops (lid close/sleep is normal, not
an error).

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

Not yet wired up — planned via Task Scheduler (run `python esp32-claude.py`
at login, no window). See `docs/BUILD_PROGRESS.md` for status.

## Build order

See `docs/handover.md` section 8 for the full sequence and rationale.
`docs/BUILD_PROGRESS.md` tracks current status against it.
