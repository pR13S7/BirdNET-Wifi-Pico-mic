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
import shutil
import time
import threading
import html

LISTEN_IP = "0.0.0.0"
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "5005"))
BUFFER_SIZE = 4096
CONN_TIMEOUT_SEC = int(os.environ.get("CONN_TIMEOUT_SEC", "45"))
PROGRESS_LOG_MB = float(os.environ.get("PROGRESS_LOG_MB", "1.0"))

INPUT_RATE = int(os.environ.get("INPUT_RATE", "16000"))
OUTPUT_RATE = 48000
CHANNELS = 1
SEGMENT_SEC = 15

# Optional narrow notch to remove the INMP441 sigma-delta idle tone.
# Measured (FFT of raw streams): Pico ~3547 Hz, ESP32 ~3600 Hz cluster.
# A shared center of 3575 Hz with a 180 Hz width covers both rigs.
# NOTCH_HZ=0 disables it. NOTCH_W is the notch width in Hz (ffmpeg width_type=h).
NOTCH_HZ = float(os.environ.get("NOTCH_HZ", "0"))
NOTCH_W = float(os.environ.get("NOTCH_W", "180"))
DECLICK_W = int(os.environ.get("DECLICK_W", "10"))
# Lower default aggressiveness to reduce "mechanical/ratchet" artifacts.
DECLICK_O = int(os.environ.get("DECLICK_O", "20"))

RECS_DIR = os.environ.get("RECS_DIR", "/home/pr13s7/BirdSongs/StreamData")
SOURCE_TAG = os.environ.get("SOURCE_TAG", "")
SERVICE_NAME = os.environ.get("SERVICE_NAME", "birdnet-mic-bridge")
SOURCE_LABEL = os.environ.get("SOURCE_LABEL", SOURCE_TAG or f"port-{LISTEN_PORT}")
TELEGRAM_SEND_CMD = os.environ.get("TELEGRAM_SEND_CMD", "").strip()
TELEGRAM_NOTIFY_TIMEOUT_SEC = float(os.environ.get("TELEGRAM_NOTIFY_TIMEOUT_SEC", "8"))

STAGING_DIR = os.path.join(RECS_DIR, ".staging" + (f"-{SOURCE_TAG}" if SOURCE_TAG else ""))

running = True


def shutdown(sig, frame):
    global running
    running = False
    sys.exit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)


def mover_thread():
    """Copy completed segments from staging to RECS_DIR.

    A segment is considered complete once a newer file appears in staging
    (ffmpeg only creates the next file after closing the previous one).
    We use copy+unlink instead of rename because BirdNET-Pi's inotify
    watcher only responds to IN_CREATE/IN_CLOSE_WRITE, not IN_MOVED_TO.
    Copying a finished 1.4MB file takes <50ms so the event race window
    is negligible compared to the original 15s streaming write.
    """
    while running:
        try:
            files = sorted(
                (f for f in os.listdir(STAGING_DIR) if f.endswith(".wav")),
                key=lambda f: os.path.getmtime(os.path.join(STAGING_DIR, f))
            )
        except OSError:
            time.sleep(0.5)
            continue

        if len(files) >= 2:
            for f in files[:-1]:
                src = os.path.join(STAGING_DIR, f)
                dst = os.path.join(RECS_DIR, f)
                try:
                    shutil.copy2(src, dst)
                    os.unlink(src)
                except OSError:
                    pass
        time.sleep(0.5)


def flush_staging():
    """Copy any remaining file in staging (called after ffmpeg exits)."""
    try:
        for f in os.listdir(STAGING_DIR):
            if f.endswith(".wav"):
                src = os.path.join(STAGING_DIR, f)
                dst = os.path.join(RECS_DIR, f)
                try:
                    shutil.copy2(src, dst)
                    os.unlink(src)
                except OSError:
                    pass
    except OSError:
        pass


def build_af_chain():
    chain = f"adeclick=w={DECLICK_W}:o={DECLICK_O},highpass=f=200:poles=2"
    if NOTCH_HZ > 0:
        chain += f",bandreject=f={NOTCH_HZ:g}:t=h:w={NOTCH_W:g}"
    return chain


