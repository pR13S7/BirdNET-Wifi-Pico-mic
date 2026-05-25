# Wireless Microphone Streaming to BirdNET-Pi

> **Build Guide** — Stream audio from an INMP441 I2S MEMS mic on a Pico 2W over WiFi to a Raspberry Pi 4B running [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi) for realtime bird classification.

**Stack:** `Pico 2W (MicroPython)` · `Pi 4B (BirdNET-Pi)` · `INMP441 I2S` · `TCP → direct WAV`

---

## Project files

```
pico 2w/
├── README.md                              ← this file
├── pico/                                  ← upload to Pico 2W via Thonny
│   ├── main.py                            ← streaming firmware (with LCD support)
│   ├── lcd.py                             ← Waveshare LCD 1.3" driver (optional)
│   └── wiring_inmp441_pico2w.svg          ← wiring diagram (open in browser)
└── bridge/                                ← deploy to Pi 4B
    ├── install.sh                         ← automated installer (sudo bash install.sh)
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
10. [Appendix: Why INMP441?](#appendix-why-inmp441)

---

## 1. How it works

The INMP441 is a **digital I2S microphone** — it outputs a clocked digital bitstream, not an analog voltage. The Pico 2W's hardware I2S peripheral (driven by PIO) captures the audio data directly into memory buffers at 22050 Hz. The CPU is free for WiFi — no manual ADC timing or gain calibration needed.

On the Pi, a **bridge service** accepts the TCP connection, pipes the audio through `ffmpeg` for gentle filtering (high-pass at 200 Hz, low-pass at 10 kHz) and resampling (22050 Hz → 48 kHz), and writes **15-second WAV files** directly to BirdNET-Pi's `StreamData` folder.

### Architecture

```
┌──────────────────┐        ┌──────────────────┐        ┌──────────────────┐
│     INMP441      │        │    Pico 2W       │        │     Pi 4B        │
│                  │  I2S   │                  │  TCP   │                  │
│  Digital MEMS    │───────>│  I2S + WiFi      │───────>│  BirdNET-Pi      │
│  microphone      │ 3 wire │                  │  WiFi  │                  │
└──────────────────┘        └──────────────────┘        └──────────────────┘
```

```
 Detailed Signal Flow:

 Bird song
      │
      ▼
 ┌─────────┐  I2S        ┌─────────────┐  TCP stream   ┌───────────────────────────────────┐
 │  INMP441 │  digital    │  Pico 2W    │  over WiFi   │            Pi 4B                  │
 │  MEMS    │ ─────────> │  HW I2S RX  │ ───────────> │                                   │
 │  mic     │  SCK/WS/SD │  → buffer   │  22050Hz     │  ┌─────────┐    ┌──────────────┐  │
 └─────────┘  24-bit     │  → send     │  s16le PCM   │  │ Bridge  │    │  WAV files   │  │
              data        │  → yield    │  1024 smp/pk │  │ service │───>│  (15s each)  │  │
                          └─────────────┘              │  │ +ffmpeg │    └──────┬───────┘  │
                                                       │  └─────────┘          │          │
                                                       │   HP 200Hz             ▼          │
                                                       │   LP 10kHz   ┌──────────────┐    │
                                                       │   22k → 48k  │  BirdNET-Pi  │    │
                                                       │              │  analysis    │    │
                                                       │              │  + web UI    │    │
                                                       │              └──────────────┘    │
                                                       └───────────────────────────────────┘
