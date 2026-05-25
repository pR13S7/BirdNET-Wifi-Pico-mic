import time
import socket
import network
from machine import ADC, Pin

try:
    from lcd import LCD, BLACK, WHITE, RED, GREEN, YELLOW, GRAY, ORANGE, CYAN, BLUE
    lcd = LCD()
    HAS_LCD = True
except Exception:
    HAS_LCD = False

WIFI_SSID = "cave"
WIFI_PASSWORD = "deadbeef00"

SERVER_IP = "192.168.0.50"
SERVER_PORT = 5005

SAMPLE_RATE = 16000
PACKET_FRAMES = 512
RECONNECT_DELAY = 3
GAIN = 4

led = Pin("LED", Pin.OUT)

# ── Joystick for screen switching (Waveshare Pico LCD 1.3") ──

if HAS_LCD:
    JOY_UP = Pin(2, Pin.IN, Pin.PULL_UP)
    JOY_DOWN = Pin(18, Pin.IN, Pin.PULL_UP)
else:
    JOY_UP = JOY_DOWN = None

SCREEN_INFO = 0
SCREEN_VU = 1
SCREEN_WAVE = 2
SCREEN_COUNT = 3
current_screen = SCREEN_INFO

# ── Audio stats collected during streaming ────────────────

pkt_peak = 0
vu_history = bytearray(220)
vu_pos = 0
wave_buf = [0] * 240
wave_idx = 0
wave_downsample = PACKET_FRAMES // 240 or 1

# ── Color helpers for VU bars ─────────────────────────────


def vu_color(level):
    if level > 200:
        return RED
    if level > 140:
        return ORANGE
    if level > 80:
        return YELLOW
    return GREEN

# ── Display helpers ───────────────────────────────────────


def fmt_uptime(sec):
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h}h {m:02d}m {s:02d}s"


def draw_info(wifi_ip, bridge, uptime_s, sent_mb, midpoint, msg):
    lcd.clear()
    lcd.text("BirdNET Mic", 4, 4, GREEN)
    lcd.hline(4, 16, 232, GRAY)

    if wifi_ip:
        lcd.text("WiFi: Connected", 4, 24, GREEN)
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

    lcd.text(f"Up:   {fmt_uptime(uptime_s)}", 4, 92, WHITE)
    lcd.text(f"Sent: {sent_mb:.1f} MB", 4, 104, WHITE)
    lcd.text(f"Mid:  {midpoint}", 4, 116, GRAY)
    lcd.text(f"Rate: {SAMPLE_RATE} Hz", 4, 132, GRAY)
    lcd.text(f"Gain: {GAIN}x", 4, 144, GRAY)

    lcd.text(f"Peak: {pkt_peak}", 4, 164, CYAN)

    if msg:
        lcd.hline(4, 220, 232, GRAY)
        lcd.text(msg, 4, 228, ORANGE)

    lcd.text("[1/3] Info", 170, 4, GRAY)
    lcd.show()


