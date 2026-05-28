import time
import socket
import network
import gc
from machine import I2S, Pin

WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

SERVER_IP = "192.168.0.50"
SERVER_PORT = 5005

SAMPLE_RATE = 16000
PACKET_FRAMES = 1024
I2S_BITS = 32
I2S_FMT = I2S.MONO
RECONNECT_DELAY = 3

# ESP32-S3 I2S pins (any GPIO works, no WS=SCK+1 constraint)
I2S_SCK = 4    # BCLK
I2S_WS = 5     # LRCLK / WS
I2S_SD = 6     # DATA / DOUT

led = Pin(48, Pin.OUT)  # onboard RGB LED data pin on most ESP32-S3-N16R8 boards


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.disconnect()
    time.sleep(1)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    print("Connecting to WiFi", end="")
    for _ in range(30):
        if wlan.isconnected():
            break
        print(".", end="")
        time.sleep(1)

    if not wlan.isconnected():
        print("\nWiFi FAILED — rebooting in 5s")
        time.sleep(5)
        import machine
        machine.reset()

    ip = wlan.ifconfig()[0]
    print(f"\nConnected! IP: {ip}")
    led.on()
    wlan.config(pm=network.WLAN.PM_NONE)
    return wlan, ip


def tcp_connect():
    for attempt in range(10):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((SERVER_IP, SERVER_PORT))
            print(f"TCP connected to {SERVER_IP}:{SERVER_PORT}")
            led.on()
            return s
        except OSError as e:
            print(f"TCP failed ({e}), retry {attempt+1}/10...")
            led.off()
            try:
                s.close()
            except:
                pass
            time.sleep(RECONNECT_DELAY)
    raise OSError("TCP connect failed 10 times")


def run():
    wlan, wifi_ip = connect_wifi()

    audio_in = I2S(
        0,
        sck=Pin(I2S_SCK),
        ws=Pin(I2S_WS),
        sd=Pin(I2S_SD),
        mode=I2S.RX,
        bits=I2S_BITS,
        format=I2S_FMT,
        rate=SAMPLE_RATE,
        ibuf=40000,
    )

    print(f"I2S: INMP441 at {SAMPLE_RATE} Hz → {SERVER_IP}:{SERVER_PORT}")

    buf = bytearray(PACKET_FRAMES * 4)  # 32-bit mono = 4 bytes/frame
    out = bytearray(PACKET_FRAMES * 2)  # 16-bit output = 2 bytes/frame
    out_mv = memoryview(out)

    while True:
        sock = tcp_connect()
        sock.settimeout(30)
        total_bytes = 0
        start = time.ticks_ms()
        last_log = start

        try:
            while True:
                num_read = audio_in.readinto(buf)
                if num_read == 0:
                    continue

                frames = num_read // 4
                for i in range(frames):
                    out[i * 2] = buf[i * 4 + 2]
                    out[i * 2 + 1] = buf[i * 4 + 3]

                to_send = out_mv[:frames * 2]
                sent = 0
                while sent < len(to_send):
                    n = sock.send(to_send[sent:])
                    if n == 0:
                        raise OSError("send returned 0")
                    sent += n
                total_bytes += frames * 2

                now = time.ticks_ms()
                if time.ticks_diff(now, last_log) > 60_000:
                    elapsed = time.ticks_diff(now, start) // 1000
                    kbps = total_bytes / 1024 / max(elapsed, 1)
                    sent_mb = total_bytes / (1024 * 1024)
                    rssi = wlan.status('rssi') if wlan.isconnected() else 0
                    mem_kb = gc.mem_free() // 1024
                    print(f"[esp32] {elapsed}s {kbps:.0f}KB/s sent={sent_mb:.1f}MB rssi={rssi} mem={mem_kb}KB")
                    last_log = now
                    gc.collect()

        except OSError as e:
            print(f"Connection lost ({e}), reconnecting...")

        try:
            sock.close()
        except:
            pass

        if not wlan.isconnected():
            print("WiFi lost, reconnecting...")
            wlan, wifi_ip = connect_wifi()

        time.sleep(1)


crash_count = 0
while True:
    try:
        run()
    except Exception as e:
        crash_count += 1
        print(f"\n*** CRASH #{crash_count}: {type(e).__name__}: {e} ***")
        led.off()
        time.sleep(3)
    except KeyboardInterrupt:
        print("Stopped by user")
        led.off()
        break