```

> **Advantages over analog mic + ADC:**
> - No ADC noise, no DC offset drift, no gain calibration
> - Hardware-clocked sampling — 22050 Hz sustained without CPU bottleneck
> - Full 24-bit dynamic range from the mic (truncated to 16-bit for streaming)
> - Higher sample rate captures bird frequencies up to 11 kHz (vs 8 kHz with 16 kHz ADC)
> - Simpler wiring — no decoupling capacitors needed

---

## 2. What you need

### Hardware

- Raspberry Pi **Pico 2W** (the W variant with WiFi)
- **INMP441** I2S MEMS Microphone module (available on AliExpress, Amazon, eBay — ~$2-5)
- 5 short jumper wires (or solder directly)
- Breadboard (optional, for prototyping)
- Raspberry Pi **4B** — already on your WiFi network, running BirdNET-Pi

### Software (already on Pi if BirdNET-Pi is installed)

- [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi) installed on the Pi 4B
- MicroPython firmware on the Pico 2W
- Thonny or `mpremote` to upload code to the Pico
- `ffmpeg` on the Pi (`sudo apt install ffmpeg` — may already be present)

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
| Data format          | 24-bit I2S, MSB-first                           |
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

Five signal connections plus one channel-select tie to ground. No decoupling capacitors needed — the INMP441 is a digital device that's immune to power rail noise affecting the audio signal.

### Wiring diagram

Open `pico/wiring_inmp441_pico2w.svg` in a browser for the full color diagram.

```
      INMP441 Module                        Pico 2W
    ┌──────────────────┐              ┌──────────────────┐
    │                  │              │                  │
    │  VDD  ●──────────│── Red ──────│── Pin 36  3V3    │
    │                  │              │                  │
    │  GND  ●──────────│── Black ────│── Pin 23  GND    │
    │                  │              │                  │
    │  SCK  ●──────────│── Blue ─────│── Pin 21  GP16   │
    │                  │              │                  │
    │  WS   ●──────────│── Green ────│── Pin 22  GP17   │
    │                  │              │                  │
    │  SD   ●──────────│── Yellow ───│── Pin 24  GP18   │
    │                  │              │                  │
    │  L/R  ●──────────│── (to GND) ─│── Pin 23  GND    │
    │                  │              │                  │
    └──────────────────┘              └──────────────────┘
```

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

> **Tip:** The INMP441's sound port (tiny hole) is on the **bottom** of the module. When mounting, ensure nothing covers the underside.

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
import time
import socket
import network
from machine import I2S, Pin

# ── Configuration ─────────────────────────────────────────
WIFI_SSID     = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

SERVER_IP   = "192.168.1.XXX"   # ← your Pi 4B IP address
SERVER_PORT = 5005

SAMPLE_RATE   = 22050     # Hz — I2S hardware-clocked, reliable
PACKET_FRAMES = 1024      # samples per send (~46 ms at 22050 Hz)
RECONNECT_DELAY = 3       # seconds to wait before retrying connection

# I2S pins (WS must be SCK + 1 on Pico)
I2S_SCK = 16
I2S_WS  = 17
I2S_SD  = 18

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

audio_in = I2S(
    0,
    sck=Pin(I2S_SCK),
    ws=Pin(I2S_WS),
    sd=Pin(I2S_SD),
    mode=I2S.RX,
    bits=16,
    format=I2S.MONO,
    rate=SAMPLE_RATE,
    ibuf=20000,
)

print(f"I2S mic ready: INMP441 at {SAMPLE_RATE} Hz")
print(f"Streaming to {SERVER_IP}:{SERVER_PORT}")

while True:
    sock = tcp_connect()
    buf = bytearray(PACKET_FRAMES * 2)

    try:
        while True:
            num_read = audio_in.readinto(buf)
            if num_read > 0:
                sock.send(buf[:num_read])
    except OSError as e:
        print(f"Connection lost ({e}), reconnecting...")
        try:
            sock.close()
        except:
            pass
```

> **Warning:** Replace `YOUR_WIFI_SSID`, `YOUR_WIFI_PASSWORD`, and `192.168.1.XXX` with your actual network credentials and Pi 4B IP before uploading. Find the Pi's IP with `hostname -I` on the Pi.

> **Warning:** The Pico 2W only supports **2.4 GHz WiFi**. It cannot connect to 5 GHz networks.

To **test**, click the green Run button in Thonny. To **deploy**, save to the Pico permanently: **File → Save as → Raspberry Pi Pico → `main.py`**. The Pico will then auto-stream on every power-up — no computer needed.

### Why 22050 Hz?

With I2S, sample rate is no longer limited by MicroPython's CPU speed — the PIO hardware handles clocking. We chose **22050 Hz** because:

| Rate         | Captures up to | Bandwidth      | Bird coverage                                |
| ------------ | -------------- | -------------- | -------------------------------------------- |
| 16000 Hz     | ~8 kHz         | ~31 KB/s       | Most birds (1–8 kHz)                         |
| **22050 Hz** | **~11 kHz**    | **~43 KB/s**   | **Nearly all birds including high trills**   |
| 44100 Hz     | ~22 kHz        | ~86 KB/s       | Overkill — doubles bandwidth for no gain     |

### Why I2S instead of ADC?

The previous version used an analog mic (SPH8878LR5H-1) with the Pico's 12-bit ADC. This had several issues:
- ADC sampling was software-timed, capping at 16 kHz with WiFi active
- WiFi switching noise corrupted the analog signal (required decoupling caps)
- DC offset calibration needed at every boot
- Manual gain stage on both Pico and bridge sides

