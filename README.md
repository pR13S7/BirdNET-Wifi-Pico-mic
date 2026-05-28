# Wireless Microphone Streaming to BirdNET-Pi

> **Build Guide** — Stream audio from an INMP441 I2S MEMS mic on a Pico 2W over WiFi to a Raspberry Pi 4B running [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi) for realtime bird classification.

**Stack:** `Pico 2W (MicroPython)` · `Pi 4B (BirdNET-Pi)` · `INMP441 I2S` · `TCP → direct WAV`

---

## Contents

1. [How it works](#1-how-it-works)
2. [What you need](#2-what-you-need)
3. [Microphone specs](#3-microphone-specs)
4. [Wiring](#4-wiring-the-mic-to-the-pico-2w)
5. [Pico 2W firmware](#5-pico-2w-firmware)
6. [Pi 4B setup](#6-pi-4b-setup)
7. [Running it](#7-running-it)
8. [Troubleshooting](#8-troubleshooting)
9. [Appendix: Why INMP441?](#appendix-why-inmp441)

---

## 1. How it works

The INMP441 is a **digital I2S microphone** — it outputs a clocked digital bitstream, not an analog voltage. The Pico 2W's hardware I2S peripheral (driven by PIO) captures the audio data directly into memory buffers at 22050 Hz. The CPU is free for WiFi — no manual ADC timing or gain calibration needed.

On the Pi, a **bridge service** accepts the TCP connection, pipes the audio through `ffmpeg` for gentle filtering (high-pass at 200 Hz, low-pass at 10 kHz) and resampling (22050 Hz → 48 kHz), and writes **15-second WAV files** directly to BirdNET-Pi's `StreamData` folder. The **analysis service** picks up each new WAV file and runs it through the BirdNET neural network for species identification.

### Architecture

![Architecture & Signal Flow](docs/architecture.svg)

### Boot Sequence

![Boot Sequence](docs/boot-sequence.svg)

---

## 2. What you need

### Hardware

- Raspberry Pi **Pico 2W** (the W variant with WiFi)
- **INMP441** I2S MEMS Microphone module (~$2-5 on AliExpress/Amazon/eBay)
- 5 short jumper wires (or solder directly)
- Breadboard (optional, for prototyping)
- Raspberry Pi **4B** — already on your WiFi network

### Software

- [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi) installed on the Pi 4B
- MicroPython firmware on the Pico 2W
- [Thonny](https://thonny.org) or `mpremote` to upload code to the Pico
- `ffmpeg` on the Pi (included with BirdNET-Pi)

> **Warning:** Make sure you have the **Pico 2W**, not the plain Pico 2. Only the W variant has the WiFi chip. You can also use the original Pico W — the code is identical.

---

## 3. Microphone specs

### INMP441 — Key parameters

| Parameter            | Value                                           |
| -------------------- | ----------------------------------------------- |
| Interface            | I2S (digital) — SCK, WS, SD                    |
| Supply voltage (VDD) | 1.8V – 3.3V → use Pico's **3.3V**              |
| Current draw         | ~1.4 mA                                         |
| SNR                  | 61 dB(A)                                        |
| Sensitivity          | -26 dBFS                                        |
| Frequency range      | 60 Hz – 15 kHz                                  |
| Data format          | 24-bit I2S, MSB-first, requires 64 SCK/frame   |
| Channel select (L/R) | Low = left channel, High = right channel        |
| Sample rates         | Up to 48 kHz (we use 22050 Hz)                  |

> **Tip:** The 60 Hz – 15 kHz range easily covers typical bird vocalizations (1 kHz – 10 kHz). At 22050 Hz sample rate, we capture frequencies up to ~11 kHz — more than enough for most species.

### Why I2S beats analog ADC for this project

| Aspect           | Analog (SPH8878LR5H-1 + ADC) | Digital (INMP441 I2S)       |
| ---------------- | ----------------------------- | --------------------------- |
| Noise floor      | ADC + WiFi switching noise    | Clean digital signal        |
| Sample rate      | 16 kHz max (MicroPython CPU)  | 22050+ Hz (hardware PIO)   |
| Gain calibration | Required (DC offset + gain)   | Not needed                  |
| Wiring           | 3 wires + 2 caps recommended  | 5 wires, no caps            |
| Dynamic range    | 12-bit effective              | 24-bit from mic             |
| CPU load         | High (manual timing loop)     | Low (DMA fills buffer)      |

---

## 4. Wiring the mic to the Pico 2W

Five signal connections plus one channel-select tie to ground. No decoupling capacitors needed — the INMP441 is a digital device.

### Wiring diagram

![INMP441 to Pico 2W Wiring](pico2w/wiring_inmp441_pico2w.svg)

### Pin mapping

| INMP441 pin | Pico 2W pin         | Wire      | Notes                                          |
| ----------- | ------------------- | --------- | ---------------------------------------------- |
| **VDD**     | Pin 36 — 3V3(OUT)   | Red       | 3.3V supply. Range: 1.8V – 3.3V               |
| **GND**     | Pin 23 — GND        | Black     | Ground                                         |
| **SCK**     | Pin 21 — GP16       | Blue      | I2S serial clock (bit clock)                   |
| **WS**      | Pin 22 — GP17       | Green     | I2S word select (LRCLK). **Must be SCK + 1**  |
| **SD**      | Pin 24 — GP18       | Yellow    | I2S serial data output                         |
| **L/R**     | Pin 23 — GND        | (jumper)  | Tied low = output on left channel              |

> **Important:** On the Pico 2W with MicroPython, the WS pin number **must** be exactly one greater than the SCK pin number. GP16/GP17 satisfies this constraint.

> **Important:** The L/R pin **must** be connected to GND (not left floating). A floating L/R pin causes the mic to produce no audio output.

> **Tip:** The INMP441's sound port (tiny hole) is on the **bottom** of the module. When mounting, ensure nothing covers the underside.

---

## 5. Pico 2W firmware

### Installing MicroPython

1. Go to [micropython.org/download/RPI_PICO2_W](https://micropython.org/download/RPI_PICO2_W/) — make sure it's the **W** variant firmware
2. Hold **BOOTSEL** on the Pico 2W, plug it into your computer via USB
3. Drag the `.uf2` file onto the **RPI-RP2** USB drive
4. The Pico reboots with MicroPython

### Uploading the firmware

Open [Thonny](https://thonny.org), select **MicroPython (Raspberry Pi Pico)** as the interpreter, then:

1. Open `pico/main.py` — edit `WIFI_SSID`, `WIFI_PASSWORD`, and `SERVER_IP` (your Pi 4B's IP address)
2. Save to the Pico: **File → Save as → Raspberry Pi Pico → `main.py`**
3. (Optional) Also upload `pico/lcd.py` if using the Waveshare LCD 1.3" display

The firmware reads audio from the INMP441 via hardware I2S (32-bit stereo framing for proper 64 SCK/frame timing required by the INMP441), extracts the left channel as 16-bit samples, and streams them over TCP to the Pi at ~43 KB/s.

> **Warning:** Replace `YOUR_WIFI_SSID`, `YOUR_WIFI_PASSWORD`, and `192.168.1.XXX` with your actual network credentials and Pi 4B IP before uploading. Find the Pi's IP with `hostname -I` on the Pi.

> **Warning:** The Pico 2W only supports **2.4 GHz WiFi**. It cannot connect to 5 GHz networks.

### Key parameters

| Parameter       | Value            | Notes                                         |
| --------------- | ---------------- | --------------------------------------------- |
| Sample rate     | 22050 Hz         | Hardware-clocked via PIO                      |
| I2S format      | 32-bit stereo    | Required for INMP441's 64 SCK/frame           |
| Output format   | 16-bit mono      | Left channel extracted, sent over TCP         |
| Packet size     | 1024 samples     | 2048 bytes per send (~46 ms of audio)         |
| TCP port        | 5005             | Connects to bridge on Pi                      |
| Reconnect delay | 3 seconds        | Auto-reconnects on connection loss            |

---

## 6. Pi 4B setup

### Installing BirdNET-Pi

Follow the official [BirdNET-Pi installation guide](https://github.com/Nachtzuster/BirdNET-Pi/wiki/Installation-Guide):

```bash
curl -s https://raw.githubusercontent.com/Nachtzuster/BirdNET-Pi/main/newinstaller.sh | bash
```

After installation, verify:
- Web UI is accessible at `http://<PI_IP>:80` or `http://birdnetpi.local`
- Analysis service is running: `sudo systemctl status birdnet_analysis.service`

### Installing the bridge service

The bridge receives TCP audio from the Pico and writes WAV files for BirdNET-Pi to analyze.

**Automated install (recommended):**

```bash
cd bridge/
sudo bash install.sh
```

The script automatically:

- Copies `birdnet_mic_bridge.py` to `/opt/mic_bridge/`
- Creates and enables a systemd service
- Detects your username and StreamData path
- Masks `birdnet_recording.service` (conflicts with the bridge)

**Override defaults if needed:**

```bash
sudo bash install.sh --user pi --recs-dir /home/pi/BirdSongs/StreamData
```

**Uninstall:**

```bash
sudo bash install.sh --uninstall
```

### What the bridge does

1. Listens on TCP port 5005 for Pico connection
2. Receives raw 16-bit 22050 Hz mono PCM audio
3. Pipes through `ffmpeg` with:
   - **High-pass 200 Hz** — removes DC offset, wind noise, handling rumble
   - **Low-pass 10 kHz** — removes artifacts above bird frequency range
   - **Resample 22050 → 48000 Hz** — BirdNET-Pi expects 48 kHz
4. Writes 15-second WAV files to `~/BirdSongs/StreamData/`
5. Auto-reconnects when Pico disconnects

### BirdNET-Pi configuration

Since the bridge writes WAV files directly to StreamData, BirdNET-Pi's analysis service picks them up automatically. No additional configuration is required.

**Optional tuning:**

- **Lower confidence threshold** for more detections (default 0.7):

  Edit `/etc/birdnet/birdnet.conf` → set `CONFIDENCE=0.6` → restart analysis
- **Lower species occurrence filter** to allow rarer species:
  Edit `/etc/birdnet/birdnet.conf` → set `SF_THRESH=0.001` (default 0.03)
- **Mask the built-in recording service** (install.sh does this automatically):
  `sudo systemctl mask birdnet_recording.service`

---

## 7. Running it

### Step 1 — Verify services on Pi

```bash
sudo systemctl status birdnet-mic-bridge.service
sudo systemctl status birdnet_analysis.service
```

### Step 2 — Power on the Pico 2W

The LED blinks while connecting to WiFi, then goes solid. Within seconds, WAV files start appearing in `~/BirdSongs/StreamData/`.

### Step 3 — Check detections

Open the BirdNET-Pi web UI at `http://birdnetpi.local`.

---

## 8. Troubleshooting

### Common issues

| Symptom                                        | Cause                                                    | Fix                                                                                       |
| ---------------------------------------------- | -------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| No detections in BirdNET-Pi                    | Bridge not writing WAV files or analysis not running     | Check `ls -lt ~/BirdSongs/StreamData/*.wav` and `systemctl status birdnet_analysis`       |
| WAV files are silent (peak < 100)              | I2S wiring wrong or mic not powered                      | Check VDD is 3.3V, SCK/WS/SD connected, L/R tied to GND                                  |
| WAV files have only DC offset (no audio)       | Dead MEMS element in INMP441 module                      | Try a different INMP441 module — bottom port mic, check sound hole is unobstructed        |
| `ValueError: invalid I2S pin`                  | WS pin not SCK+1                                         | Use GP16 (SCK) + GP17 (WS) — WS must always be SCK + 1                                   |
| Pico LED blinks fast forever                   | WiFi credentials wrong or 5 GHz network                  | Pico 2W only supports 2.4 GHz; check SSID/password                                       |
| Audio sounds choppy                            | WiFi interference or TCP backpressure                    | Move Pico closer to router; check WiFi congestion                                         |
| All samples are zero                           | L/R pin floating (not connected)                         | Tie L/R to GND (left channel)                                                             |
| `arecord: Device or resource busy`             | `birdnet_recording.service` conflicts with bridge        | `sudo systemctl mask birdnet_recording.service`                                           |
| Species excluded as "below occurrence"         | SF_THRESH too high for your location                     | Set `SF_THRESH=0.001` in `/etc/birdnet/birdnet.conf`                                     |

### WiFi router settings (important for Pico 2W stability)

The Pico 2W's CYW43439 WiFi chip is sensitive to radio conditions. These router settings significantly improve streaming stability:

| Setting         | Recommended | Why                                                                 |
| --------------- | ----------- | ------------------------------------------------------------------- |
| Channel         | **1, 6, or 11** | Standard non-overlapping channels. Avoid channel 12+ (known CYW43 issues) |
| Channel width   | **20 MHz**  | 40 MHz adds noise susceptibility; 20 KB/s stream doesn't need extra bandwidth |
| Band            | **2.4 GHz** | Pico 2W does not support 5 GHz                                      |

> **Tested result:** Switching from channel 12 / 40 MHz to channel 6 / 20 MHz doubled throughput (15-20 KB/s → 31 KB/s) and eliminated periodic stalls.

### Weatherproof outdoor deployment

- Power the Pico 2W from a USB power bank (~40–80 mA over WiFi — a 1000 mAh bank gives **10+ hours**)
- Use a waterproof enclosure with a small hole for the mic's sound port
- Point the mic hole downward to prevent rain ingress
- Place near known bird activity (feeders, nest boxes, water features)

---

## Appendix: Why INMP441?

Three I2S MEMS microphones were evaluated for this project:

| Feature              | INMP441                     | SPH0645LM4H                       | ICS-43434                       |
| -------------------- | --------------------------- | ---------------------------------- | ------------------------------- |
| I2S timing           | Standard Philips I2S        | **Non-standard** (1-bit shift)     | Standard                        |
| MicroPython support  | Tested & documented         | Requires `I2S.shift()` workaround  | Not tested in community         |
| SNR                  | 61 dB(A)                    | 65 dB(A)                           | 65 dB(A)                        |
| Price                | ~$2-5                       | ~$7 (Adafruit)                     | ~$8-12                          |
| Availability         | Widely available            | Adafruit only                      | Limited breakout boards         |
| Community examples   | Many (miketeachman/i2s)     | Few (with workaround notes)        | Very few                        |

**INMP441 wins** because:
1. **Standard I2S timing** — works directly with MicroPython's `machine.I2S` class, no bit-shift workarounds
2. **Extensively tested** — the [micropython-i2s-examples](https://github.com/miketeachman/micropython-i2s-examples) repo uses INMP441 as the primary test mic
3. **Cheap and available** — $2-5 on AliExpress/Amazon/eBay with fast shipping
4. **61 dB SNR is sufficient** — the 4 dB gap vs SPH0645/ICS-43434 is negligible for outdoor bird monitoring where ambient noise dominates

---

## Quick reference

![Quick Reference](docs/quick-reference.svg)