def notify_telegram(message):
    if not TELEGRAM_SEND_CMD:
        return
    try:
        subprocess.run(
            [TELEGRAM_SEND_CMD, message],
            check=False,
            timeout=TELEGRAM_NOTIFY_TIMEOUT_SEC,
        )
    except PermissionError:
        # Fallback: some users keep script readable but not executable (0644).
        # Running via bash keeps notifications working in that setup.
        try:
            subprocess.run(
                ["/bin/bash", TELEGRAM_SEND_CMD, message],
                check=False,
                timeout=TELEGRAM_NOTIFY_TIMEOUT_SEC,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            print(
                f"[bridge] Telegram notify failed: {e} "
                f"(check read/execute permissions on {TELEGRAM_SEND_CMD})",
                flush=True,
            )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(
            f"[bridge] Telegram notify failed: {e} "
            f"(check read/execute permissions on {TELEGRAM_SEND_CMD})",
            flush=True,
        )


def notify_connected(addr):
    message = (
        "🟢 <b>Mic connected</b>\n"
        f"🔌 <b>Service:</b> {html.escape(SERVICE_NAME, quote=False)}\n"
        f"🎙️ <b>Source:</b> {html.escape(SOURCE_LABEL, quote=False)}\n"
        f"🌐 <b>Peer:</b> {html.escape(addr[0], quote=False)}:{addr[1]}"
    )
    notify_telegram(message)


def notify_disconnected(addr, reason, byte_count, started_at):
    peer = f"{addr[0]}:{addr[1]}" if addr else "unknown"
    duration_sec = max(int(time.time() - started_at), 0)
    mb = byte_count / (1024 * 1024)
    message = (
        "🔴 <b>Mic disconnected</b>\n"
        f"🔌 <b>Service:</b> {html.escape(SERVICE_NAME, quote=False)}\n"
        f"🎙️ <b>Source:</b> {html.escape(SOURCE_LABEL, quote=False)}\n"
        f"🌐 <b>Peer:</b> {html.escape(peer, quote=False)}\n"
        f"⚠️ <b>Reason:</b> {html.escape(reason, quote=False)}\n"
        f"⏱️ <b>Session:</b> {duration_sec}s, {mb:.1f} MB"
    )
    notify_telegram(message)


def start_ffmpeg():
    return subprocess.Popen(
        [
            "ffmpeg",
            "-hide_banner", "-loglevel", "warning",
            "-f", "s16le",
            "-ar", str(INPUT_RATE),
            "-ac", str(CHANNELS),
            "-i", "pipe:0",
            "-af", build_af_chain(),
            "-ar", str(OUTPUT_RATE),
            "-ac", str(CHANNELS),
            "-f", "segment",
            "-segment_time", str(SEGMENT_SEC),
            "-segment_format", "wav",
            "-strftime", "1",
            os.path.join(STAGING_DIR, "%Y-%m-%d-birdnet-%H:%M:%S.wav")
        ],
        stdin=subprocess.PIPE
    )


def main():
    os.makedirs(RECS_DIR, exist_ok=True)
    os.makedirs(STAGING_DIR, exist_ok=True)

    mover = threading.Thread(target=mover_thread, daemon=True)
    mover.start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_IP, LISTEN_PORT))
    srv.listen(1)

    print(f"[bridge] Listening on :{LISTEN_PORT}")
    print(f"[bridge] Writing {SEGMENT_SEC}s WAV files to {RECS_DIR}")
    print(f"[bridge] Staging via {STAGING_DIR} (atomic move)")
    print(f"[bridge] Input: {INPUT_RATE} Hz s16le mono → {OUTPUT_RATE} Hz WAV")
    print(f"[bridge] Declick: w={DECLICK_W} o={DECLICK_O}")
    if NOTCH_HZ > 0:
        print(f"[bridge] Notch: {NOTCH_HZ:g} Hz, width {NOTCH_W:g} Hz")
    else:
        print("[bridge] Notch: disabled")
    print(f"[bridge] Filter chain: {build_af_chain()}")
    print(f"[bridge] Socket timeout: {CONN_TIMEOUT_SEC}s")
    print(f"[bridge] Source tag: {SOURCE_TAG or '(none)'}")
    print(f"[bridge] Service name: {SERVICE_NAME}")
    print(f"[bridge] Source label: {SOURCE_LABEL}")
    if TELEGRAM_SEND_CMD:
        print(f"[bridge] Telegram notify: enabled ({TELEGRAM_SEND_CMD})")
    else:
        print("[bridge] Telegram notify: disabled (TELEGRAM_SEND_CMD not set)")
    print("[bridge] No ALSA loopback needed for recording!")

    while running:
        print(f"[bridge] Waiting for {SOURCE_LABEL} connection...", flush=True)
        conn, addr = srv.accept()
        conn.settimeout(CONN_TIMEOUT_SEC)
        print(f"[bridge] Connected: {addr}", flush=True)

        try:
            proc = start_ffmpeg()
        except OSError as e:
            print(f"[bridge] Failed to start ffmpeg: {e}", flush=True)
            conn.close()
            continue

        notify_connected(addr)
        byte_count = 0
        progress_step_bytes = max(int(PROGRESS_LOG_MB * 1024 * 1024), 1024 * 1024)
        next_log_at = progress_step_bytes
        last_rate_time = time.time()
        last_rate_bytes = 0
        session_started = time.time()
        disconnect_reason = "peer closed connection"

        try:
            while True:
                data = conn.recv(BUFFER_SIZE)
                if not data:
                    disconnect_reason = "peer closed connection"
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
            disconnect_reason = str(e) or e.__class__.__name__
            print(f"[bridge] Connection lost: {e}", flush=True)
        finally:
            conn.close()
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.wait()
            flush_staging()
            print("[bridge] Client disconnected, restarting on next connect", flush=True)
            notify_disconnected(addr, disconnect_reason, byte_count, session_started)


if __name__ == "__main__":
    main()
