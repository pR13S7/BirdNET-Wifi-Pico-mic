#!/usr/bin/env python3
"""
TCP → WAV bridge for BirdNET-Pi.

Accepts a TCP connection streaming raw s16le PCM (from INMP441 I2S mic),
pipes it through ffmpeg for filtering and resampling, and writes 15-second
WAV files directly to BirdNET-Pi's StreamData folder.

Supports multiple clients on separate ports via INPUT_RATE env var:
  - Pico 2W:   port 5005, INPUT_RATE=16000
  - ESP32-S3:  port 5006, INPUT_RATE=48000

Install: sudo ./install.sh --mode both
"""

import socket
import subprocess
import sys
import signal
import os
import time

LISTEN_IP = "0.0.0.0"
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "5005"))
BUFFER_SIZE = 4096
CONN_TIMEOUT_SEC = int(os.environ.get("CONN_TIMEOUT_SEC", "45"))
PROGRESS_LOG_MB = float(os.environ.get("PROGRESS_LOG_MB", "1.0"))

INPUT_RATE = int(os.environ.get("INPUT_RATE", "16000"))
OUTPUT_RATE = 48000
CHANNELS = 1
SEGMENT_SEC = 15

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
            "-af", "adeclick=w=10:o=75,highpass=f=200:poles=2",
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
    print(f"[bridge] Input: {INPUT_RATE} Hz s16le mono → {OUTPUT_RATE} Hz WAV")
    print(f"[bridge] Socket timeout: {CONN_TIMEOUT_SEC}s")
    print(f"[bridge] Progress log interval: {PROGRESS_LOG_MB:.1f} MB")
    print(f"[bridge] No ALSA loopback needed for recording!")

    while running:
        print("[bridge] Waiting for Pico 2W connection...", flush=True)
        conn, addr = srv.accept()
        conn.settimeout(CONN_TIMEOUT_SEC)
        print(f"[bridge] Connected: {addr}", flush=True)

        try:
            proc = start_ffmpeg()
        except OSError as e:
            print(f"[bridge] Failed to start ffmpeg: {e}", flush=True)
            conn.close()
            continue

        byte_count = 0
        progress_step_bytes = max(int(PROGRESS_LOG_MB * 1024 * 1024), 1024 * 1024)
        next_log_at = progress_step_bytes
        last_rate_time = time.time()
        last_rate_bytes = 0

        try:
            while True:
                data = conn.recv(BUFFER_SIZE)
                if not data:
                    break
                proc.stdin.write(data)
                byte_count += len(data)

                now = time.time()
                dt = now - last_rate_time
                if dt >= 10.0:
                    rate = (byte_count - last_rate_bytes) / dt / 1024
                    print(f"[bridge] {byte_count/1024:.0f} KB  {rate:.1f} KB/s", flush=True)
                    last_rate_time = now
                    last_rate_bytes = byte_count

                if byte_count >= next_log_at:
                    mb = byte_count / (1024 * 1024)
                    print(f"[bridge] {mb:.1f} MB received", flush=True)
                    while next_log_at <= byte_count:
                        next_log_at += progress_step_bytes
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
