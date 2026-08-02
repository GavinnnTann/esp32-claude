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

Working end-to-end on real hardware: boots, advertises over BLE, host
connects at the full requested MTU of 247, time syncs, and pushes live data
whose percentages match Claude Code's own Account & Usage panel exactly
(see `docs/BUILD_PROGRESS.md` for the full log).

Three views, cycled with the two buttons (GPIO4 up / GPIO19 down):

```
   SESSION            WEEKLY             TODAY
     35%                11%              141.0M
  57.8M tok $10.81   645.1M tok $49.81  today $26.19
  resets 12:20 SGT   resets 05:00 +5d   opus-5 / xhigh
      • ○ ○              ○ • ○              ○ ○ •
```

An arc around the rim tracks the percentage, coloured green / orange / red at
70% / 90% like Claude.ai's own usage bar. A dot at the top shows connection
state (green fresh / yellow stale / red disconnected); the dots at the bottom
show which view you're on.

The percentages are the **real** figures from Claude Code's own cache, not an
estimate — see "Quota percentages" below.

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
| Usage State | Read, Write | `UsageState` struct (68 bytes, version 4) |
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

The current model and reasoning effort come from a second source: ccusage
exposes model names but **drops the `effort` field during aggregation**, so
`host/transcript_reader.py` reads both directly from Claude Code's own
transcripts (`~/.claude/projects/**/*.jsonl`). Only the `effort` and
`message.model` fields are read — never conversation content.

Pinned against `ccusage 20.0.19` — schemas have already drifted once between
versions during this project (see `host/ccusage_reader.py` docstring); re-verify
with `jq 'keys'` on live output before bumping it.

### Quota percentages

The session and weekly percentages are the **real** figures — the same ones
Claude Code's own Account & Usage panel shows. They're read from
`~/.claude.json`'s `cachedUsageUtilization`, which Claude Code refreshes from
the server.

No calibration required. An earlier version of this project tried to estimate
them against a hand-tuned token ceiling (per `docs/handover.md` section 4,
which assumed the plan limit wasn't exposed) — that's obsolete. It also
wouldn't have worked well: the percentages are weighted, since longer contexts
cost more even when cached, so they aren't a linear function of token counts.

Two caveats:

- It's a **cache**, refreshed only while Claude Code is running. If Claude
  Code has been closed for a while the percentages freeze; `limits_fetched`
  is sent to the device so this can be surfaced.
- ccusage's rolling 5-hour block boundary is **not** the quota reset (seen
  12:00 vs the real 12:20 SGT). The display uses the real `resets_at`.

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
