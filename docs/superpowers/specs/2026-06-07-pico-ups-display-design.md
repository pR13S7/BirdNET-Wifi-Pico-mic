# Pico2W UPS Status Display Design

## Goal

Display UPS power-source state and battery percentage on the existing LCD used by `pico2w/main.py`, without impacting microphone streaming stability.

## Scope

- Read power telemetry from Waveshare Pico-UPS-A (INA219 over I2C).
- Detect whether the system is running from external power (`USB`) or battery (`BAT`).
- Estimate and display battery percentage and voltage.
- Keep UPS integration non-fatal and low-frequency so audio streaming remains primary.

Out of scope:

- Precise battery fuel-gauge calibration across different battery chemistries.
- New screens or UI navigation flows.

## Hardware and Interfaces

- Board: Raspberry Pi Pico 2W
- UPS: Waveshare Pico-UPS-A
- I2C pins: `SDA=GP6`, `SCL=GP7`
- LCD: existing Waveshare LCD integration in `pico2w/lcd.py` and `pico2w/main.py`

## Architecture

Add a lightweight UPS monitor section into `pico2w/main.py`:

1. **UPS init**
   - Initialize I2C bus using `machine.I2C` on `GP6/GP7`.
   - Probe INA219 at `0x43` first (Waveshare default), with optional fallback scan (`0x40/0x41/0x44/0x45`) for compatibility.
2. **UPS sample state**
   - Keep mutable state for:
     - last sample timestamp
     - source mode (`USB`, `BAT`, `UNKNOWN`)
     - battery voltage (V)
     - current (mA)
     - battery percent (0-100)
     - availability/error flag
3. **Polling cadence**
   - Read INA219 on a coarse timer (every 3000 ms), not inside per-packet hot path.
4. **Display integration**
   - Extend `draw_info()` to render one UPS line, reusing current text layout.

## Data Mapping Rules

### Power Source

- Use current sign with deadband hysteresis:
  - `current_mA > +20` => `USB` (charging / external input present)
  - `current_mA < -20` => `BAT` (discharging / running on battery)
  - Otherwise keep previous state to avoid flicker near zero current.

### Battery Percentage

- Estimate from battery voltage:
  - `V_EMPTY = 3.0`
  - `V_FULL = 4.2`
  - `pct = clamp((voltage - V_EMPTY) / (V_FULL - V_EMPTY) * 100, 0, 100)`
- Display rounded integer percent plus voltage with 2 decimals.

## UI/Display Behavior

Display format (single line):

- Normal: `PWR: USB  BAT: 87%  4.06V`
- Battery mode: `PWR: BAT  BAT: 52%  3.78V`
- Sensor unavailable: `PWR: ?  BAT: N/A`

Screen-on/off toggle behavior remains unchanged.

## Error Handling

- INA219 init failure must not stop boot or streaming.
- Read failures during runtime:
  - keep last known good display values
  - mark sensor unavailable after 2 consecutive failures
  - retry on next poll cycle
- Any UPS exception is contained in UPS code path and never bubbles into audio transport loop.

## Testing and Validation

Hardware checks:

1. Boot with USB connected + battery installed -> expect `PWR: USB`.
2. Unplug USB while running -> expect transition to `PWR: BAT`.
3. Replug USB -> expect transition back to `PWR: USB`.
4. Temporarily break UPS I2C path -> expect `BAT: N/A` while streaming continues.

Functional checks:

- Confirm LCD refresh still updates once per stats interval.
- Confirm no measurable impact on throughput logs (`KB/s`, packet count).

## Implementation Plan (High-level)

1. Add INA219 register read helpers and UPS state container in `pico2w/main.py`.
2. Add periodic `update_ups_state(now_ms)` call in main streaming loop.
3. Extend `draw_info()` signature to accept UPS status fields.
4. Update `update_display()` and all call sites to pass UPS data.
5. Keep defensive fallbacks for missing UPS/LCD paths.