With I2S, the Pico's PIO peripheral handles all timing in hardware. The `audio_in.readinto(buf)` call blocks until the buffer is full — no busy-wait loop, no timing jitter, no CPU starvation of the WiFi stack.

---

## 6. Bridge service (TCP → WAV files)

The bridge listens for a TCP connection from the Pico 2W, pipes the audio through `ffmpeg` for filtering and resampling, and writes **15-second WAV files** directly to BirdNET-Pi's `StreamData` folder.

### `birdnet_mic_bridge.py` (on Pi 4B)

```python
#!/usr/bin/env python3
import socket, subprocess, sys, signal, os

LISTEN_IP    = "0.0.0.0"
LISTEN_PORT  = 5005
BUFFER_SIZE  = 4096

INPUT_RATE   = 22050
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
            "-af", "highpass=f=200:poles=2,lowpass=f=10000",
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
    print(f"[bridge] Filters: HP 200Hz, LP 10kHz (I2S — no volume boost needed)")

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

- **`highpass=f=200:poles=2`** — Removes wind noise and handling rumble below 200 Hz. Bird vocalizations start above 1 kHz so nothing useful is lost. (Lighter than before — I2S has no low-frequency ADC noise to fight.)
- **`lowpass=f=10000`** — Removes ultrasonic artifacts above 10 kHz. With 22050 Hz sample rate, Nyquist is 11025 Hz.
- **No volume boost** — The INMP441 outputs full-scale digital audio at -26 dBFS sensitivity. No gain stage needed.
- **`-f segment -segment_time 15`** — Splits audio into 15-second WAV files with timestamped names.

### Install as a systemd service

**Automated (recommended):** Use the install script — it detects your user, sets up paths, and masks conflicting services:

```bash
cd bridge/
sudo bash install.sh
```

The script auto-detects your username and StreamData path. Override with flags if needed:

```bash
sudo bash install.sh --user pi --recs-dir /home/pi/BirdSongs/StreamData
```

To uninstall (stops service, removes files, unmasks recording service):

```bash
sudo bash install.sh --uninstall
```

**Manual alternative:** If you prefer to install step by step:

```bash
sudo mkdir -p /opt/mic_bridge
sudo cp birdnet_mic_bridge.py /opt/mic_bridge/birdnet_mic_bridge.py

sudo tee /etc/systemd/system/birdnet-mic-bridge.service << 'EOF'
[Unit]
Description=Pico 2W wireless mic bridge for BirdNET-Pi
After=network-online.target
Wants=network-online.target
Before=birdnet_analysis.service

[Service]
Type=simple
User=YOUR_USER
ExecStart=/usr/bin/python3 /opt/mic_bridge/birdnet_mic_bridge.py
Environment=RECS_DIR=/home/YOUR_USER/BirdSongs/StreamData
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now birdnet-mic-bridge.service
sudo systemctl stop birdnet_recording.service
sudo systemctl mask birdnet_recording.service
```

---

## 7. BirdNET-Pi configuration

Since the bridge writes WAV files directly to StreamData, BirdNET-Pi's **analysis service** picks them up automatically.

### Confidence threshold

With the INMP441's clean digital output, the default threshold should work well. If you want to catch more distant birds, lower it:

```bash
sudo nano /etc/birdnet/birdnet.conf

# Optional — default 0.7 is fine with I2S mic
CONFIDENCE=0.6
```

Then restart: `sudo systemctl restart birdnet_analysis.service`

### Mask the recording service

```bash
sudo systemctl stop birdnet_recording.service
sudo systemctl mask birdnet_recording.service
```

> **Tip:** To undo later: `sudo systemctl unmask birdnet_recording.service`

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
 │  Pico I2S → TCP → bridge → ffmpeg → 15s WAV files      │
 │                              → birdnet_analysis          │
 └─────────────────────────────────────────────────────────┘
```

### Step 1 — Make sure services are running on Pi

```bash
sudo systemctl status birdnet-mic-bridge.service
sudo systemctl status birdnet_analysis.service
```

### Step 2 — Power on the Pico 2W

The LED will blink while connecting to WiFi, then go solid. Within seconds, WAV files start appearing in `~/BirdSongs/StreamData/`.

### Step 3 — Verify detections

Open the BirdNET-Pi web UI at `http://birdnetpi.local`.

### Quick debug commands

```bash
# Are services running?
sudo systemctl status birdnet-mic-bridge.service
sudo systemctl status birdnet_analysis.service

# Is the TCP connection established?
ss -tn sport = :5005

# Is the bridge running and forwarding?
journalctl -u birdnet-mic-bridge -f

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
```

