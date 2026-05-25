# Wireless Microphone Streaming to BirdNET-Pi

> **Build Guide** — Stream audio from an SPH8878LR5H-1 MEMS mic on a Pico 2W over WiFi to a Raspberry Pi 4B running [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi) for realtime bird classification.

**Stack:** `Pico 2W (MicroPython)` · `Pi 4B (BirdNET-Pi)` · `SPH8878LR5H-1` · `TCP → direct WAV`

---

## Project files

```
pico 2w/
├── README.md                              ← this file
├── pico/                                  ← upload to Pico 2W via Thonny
│   ├── main.py                            ← streaming firmware (with LCD support)
│   └── lcd.py                             ← Waveshare LCD 1.3" driver (optional)
└── bridge/                                ← deploy to Pi 4B
    ├── birdnet_mic_bridge.py              ← TCP → WAV bridge service
    ├── birdnet-mic-bridge.service         ← systemd unit file
    └── birdnet.conf.example               ← BirdNET-Pi config changes
```

**Pico 2W:** upload `pico/main.py` (and optionally `pico/lcd.py`) to the Pico via Thonny → **File → Save as → Raspberry Pi Pico**.

**Pi 4B:** copy `bridge/birdnet_mic_bridge.py` to `/opt/mic_bridge/` and `bridge/birdnet-mic-bridge.service` to `/etc/systemd/system/`. See [section 6](#6-bridge-service-tcp--wav-files) for full install instructions.

---

## Contents

1. [How it works](#1-how-it-works)
2. [What you need](#2-what-you-need)
3. [Microphone specs](#3-microphone-specs)
4. [Wiring](#4-wiring-the-mic-to-the-pico-2w)
5. [Pico 2W firmware](#5-pico-2w-firmware-micropython)
6. [Bridge service](#6-bridge-service-tcp--wav-files)
7. [BirdNET-Pi configuration](#7-birdnet-pi-configuration)
8. [Running it](#8-running-it)
9. [Troubleshooting & improvements](#9-troubleshooting--improvements)
10. [Appendix: Design decisions from open-source research](#appendix-design-decisions-from-open-source-research)

---

## 1. How it works

The SPH8878LR5H-1 is an **analog mic** — it outputs a varying voltage, not digital data. The Pico 2W samples that voltage through its built-in ADC, applies gain and DC offset calibration, packs the samples into buffers, and streams them over a **TCP connection** to your Pi 4B.

On the Pi, a **bridge service** accepts the TCP connection, pipes the audio through `ffmpeg` for filtering (two stacked high-pass at 500 Hz, low-pass at 7.5 kHz, 2× gain) and resampling (16 kHz → 48 kHz), and writes **15-second WAV files** directly to BirdNET-Pi's `StreamData` folder. The analysis service picks them up automatically — no ALSA loopback needed.

### Architecture

```
┌──────────────────┐        ┌──────────────────┐        ┌──────────────────┐
│   SPH8878LR5H-1  │        │    Pico 2W       │        │     Pi 4B        │
│                  │ analog │                  │  TCP   │                  │
│  Analog MEMS mic │───────>│  ADC + WiFi      │───────>│  BirdNET-Pi      │
│                  │ 0–3.3V │                  │  WiFi  │                  │
└──────────────────┘        └──────────────────┘        └──────────────────┘
```

```
 Detailed Signal Flow:

 Bird song
      │
      ▼
 ┌─────────┐  Analog     ┌─────────────┐  TCP stream   ┌───────────────────────────────────┐
 │  MEMS    │  voltage    │  Pico 2W    │  over WiFi   │            Pi 4B                  │
 │  Mic     │ ─────────> │  12-bit ADC │ ───────────> │                                   │
 │  +OpAmp  │  0–3.3V    │  → calibrate│  16kHz mono  │  ┌─────────┐    ┌──────────────┐  │
 └─────────┘  ×64 gain   │  → 2x gain  │  s16le PCM   │  │ Bridge  │    │  WAV files   │  │
                          │  → pack     │  512 smp/pkt │  │ service │───>│  (15s each)  │  │
                          │  → send     │              │  │ +ffmpeg │    └──────┬───────┘  │
                          │  → yield    │              │  └─────────┘          │          │
                          └─────────────┘              │   2x HP 500Hz          ▼          │
                                                       │   LP 7500Hz  ┌──────────────┐    │
                                                       │   ×2 gain    │  BirdNET-Pi  │    │
                                                       │   16k → 48k  │  analysis    │    │
                                                       │              │  + web UI    │    │
                                                       │              └──────────────┘    │
                                                       └───────────────────────────────────┘
```

> **ℹ Info:** TCP guarantees all audio data arrives in order with no gaps — no glitches from dropped packets. The bridge writes WAV files directly to BirdNET-Pi's StreamData folder, bypassing ALSA loopback entirely. This eliminates device locking issues and simplifies the setup. Both the Pico and the bridge handle disconnections gracefully with automatic reconnection.

---

## 2. What you need

### Hardware

- Raspberry Pi **Pico 2W** (the W variant with WiFi)
- SparkFun Analog MEMS Microphone Breakout — **SPH8878LR5H-1** (BOB-19389)
- 3 short jumper wires (red, black, yellow)
- Breadboard (optional, for prototyping)
- Raspberry Pi **4B** — already on your WiFi network, running BirdNET-Pi

### Software (already on Pi if BirdNET-Pi is installed)

- [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi) installed on the Pi 4B
- MicroPython firmware on the Pico 2W
- Thonny or `mpremote` to upload code to the Pico
- `ffmpeg` on the Pi (`sudo apt install ffmpeg` — may already be present)

> **⚠ Warning:** Make sure you have the **Pico 2W**, not the plain Pico 2. Only the W variant has the WiFi chip. You can also use the original Pico W — the code is identical.

---

## 3. Microphone specs

Relevant specs for this project:

| Parameter                    | Value                                        |
| ---------------------------- | -------------------------------------------- |
| Supply voltage (VCC)         | 2.3V – 3.6V → use Pico's **3.3V**            |
| Current draw                 | ~265 µA (very low power)                     |
| Output at idle               | ½ × VCC ≈ **1.65V** (the ADC midpoint)       |
| Peak-to-peak at arm's length | ~200 mV around the 1.65V center              |
| Frequency range              | 7 Hz – 36 kHz                                |
| SNR                          | 66 dB (good clarity for voice/birds)         |
| Gain (on-board op-amp)       | ×64 — already amplified, no extra amp needed |
| Pico 2W ADC input range      | 0 – 3.3V (12-bit, 4096 steps)                |

The op-amp on the breakout board keeps the output biased at half VCC when quiet. Your ADC readings will hover around **2048** in silence and swing above/below with sound.

> **✦ Tip:** The 7 Hz – 36 kHz range easily covers the typical bird vocalization spectrum (1 kHz – 10 kHz). The 66 dB SNR is sufficient for outdoor bird monitoring at moderate distances.

---

## 4. Wiring the mic to the Pico 2W

Only three connections are needed. The breakout already has an op-amp and bias resistors — nothing else is required.

### Wiring diagram

```
        Mic Breakout                         Pico 2W
      ┌──────────────┐                 ┌──────────────────┐
      │              │                 │                  │
      │  VCC  ●──────│── Red ─────────│── Pin 36  3V3    │
      │              │                 │                  │
      │  GND  ●──────│── Black ───────│── Pin 38  GND    │
      │              │                 │                  │
      │  AUD  ●──────│── Yellow ──────│── Pin 31  GP26   │
      │              │                 │     (ADC0)       │
      └──────────────┘                 └──────────────────┘
```

### Pin mapping

| Mic breakout pin | Pico 2W pin          | Wire      | Notes                                       |
| ---------------- | -------------------- | --------- | ------------------------------------------- |
| **VCC**          | Pin 36 — 3V3(OUT)    | 🔴 Red    | 3.3V supply. **Never use 5V** — max is 3.6V |
| **GND**          | Pin 38 — GND         | ⚫ Black   | Any GND pin works                           |
| **AUD**          | Pin 31 — GP26 (ADC0) | 🟡 Yellow | Analog audio signal                         |

> **✦ Tip:** GP26 (ADC0) is the cleanest ADC pin on the Pico — it has the least noise from the digital side. GP27 (ADC1) and GP28 (ADC2) also work fine if GP26 is occupied. **Avoid GP29** — it's used for VSYS monitoring.

The mic's audio input port is on the **bottom** of the breakout board — make sure nothing is covering the tiny hole on the underside when you mount it.

---

## 5. Pico 2W firmware (MicroPython)

Install MicroPython on your Pico 2W first:

1. Go to [micropython.org/download/RPI_PICO2_W](https://micropython.org/download/RPI_PICO2_W/) — make sure it's the **W** variant firmware
2. Download the latest `.uf2` file
3. Hold **BOOTSEL** on the Pico 2W, plug it into your computer via USB
4. It appears as a USB drive called **RPI-RP2** — drag the `.uf2` onto it
5. The Pico reboots automatically with MicroPython

Open [Thonny](https://thonny.org), go to **Run → Configure interpreter**, select **MicroPython (Raspberry Pi Pico)** and your USB serial port. Then create this file on the Pico:

### `main.py` (on Pico 2W)

```python
import network, socket, time
from machine import ADC, Pin

# ── Configuration ─────────────────────────────────────────
WIFI_SSID     = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

SERVER_IP   = "192.168.1.XXX"   # ← your Pi 4B IP address
SERVER_PORT = 5005

SAMPLE_RATE   = 16000     # Hz — see "Why 16000 Hz?" below
PACKET_FRAMES = 512       # samples per send (~32 ms at 16000 Hz)
RECONNECT_DELAY = 3       # seconds to wait before retrying connection
GAIN = 2                  # Pico-side gain (adjust based on mic solder quality)

# ── LED for status ─────────────────────────────────────────
led = Pin("LED", Pin.OUT)

# ── WiFi connection with retry ─────────────────────────────
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.disconnect()
    time.sleep(1)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    print("Connecting to WiFi", end="")
    for attempt in range(30):
        s = wlan.status()
        if s == 3:
            break
        if s < 0:
            print(f" status={s}, retrying...", end="")
            wlan.disconnect()
            time.sleep(2)
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        print(".", end="")
        time.sleep(1)

    if not wlan.isconnected():
        print("\nWiFi FAILED — blinking LED")
        while True:
            led.toggle()
            time.sleep(0.1)

    print(f"\nConnected! IP: {wlan.ifconfig()[0]}")
    led.on()
    wlan.config(pm=0xa11140)  # WIFI_PS_NONE — disable power-save
    return wlan

# ── TCP connect with retry ─────────────────────────────────
def tcp_connect():
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((SERVER_IP, SERVER_PORT))
            print(f"TCP connected to {SERVER_IP}:{SERVER_PORT}")
            led.on()
            return s
        except OSError as e:
            print(f"TCP failed ({e}), retry in {RECONNECT_DELAY}s...")
            led.off()
            try:
                s.close()
            except:
                pass
            time.sleep(RECONNECT_DELAY)

# ── Main ───────────────────────────────────────────────────
wlan = connect_wifi()
mic = ADC(Pin(26))   # GP26 = ADC0
interval_us = 1_000_000 // SAMPLE_RATE

# Calibrate DC offset in silence (0.5s)
print("Calibrating mic offset...", end="")
total = 0
for _ in range(500):
    total += mic.read_u16()
    time.sleep_us(1000)
midpoint = total // 500
print(f" midpoint={midpoint}")

print(f"Streaming to {SERVER_IP}:{SERVER_PORT} at {SAMPLE_RATE} Hz, gain={GAIN}x")

while True:
    sock = tcp_connect()
    buf = bytearray(PACKET_FRAMES * 2)
    idx = 0
    t_next = time.ticks_us()

    try:
        while True:
            t_next = time.ticks_add(t_next, interval_us)
            while time.ticks_diff(t_next, time.ticks_us()) > 0:
                pass

            raw = mic.read_u16()

            sample = (raw - midpoint) * GAIN

            if sample > 32767:
                sample = 32767
            elif sample < -32768:
                sample = -32768

            buf[idx]     = sample & 0xFF
            buf[idx + 1] = (sample >> 8) & 0xFF
            idx += 2

            if idx >= len(buf):
                sock.send(buf)
                idx = 0
                time.sleep_ms(1)  # yield to WiFi stack
    except OSError as e:
        print(f"Connection lost ({e}), reconnecting...")
        try:
            sock.close()
        except:
            pass
```

> **⚠ Warning:** Replace `YOUR_WIFI_SSID`, `YOUR_WIFI_PASSWORD`, and `192.168.1.XXX` with your actual network credentials and Pi 4B IP before uploading. Find the Pi's IP with `hostname -I` on the Pi.

> **⚠ Warning:** The Pico 2W only supports **2.4 GHz WiFi**. It cannot connect to 5 GHz networks. If your router broadcasts both, use the 2.4 GHz SSID (often named `YourNetwork_2G` or similar).

To **test**, click the green Run button in Thonny. To **deploy**, save to the Pico permanently: **File → Save as → Raspberry Pi Pico → `main.py`**. The Pico will then auto-stream on every power-up — no computer needed.

### Why 16000 Hz?

BirdNET internally operates at 48 kHz, but the bridge on the Pi handles resampling. We use **16000 Hz** on the Pico because MicroPython's bytecode interpreter + WiFi overhead limits the achievable ADC sample rate.

**Field-tested measurement:** at a target of 22050 Hz, the Pico 2W with 2× oversampling and active WiFi only achieves **~17000 Hz actual throughput**. The mismatch causes pitch distortion (voices sound like cartoon characters). Setting the target to 16000 Hz gives headroom for consistent, accurate timing.

| Rate         | Captures up to | MicroPython feasibility                    | Bird coverage                                |
| ------------ | -------------- | ------------------------------------------ | -------------------------------------------- |
| **16000 Hz** | **~8 kHz**     | **Tested — reliable with WiFi active**     | **Covers most bird vocalizations (1–8 kHz)** |
| 22050 Hz     | ~11 kHz        | Too fast — actual rate drops to ~17 kHz    | N/A without C firmware                       |
| 48000 Hz     | ~24 kHz        | Far too fast for MicroPython               | N/A                                          |

### Why single ADC read with WiFi yield?

Earlier versions used median-of-3 ADC reads to reject WiFi noise spikes. However, field testing showed that 3 reads per sample consume too much CPU, starving the WiFi stack and causing periodic TCP disconnects (every 1–3 minutes). A single ADC read with a **1ms yield** (`time.sleep_ms(1)`) after each packet send gives the WiFi stack enough time to maintain the connection, achieving **10+ hours** of continuous streaming.

### Why Pico-side gain?

The mic produces only ~200 mV peak-to-peak in a 3.3V ADC range — about 9% of full scale. Without gain, BirdNET sees only noise. With properly soldered mic connections, `GAIN = 2` on the Pico plus `volume=2` in ffmpeg (4× total) produces peaks near -1 dB — close to full scale. Adjust `GAIN` based on your mic's solder quality:

| GAIN | Total (with volume=2) | Use when... |
|------|----------------------|-------------|
| 1    | 2× | Signal clips (peak > 0 dB) |
| **2** | **4×** | **Default — properly soldered mic** |
| 4    | 8× | Signal is too quiet |
| 8    | 16× | Very weak signal or distant sound source |

---

## 6. Bridge service (TCP → WAV files)

The bridge listens for a TCP connection from the Pico 2W, pipes the audio through `ffmpeg` for filtering and resampling, and writes **15-second WAV files** directly to BirdNET-Pi's `StreamData` folder. No ALSA loopback, no arecord, no device conflicts.

### Why direct WAV instead of ALSA loopback?

The original design used `snd-aloop` (ALSA loopback) to bridge audio into BirdNET-Pi's recording pipeline. Field testing revealed that ffmpeg's ALSA output locks the loopback cable, preventing BirdNET-Pi's `arecord` from opening the capture side. This caused `Device or resource busy` errors that were difficult to resolve reliably. Writing WAV files directly bypasses ALSA entirely and is simpler, more reliable, and produces identical results.

### `birdnet_mic_bridge.py` (on Pi 4B)

```python
#!/usr/bin/env python3
import socket, subprocess, sys, signal, os

LISTEN_IP    = "0.0.0.0"
LISTEN_PORT  = 5005
BUFFER_SIZE  = 4096

INPUT_RATE   = 16000
OUTPUT_RATE  = 48000
CHANNELS     = 1
SEGMENT_SEC  = 15

RECS_DIR = os.environ.get("RECS_DIR", "/home/pr13s7/BirdSongs/StreamData")

running = True

def shutdown(sig, frame):
    global running
    running = False
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

def start_ffmpeg():
    return subprocess.Popen(
        [
            "ffmpeg",
            "-hide_banner", "-loglevel", "warning",
            "-f", "s16le",
            "-ar", str(INPUT_RATE),
            "-ac", str(CHANNELS),
            "-i", "pipe:0",
            "-af", "highpass=f=500:poles=2,highpass=f=500:poles=2,lowpass=f=7500,volume=2",
            "-ar", str(OUTPUT_RATE),
            "-ac", str(CHANNELS),
            "-f", "segment",
            "-segment_time", str(SEGMENT_SEC),
            "-segment_format", "wav",
            "-strftime", "1",
            os.path.join(RECS_DIR, "%Y-%m-%d-birdnet-%H:%M:%S.wav")
        ],
        stdin=subprocess.PIPE
    )

def main():
    os.makedirs(RECS_DIR, exist_ok=True)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_IP, LISTEN_PORT))
    srv.listen(1)

    print(f"[bridge] Listening on :{LISTEN_PORT}")
    print(f"[bridge] Writing {SEGMENT_SEC}s WAV files to {RECS_DIR}")
    print(f"[bridge] Filters: 2x HP 500Hz, LP 7500Hz, volume 2x")
    print(f"[bridge] No ALSA loopback needed for recording!")

    while running:
        print("[bridge] Waiting for Pico 2W connection...", flush=True)
        conn, addr = srv.accept()
        conn.settimeout(30)
        print(f"[bridge] Connected: {addr}", flush=True)

        proc = start_ffmpeg()
        byte_count = 0

        try:
            while True:
                data = conn.recv(BUFFER_SIZE)
                if not data:
                    break
                proc.stdin.write(data)
                byte_count += len(data)
                if byte_count % (BUFFER_SIZE * 1000) < BUFFER_SIZE:
                    mb = byte_count / (1024 * 1024)
                    print(f"[bridge] {mb:.1f} MB received", flush=True)
        except (BrokenPipeError, ConnectionResetError, OSError, TimeoutError) as e:
            print(f"[bridge] Connection lost: {e}", flush=True)
        finally:
            conn.close()
            try:
                proc.stdin.close()
            except:
                pass
            proc.wait()
            print("[bridge] Pico disconnected, restarting on next connect", flush=True)

if __name__ == "__main__":
    main()
```

### Why the ffmpeg filter chain?

- **`highpass=f=500:poles=2` (×2)** — Two stacked high-pass filters at 500 Hz (4th-order rolloff). Removes WiFi noise concentrated below 500 Hz. Bird vocalizations start above 1 kHz so nothing useful is lost.
- **`lowpass=f=7500`** — Removes high-frequency ADC/digital noise above 7.5 kHz.
- **`volume=2`** — 2× gain on the bridge side (combined with 2× on the Pico = 4× total).
- **`-f segment -segment_time 15`** — Splits audio into 15-second WAV files with timestamped names, matching BirdNET-Pi's expected format.

### Install as a systemd service

Place the bridge script:

```bash
sudo mkdir -p /opt/mic_bridge
sudo cp birdnet_mic_bridge.py /opt/mic_bridge/birdnet_mic_bridge.py
```

Create the service file:

```bash
sudo tee /etc/systemd/system/birdnet-mic-bridge.service << 'EOF'
[Unit]
Description=Pico 2W wireless mic bridge for BirdNET-Pi
After=network-online.target
Wants=network-online.target
Before=birdnet_analysis.service

[Service]
Type=simple
User=pr13s7
ExecStart=/usr/bin/python3 /opt/mic_bridge/birdnet_mic_bridge.py
Environment=RECS_DIR=/home/pr13s7/BirdSongs/StreamData
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable birdnet-mic-bridge.service
sudo systemctl start birdnet-mic-bridge.service

# Check status
sudo systemctl status birdnet-mic-bridge.service
```

> **✦ Tip:** The bridge writes WAV files directly — no ALSA loopback or recording service needed. **Mask** `birdnet_recording.service` to prevent conflicts (disable alone is not enough — BirdNET's watchdog re-enables it):
>
> ```bash
> sudo systemctl stop birdnet_recording.service
> sudo systemctl mask birdnet_recording.service
> ```

---

## 7. BirdNET-Pi configuration

Since the bridge writes WAV files directly to StreamData, BirdNET-Pi's **analysis service** picks them up automatically. You only need to adjust the confidence threshold.

### Lower the confidence threshold

The analog mic through the Pico's ADC produces lower-quality audio than a USB mic. Lower the confidence threshold from the default 0.7 to 0.5:

```bash
sudo nano /etc/birdnet/birdnet.conf

# Change this line:
CONFIDENCE=0.5
```

Then restart the analysis service:

```bash
sudo systemctl restart birdnet_analysis.service
```

### Mask the recording service

The bridge replaces the recording service. You must **mask** it — a simple `disable` is not enough because BirdNET-Pi's watchdog re-enables it:

```bash
sudo systemctl stop birdnet_recording.service
sudo systemctl mask birdnet_recording.service
```

> **✦ Tip:** To undo later: `sudo systemctl unmask birdnet_recording.service`

---

## 8. Running it

### Startup order

```
 ┌─────────────────────────────────────────────────────────┐
 │  Boot sequence (all automatic via systemd)              │
 │                                                         │
 │  1. birdnet-mic-bridge      ← waits for TCP connection  │
 │  2. birdnet_analysis        ← analyzes WAV files        │
 │  3. Power on Pico 2W        ← connects and streams     │
 │                                                         │
 │  Audio flows:                                           │
 │  Pico → TCP → bridge → ffmpeg → 15s WAV files          │
 │                              → birdnet_analysis          │
 └─────────────────────────────────────────────────────────┘
```

### Step 1 — Make sure services are running on Pi

```bash
sudo systemctl status birdnet-mic-bridge.service
sudo systemctl status birdnet_analysis.service
```

Both should show `active (running)`.

### Step 2 — Power on the Pico 2W

The LED will blink while connecting to WiFi, then go solid. Within seconds, WAV files start appearing in `~/BirdSongs/StreamData/`.

### Step 3 — Verify detections

Open the BirdNET-Pi web UI at `http://birdnetpi.local`. You should see:
- Bird detections appearing in the log as birds vocalize

### Quick debug commands

```bash
# Are services running?
sudo systemctl status birdnet-mic-bridge.service
sudo systemctl status birdnet_analysis.service

# Is the TCP connection established?
ss -tn sport = :5005

# Is the bridge running and forwarding?
journalctl -u birdnet-mic-bridge -f

# Is BirdNET analyzing?
journalctl -u birdnet_analysis -f

# Are WAV files being created?
ls -lt ~/BirdSongs/StreamData/*.wav | head -5

# Check a WAV file's audio levels
python3 -c "
import wave, struct, math
w = wave.open('$(ls -t ~/BirdSongs/StreamData/*.wav | head -1)','r')
frames = w.readframes(w.getnframes())
samples = struct.unpack(f'<{len(frames)//2}h', frames)
peak = max(abs(s) for s in samples)
rms = math.sqrt(sum(s*s for s in samples)/len(samples))
print(f'Peak: {peak} ({peak/32768*100:.1f}%)  RMS: {rms:.0f}')
print('SILENT — check bridge and Pico' if peak < 100 else 'HAS AUDIO')
"

# Check latest detection results
cat ~/BirdSongs/StreamData/*.json
```

> **⚠ Warning:** If the Pico loses WiFi, the TCP connection drops. The bridge detects this and waits for a new connection. The Pico's reconnect loop retries every 3 seconds until the bridge is reachable again.

---

## 9. Troubleshooting & improvements

### Common issues

| Symptom                                        | Cause                                                           | Fix                                                                                         |
| ---------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| No detections in BirdNET-Pi                    | Bridge not writing WAV files or analysis not running            | Check `ls -lt ~/BirdSongs/StreamData/*.wav` and `systemctl status birdnet_analysis`         |
| BirdNET always reports same species at 0.015   | WAV files are silent — bridge not receiving data                | Check Pico is connected: `ss -tn sport = :5005`                                             |
| Voice sounds like cartoon character            | Sample rate mismatch — Pico samples slower than configured rate | Use `SAMPLE_RATE = 16000` on Pico (MicroPython can't sustain 22050 Hz with WiFi)            |
| `ModuleNotFoundError: network`                 | Wrong MicroPython firmware (non-W) or Thonny using local Python | Flash `RPI_PICO2_W` firmware; set Thonny interpreter to "MicroPython (Raspberry Pi Pico)"   |
| Pico WiFi connects manually but not in script  | Stale WiFi state after soft reboot                              | The `wlan.disconnect()` + `time.sleep(1)` in the script handles this                       |
| Pico LED blinks fast forever                   | WiFi credentials wrong or 5 GHz network                         | Pico 2W only supports 2.4 GHz; check SSID/password                                          |
| Audio sounds choppy                            | WiFi interference or TCP backpressure                           | Move Pico closer to router, check for WiFi congestion                                       |
| Pico LED turns off every few minutes           | TCP disconnect — WiFi stack starved of CPU                      | Ensure `time.sleep_ms(1)` after each `sock.send()` in the sampling loop                     |
| `arecord: Device or resource busy`             | `birdnet_recording.service` conflicts with bridge               | `sudo systemctl mask birdnet_recording.service` (disable alone is not enough)                |
| ADC reads stuck at ~63000                      | Mic wiring issue or ADC pin damaged by overvoltage              | Check wiring; test with `ADC(Pin(27))` to rule out pin damage                                |

### Field-tested lessons learned

1. **MicroPython can't sustain 22050 Hz with WiFi active.** Measured throughput is ~17000 Hz. Use `SAMPLE_RATE = 16000` for consistent timing.

2. **WiFi `disconnect()` before `connect()` is essential.** After a MicroPython soft reboot, the WiFi radio can be in a stale state.

3. **The analog mic signal needs amplification.** The SPH8878LR5H-1 produces ~200 mV peak-to-peak at arm's length — only ~9% of the ADC's full scale. With a properly soldered mic, `GAIN=2` on Pico + `volume=2` in ffmpeg (4× total) is sufficient.

4. **ALSA loopback causes device locking issues.** ffmpeg's ALSA output locks the loopback cable, preventing arecord from opening the capture side. Writing WAV files directly to StreamData bypasses this entirely.

5. **Mask `birdnet_recording.service`, don't just disable it.** BirdNET-Pi's watchdog re-enables the recording service after a simple `disable`. Use `sudo systemctl mask birdnet_recording.service` to prevent it from starting by any means.

6. **Single ADC read + WiFi yield is more stable than median-of-3.** Median-of-3 uses too much CPU and starves the WiFi stack, causing disconnects every 1–3 minutes. A single read with `time.sleep_ms(1)` after each packet achieves 10+ hours of continuous streaming.

7. **DC offset calibration matters.** The ADC midpoint varies per board (~32505 vs theoretical 32768). Calibrating at startup prevents amplified DC drift.

8. **Solder quality dramatically affects signal.** A poorly soldered mic connection adds 20+ dB of noise. Re-soldering improved SNR from 5 dB to 24 dB in field testing.

9. **Never power the mic from an external source above 3.3V without a voltage divider.** The mic's op-amp output can reach VCC, and voltages above 3.3V will damage the Pico's ADC pins.

### Tuning the ffmpeg filter chain

The bridge uses: `highpass=f=500:poles=2,highpass=f=500:poles=2,lowpass=f=7500,volume=2`

| Parameter     | Default  | Increase if...                         | Decrease if...                         |
| ------------- | -------- | -------------------------------------- | -------------------------------------- |
| `highpass=f=` | 500 Hz   | Low-frequency noise is audible         | Low-frequency bird calls are being cut |
| `lowpass=f=`  | 7500 Hz  | High-pitched noise is present          | You want to capture higher frequencies |
| `volume=`     | 2        | BirdNET confidence is consistently low | Audio is clipping (distorted at peak)  |
| Pico `GAIN`   | 2        | Signal is too quiet                    | Signal clips at 32767                  |

After editing bridge filters, restart: `sudo systemctl restart birdnet-mic-bridge.service`

### Hardware noise reduction (optional but recommended)

The Pico 2W's WiFi chip puts switching noise on the 3.3V power rail. This noise travels through the VCC wire to the mic's op-amp, corrupting the analog signal. Two capacitors between **VCC and GND** at the mic end filter this conducted noise:

- **100nF ceramic capacitor** — filters high-frequency noise (MHz range, WiFi switching)
- **10µF electrolytic capacitor** — filters low-frequency noise (kHz range, power supply ripple)

```
        Pico 2W                          Mic Breakout
      ┌──────────┐                      ┌──────────────┐
      │          │                      │              │
      │  3V3 ────│── Red wire ──────┬───│── VCC        │
      │          │                  │   │              │
      │          │             ┌────┤   │              │
      │          │             │  ┌─┤   │              │
      │          │           100nF │ │   │              │
      │          │          (ceramic) │   │              │
      │          │             │  │10µF │              │
      │          │             │  │(+)  │              │
      │          │             │  │(electrolytic)      │
      │          │             └────┤   │              │
      │          │                  │   │              │
      │  GND ────│── Black wire ────┴───│── GND        │
      │          │                      │              │
      │  GP26 ───│── Yellow wire ───────│── AUD        │
      └──────────┘                      └──────────────┘
```

**Placement rules:**

- Both capacitors go **in parallel** between VCC and GND
- Place them **as close to the mic breakout as possible** (not near the Pico)
- **100nF ceramic** — no polarity, either leg in either hole
- **10µF electrolytic** — **POLARIZED!** Longer leg (+) → VCC, shorter leg with stripe (−) → GND. Reversing it damages the cap.
- On a breadboard: both caps share the same two rows (one row = VCC, other row = GND)

> **⚠ Warning:** The electrolytic capacitor is polarized. The **longer leg** (or the leg **without** the stripe on the body) connects to **VCC (3.3V)**. The **shorter leg** (stripe side) connects to **GND**. Reversing polarity can cause the capacitor to overheat or burst.

### Weatherproof outdoor deployment

For a wireless outdoor bird monitoring station:

- Power the Pico 2W from a small USB power bank (the mic draws ~265 µA, the Pico uses ~40–80 mA over WiFi — a 1000 mAh bank gives **10+ hours**)
- Use a waterproof enclosure with a small hole for the mic's sound port
- Point the mic hole downward to prevent rain ingress
- Place it near known bird activity (feeders, nest boxes, water features)

---

## Appendix: Design decisions from open-source research

This guide's design was informed by analyzing 6 open-source wireless microphone projects. Here's what we learned and why we made the choices we did:

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │              DESIGN DECISIONS — WHAT WE TOOK FROM EACH PROJECT          │
 ├──────────────────────────────────────────────────────────────────────────┤
 │                                                                         │
 │  BirdEdge-Mic (umr-ds)                                                  │
 │  └─ HTTP chunked WAV stream, 44.1 kHz, mDNS discovery                  │
 │     ✗ Too heavy for MicroPython (async web server)                      │
 │     ✓ Confirmed: no bird-specific DSP needed in firmware                │
 │     ✓ Insight: single-client streaming is fine for our use case         │
 │                                                                         │
 │  esp32_wireless_microphone (atomic14)                                   │
 │  └─ TCP sockets + WebSocket, 44.1 kHz, Python receiver                 │
 │     ✓ ADOPTED: TCP as primary transport (reliable delivery)             │
 │     ✓ ADOPTED: WiFi.setSleep(NONE) → wlan.config(pm=...) on Pico       │
 │     ✓ Validated: Python receiver pattern matches our bridge design      │
 │                                                                         │
 │  esp32-walkie-talkie (atomic14)                                         │
 │  └─ UDP broadcast, 16 kHz, 8-bit unsigned PCM, 1436-byte packets       │
 │     ✓ ADOPTED: WiFi power-save disable for packet timing               │
 │     ✓ Insight: 1436 bytes/packet near MTU — we use 1024 (safer)        │
 │                                                                         │
 │  ESP32_PRJ (vernonet)                                                   │
 │  └─ WiFi mic @ 22050 Hz, WAV + AAC variants, ring buffer               │
 │     ✓ Insight: AAC (FDK) needs PSRAM — not feasible on Pico 2W         │
 │     ✓ Insight: ring buffer + backpressure pattern for reliability       │
 │                                                                         │
 │  Micro-RTSP-Audio (pschatzmann)                                         │
 │  └─ RTSP + RTP/UDP, L16 PCM, 16 kHz, RP2040 socket glue               │
 │     ✗ ESP32 timer dependency — not portable to MicroPython              │
 │     ✓ Insight: RTP fragment_size=640 bytes works at 16 kHz              │
 │                                                                         │
 │  arduino-liblc3 (pschatzmann)                                           │
 │  └─ LC3 codec, 16-320 kbps, float-based, 4-16× compression            │
 │     ✗ Requires C native module — too complex for this guide             │
 │     ✓ Insight: RP2350 FPU makes LC3 feasible as future optimization    │
 │                                                                         │
 └──────────────────────────────────────────────────────────────────────────┘
```

### Why we chose TCP + raw 16-bit PCM as the default

| Alternative                       | Pros                                                       | Cons for Pico 2W                                                            |
| --------------------------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------- |
| **HTTP WAV stream** (BirdEdge)    | Standard, works with any player                            | Needs async web server; MicroPython can't serve reliably at 16 kHz          |
| **UDP stream**                    | Simplest code, no connection state                         | Packet loss causes audio glitches; no flow control; no disconnect detection |
| **RTSP/RTP** (Micro-RTSP)         | Industry standard, works with VLC                          | Complex protocol; ESP32 timer dependency; overkill for point-to-point       |
| **TCP + 16-bit PCM** (our choice) | Reliable delivery, clean disconnection, full dynamic range | ~250 kbit/s bandwidth — acceptable on any WiFi                              |

---

## Quick reference

```
┌─────────────────────────────────────────────────────────────────────┐
│                        STREAM FORMAT                                │
├─────────────────────────────────────────────────────────────────────┤
│  Transport: TCP (port 5005)                                         │
│  512 × int16 samples (little-endian)  =  1024 bytes per send       │
│  Pico sample rate: 16000 Hz  →  ~32 ms of audio per send           │
│  Sends per second: ~31                                              │
│  Bandwidth: ~31 KB/s  (~250 kbit/s)                                 │
│  Pico gain: 2x  +  ffmpeg gain: 2x  =  4x total                    │
│  ffmpeg filters: 2x HP 500Hz, LP 7500Hz                             │
│  Resampled on Pi to: 48000 Hz (required by BirdNET)                │
│  Output: 15-second WAV files to StreamData/                         │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        PIN QUICK REF                                │
├─────────────────────────────────────────────────────────────────────┤
│  Mic VCC  →  Pico Pin 36 (3V3)                                     │
│  Mic GND  →  Pico Pin 38 (GND)                                     │
│  Mic AUD  →  Pico Pin 31 (GP26 / ADC0)                             │
│  Optional: 100nF + 10µF caps between VCC and GND (noise reduction) │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     BIRDNET.CONF SETTINGS                            │
├─────────────────────────────────────────────────────────────────────┤
│  CONFIDENCE=0.5  (lower than default 0.7 for analog mic)            │
│  birdnet_recording.service: MASKED (bridge writes WAV directly)     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     SYSTEMD SERVICES                                 │
├─────────────────────────────────────────────────────────────────────┤
│  Bridge:    sudo systemctl {start|stop|status} birdnet-mic-bridge   │
│  Analysis:  sudo systemctl {start|stop|status} birdnet_analysis     │
│  Logs:      journalctl -u birdnet-mic-bridge -f                     │
│              journalctl -u birdnet_analysis -f                       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     FILE LOCATIONS                                    │
├─────────────────────────────────────────────────────────────────────┤
│  Bridge script:   /opt/mic_bridge/birdnet_mic_bridge.py              │
│  BirdNET config:  /etc/birdnet/birdnet.conf                          │
│  Stream data:     ~/BirdSongs/StreamData/                            │
└─────────────────────────────────────────────────────────────────────┘
```
