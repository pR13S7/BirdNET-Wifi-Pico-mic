# ESP32-S3-N16R8 WiFi Microphone for BirdNET

Stream audio from an INMP441 I2S microphone over WiFi to BirdNET-Pi using an ESP32-S3-N16R8 board.

## Hardware

- **Board:** ESP32-S3-N16R8 (16MB Flash, 8MB PSRAM)
- **Microphone:** INMP441 I2S MEMS digital microphone (same as the Pico version)

## Wiring: INMP441 → ESP32-S3-N16R8

![Wiring Diagram](wiring_inmp441_esp32s3.svg)

### Pin Summary

| INMP441 Pin | ESP32-S3 Pin | Function            |
|-------------|--------------|---------------------|
| VDD         | 3V3          | Power (3.3V)        |
| GND         | GND          | Ground              |
| SCK         | GPIO 4       | I2S Bit Clock       |
| WS          | GPIO 5       | I2S Word Select     |
| SD          | GPIO 6       | I2S Data Out        |
| L/R         | GND          | Left channel select |

### Notes

- **L/R pin** tied to GND selects the left channel. Tie to 3V3 for right channel.
- **No pull-up/pull-down resistors needed** — the INMP441 breakout includes them.
- **Any GPIO works** on ESP32-S3 for I2S (unlike Pico, there's no WS=SCK+1 constraint).
- **Keep wires short** (< 10cm) to avoid noise on the I2S bus.
- If your board has a different pinout or GPIO 4/5/6 are unavailable, change `I2S_SCK`, `I2S_WS`, `I2S_SD` in `main.py`.

## MicroPython Firmware for ESP32-S3-N16R8

### Where to Download

Official MicroPython builds for ESP32-S3 with OctalSPI PSRAM (N16R8):

**Download page:** https://micropython.org/download/ESP32_GENERIC_S3/

Pick the firmware file ending in:
```
ESP32_GENERIC_S3-SPIRAM_OCT-20xxxxxx-vX.X.X.bin
```

The `SPIRAM_OCT` variant is required for boards with 8MB octal PSRAM (like the N16R8).

### How to Flash

1. **Install esptool:**
   ```bash
   pip install esptool
   ```

2. **Put the board in download mode:**
   - Hold the **BOOT** button
   - Press and release **RESET**
   - Release **BOOT**
   
   (Some boards enter download mode automatically when flashing)

3. **Erase flash:**
   ```bash
   esptool.py --chip esp32s3 --port /dev/ttyACM0 erase_flash
   ```

4. **Flash MicroPython:**
   ```bash
   esptool.py --chip esp32s3 --port /dev/ttyACM0 write_flash -z 0 ESP32_GENERIC_S3-SPIRAM_OCT-20xxxxxx-vX.X.X.bin
   ```

5. **On macOS**, the port is typically `/dev/cu.usbmodem*` instead of `/dev/ttyACM0`.

6. **Press RESET** after flashing to boot into MicroPython.

### Verify Installation

Connect via serial (115200 baud) and you should see the MicroPython REPL:
```
>>> import machine
>>> machine.freq()
240000000
```

### Upload main.py

Use **mpremote** (recommended) or Thonny:

```bash
pip install mpremote
mpremote connect /dev/cu.usbmodem* cp main.py :main.py
mpremote connect /dev/cu.usbmodem* reset
```

## Configuration

Edit these values at the top of `main.py`:

```python
WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
SERVER_IP = "192.168.0.50"     # IP of your BirdNET-Pi
SERVER_PORT = 5005
```

## Differences from Pico Version

| Feature          | Pico 2W                    | ESP32-S3-N16R8            |
|------------------|----------------------------|---------------------------|
| I2S format       | Stereo (extract left ch)   | Mono (direct)             |
| Pin constraint   | WS must be SCK+1           | Any GPIO                  |
| WiFi PM          | `config(pm=0xa11140)`      | `PM_NONE`                 |
| CPU frequency    | 150 MHz                    | 240 MHz (default)         |
| RAM              | 520 KB                     | 512 KB + 8 MB PSRAM       |
| LCD support      | Yes (optional)             | Not included (add if needed) |
| Power mgmt       | `rp2.country()` + `freq()` | Default (already fast)    |

## Troubleshooting

- **No audio / silence:** Check L/R pin is connected to GND, verify GPIO numbers match your wiring.
- **WiFi won't connect:** Ensure 2.4 GHz network (ESP32-S3 doesn't support 5 GHz).
- **Firmware won't flash:** Make sure you're in download mode (BOOT + RESET sequence).
- **`OSError: [Errno 110]` on I2S:** Wrong pin numbers or mic not powered — check 3V3 connection.
- **Choppy audio:** Reduce distance to WiFi router, or increase `ibuf` in the I2S config.
