import time
import socket
import network
from machine import I2S, Pin

try:
    from lcd import LCD, WHITE, RED, GREEN, YELLOW, GRAY, ORANGE, CYAN
    lcd = LCD()
    HAS_LCD = True
except Exception:
    HAS_LCD = False

WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

SERVER_IP = "192.168.0.50"
SERVER_PORT = 5005

SAMPLE_RATE = 16000
PACKET_FRAMES = 1024
I2S_BITS = 32
I2S_FMT = I2S.STEREO
RECONNECT_DELAY = 3

I2S_SCK = 16
I2S_WS = 17
I2S_SD = 18

led = Pin("LED", Pin.OUT)


@micropython.native
def extract_left(buf32, buf16, n):
    j = 0
    for i in range(0, n, 8):
        buf16[j] = buf32[i + 2]
        buf16[j + 1] = buf32[i + 3]
        j += 2


stat_kbps = [0]
stat_rssi = [0]
stat_pkts = [0]


def fmt_uptime(sec):
    d = sec // 86400
    h = (sec % 86400) // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if d > 0:
        return f"{d}d {h:02d}h {m:02d}m {s:02d}s"
    return f"{h}h {m:02d}m {s:02d}s"


def draw_info(wifi_ip, bridge, uptime_s, sent_mb, msg):
    lcd.clear()
    lcd.text("BirdNET Mic", 4, 4, GREEN)
    lcd.hline(4, 16, 232, GRAY)

    if wifi_ip:
        lcd.text(f"WiFi: OK  {stat_rssi[0]}dBm", 4, 24, GREEN)
        lcd.text(wifi_ip, 4, 36, WHITE)
    else:
        lcd.text("WiFi: connecting", 4, 24, YELLOW)

    lcd.hline(4, 50, 232, GRAY)

    if bridge:
        lcd.text("Bridge: Streaming", 4, 58, GREEN)
    else:
        lcd.text("Bridge: Waiting", 4, 58, RED)
    lcd.text(f"{SERVER_IP}:{SERVER_PORT}", 4, 70, GRAY)

    lcd.hline(4, 84, 232, GRAY)

    lcd.text(f"Up: {fmt_uptime(uptime_s)}", 4, 92, WHITE)
    lcd.text(f"Sent: {sent_mb:.1f} MB  {stat_kbps[0]:.0f} KB/s", 4, 104, GREEN)
    lcd.text(f"Pkts: {stat_pkts[0]}  Crash: {crash_count}", 4, 116, CYAN)

    if msg:
        lcd.hline(4, 220, 232, GRAY)
        lcd.text(msg, 4, 228, ORANGE)

    lcd.show()


def update_display(wifi_ip, bridge, uptime_s, sent_mb, msg=None):
    if not HAS_LCD:
        return
    draw_info(wifi_ip, bridge, uptime_s, sent_mb, msg)


def connect_wifi():
    if HAS_LCD:
        draw_info(None, False, 0, 0, "Booting...")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.disconnect()
    time.sleep(1)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    print("Connecting to WiFi", end="")
    for _ in range(30):
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
        if HAS_LCD:
            draw_info(None, False, 0, 0, "WiFi FAILED!")
        while True:
            led.toggle()
            time.sleep(0.1)

    ip = wlan.ifconfig()[0]
    print(f"\nConnected! IP: {ip}")
    led.on()
    wlan.config(pm=0xa11140)
    if HAS_LCD:
        draw_info(ip, False, 0, 0, "WiFi OK")
    return wlan, ip


def tcp_connect(wifi_ip):
    if HAS_LCD:
        draw_info(wifi_ip, False, 0, 0, "Connecting TCP...")
    for attempt in range(3):
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
    raise OSError("TCP connect failed 10 times, WiFi likely dead")


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

    buf = bytearray(PACKET_FRAMES * 8)
    out = bytearray(PACKET_FRAMES * 2)
    out_mv = memoryview(out)

    while True:
        sock = tcp_connect(wifi_ip)
        sock.settimeout(30)
        total_bytes = 0
        start = time.ticks_ms()
        last_log = start

        update_display(wifi_ip, True, 0, 0)

        try:
            while True:
                num_read = audio_in.readinto(buf)
                if num_read == 0:
                    continue

                extract_left(buf, out, num_read)
                j = num_read // 4

                sock.send(out_mv[:j])
                total_bytes += j

                now = time.ticks_ms()
                if time.ticks_diff(now, last_log) > 60_000:
                    elapsed = time.ticks_diff(now, start) // 1000
                    sent_mb = total_bytes / (1024 * 1024)
                    stat_kbps[0] = total_bytes / 1024 / max(elapsed, 1)
                    stat_pkts[0] = total_bytes // (PACKET_FRAMES * 2)
                    try:
                        stat_rssi[0] = wlan.status('rssi')
                    except:
                        pass
                    print(f"[pico] {elapsed}s {stat_kbps[0]:.0f}KB/s sent={sent_mb:.1f}MB")
                    update_display(wifi_ip, True, elapsed, sent_mb)
                    last_log = now

        except OSError as e:
            print(f"Connection lost ({e}), reconnecting...")

        try:
            sock.close()
        except:
            pass
        time.sleep(1)


crash_count = 0
while True:
    try:
        run()
    except Exception as e:
        crash_count += 1
        print(f"\n*** CRASH #{crash_count}: {type(e).__name__}: {e} ***")
        if HAS_LCD:
            lcd.clear()
            lcd.text("CRASH - restarting", 4, 100, RED)
            lcd.text(f"#{crash_count}: {type(e).__name__}", 4, 116, ORANGE)
            lcd.text(str(e)[:28], 4, 132, GRAY)
            lcd.show()
        led.off()
        time.sleep(3)
    except KeyboardInterrupt:
        print("Stopped by user")
        led.off()
        if HAS_LCD:
            lcd.clear()
            lcd.text("Stopped", 4, 110, YELLOW)
            lcd.show()
        break