def draw_vu():
    lcd.clear()
    lcd.text("VU Meter", 4, 4, GREEN)
    lcd.text("[2/3]", 192, 4, GRAY)
    lcd.hline(4, 16, 232, GRAY)

    bar_w = 30
    level = min(pkt_peak * 220 // 32768, 220)
    bar_y = 240 - 18 - level
    lcd.fill_rect(4, 240 - 18, bar_w, -220, BLACK)
    lcd.fill_rect(4, bar_y, bar_w, level, vu_color(level))
    lcd.rect(4, 20, bar_w, 220, GRAY)

    lcd.text("0", 36, 20, RED)
    lcd.text("-6", 36, 55, ORANGE)
    lcd.text("-12", 36, 90, YELLOW)
    lcd.text("-24", 36, 145, GREEN)
    lcd.text("-48", 36, 210, GRAY)

    hist_x0 = 72
    hist_w = 164
    lcd.rect(hist_x0, 20, hist_w, 220, GRAY)

    cols = min(len(vu_history), hist_w)
    for i in range(cols):
        hi = (vu_pos - cols + i) % len(vu_history)
        h = vu_history[hi]
        if h > 0:
            x = hist_x0 + i
            lcd.vline(x, 240 - 18 - h, h, vu_color(h))

    lcd.show()


def draw_waveform():
    lcd.clear()
    lcd.text("Waveform", 4, 4, CYAN)
    lcd.text("[3/3]", 192, 4, GRAY)
    lcd.hline(4, 16, 232, GRAY)

    mid_y = 130
    scale_h = 100

    lcd.hline(0, mid_y, 240, GRAY)
    lcd.hline(0, mid_y - scale_h, 240, GRAY)
    lcd.hline(0, mid_y + scale_h, 240, GRAY)
    lcd.text("+max", 0, mid_y - scale_h - 10, GRAY)
    lcd.text("-max", 0, mid_y + scale_h + 2, GRAY)
    lcd.text("0", 0, mid_y - 10, GRAY)

    prev_y = mid_y
    for x in range(240):
        wi = (wave_idx + x) % 240
        s = wave_buf[wi]
        py = mid_y - (s * scale_h // 32768)
        if py < 20:
            py = 20
        elif py > 238:
            py = 238
        if x > 0:
            lcd.line(x - 1, prev_y, x, py, GREEN)
        prev_y = py

    lcd.show()


def update_display(wifi_ip, bridge, uptime_s, sent_mb, midpoint, msg=None):
    if not HAS_LCD:
        return
    if current_screen == SCREEN_INFO:
        draw_info(wifi_ip, bridge, uptime_s, sent_mb, midpoint, msg)
    elif current_screen == SCREEN_VU:
        draw_vu()
    elif current_screen == SCREEN_WAVE:
        draw_waveform()


def check_joystick():
    global current_screen
    if not HAS_LCD:
        return False
    changed = False
    if JOY_UP.value() == 0:
        current_screen = (current_screen - 1) % SCREEN_COUNT
        changed = True
        while JOY_UP.value() == 0:
            time.sleep_ms(10)
    elif JOY_DOWN.value() == 0:
        current_screen = (current_screen + 1) % SCREEN_COUNT
        changed = True
        while JOY_DOWN.value() == 0:
            time.sleep_ms(10)
    return changed

# ── WiFi connection with retry ────────────────────────────


def connect_wifi():
    if HAS_LCD:
        draw_info(None, False, 0, 0, 0, "Booting...")
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
        if HAS_LCD:
            draw_info(None, False, 0, 0, 0, "WiFi FAILED!")
        while True:
            led.toggle()
            time.sleep(0.1)

    ip = wlan.ifconfig()[0]
    print(f"\nConnected! IP: {ip}")
    led.on()
    wlan.config(pm=0xa11140)
    if HAS_LCD:
        draw_info(ip, False, 0, 0, 0, "WiFi OK")
    return wlan, ip

# ── TCP connect with retry ────────────────────────────────


def tcp_connect(wifi_ip):
    if HAS_LCD:
        draw_info(wifi_ip, False, 0, 0, 0, "Connecting TCP...")
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

# ── Main ──────────────────────────────────────────────────


def run():
    global pkt_peak, vu_pos, wave_idx

    wlan, wifi_ip = connect_wifi()

    mic = ADC(Pin(26))
    interval_us = 1_000_000 // SAMPLE_RATE

    print("Calibrating mic offset...", end="")
    total = 0
    for _ in range(500):
        total += mic.read_u16()
        time.sleep_us(1000)
    midpoint = total // 500
    print(f" midpoint={midpoint}")

    print(
        f"Streaming to {SERVER_IP}:{SERVER_PORT} at {SAMPLE_RATE} Hz, gain={GAIN}x")

    INFO_INTERVAL_MS = 60_000
    VIS_INTERVAL_MS = 2_000

    while True:
        sock = tcp_connect(wifi_ip)
        buf = bytearray(PACKET_FRAMES * 2)
        idx = 0
        t_next = time.ticks_us()
        stream_start = time.ticks_ms()
        total_bytes = 0
        last_display = 0
        pkt_count = 0

        update_display(wifi_ip, True, 0, 0, midpoint)
        pk = 0

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

                av = sample if sample >= 0 else -sample
                if av > pk:
                    pk = av

                buf[idx] = sample & 0xFF
                buf[idx + 1] = (sample >> 8) & 0xFF
                idx += 2

                if HAS_LCD and pkt_count % wave_downsample == 0:
                    wave_buf[wave_idx] = sample
                    wave_idx = (wave_idx + 1) % 240
                pkt_count += 1

                if idx >= len(buf):
                    sock.send(buf)
                    idx = 0
                    total_bytes += len(buf)
                    time.sleep_ms(1)

                    pkt_peak = pk
                    vu_level = min(pk * 220 // 32768, 220)
                    vu_history[vu_pos] = vu_level
                    vu_pos = (vu_pos + 1) % len(vu_history)
                    pk = 0

                    if check_joystick():
                        now = time.ticks_ms()
                        elapsed = time.ticks_diff(now, stream_start) // 1000
                        sent_mb = total_bytes / (1024 * 1024)
                        update_display(wifi_ip, True, elapsed,
                                       sent_mb, midpoint)
                        last_display = now
                    else:
                        now = time.ticks_ms()
                        interval = INFO_INTERVAL_MS if current_screen == SCREEN_INFO else VIS_INTERVAL_MS
                        if time.ticks_diff(now, last_display) > interval:
                            elapsed = time.ticks_diff(
                                now, stream_start) // 1000
                            sent_mb = total_bytes / (1024 * 1024)
                            update_display(
                                wifi_ip, True, elapsed, sent_mb, midpoint)
                            last_display = now

        except OSError as e:
            print(f"Connection lost ({e}), reconnecting...")
        except Exception as e:
            print(f"Unexpected error ({type(e).__name__}: {e}), recovering...")

        elapsed = time.ticks_diff(time.ticks_ms(), stream_start) // 1000
        sent_mb = total_bytes / (1024 * 1024)
        if HAS_LCD:
            draw_info(wifi_ip, False, elapsed, sent_mb,
                      midpoint, "Reconnecting...")
        try:
            sock.close()
        except:
            pass
        time.sleep(1)


# Top-level crash guard — auto-restart on any unhandled exception
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
