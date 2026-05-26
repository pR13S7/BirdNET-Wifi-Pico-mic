import gc
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

SAMPLE_RATE = 22050
PACKET_FRAMES = 512
I2S_BITS = 32
I2S_FMT = I2S.STEREO
RECONNECT_DELAY = 3

# I2S pins (WS must be SCK + 1 on Pico)
I2S_SCK = 16
I2S_WS = 17
I2S_SD = 18

led = Pin("LED", Pin.OUT)

# ── DSP functions ────────────────────────────────────────


@micropython.native
def extract_left_dc(buf32, buf16, n, dc):
    j = 0
    for i in range(0, n, 8):
        sample = buf32[i + 2] | (buf32[i + 3] << 8)
        if sample >= 0x8000:
            sample -= 0x10000
        dc = dc + (sample - dc) // 256
        out = sample - dc
        if out > 32767:
            out = 32767
        if out < -32768:
            out = -32768
        buf16[j] = out & 0xFF
        buf16[j + 1] = (out >> 8) & 0xFF
        j += 2
    return dc


@micropython.native
def compute_peak(buf, n):
    pk = 0
    for i in range(0, n, 2):
        sample = buf[i] | (buf[i + 1] << 8)
        if sample >= 0x8000:
            sample -= 0x10000
        if sample < 0:
            sample = -sample
        if sample > pk:
            pk = sample
    return pk

# ── Joystick for screen switching (Waveshare Pico LCD 1.3") ──

if HAS_LCD:
    BTN_PREV = Pin(2, Pin.IN, Pin.PULL_UP)   # Joystick UP
    BTN_NEXT = Pin(15, Pin.IN, Pin.PULL_UP)  # Key A
else:
    BTN_PREV = BTN_NEXT = None

SCREEN_INFO = 0
SCREEN_OFF = 1
SCREEN_COUNT = 2
current_screen = SCREEN_INFO

# ── Audio stats collected during streaming ────────────────

pkt_peak = 0
stat_pkts = 0
stat_worst_ms = 0
stat_dsp_ms = 0
stat_send_ms = 0
stat_mem = 0
stat_kbps = 0
stat_rssi = 0

# ── Display helpers ───────────────────────────────────────


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
        lcd.text(f"WiFi: OK  {stat_rssi}dBm", 4, 24, GREEN)
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
    lcd.text(f"Sent: {sent_mb:.1f} MB  {stat_kbps:.0f} KB/s", 4, 104, GREEN)
    lcd.text(f"Pkts: {stat_pkts}  Pk: {pkt_peak}", 4, 116, CYAN)

    lcd.hline(4, 128, 232, GRAY)

    lcd.text(f"Loop: {stat_worst_ms}ms  Crash: {crash_count}", 4, 136, ORANGE)
    lcd.text(f"DSP: {stat_dsp_ms}ms  TX: {stat_send_ms}ms", 4, 148, YELLOW)
    lcd.text(f"Mem: {stat_mem}", 4, 160, GRAY)

    if msg:
        lcd.hline(4, 220, 232, GRAY)
        lcd.text(msg, 4, 228, ORANGE)

    lcd.show()


def update_display(wifi_ip, bridge, uptime_s, sent_mb, msg=None):
    if not HAS_LCD:
        return
    if current_screen == SCREEN_OFF:
        lcd.backlight(False)
        return
    lcd.backlight(True)
    if current_screen == SCREEN_INFO:
        draw_info(wifi_ip, bridge, uptime_s, sent_mb, msg)


def check_joystick():
    global current_screen
    if not HAS_LCD:
        return False
    changed = False
    if BTN_PREV.value() == 0:
        current_screen = (current_screen - 1) % SCREEN_COUNT
        changed = True
        while BTN_PREV.value() == 0:
            time.sleep_ms(10)
    elif BTN_NEXT.value() == 0:
        current_screen = (current_screen + 1) % SCREEN_COUNT
        changed = True
        while BTN_NEXT.value() == 0:
            time.sleep_ms(10)
    return changed

# ── WiFi connection with retry ────────────────────────────


def connect_wifi():
    if HAS_LCD:
        draw_info(None, False, 0, 0, "Booting...")
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

# ── TCP connect with retry ────────────────────────────────