---

## 9. Troubleshooting & improvements

### Common issues

| Symptom                                        | Cause                                                    | Fix                                                                                       |
| ---------------------------------------------- | -------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| No detections in BirdNET-Pi                    | Bridge not writing WAV files or analysis not running     | Check `ls -lt ~/BirdSongs/StreamData/*.wav` and `systemctl status birdnet_analysis`       |
| WAV files are silent (peak < 100)              | I2S wiring wrong or mic not powered                      | Check VDD is 3.3V, SCK/WS/SD connected, L/R tied to GND                                  |
| `ValueError: invalid I2S pin`                  | WS pin not SCK+1                                         | Use GP16 (SCK) + GP17 (WS) — WS must always be SCK + 1                                   |
| `ModuleNotFoundError: network`                 | Wrong MicroPython firmware (non-W)                       | Flash `RPI_PICO2_W` firmware                                                              |
| Pico LED blinks fast forever                   | WiFi credentials wrong or 5 GHz network                  | Pico 2W only supports 2.4 GHz; check SSID/password                                       |
| Audio sounds choppy                            | WiFi interference or TCP backpressure                    | Move Pico closer to router; check WiFi congestion                                         |
| All samples are zero                           | L/R pin floating (not connected)                         | Tie L/R to GND (left channel) or 3V3 (right channel)                                     |
| Audio is half the expected volume              | L/R on wrong channel                                     | Ensure `format=I2S.MONO` and L/R matches (GND = left)                                    |
| `arecord: Device or resource busy`             | `birdnet_recording.service` conflicts with bridge        | `sudo systemctl mask birdnet_recording.service`                                           |

### Tuning the ffmpeg filter chain

The bridge uses: `highpass=f=200:poles=2,lowpass=f=10000`

| Parameter     | Default   | Increase if...                         | Decrease if...                          |
| ------------- | --------- | -------------------------------------- | --------------------------------------- |
| `highpass=f=` | 200 Hz    | Wind noise is dominant                 | Low-frequency bird calls being cut      |
| `lowpass=f=`  | 10000 Hz  | High-pitched noise artifacts           | You hear ultrasonic artifacts (unlikely)|

After editing bridge filters, restart: `sudo systemctl restart birdnet-mic-bridge.service`

### Weatherproof outdoor deployment

- Power the Pico 2W from a small USB power bank (~40–80 mA over WiFi — a 1000 mAh bank gives **10+ hours**)
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

The SPH0645's non-standard timing (documented at [StreetSense project](https://hackaday.io/project/162059-street-sense/log/160705-new-i2s-microphone)) means audio samples arrive shifted left by one bit, causing a 6 dB level increase that requires software correction on every read.

---

## Quick reference

```
┌─────────────────────────────────────────────────────────────────────┐
│                        STREAM FORMAT                                │
├─────────────────────────────────────────────────────────────────────┤
│  Transport: TCP (port 5005)                                         │
│  1024 × int16 samples (little-endian)  =  2048 bytes per send      │
│  Pico sample rate: 22050 Hz (I2S hardware-clocked)                  │
│  Sends per second: ~21                                              │
│  Bandwidth: ~43 KB/s  (~345 kbit/s)                                 │
│  No gain needed — I2S provides full-scale digital audio             │
│  ffmpeg filters: HP 200Hz, LP 10kHz                                 │
│  Resampled on Pi to: 48000 Hz (required by BirdNET)                │
│  Output: 15-second WAV files to StreamData/                         │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        PIN QUICK REF                                │
├─────────────────────────────────────────────────────────────────────┤
│  INMP441 VDD  →  Pico Pin 36 (3V3)                                 │
│  INMP441 GND  →  Pico Pin 23 (GND)                                 │
│  INMP441 SCK  →  Pico Pin 21 (GP16)                                │
│  INMP441 WS   →  Pico Pin 22 (GP17)   ← must be SCK + 1           │
│  INMP441 SD   →  Pico Pin 24 (GP18)                                │
│  INMP441 L/R  →  Pico Pin 23 (GND)    ← left channel              │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     SYSTEMD SERVICES                                 │
├─────────────────────────────────────────────────────────────────────┤
│  Bridge:    sudo systemctl {start|stop|status} birdnet-mic-bridge   │
│  Analysis:  sudo systemctl {start|stop|status} birdnet_analysis     │
│  Logs:      journalctl -u birdnet-mic-bridge -f                     │
└─────────────────────────────────────────────────────────────────────┘
```