def tcp_connect(wifi_ip):
    if HAS_LCD:
        draw_info(wifi_ip, False, 0, 0, "Connecting TCP...")
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((SERVER_IP, SERVER_PORT))
            s.setblocking(False)
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
    global pkt_peak, stat_pkts, stat_worst_ms, stat_dsp_ms, stat_send_ms
    global stat_mem, stat_kbps, stat_rssi

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

    print(f"I2S mic initialized: INMP441 at {SAMPLE_RATE} Hz")
    print(f"Streaming to {SERVER_IP}:{SERVER_PORT}")

    INFO_INTERVAL_MS = 60_000
    HB_MS = 10_000

    dc_estimate = 0
    last_hb = time.ticks_ms()
    last_pkt_time = last_hb
    pkt_count = 0
    max_loop_ms = 0

    while True:
        sock = tcp_connect(wifi_ip)
        buf = bytearray(PACKET_FRAMES * 8)
        out = bytearray(PACKET_FRAMES * 2)
        stream_start = time.ticks_ms()
        total_bytes = 0
        last_display = 0

        update_display(wifi_ip, True, 0, 0)

        try:
            while True:
                num_read = audio_in.readinto(buf)
                if num_read == 0:
                    continue

                # Extract left channel, remove DC offset
                t0 = time.ticks_ms()
                dc_estimate = extract_left_dc(buf, out, num_read, dc_estimate)
                j = num_read // 4  # stereo 32-bit pairs → mono 16-bit bytes
                t_dsp = time.ticks_diff(time.ticks_ms(), t0)

                t0 = time.ticks_ms()
                try:
                    sock.send(out[:j])
                except OSError as e:
                    if e.errno == 11:  # EAGAIN — WiFi busy, drop packet
                        pass
                    else:
                        raise
                t_send = time.ticks_diff(time.ticks_ms(), t0)
                total_bytes += j

                pkt_peak = compute_peak(out, j)
                stat_dsp_ms = t_dsp
                stat_send_ms = t_send

                # Heartbeat
                pkt_count += 1
                now_hb = time.ticks_ms()
                pkt_ms = time.ticks_diff(now_hb, last_pkt_time)
                last_pkt_time = now_hb
                if pkt_ms > max_loop_ms:
                    max_loop_ms = pkt_ms
                if pkt_ms > 500:
                    print(f"[SLOW] {pkt_ms}ms dsp={t_dsp}ms send={t_send}ms")
                if time.ticks_diff(now_hb, last_hb) > HB_MS:
                    gc.collect()
                    stat_pkts = pkt_count
                    stat_worst_ms = max_loop_ms
                    stat_mem = gc.mem_free()
                    elapsed_s = time.ticks_diff(now_hb, stream_start) // 1000
                    stat_kbps = total_bytes / 1024 / max(elapsed_s, 1)
                    try:
                        stat_rssi = wlan.status('rssi')
                    except:
                        pass
                    print(f"[HB] pkt={pkt_count} mem={stat_mem} worst={max_loop_ms}ms kb={total_bytes//1024}")
                    last_hb = now_hb
                    max_loop_ms = 0

                # Display update
                if check_joystick():
                    now = time.ticks_ms()
                    elapsed = time.ticks_diff(now, stream_start) // 1000
                    sent_mb = total_bytes / (1024 * 1024)
                    update_display(wifi_ip, True, elapsed, sent_mb)
                    last_display = now
                else:
                    now = time.ticks_ms()
                    interval = INFO_INTERVAL_MS
                    if time.ticks_diff(now, last_display) > interval:
                        elapsed = time.ticks_diff(now, stream_start) // 1000
                        sent_mb = total_bytes / (1024 * 1024)
                        update_display(wifi_ip, True, elapsed, sent_mb)
                        last_display = now

        except OSError as e:
            print(f"Connection lost ({e}), reconnecting...")
        except Exception as e:
            print(f"Unexpected error ({type(e).__name__}: {e}), recovering...")

        elapsed = time.ticks_diff(time.ticks_ms(), stream_start) // 1000
        sent_mb = total_bytes / (1024 * 1024)
        if HAS_LCD:
            draw_info(wifi_ip, False, elapsed, sent_mb, "Reconnecting...")
        try:
            sock.close()
        except:
            pass
        audio_in.deinit()
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
